# appdrive

Drive one Windows application window through [cua-driver](https://github.com/trycua/cua), from Python or
the command line, under a capability manifest that limits the driver to that app.

Built for apps with **no accessibility tree** (a DirectX game), where the only views are pixels and OCR.
It works on ordinary apps too.

## Setup

1. Install cua-driver (the `cua-driver` binary on PATH) and the Python wheel of the same version
   (`pip install -e .` pulls `cua-driver` and `pillow`).
2. For `regions` / `clicktext` / `play` / video text: `cua-driver extension install cua-perception`.
3. Copy `capabilities.example.yaml` to `capabilities.yaml` and put your app's exe path in it.
4. Copy `appdrive.example.toml` to `appdrive.toml` and name the window (`python -m appdrive windows`
   lists what the manifest lets the driver see).
5. If the app runs elevated, input from a normal process is dropped (UIPI). Run the app non-elevated
   (`__COMPAT_LAYER=RunAsInvoker` works for exes that merely request admin).

## Command line

```
python -m appdrive status | shot | regions [--find TEXT] | clicktext TEXT [--dx --dy --double]
python -m appdrive click X Y | dclick X Y | drag X1 Y1 X2 Y2 | key KEY | type TEXT | hold KEY... --ms 500
python -m appdrive run demo.take
python -m appdrive play "open the settings and turn sound off" --dry-run
python -m appdrive.post clip raw.mp4 out.mp4          # trim to the last `cut`, burn in captions
python -m appdrive.video_text check out.mp4 --strip 0,832,560,862 --leak "exception|debug"
```

Coordinates are pixels of a fresh capture of the window (not the screen). `regions` prints each
region's center in that space, so its output feeds `click` directly.

## Library

```python
from appdrive import AppConfig, AppWindow, TakeRunner, create_driver, open_window, window_matcher

config = AppConfig(manifest=Path("capabilities.yaml"), match=window_matcher(app_name="mygame.exe"))
driver = create_driver(config.manifest)
window = await open_window(driver, config)          # or your own AppWindow subclass
await window.click_text("Start")
await window.hold(["right"], 800)
regions = await window.regions(["text"])
```

| Module | Contents |
|---|---|
| `window.py` | `AppWindow`: focus, capture, shot, regions, find_text, click, click_text, drag, key, type, hold, pin, park_cursor |
| `perception.py` | `perceive` (column-split parse at native resolution), `match_text` (equal > contains > fuzzy), `only_match`, `mosaic` |
| `win32.py` | SendInput key holds, foreground-lock workaround, responsiveness wait |
| `takes.py` | `TakeRunner` (step scripts with your own verbs), `Recorder` (ffmpeg gdigrab) |
| `post.py` | `clip` (trim + captions from the take's step log), `combine` |
| `video_text.py` | OCR one strip of a clip over time; `check` as a leak gate |
| `agent.py` | `play`: capture → candidates → one typed Choice (TypeSafe Jev) → input, with a confidence floor |

Subclass `AppWindow` for app state (`in_game()`, `chat_open()`), and register verbs on a `TakeRunner`
for app steps. `examples/botstory/tools/cua/maple_client.py` does both.

## Takes

```
# demo.take
clicktext Start
record out/raw/demo.mp4
wait 1
cut
caption 4 What this clip shows
hold right 1500
expectlog level loaded
stop
```

Built-in steps: `wait key type hold click clicktext shot park record stop cut caption expectlog`.
`{name}` is replaced by the runner's variables. A failed step stops the take and the recording.
`stop` writes `<out>.steps.txt`; `post.py clip` uses it to trim to the last `cut` and place captions.

## What was learned the hard way

- Skip the accessibility walk for a game: there is no UIA tree and the walk times out, stalling the capture.
- Clicks need `delivery_mode="foreground"`; posted keys and typed text land only while the app is foreground.
- Movement and hotkeys are read from real key state: only a SendInput hold reaches them. Release the
  keys if focus is lost, or the hold types into whatever the user switched to.
- Windows refuses to switch foreground unless your process sent the last input: an Alt tap lifts the lock.
- An app that is still loading stops pumping messages; input sent then gets a "(Not Responding)" ghost.
- Capture before each pixel input: the driver grounds clicks on the latest capture of that window.
- OCR: the detector downsamples past ~1280 px and small fonts vanish. Crop at native resolution;
  upscaling adds nothing. Parses are serial (~3 s each, cold worker), so stack crops into one mosaic.
- Clicking by text is only safe when exactly one region matches; otherwise fail and list what was found.
- Stylised or graphic text does not OCR; keep fixed pixels for those and OCR for the rest.
- A game draws its own cursor where the mouse rests: park it in a corner before recording.
