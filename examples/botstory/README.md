# BotStory example

How the generic kit is specialised for one product: a MapleStory v83 client (window title "BotStory",
no accessibility tree) and its Java server with an admin HTTP port. Read it for the patterns; to run it
you need that project.

The folder mirrors the project's own `tools/` layout, so the scripts' relative paths still work when
`tools/` is copied into the project root. Run in place by setting the environment instead:

| Variable | Meaning |
|---|---|
| `BOTSTORY_ROOT` | the folder that holds `FriendServer\` (server, client, `.env`) |
| `MAPLE_CAPABILITIES` | capability manifest (default `tools/cua/maple-capabilities.yaml`; copy the `.example.yaml`) |
| `MAPLE_CAMERA_CHARACTER` | the character the client plays; `{me}` in takes |
| `MAPLE_GM_CHARACTER` | the online GM that `maple_client.py gm` runs commands as |
| `MAPLE_CHARACTER_SLOTS` | optional `Name=x,y;Name=x,y` click points on character select (others are found by OCR) |
| `REWRITE_*` | for the release smoke test's export step (see `harness/README.md`) |

Character and account names in the scripts are placeholders (`TestGm`, `TestFriend`, `CameraChar`,
`testaccount1`): replace them with your own. Real accounts go in `*.local.txt` files, which are git-ignored
(`tools/cua/test-account.example.txt` shows the format).

## What each piece shows

| File | Pattern |
|---|---|
| `tools/cua/maple_client.py` | An `AppWindow` subclass with screen state read from pixels (`in_game`, `chat_open`), a keyboard-first login, server readback (`positions`, `buffs`), and take verbs that assert against the server (`online`, `party`, `map`, `freshbuff`, `apart`, `warp` with retry, `signoff`) |
| `tools/cua/takes/*.take` | Filming scripts: setup (not recorded), `record`, `cut`, captions, beats proven with `expectlog`/`freshbuff`, teardown |
| `tools/cua/takes/film.sh` | The pipeline: take → `post.py clip` → `video_audit.py check`; stops at the first failure |
| `tools/cua/video_audit.py` | Said-vs-seen: every skill callout the server logged during the recording must be readable in the clip, and no chat line may look like a leak |
| `tools/cua/playtest.py` | A candidate table for the model: name tags of known NPCs/characters become "talk to" actions, filtered by a measurable property (dark tag background) |
| `tools/cua/start-maple-stack.ps1` | Starting server + patch server + client so they outlive the agent session (created through WMI, not as children of the shell) and non-elevated so input reaches them; restart only through the server's own shutdown route |
| `tools/checks/*.ps1` | The "check stack": a second server on its own ports, database (seeded from a read-only dump) and token; guards that refuse the live database and tree |
| `tools/checks/botstory_verbs.py` | Plugin for the check runner: GM commands, SQL, admin routes, client steps |
| `tools/checks/checks/` | Sample checks: a green self-test, a red-on-purpose one, and one feature's set |
| `tools/release/` | Public-release smoke test: export the tree exactly as it would be published, boot it on spare ports against a fresh database, log in headlessly (`PublicSmokeLogin.java`) and with the real client |
| `tools/suite/groups.json` | Package groups for `harness/suite/run-split-suite.ps1` |
| `skill/maple-client/SKILL.md` | The Claude Code skill that teaches an agent all of the above: command table, measured facts, gotchas |

## Status

These files were moved onto the `appdrive` library and the generic check runner for this kit. Their
entry points load and the check plugin registers, but they have not been run against the game in this
form. `skill/maple-client/SKILL.md` and `tools/checks/README.md` are copied from the project and describe
the original single-file layout (`tools/cua/.venv`, `tools/checks/check.py`), plus project plan references
that mean nothing outside it.
