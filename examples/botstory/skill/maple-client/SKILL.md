---
name: maple-client
description: Drive the real BotStory MapleStory client (MapleStory.exe, window "BotStory") with cua-driver - start the server/client stack, log in, send chat and GM commands, walk, follow a bot with the camera, capture screenshots. Use for filming bot demos, reproducing client-side bugs, or any task that needs the game client operated.
---

# Driving the MapleStory client with cua

Two routes, same capability manifest (`tools/cua/maple-capabilities.yaml`: only
MapleStory.exe and Launch Game.exe, no launch/terminate, writes only under the
server's `docs/agents/evidence`):

- **`tools/cua/maple_client.py`** - scripted commands, one Bash call each. Runs
  cua-driver in-process via its Python SDK (venv `tools/cua/.venv`, cua-driver 0.30.3
  since 2026-09-28; upgrade the venv with `uv pip install --python tools/cua/.venv/Scripts/python.exe <wheel>`,
  since it has no pip).
- **`cua-maple` MCP tools** - for exploring something the script does not cover.

## 1. Bring the stack up

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File tools/cua/start-maple-stack.ps1
```

Starts only what is down: game server (`launch.bat`, rebuilds first, minimized
console), patch server (8485, hidden), client with `__COMPAT_LAYER=RunAsInvoker`. All are
created through WMI so they outlive the agent session. It reports the client
ready only after the launcher splash closes and the client answers messages
again (it stops answering while it loads; clicking during that stall makes
Windows show a "(Not Responding)" ghost and the click is lost). A fresh client
was ready 31 s after launch. `-NoServer` / `-NoClient`.

To restart the server (to deploy a build), use `-NoClient -RestartServer`: it shuts
the server down through the admin port, waits for java to exit, then starts it
again. Don't kill java or start `launch.bat` by hand: that is how dead "Cosmic"
consoles piled up. A console closes by itself once its server exits, and a start
closes any left over from older launches.

## 2. Commands (run from MapleServer/)

**Keyboard first (2026-11-21).** Reach for these before any click; a click needs a fresh capture, exact pixels
and the foreground, and is the first thing a moved window or a changed UI breaks:

| Need | Use | Clicks |
|---|---|---|
| A GM command (warp, hide, level, items, `!pvp ...`) | `gm "!warp 260000000" [--as TestGm]` | none, no client input at all |
| Log in | `login` (Return logs in, picks the highlighted world, enters the channel, starts the character) | none |
| Say something / a player `@command` | `chat "..."` (Return, type, Return, Return) | none |
| A game hotkey (Crew window `u`, skills, inventory) | `press u` (real key tap) | none |
| NPC window: Next / OK / Yes, or End Chat / No | `dialog next`, `dialog yes`, `dialog no`, `dialog end` | none |
| A number box | `key backspace`, `key 1` `key 0` `key 0`, `dialog ok` | none |
| Walk, jump, attack | `hold right --ms 800` | none |
| An NPC menu option, a custom window's button or tab, talking to someone | `clicktext "..."` / `click` / `dclick` | yes: the client has no keys for these |

`gm` appends to the server's test queue (`logs/gm-command-queue.txt`, `server.ENABLE_GM_TEST_QUEUE`), so it works
with the client closed, minimised or mid-dialog; the GM it runs as must be online, and it fails with the server's
own reason ("not a GM or no client", "character offline"). `login --click-login` keeps the old mouse path.
`login --account-file` still clicks the two fields (which one has the keyboard depends on "Save login ID") and
`login --character` still clicks the slot.

```bash
PY=tools/cua/.venv/Scripts/python.exe
$PY tools/cua/maple_client.py status              # pid, window, login|in-game, focused
$PY tools/cua/maple_client.py login               # Return x3: log in -> highlighted world -> channel -> selected character, pins window at 0,0
$PY tools/cua/maple_client.py login --character OtherChar   # any character: unknown names are found by their label (OCR)
$PY tools/cua/maple_client.py gm "!warp 240050300"          # server-side: no chat box, no focus needed
$PY tools/cua/maple_client.py press u             # real key tap (SendInput), for hotkeys the game reads from DirectInput
$PY tools/cua/maple_client.py dialog next --times 3         # NPC window by keyboard
$PY tools/cua/maple_client.py chat "!warp 240050300"
$PY tools/cua/maple_client.py chat "grind" --shot # --shot saves a capture after sending
$PY tools/cua/maple_client.py hold left --ms 800  # real key hold (walk, jump=alt, attack=ctrl)
$PY tools/cua/maple_client.py follow ProbeHero --seconds 45   # camera: walks toward the bot when |dx| > 250
$PY tools/cua/maple_client.py key return          # posted key (UI keys only, not movement)
$PY tools/cua/maple_client.py click 543 822       # foreground click, pixels of a fresh 1568x907 in-game capture
$PY tools/cua/maple_client.py shot [--out PATH]   # PNG under docs/agents/evidence/cua/<timestamp>/
$PY tools/cua/maple_client.py regions             # OCR text + icon regions: kind, confidence, (center x,y), text
$PY tools/cua/maple_client.py regions --find "Start"   # text regions matching it; feed the center to click
$PY tools/cua/maple_client.py clicktext "END CHAT"      # foreground click on the one matching text (dialog options, buttons)
$PY tools/cua/maple_client.py clicktext Olwen --dy -35 --double   # talk to someone: double-click above their name tag
$PY tools/cua/playtest.py "talk to Olwen and accept her quest" [--steps 12] [--dry-run]   # Jev plays toward a goal
```

`regions` (and `Maple.regions()` / `Maple.find_text()` for take scripts) is the
only semantic view of the client, since it has no UIA tree. It needs the
cua-perception extension (section 7). Bounds are capture pixels, the same frame
`click` takes. Text matching goes equal, then containing, then near miss (OCR
reads "Never mind" as "lever mind"), and `clicktext` refuses unless exactly one
region matches.

`playtest.py` is the jev-use loop adapted to this client. Each step reads the
screen and builds a table of candidate actions: click a short text, talk to
someone whose name tag is a known NPC (String.wz) or an online character
(`/runtime/positions`), or wait / done / give_up. Jev picks one option from that
table, and the tool performs it and looks again. Rules:
- The player's own tag, read from the status bar, is never a talk target.
- A talk target must sit on a dark name-tag box: median luminance under 120 (tags measure 54-84).
  The same names in the party window or the party HP box sit on light panels (166-246).
- The instructions tell Jev that speech balloons are ambient chatter; a conversation is a dialog
  window with NEXT / END CHAT / Yes / No.
- An NPC's title rides along in its talk option.
- An answer below 0.35 confidence stops the run instead of guessing.
- Each step takes about 9 s and 3k Jev input tokens.
- Evidence (captures + `playtest.jsonl`) goes to the run dir.
- `--dry-run` decides once and sends no input, so it is safe while someone else
  drives the client.

Exit code 1 plus `error: ...` on stderr means the step did not happen; read it,
do not retry blindly. `login` saves a capture per step in its run dir.

Verify outcomes on the server, not from transport success:
- positions: `maple_client.positions()` (AdminControlPort `/runtime/positions`,
  token from `FriendServer/.env`).
- chat: POST `/actions/toggle/chat-log {"enabled":true}` (in-memory only;
  configured value is false, set it back), then read `logs/chat.log`.

## 3. What the client accepts (measured, cua-driver 0.29.1)

| Input | Background (default) | Foreground |
|---|---|---|
| Window capture | works with `include_accessibility_tree:false` | - |
| Mouse click | silently ignored | works, one click |
| UI keys, chat text (`press_key`, `type_text`) | work only while the client is the foreground window | work |
| Movement / attack keys | ignored | an instant press is too short; needs a held key |
| `set_window_frame` | works | - |

- The client has no UIA provider: a default `get_window_state` burns its 1 s tree
  budget and then the capture times out. Always pass `include_accessibility_tree:false`.
- Foreground restore is not reliable: after a foreground click the client usually
  stays focused. That is what makes the background keys work afterwards.
- cua has no key-hold on Windows, so `hold`/`follow` use SendInput directly, and
  only while the client is focused (they release and stop if focus leaves).
- Chat: Return toggles the input and a send leaves it open (and an open input
  swallows movement keys). `chat` reads the state, opens the input if needed, types,
  sends, then closes it and checks it closed; it never clicks. State (2026-11-21): with
  a collapsed chat bar the "To All" box pixel (40,850) is dark blue only while the input
  is open; with the bar pinned open (the small button right of the field) the box is
  always drawn, so the tool then looks for the caret or typed text in the field
  (81-500, 851-860). The old box-only check read a pinned bar as always open,
  which made `chat`, `hold` and `close_chat` fail with "chat input would not close".
  Since 2026-11-23 the check reads that strip from the screen (`ImageGrab`, about 60 ms a
  sample, for 0.8 s) after focusing the client: a cua capture takes about 1 s and the caret
  blinks 0.3 s on / 0.3 s off, so the old 1.3 s capture watch saw one or two frames and
  could read an open input as closed ("chat input did not open", worse while recording).
  Don't poll a blinking or short-lived cue with captures.
- Escape with nothing open brings up the game menu; a second Escape closes it. Don't use
  Escape to "make sure" a window is closed.
- A posted `key u` does not reach game hotkeys (DirectInput); `press u` does.
- NPCs with String.wz dialogue lines and no script entry in Npc.wz (Tigun, Jano) are
  answered by the client itself: the server never sees the click. A stock flavour line
  where a server window should open means that, not a server bug.
- A companion's card can bounce back with "Things changed meanwhile." while it fights;
  move to a town first (`gm "!warp ..."`) before walking its menus.
- The saved login (HKCU\Software\Wizet\MapleStory LMA/LastPassword) is rewritten by every
  login click with whatever is in the fields; back the key up before a second-account
  login and restore it after. If LastPassword is empty, `login` fails with "incorrect
  password" and only the user typing it once brings it back.
- In-game captures are 1568x907 (scaled from the 1602x927 window); login screens
  are 800x625 unscaled. Pixel inputs use the latest capture's space.
- MCP `zoom` has no `session` field, so it cannot see captures taken with a session label.
- NPC clicks: while the character is GM-hidden (or in Dark Sight) the client sends no NPC talk at all;
  the click just does nothing, while double-clicking a player still opens char info. Check
  `/runtime/positions` (`hidden`, `darkSight`) and unhide with `!hide` (it toggles) before talking to NPCs.
- If the user is typing elsewhere, foreground input steals focus from them; ask
  them to hold off while a sequence runs.

## 4. Filming with take scripts (how the 2026-09-26 showcases were made)

```bash
bash tools/cua/takes/film.sh class-01-hero chat-02-asks-then-learns   # run takes, finish each, stop on failure
$PY tools/cua/maple_client.py run tools/cua/takes/<take>.take          # one take
$PY tools/cua/post.py clip <raw.mp4> <out.mp4>                         # trim at `cut`, burn captions
$PY tools/cua/post.py combine <out.mp4> <clip.mp4>...                  # join finished clips
$PY tools/cua/video_audit.py check <out.mp4>                           # what a viewer sees (film.sh runs it)
$PY tools/cua/video_audit.py transcript <clip.mp4>                     # timestamped player-view chat lines
```

`film.sh` audits each finished clip and stops on failure, like a failed take. The audit reads the
collapsed chat line twice a second, stacking 28 strips per parse, so a clip takes about 13 s. It
fails when:
- a chat line looks like a leak (`!command`, `[probe]`, `0x..`, exception text), or
- a showcase callout the server logged (`[showcase] <bot> said <skill>` in cosmic-log, while the raw
  take was recording) never shows up in the clip.

Callouts are snapped to String.wz skill names ("Infemo" becomes Inferno; weapon qualifiers are
dropped). The server log line exists from the 2026-09-28 build on. Older takes report the server
side as "not checked".

A `.take` file is one step per line (see `cmd_run`): `warp <map> <min_y>` (re-warps until on the
floor; `!warp` picks a random spawn point), `darksight CameraChar`, `say`, `wait`, `hold`, `follow`,
`record`/`stop` (step times go to `<raw>.steps.txt`), `cut`, `caption <s> <text>`, `party <name> <n>`,
`online`/`offline`, `expectlog <regex>` (a server-log beat since the take started),
`freshbuff <name> <skill> <s>` (that buff on <name>, cast within <s> s, per `/runtime/buffs`),
`apart <name> <dx> <dy> <s>` (<name> outside that box around the camera), `signoff <bot...>`.
Every take verifies its setup and teardown against the server, so a failed step means a retake.

- Camera character: **CameraChar is a L130 Shadower** with Dark Sight (4001003 L20, 200 s, no speed
  penalty) on key A. `darksight` turns GM hide off (it toggles) and casts it.
- `!spawnbot <bot> showcase`: fresh two-person party + the bot names each skill aloud and rotates
  through its kit while grinding normally. `!leaveparty` dissolves the party after the take.
- Chat takes: check each beat with `expectlog`. Before `chat-02`, `forget-demo-phrases.sh` deletes the
  learned demo phrases (film.sh does it, also before `chat-04`). Rehearse new lines with
  `rehearse-*.take` first.
- One untargeted "logout" + "yes" signs off every one of the owner's bots (there is no per-bot
  logout), so a multi-bot take signs off once: `signoff ProbeHero ProbeBish`.
- A map change reopens the chat input; `chat(..., close=False)` then `close_chat` after the map loads.
- Captions are for context only; skill names stay in the bots' balloons.

## 5. Recording tools

Window-scoped video: `FriendServer/BotStory Server/tools/video/record-client.ps1
-Out <mp4> -Seconds N` (gdigrab of the BotStory window). cua's own
`start_recording(record_video:true)` records the whole primary display, but its
per-action turn folders (before/after PNG, click marker, action.json) are good
evidence for a reproduction. Stage and bot recipes: memory
`computer-use-client-driving`, `bot-demo-videos-2026-09-23`.

## 6. Upstream pieces

- `jev-use` recipe (cua repo `skills/jev-use`, `libs/cua-driver/examples/jev-use`):
  `playtest.py` adapts it. Upstream runs perception over the MCP binary, which parses
  only the full frame and so loses the client's small fonts, and its `core.py`
  rejects the `affine` coordinate space our in-game captures report.

## 7. Perception (cua-perception 0.2.1, installed 2026-09-28)

Signed extension at `~/.cua-driver/extensions/cua-perception` (OmniParser icon
detector + PP-OCRv5 via ONNX Runtime, local, no network). Check it with
`cua-driver extension status cua-perception --self-test`. Update by downloading
the next `cua-perception-v*` release's Windows catalog + archive into one folder,
then `cua-driver extension inspect`, then `extension update`. The catalog expires
one year after release.

- **Script route:** the in-process Python SDK always answers `not_installed`,
  because only the `cua-driver` binary wires the extension resolver. So
  `maple_client.py regions` saves the capture and runs
  `cua-driver perception parse --image <png> --capture <json> --json`. Each parse
  cold-starts the worker: about 3 s. The result is read-only (`action_eligible:false`).
  Click its center with the normal `click`, which re-captures first.
- **Parse at native resolution.** The detector shrinks anything wider than 1280 px, which
  loses dialog options, chat and name tags. So `perceive()` parses an in-game capture
  (1568 px) as two overlapping halves (about 6.6 s); login screens (800 px) take one parse.
  Parses are serial: two at once fail with "extension cua-perception is already being
  modified". To batch, stack crops into one image of at most 1280 px.
- **MCP route (verified on 0.30.3):** `get_window_state` (`include_accessibility_tree:false`)
  returns a `capture_id`. Pass it to `parse_visual_regions`
  (`{capture_id, options:{kinds, min_confidence, max_regions}}`). The capture must
  be in the session the parse runs in, and the MCP parse schema has **no
  `session` field**. So take that capture **without** a `session` label; a labelled
  capture fails with `capture_generation_mismatch`. The SDK accepts `session` on
  both calls. A capture-bound `click` (same `capture_id`) consumes the capture, so
  re-capture after every action. About 3.2 s per parse; NPC speech bubbles are
  readable too.
- **What it reads (measured):** window titles, party/guild list names, jobs and
  levels, HP/MP bars (`HP |280/280]`), meso, map name, and login and character-select
  text and stats. Weak spots: stylised text ("LV." often reads "LU", "Inventory"
  reads "InVENTORY") and every icon is the generic `icon-class-0`. Match text
  loosely, use icons only as clickable boxes, and keep server-side verification
  as the source of truth.
- The detector is AGPL-3.0: fine for local use. Do not redistribute the bundle
  or serve it over a network without reading `perception-third-party-notices.md`.
