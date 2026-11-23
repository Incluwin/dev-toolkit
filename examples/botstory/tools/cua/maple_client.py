"""Drive the BotStory MapleStory client through cua-driver: appdrive plus what is specific to this game.

A worked example of building an app layer on appdrive. The generic parts (capture, OCR, clicks, key
holds, takes, recording) come from the library; this file adds the game's screens (login, world and
character select), its chat input, server readback through the admin port, and take steps that assert
against the server (online, party, map, freshbuff, ...).

What the v83 client accepts, measured against cua-driver 0.29.1 on 2026-09-26:

  * window capture works; skip the UIA walk (the game has no UIA provider and
    the walk times out, which also stalls the capture)
  * mouse clicks land only with delivery_mode="foreground"
  * posted keys and WM_CHAR text (background) land only while the game is the
    foreground window
  * movement/attack keys are read from real key state and need a real hold;
    cua has no key-hold on Windows, so `hold` uses SendInput itself, and only
    while the game is focused
  * `regions` reads the screen through the cua-perception extension; it is the
    only semantic view of the client, since there is no UIA tree.

Usage (from the project root; BOTSTORY_ROOT overrides the root, default: two folders above tools/cua):
  python tools/cua/maple_client.py status
  ... maple_client.py shot
  ... maple_client.py regions --find "Start"
  ... maple_client.py login --character <name>
  ... maple_client.py chat "follow me"
  ... maple_client.py hold right --ms 800
  ... maple_client.py follow <bot> --me <camera character> --seconds 45
  ... maple_client.py run tools/cua/takes/class-02-paladin.take --me <camera character>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from PIL import ImageGrab

try:
    import appdrive
except ImportError:  # running from the kit without `pip install -e appdrive`
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "appdrive"))
    import appdrive
from appdrive import AppConfig, AppWindow, TakeRunner, create_driver, open_window, region_center, only_match

MAPLE_ROOT = Path(os.environ.get("BOTSTORY_ROOT") or Path(__file__).resolve().parents[2])
SERVER = MAPLE_ROOT / "FriendServer" / "BotStory Server"
MANIFEST = Path(os.environ.get("MAPLE_CAPABILITIES") or Path(__file__).with_name("maple-capabilities.yaml"))
ENV_FILE = MAPLE_ROOT / "FriendServer" / ".env"
EVIDENCE = SERVER / "docs" / "agents" / "evidence" / "cua"
ADMIN_RUNTIME = os.environ.get("MAPLE_ADMIN_RUNTIME", "http://127.0.0.1:8480/runtime")
WINDOW_TITLE = "BotStory"
DEFAULT_CAMERA = os.environ.get("MAPLE_CAMERA_CHARACTER", "")  # the character the client plays (--me)
DEFAULT_GM = os.environ.get("MAPLE_GM_CHARACTER", "")          # the online GM `gm` commands run as (--as)

IN_GAME_MIN_WIDTH = 1000  # login screens are 802 px wide, the game is 1602
# Login-screen pixels in the 800x625 capture of the 802x627 window.
LOGIN_BUTTON = (636, 270)
LOGIN_ID_FIELD = (513, 270)
PASSWORD_FIELD = (513, 299)
SCANIA_WORLD = (244, 178)
# In-game pixels in the 1568x907 capture of the 1602x927 window. The chat input
# bar only exists while chat is open; its "To All" box reads dark blue
# (~51,85,119), and the collapsed chat log that replaces it reads grey (225,225,225).
CHAT_TO_ALL_PIXEL = (40, 850)
# The typing field right of the "To All" box. With the chat bar pinned open the box is always drawn, so an open
# input shows only by what is in the field: its caret (x 83, blinking) or typed text.
CHAT_FIELD_BOX = (81, 851, 500, 860)
# The caret blinks 0.3 s on, 0.3 s off. A cua capture takes about 1 s (a 4 MB PNG), so captures alias with the blink
# and can all land on the off phase; the watch reads just this strip from the screen instead (about 60 ms a sample).
CHAT_STRIP = (30, 845, 500, 862)
CHAT_CARET_WATCH_S = 0.8
GM_QUEUE = SERVER / "logs" / "gm-command-queue.txt"
SERVER_LOG = SERVER / "logs" / "cosmic-log.log"
# Character select: known sprite click points (same 800x625 capture) can be listed as name=x,y pairs in
# MAPLE_CHARACTER_SLOTS ("Alice=250,370;Bob=378,370"); any other name is found by its label through OCR.
CHARACTER_SLOTS = {name: tuple(int(v) for v in at.split(","))
                   for name, _, at in (pair.partition("=") for pair in
                                       os.environ.get("MAPLE_CHARACTER_SLOTS", "").split(";") if pair)}
NAME_LABEL_TO_SPRITE = (-4, -35)  # the click point (sprite) sits this far from the center of its name label
UI_SETTLE_S = 0.2
# Camera: keep the followed character 150-300 px to the side of the camera character.
FOLLOW_MIN_PX = 150
FOLLOW_MAX_PX = 300
FOLLOW_STEP_MS = 400
DARK_SIGHT_KEY = "a"  # the camera character's keymap binds Dark Sight (4001003) to A
WARP_ATTEMPTS = 8
FRESH_BUFF_TIMEOUT_S = 20  # a gathering waits up to 12 s for the party before its buffers cast
SIGNOFF_ATTEMPTS = 2
SIGNOFF_CONFIRM_WAIT_S = 4


def maple_config() -> AppConfig:
    pid = int(os.environ["MAPLE_PID"]) if os.environ.get("MAPLE_PID") else None

    def match(w: dict) -> bool:
        if pid:  # a second client for two-player tests (its title may be changed for recording)
            return w["pid"] == pid and w["title"].startswith(WINDOW_TITLE)
        return w["app_name"].lower() == "maplestory.exe" and w["title"] == WINDOW_TITLE

    return AppConfig(manifest=MANIFEST, match=match, evidence=EVIDENCE, session="maple",
                     not_found="no BotStory window; run tools/cua/start-maple-stack.ps1")


class Maple(AppWindow):
    _capture_scale: tuple | None = None   # (window width, x scale, y scale) from capture to screen pixels

    def in_game(self) -> bool:
        return self.bounds["width"] >= IN_GAME_MIN_WIDTH

    async def chat_open(self) -> bool:
        """Whether the chat input is taking keys. A collapsed chat bar hides the "To All" box while the input is
        closed; a pinned bar always draws it, and then only the caret or typed text in the field tells.
        Reads the screen, so the client is brought to the front first (every caller is about to send keys anyway)."""
        await self.focus()
        if self._capture_scale is None or self._capture_scale[0] != self.bounds["width"]:
            capture = await self.capture()
            self._capture_scale = (self.bounds["width"], self.bounds["width"] / capture.width,
                                   self.bounds["height"] / capture.height)
        _, sx, sy = self._capture_scale
        x0, y0, x1, y1 = CHAT_STRIP
        screen_box = (round(self.bounds["x"] + x0 * sx), round(self.bounds["y"] + y0 * sy),
                      round(self.bounds["x"] + x1 * sx), round(self.bounds["y"] + y1 * sy))
        to_all = (round((CHAT_TO_ALL_PIXEL[0] - x0) * sx), round((CHAT_TO_ALL_PIXEL[1] - y0) * sy))
        field = (round((CHAT_FIELD_BOX[0] - x0) * sx), round((CHAT_FIELD_BOX[1] - y0) * sy),
                 round((CHAT_FIELD_BOX[2] - x0) * sx), round((CHAT_FIELD_BOX[3] - y0) * sy))
        deadline = time.monotonic() + CHAT_CARET_WATCH_S
        while True:
            strip = ImageGrab.grab(screen_box)
            r, _, b = strip.getpixel(to_all)
            if not (b > 100 and r < 90):
                return False
            if strip.crop(field).convert("L").getextrema()[0] < 110:
                return True
            if time.monotonic() > deadline:
                return False
            await asyncio.sleep(0.05)

    async def close_chat(self) -> None:
        """An open chat input (a map change can reopen it) swallows movement keys; an empty Return closes it."""
        if self.in_game() and await self.chat_open():
            await self.key("return")
            await asyncio.sleep(UI_SETTLE_S)
            if await self.chat_open():
                raise RuntimeError("chat input would not close")

    async def chat(self, text: str, close: bool = True) -> None:
        """close=False for commands that change map: the map load reopens the input at an unpredictable
        moment, so the caller closes it (close_chat) once the new map has settled."""
        # Return toggles the chat input, and a send leaves it open, so read its state first.
        if not self.in_game():
            raise RuntimeError("not in game; run login first")
        if not await self.chat_open():
            await self.key("return")
            await asyncio.sleep(UI_SETTLE_S)
            if not await self.chat_open():
                raise RuntimeError("chat input did not open")
        await self.type(text)  # the open input has the keyboard: no click needed
        await self.key("return")
        if not close:
            return
        # The input stays focused after a send and swallows movement keys; an empty Return closes it.
        await asyncio.sleep(UI_SETTLE_S)
        await self.key("return")
        await asyncio.sleep(UI_SETTLE_S)
        if await self.chat_open():
            raise RuntimeError("message sent, but the chat input is still open")


def run_dir() -> Path:
    return maple_config().run_dir()


async def open_client(driver) -> Maple:
    return await open_window(driver, maple_config(), Maple)


def create_maple_driver():
    return create_driver(MANIFEST)


# ------------------------------------------------------------ server readback

def admin_get(path: str) -> dict:
    token = next((line.split("=", 1)[1].strip().strip('"') for line in ENV_FILE.read_text().splitlines()
                  if line.startswith("ADMIN_CONTROL_TOKEN=")), None)
    request = urllib.request.Request(f"{ADMIN_RUNTIME}/{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def positions() -> dict[str, dict]:
    return {p["name"]: p for p in admin_get("positions")["positions"]}


def buffs(name: str) -> list[dict]:
    """Active buffs: [{"source": skill id, "usedMs": ..., "durationMs": ...}]."""
    return admin_get(f"buffs/{name}").get("buffs", [])


async def expect(name: str, field: str | None, value, timeout_s: float = 8) -> None:
    """Waits until `name` is online (field None, value True) or offline (False), or its field equals value."""
    deadline = time.monotonic() + timeout_s
    while True:
        player = positions().get(name)
        if field is None and (player is not None) == value:
            return
        if field is not None and player is not None and player.get(field) == value:
            return
        if time.monotonic() > deadline:
            wanted = ("online" if value else "offline") if field is None else f"{field}={value}"
            raise RuntimeError(f"expectation failed: {name} {wanted} (now {player})")
        await asyncio.sleep(0.5)


def gm_command(character: str, command: str, wait_s: float = 2.0) -> None:
    """Run a GM command without the client: the server tails logs/gm-command-queue.txt
    (server.ENABLE_GM_TEST_QUEUE) and runs each "<character>|<!command>" line as that online GM."""
    if not command.startswith(("!", "@")):
        raise RuntimeError("a GM command starts with ! or @")
    if not character:
        raise RuntimeError("which GM? pass --as <character> or set MAPLE_GM_CHARACTER")
    if not GM_QUEUE.parent.is_dir():
        raise RuntimeError(f"no server log folder at {GM_QUEUE.parent}")
    log_from = SERVER_LOG.stat().st_size if SERVER_LOG.exists() else 0
    with GM_QUEUE.open("a", encoding="utf-8", newline="\n") as queue:
        queue.write(f"{character}|{command}\n")
    time.sleep(wait_s)
    if SERVER_LOG.exists() and SERVER_LOG.stat().st_size >= log_from:
        with SERVER_LOG.open("r", encoding="utf-8", errors="replace") as log:
            log.seek(log_from)
            refused = [line.strip() for line in log if "GmTestQueueWatcher" in line and " WARN " in line]
        if refused:
            raise RuntimeError(f"the server refused it: {refused[-1][-160:]}")


# ------------------------------------------------------------------ commands

async def character_slot(maple: Maple, name: str) -> tuple[int, int]:
    """A character-select slot not in CHARACTER_SLOTS: the sprite above its name label."""
    x, y = region_center(only_match(await maple.find_text(name), name))
    return x + NAME_LABEL_TO_SPRITE[0], y + NAME_LABEL_TO_SPRITE[1]


async def login(maple: Maple, character: str | None, account_file: str | None, click_login: bool) -> None:
    if maple.in_game():
        print("already in game")
        return
    shots = run_dir()
    await maple.wait_responsive()
    if account_file:  # another account: fill the fields from a local note, never echo the password
        creds = dict(line.split("=", 1) for line in Path(account_file).read_text(encoding="utf-8").splitlines()
                     if "=" in line and not line.startswith("#"))
        for field, value in ((LOGIN_ID_FIELD, creds["account"]), (PASSWORD_FIELD, creds["password"])):
            await maple.click(*field)
            await maple.key("end")
            for _ in range(24):
                await maple.key("backspace")
            for i in range(0, len(value), 6):  # long type_text goes through the clipboard; short chunks are typed
                await maple.type(value[i:i + 6])
    # Keyboard all the way: Return logs in from either field, picks the highlighted world (the one played last,
    # Scania on a fresh client) and enters the highlighted channel. --click-login keeps the mouse path.
    if click_login:
        await maple.click(*LOGIN_BUTTON)
    else:
        await maple.key("return")
    await asyncio.sleep(2.5)
    await maple.shot(shots / "1-world-select.png")
    if click_login:
        await maple.click(*SCANIA_WORLD)
    else:
        await maple.key("return")  # the highlighted world: its channel list opens
    await asyncio.sleep(1.5)
    await maple.key("return")  # enter the selected channel
    await asyncio.sleep(2.5)
    if character:
        await maple.click(*(CHARACTER_SLOTS.get(character) or await character_slot(maple, character)))
        await asyncio.sleep(0.5)
    await maple.shot(shots / "2-character-select.png")
    await maple.key("return")  # start the selected character
    for _ in range(40):
        await asyncio.sleep(0.5)
        await maple.refresh()
        if maple.in_game():
            await maple.pin(0, 0)
            if character and character not in positions():
                raise RuntimeError(f"in game, but {character} is not online; see {shots}")
            print(f"in game; window pinned at 0,0; step captures in {shots}")
            return
    raise RuntimeError(f"not in game after 20 s; see captures in {shots}")


async def follow(maple: Maple, me_name: str, name: str, seconds: float) -> None:
    """Camera: keep the followed character on screen but never under the camera character."""
    await maple.close_chat()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        now = positions()
        me, them = now.get(me_name), now.get(name)
        if not me or not them or me["map"] != them["map"]:
            raise RuntimeError(f"{me_name} and {name} are not both online on one map")
        dx = them["x"] - me["x"]
        if abs(dx) > FOLLOW_MAX_PX:
            await maple.hold(["right" if dx > 0 else "left"], FOLLOW_STEP_MS)
        elif abs(dx) < FOLLOW_MIN_PX:
            await maple.hold(["left" if dx > 0 else "right"], FOLLOW_STEP_MS // 2)
        else:
            await asyncio.sleep(0.2)


DIALOG_KEYS = {"next": "return", "ok": "return", "yes": "return", "accept": "return",
               "no": "escape", "end": "escape", "decline": "escape"}


def take_runner(maple: Maple, me: str) -> TakeRunner:
    """appdrive's take steps (wait, hold, key, record, stop, cut, caption, park, expectlog, ...) plus the game's:
      say <text> | follow <name> <s> | party <name> <members> | online <name> | offline <name> | map <name> <id>
      hide <name> | darksight <name> | warp <map> [min_y] | signoff <bot...> (logout + yes, all offline)
      freshbuff <name> <skill id> <s> (that buff is on <name>, cast within the last <s> seconds)
      apart <name> <dx> <dy> <s> (within <s> seconds, <name> is at least <dx> or <dy> px from the camera)"""
    runner = TakeRunner(maple, WINDOW_TITLE, SERVER_LOG, me=me)

    @runner.verb("hold")  # replaces the library's: an open chat input would swallow the keys
    async def _hold(ctx, step):
        await maple.close_chat()
        await maple.hold(step.words[:-1], int(step.words[-1]))

    @runner.verb("say")
    async def _say(ctx, step):
        await maple.chat(step.rest)

    @runner.verb("follow")
    async def _follow(ctx, step):
        await follow(maple, me, step.words[0], float(step.words[1]))

    @runner.verb("party")
    async def _party(ctx, step):
        await expect(step.words[0], "partySize", int(step.words[1]))

    @runner.verb("online")
    async def _online(ctx, step):
        await expect(step.rest, None, True)

    @runner.verb("offline")
    async def _offline(ctx, step):
        await expect(step.rest, None, False, timeout_s=20)

    @runner.verb("map")
    async def _map(ctx, step):
        await expect(step.words[0], "map", int(step.words[1]))

    @runner.verb("hide")
    async def _hide(ctx, step):
        # !hide toggles, so only send it when the server says the character is visible.
        if not positions().get(step.rest, {}).get("hidden"):
            await maple.chat("!hide")
        await expect(step.rest, "hidden", True)

    @runner.verb("warp")
    async def _warp(ctx, step):
        # !warp picks a random spawn point; re-warp until it lands at or below min_y (the floor).
        map_id, min_y = int(step.words[0]), int(step.words[1]) if len(step.words) > 1 else -10_000
        for _ in range(WARP_ATTEMPTS):
            await maple.chat(f"!warp {map_id}", close=False)
            await asyncio.sleep(2.5)
            await maple.close_chat()
            at = positions().get(me, {})
            if at.get("map") == map_id and at.get("y", -10_000) >= min_y:
                break
        else:
            raise RuntimeError(f"no spawn point on map {map_id} at y >= {min_y} after {WARP_ATTEMPTS} warps")
        await maple.refresh()

    @runner.verb("signoff")
    async def _signoff(ctx, step):
        # "logout" makes every one of the owner's bots ask to confirm, and "yes" signs them all off;
        # answer only after they had time to ask. Retry once.
        for attempt in range(SIGNOFF_ATTEMPTS):
            await maple.chat("logout")
            await asyncio.sleep(SIGNOFF_CONFIRM_WAIT_S)
            await maple.chat("yes")
            try:
                for name in step.words:
                    await expect(name, None, False, timeout_s=20)
                break
            except RuntimeError:
                if attempt == SIGNOFF_ATTEMPTS - 1:
                    raise

    @runner.verb("freshbuff")
    async def _freshbuff(ctx, step):
        # A recast, not a leftover: the buff must be younger than the window.
        name, skill, within_ms = step.words[0], int(step.words[1]), float(step.words[2]) * 1000
        deadline = time.monotonic() + FRESH_BUFF_TIMEOUT_S
        while not any(b["source"] == skill and b["usedMs"] <= within_ms for b in buffs(name)):
            if time.monotonic() > deadline:
                raise RuntimeError(f"no fresh {skill} on {name} (now {buffs(name)})")
            await asyncio.sleep(0.5)

    @runner.verb("apart")
    async def _apart(ctx, step):
        # Party buffs reach a box around the caster, so "apart" = outside that box on either axis.
        name, min_dx, min_dy = step.words[0], int(step.words[1]), int(step.words[2])
        deadline = time.monotonic() + float(step.words[3])
        while True:
            now = positions()
            a, b = now.get(me, {}), now.get(name, {})
            if a.get("map") == b.get("map") and (abs(b.get("x", 0) - a.get("x", 0)) >= min_dx
                                                 or abs(b.get("y", 0) - a.get("y", 0)) >= min_dy):
                return
            if time.monotonic() > deadline:
                raise RuntimeError(f"{name} never got outside {min_dx}x{min_dy} px (me {a}, them {b})")
            await asyncio.sleep(0.5)

    @runner.verb("darksight")
    async def _darksight(ctx, step):
        # The camera character is a Shadower: GM hide off (it toggles), then Dark Sight
        # (bound to A, 200 s at level 20) so mobs ignore it for the whole take.
        if positions().get(step.rest, {}).get("hidden"):
            await maple.chat("!hide")
            await expect(step.rest, "hidden", False)
        if not positions().get(step.rest, {}).get("darkSight"):
            await maple.close_chat()
            await maple.hold([DARK_SIGHT_KEY], 80)
        await expect(step.rest, "darkSight", True)

    return runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("shot").add_argument("--out")
    p = sub.add_parser("regions", help="OCR text + icon regions (center pixels) via cua-perception")
    p.add_argument("--find", help="only text regions matching this")
    p.add_argument("--kind", choices=["text", "icon"])
    p.add_argument("--min-confidence", type=float, default=0.5)
    p.add_argument("--out", help="also save the parsed capture here")
    p.add_argument("--json", action="store_true", help="print the raw regions")
    p = sub.add_parser("clicktext", help="foreground click on the one text region matching TEXT (dialog options, buttons)")
    p.add_argument("text")
    p.add_argument("--dx", type=int, default=0, help="offset from the text's center (e.g. a sprite above a name tag)")
    p.add_argument("--dy", type=int, default=0)
    p.add_argument("--double", action="store_true", help="double-click (NPCs, residents)")
    p = sub.add_parser("login")
    p.add_argument("--character", help="pick this character at character select")
    p.add_argument("--account-file", help="local account=/password= note to log in with instead of the remembered login")
    p.add_argument("--click-login", action="store_true", help="click Log in and the world instead of pressing Return")
    p = sub.add_parser("chat")
    p.add_argument("text")
    p.add_argument("--shot", action="store_true")
    p.add_argument("--keep-open", action="store_true", help="leave the chat input open (for commands that open a window)")
    for name, text in (("click", "foreground click at pixels of a fresh capture"),
                       ("dclick", "foreground double-click at pixels of a fresh capture (char info, NPCs)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("x", type=float)
        p.add_argument("y", type=float)
    sub.add_parser("close_chat", help="close the chat input if it is open")
    p = sub.add_parser("drag", help="foreground drag between two capture pixels (items to quickslots)")
    for a in ("x1", "y1", "x2", "y2"):
        p.add_argument(a, type=float)
    sub.add_parser("type", help="type text into whatever has focus (dialog boxes)").add_argument("text")
    p = sub.add_parser("key")
    p.add_argument("key")
    p.add_argument("--times", type=int, default=1)
    p = sub.add_parser("hold")
    p.add_argument("keys", nargs="+")
    p.add_argument("--ms", type=int, default=500)
    p = sub.add_parser("press", help="tap real keys (SendInput): game hotkeys read DirectInput, which `key` never reaches")
    p.add_argument("keys", nargs="+")
    p.add_argument("--ms", type=int, default=120)
    p.add_argument("--times", type=int, default=1)
    p = sub.add_parser("dialog", help="answer the NPC window by keyboard")
    p.add_argument("answer", choices=sorted(DIALOG_KEYS))
    p.add_argument("--times", type=int, default=1)
    p = sub.add_parser("gm", help="run a GM command through the server's test queue: no client input at all")
    p.add_argument("text", help='the command, e.g. "!warp 260000000"')
    p.add_argument("--as", dest="character", default=DEFAULT_GM, help="the online GM it runs as")
    p = sub.add_parser("follow")
    p.add_argument("name")
    p.add_argument("--me", default=DEFAULT_CAMERA)
    p.add_argument("--seconds", type=float, default=30)
    p = sub.add_parser("run", help="run a take script (see take_runner and appdrive.takes)")
    p.add_argument("script")
    p.add_argument("--me", default=DEFAULT_CAMERA)
    return parser


async def dispatch(maple: Maple, args) -> None:
    c = args.command
    if c == "status":
        print(json.dumps({"pid": maple.pid, "window_id": maple.window_id, "bounds": maple.bounds,
                          "screen": "in-game" if maple.in_game() else "login", "focused": maple.focused()}))
    elif c == "shot":
        print(await maple.shot(Path(args.out) if args.out else run_dir() / "shot.png"))
    elif c == "regions":
        if args.find:
            regions = await maple.find_text(args.find, args.min_confidence)
        else:
            regions = await maple.regions([args.kind] if args.kind else None, args.min_confidence,
                                          Path(args.out) if args.out else None)
        if args.json:
            print(json.dumps(regions))
            return
        for r in regions:
            x, y = region_center(r)
            print(f"{r['kind']:4} {r['confidence']:.2f} ({x},{y}) {r.get('text') or r.get('label') or ''}")
    elif c == "clicktext":
        region, x, y = await maple.click_text(args.text, args.dx, args.dy, args.double)
        print(f"clicked {region['text']!r} at ({x},{y})")
    elif c == "login":
        await login(maple, args.character, args.account_file, args.click_login)
    elif c == "chat":
        # --keep-open: the closing Return would press the default button of a window the command opened.
        await maple.chat(args.text, close=not args.keep_open)
        if args.shot:
            print(await maple.shot(run_dir() / "chat.png"))
    elif c == "click":
        await maple.click(args.x, args.y)
    elif c == "dclick":
        await maple.double_click(args.x, args.y)
    elif c == "close_chat":
        await maple.close_chat()
    elif c == "drag":
        await maple.drag(args.x1, args.y1, args.x2, args.y2)
    elif c == "type":
        await maple.type(args.text)
    elif c == "key":
        for _ in range(args.times):
            await maple.key(args.key)
    elif c == "hold":
        await maple.close_chat()
        await maple.hold(args.keys, args.ms)
    elif c == "press":
        await maple.close_chat()
        for _ in range(args.times):
            await maple.hold(args.keys, args.ms)
            await asyncio.sleep(UI_SETTLE_S)
    elif c == "dialog":
        # Return is the NPC window's default button, Escape its End Chat / No.
        for _ in range(args.times):
            await maple.key(DIALOG_KEYS[args.answer])
            await asyncio.sleep(UI_SETTLE_S)
    elif c in ("follow", "run"):
        if not args.me:
            raise RuntimeError("which character is the client playing? pass --me or set MAPLE_CAMERA_CHARACTER")
        if c == "follow":
            await follow(maple, args.me, args.name, args.seconds)
        else:
            await take_runner(maple, args.me).run(Path(args.script))


async def main() -> int:
    args = build_parser().parse_args()
    if args.command == "gm":  # server side only: needs no client window
        try:
            gm_command(args.character, args.text)
            return 0
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    driver = None
    try:
        driver = create_maple_driver()
        await dispatch(await open_client(driver), args)
        return 0
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        if driver:
            await driver.shutdown()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
