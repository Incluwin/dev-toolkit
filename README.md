# dev-toolkit

Tooling for building software with coding agents when the thing you build has to be **seen running**: a
real client driven on a real desktop, a real server asserted against, clips filmed and audited. It was
extracted from the development of BotStory (a MapleStory v83 private server with AI bots), where agents
drive the game client, film demos, and prove features against a second server. The reusable parts are
generic; the BotStory files are kept as a worked example.

Windows only (Win32 input, PowerShell, gdigrab).

## What is in here

| Folder | What it is | Use it for |
|---|---|---|
| [`appdrive/`](appdrive/README.md) | Python library + CLI on [cua-driver](https://github.com/trycua/cua): capture a window, read it by OCR, click/type/hold keys, run scripted **takes** while recording, trim and caption clips, read finished clips back, let a model play toward a goal | Driving any Windows app that has no test API, above all games and custom-drawn UIs |
| [`harness/checks/`](harness/README.md#checks) | `check.py`: plain-text **live checks** against a running test instance (HTTP + log assertions with retry windows, plugins for your own steps, Markdown report) | "Does the feature work on a running server", repeatable, no test framework |
| [`harness/suite/`](harness/README.md#suite) | `run-split-suite.ps1`: a big Maven suite in package groups under a memory guard and a machine-wide lock | Full test runs on a host that is also running other things (and other agent sessions) |
| [`harness/git/`](harness/README.md#git) | `rewrite-identity-for-push.pl`: publish a branch with a different author identity and without chosen folders/media, leaving local history alone | Keeping a private email and private assets out of a public repo |
| [`examples/botstory/`](examples/botstory/README.md) | The game layer on top of all of the above: login, chat, camera follow, server-readback take steps, a playtest candidate table, a second-server "check stack", a public-release smoke test, and the agent skill that documents it | Reading how the generic parts get specialised for one product |

## The ideas worth copying

1. **A capability manifest is the safety boundary.** The driver runs under a deny-by-default manifest that
   lists the one app it may see and act on. An agent with this tool cannot touch the rest of the desktop.
2. **Every step is an assertion.** A take or a check is a list of steps; any step that does not hold stops
   the run. A clip that exists is a clip whose every beat happened; a check that passed read the value back.
3. **Read back through a second channel.** After input through the screen, confirm through the server
   (admin HTTP, log, database), and after a server action, confirm on screen. Never trust "the click was sent".
4. **Expect steps retry inside a window** (`expecthttp ... within 20`), so scripts need no sleeps and
   failures report the last value seen.
5. **Test against a second instance,** with its own ports, database and token, started and stopped by
   script, and make the tools refuse the production port.
6. **Audit what a viewer sees.** Finished clips are OCR'd for leaks (debug text, admin commands) and
   for the things that were supposed to appear on screen.
7. **The model picks from a table, never free-form.** In the playtest loop the model chooses one row of
   a bounded candidate list; an answer outside it is refused and low confidence stops the run.

## Quick start

```powershell
# 1. cua-driver (see https://github.com/trycua/cua) and, for OCR, its perception extension
cua-driver extension install cua-perception
# 2. the library, into a virtual environment
python -m venv .venv
.venv\Scripts\python -m pip install -e appdrive
# 3. describe your app: copy appdrive\capabilities.example.yaml and appdrive\appdrive.example.toml, set the exe path
.venv\Scripts\python -m appdrive windows
.venv\Scripts\python -m appdrive regions
# 4. the check runner needs nothing but Python
powershell -ExecutionPolicy Bypass -File harness\checks\selftest\run.ps1
```

ffmpeg is needed for recording and clip work (`winget install Gyan.FFmpeg`).

## What was verified

- `appdrive`: run end to end against a stock Windows app (window listing, capture, OCR regions,
  click-by-text, key hold, a recorded take, trim + caption, reading the clip back, the leak gate
  failing when it should).
- `harness/checks`: the self-test (mock server) gives PASS / FAIL / WAITS-CLIENT / BLOCKED as designed.
- `harness/git`: run on a throwaway repository (identity rewritten, folder and media dropped, local
  history untouched).
- `harness/suite`: the generalised script parses; it has **not** been run since it was generalised
  from the BotStory original.
- `examples/botstory`: entry points load and report missing configuration; they have **not** been run
  against the game since being moved onto the library. The originals they were derived from are in
  daily use.

## Notes

- The cua-perception detector (OmniParser) is AGPL-3.0: fine for local use, do not host it as a service.
- No licence file is included yet; add one before publishing.
