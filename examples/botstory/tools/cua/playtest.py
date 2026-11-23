"""A model plays the Maple client toward a goal: appdrive.agent with a candidate table built for this game.

  playtest.py "talk to Olwen and accept her quest" [--steps 12] [--dry-run]

On top of the library's "click a short text" candidates this adds talk targets: a name tag that belongs
to an NPC (String.wz) or an online character (the server's /runtime/positions) becomes "double-click the
character above it", with the NPC's title paired in. Three filters came out of live runs:
  * names inside UI panels (party window, HP box) were becoming talk targets, so a tag must sit on a
    dark name-tag box (median luminance < 120; tags measure 54-84, panels 166-246)
  * the model called the goal done after reading an NPC's ambient speech balloon, so the instructions
    say balloons are not a conversation
  * the v83 client sends no NPC talk while its character is GM-hidden or in Dark Sight, so the run warns
"""

from __future__ import annotations

import argparse
import asyncio
import html
import re
import sys

from PIL import Image, ImageStat

import maple_client as mc
from appdrive import agent, region_center

MAX_TALK_TARGETS = 12
TITLE_MAX_DX, TITLE_MAX_DY = 60, 26  # an NPC title label sits right under its name tag
# In game (1568x907) the bottom band is chat log, chat input, quickslots and the status bar, and the top
# strip is the window title: text there is never the target of a playtest click.
IN_GAME_TARGET_BAND = (25, 760)
HUD_NAME_CORNER = (200, 880)  # the status bar's character name: center left of x, below y
NAME_TAG_BOTTOM = 862         # name tags of characters on the ground reach down to the status bar
NAME_TAG_MAX_LUMINANCE = 120
NPC_STRINGS = mc.SERVER / "wz" / "String.wz" / "Npc.img.xml"
INSTRUCTIONS = (
    "You are playing MapleStory through its screen, as a human player would. `goal` is what the player wants; "
    "`player` is the player's own character; "
    "`screen.texts` is everything readable on screen now (OCR, may have typos); `history` is what was already "
    "tried. Speech balloons above characters are ambient chatter, not a conversation: a conversation is a "
    "dialog window with buttons such as NEXT, END CHAT, Yes or No. Pick the one action that best advances the "
    "goal from this screen. Choose done only if the screen shows the goal is complete, wait if the game is "
    "loading or a dialog is still appearing, and give_up if no listed action can advance the goal.")


def known_names() -> set[str]:
    """Who a name tag can belong to: NPCs (String.wz) and every online character (a tag on screen means
    online). Tab labels such as "Buddy" or "Party" are neither, so they never become talk targets."""
    names = {html.unescape(n) for n in re.findall(r'<string name="name" value="([^"]+)"',
                                                   NPC_STRINGS.read_text(encoding="utf-8"))}
    try:
        names |= set(mc.positions())
    except OSError:
        pass  # admin port down: NPC tags still work
    return names


def title_under(tag: dict, texts: list[dict]) -> dict | None:
    """An NPC's title ("World Tour Guide") is a second label right under its name tag."""
    x, y = region_center(tag)
    return next((r for r in texts if r is not tag and abs(region_center(r)[0] - x) <= TITLE_MAX_DX
                 and 0 < region_center(r)[1] - y <= TITLE_MAX_DY), None)


def player_name(regions: list[dict], names: set[str]) -> str | None:
    """The camera character, from the status bar's name label (bottom left, in game)."""
    x_max, y_min = HUD_NAME_CORNER
    return next((r["text"] for r in regions if r["kind"] == "text" and r["text"] in names
                 and region_center(r)[0] <= x_max and region_center(r)[1] >= y_min), None)


def on_name_tag(region: dict, gray: Image.Image) -> bool:
    b = region["bounds"]
    box = gray.crop((b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]))
    return ImageStat.Stat(box).median[0] < NAME_TAG_MAX_LUMINANCE


def candidates(regions: list[dict], in_game: bool, names: set[str], player: str | None,
               gray: Image.Image) -> list[agent.Candidate]:
    top, bottom = IN_GAME_TARGET_BAND if in_game else (0, 10 ** 6)
    names = names - {player}  # the player's own tag is not someone to talk to
    texts, seen = [], {player}
    for r in sorted(regions, key=lambda r: -r["confidence"]):
        if r["kind"] == "text" and r["text"] not in seen:
            seen.add(r["text"])
            texts.append(r)
    tags = [r for r in texts if in_game and r["text"] in names and IN_GAME_TARGET_BAND[0] <= region_center(r)[1]
            < NAME_TAG_BOTTOM and on_name_tag(r, gray)][:MAX_TALK_TARGETS]
    texts = [r for r in texts if top <= region_center(r)[1] < bottom]
    titles = {id(tag): title for tag in tags if (title := title_under(tag, texts))}
    title_ids = {id(t) for t in titles.values()}
    out = agent.click_candidates([r for r in texts if r["text"] not in names and id(r) not in title_ids])
    for i, r in enumerate(tags):
        x, y = region_center(r)
        who = f'"{r["text"]}"' + (f' ({titles[id(r)]["text"]})' if id(r) in titles else "")
        out.append(agent.Candidate(f"talk-{i}", f"double-click the character or NPC {who} to talk to them (opens their dialog)",
                                   "dclick", x + mc.NAME_LABEL_TO_SPRITE[0], y + mc.NAME_LABEL_TO_SPRITE[1]))
    return out + agent.stop_candidates()


def warn_if_hidden(player: str) -> None:
    try:
        state = mc.positions().get(player, {})
    except OSError:
        return
    if state.get("hidden") or state.get("darkSight"):
        print(f"warning: {player} is {'GM-hidden' if state.get('hidden') else 'in Dark Sight'}; NPC clicks will do "
              f"nothing until that is off (GM hide toggles with !hide)", flush=True)


async def play(goal: str, steps: int, dry_run: bool) -> int:
    driver = mc.create_maple_driver()  # RuntimeError (no manifest) is reported by main()
    try:
        maple = await mc.open_client(driver)
        seen = {"player": None, "warned": False}

        def build(regions: list[dict], window) -> list[agent.Candidate]:
            names = known_names()
            seen["player"] = player_name(regions, names) if maple.in_game() else None
            if seen["player"] and not seen["warned"]:
                seen["warned"] = True
                warn_if_hidden(seen["player"])
            return candidates(regions, maple.in_game(), names, seen["player"], Image.open(maple.last_shot).convert("L"))

        return await agent.play(maple, goal, steps, dry_run, build_candidates=build, instructions=INSTRUCTIONS,
                                extra_state=lambda regions: {"player": seen["player"]})
    finally:
        await driver.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("goal")
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true", help="observe and decide once; no input")
    args = parser.parse_args()
    try:
        return asyncio.run(play(args.goal, args.steps, args.dry_run))
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
