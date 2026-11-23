"""Drive any Windows app window from the command line through cua-driver.

  python -m appdrive [--config appdrive.toml] <command>

  windows                         list windows the manifest lets the driver see (find app_name/title)
  status                          pid, window id, bounds, focus
  shot [--out file.png]           capture the window
  regions [--find TEXT] [--json]  OCR text + icon regions with their center pixels
  clicktext TEXT [--dx --dy --double]
  click X Y | dclick X Y | drag X1 Y1 X2 Y2
  key KEY [--times N]             posted key press
  type TEXT
  hold KEY... [--ms 500]          real key hold (SendInput)
  run TAKE                        run a take script (appdrive.takes)
  play GOAL [--steps N] [--dry-run]   a model plays toward the goal (appdrive.agent)

The config file (default ./appdrive.toml) names the capability manifest and the window:

  manifest = "capabilities.yaml"
  evidence = "evidence"
  click_delivery = "foreground"      # or "background"
  log_file = "logs/app.log"          # optional, for the take step expectlog
  [window]
  app_name = "notepad.exe"           # any of app_name / title / title_prefix
  title_prefix = "Untitled"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
from pathlib import Path

from . import agent
from .perception import region_center
from .takes import TakeRunner
from .window import AppConfig, create_driver, list_windows, open_window, window_matcher


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise SystemExit(f"no config at {path} (see appdrive.example.toml)")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent
    window = raw.get("window", {})
    config = AppConfig(manifest=base / raw["manifest"], match=window_matcher(**window),
                       evidence=base / raw.get("evidence", "evidence"),
                       click_delivery=raw.get("click_delivery", "foreground"),
                       not_found=f"no window matching {window}; is the app running? (`windows` lists what is visible)")
    config.extra["log_file"] = base / raw["log_file"] if raw.get("log_file") else None
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="appdrive.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("windows")
    sub.add_parser("status")
    sub.add_parser("shot").add_argument("--out")
    p = sub.add_parser("regions")
    p.add_argument("--find")
    p.add_argument("--kind", choices=["text", "icon"])
    p.add_argument("--min-confidence", type=float, default=0.5)
    p.add_argument("--out")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("clicktext")
    p.add_argument("text")
    p.add_argument("--dx", type=int, default=0)
    p.add_argument("--dy", type=int, default=0)
    p.add_argument("--double", action="store_true")
    for name in ("click", "dclick"):
        p = sub.add_parser(name)
        p.add_argument("x", type=float)
        p.add_argument("y", type=float)
    p = sub.add_parser("drag")
    for a in ("x1", "y1", "x2", "y2"):
        p.add_argument(a, type=float)
    p = sub.add_parser("key")
    p.add_argument("key")
    p.add_argument("--times", type=int, default=1)
    sub.add_parser("type").add_argument("text")
    p = sub.add_parser("hold")
    p.add_argument("keys", nargs="+")
    p.add_argument("--ms", type=int, default=500)
    sub.add_parser("run").add_argument("script")
    p = sub.add_parser("play")
    p.add_argument("goal")
    p.add_argument("--steps", type=int, default=12)
    p.add_argument("--dry-run", action="store_true")
    return parser


async def run(args, config: AppConfig) -> int:
    try:
        driver = create_driver(config.manifest)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        if args.command == "windows":
            for w in await list_windows(driver, config):
                print(f"{w['pid']:>7} {w['app_name']:28} {w['title']!r}")
            return 0
        window = await open_window(driver, config)
        c = args.command
        if c == "status":
            print(json.dumps({"pid": window.pid, "window_id": window.window_id, "bounds": window.bounds,
                              "focused": window.focused()}))
        elif c == "shot":
            print(await window.shot(Path(args.out) if args.out else config.run_dir() / "shot.png"))
        elif c == "regions":
            if args.find:
                regions = await window.find_text(args.find, args.min_confidence)
            else:
                regions = await window.regions([args.kind] if args.kind else None, args.min_confidence,
                                               Path(args.out) if args.out else None)
            if args.json:
                print(json.dumps(regions))
            else:
                for r in regions:
                    x, y = region_center(r)
                    print(f"{r['kind']:4} {r['confidence']:.2f} ({x},{y}) {r.get('text') or r.get('label') or ''}")
        elif c == "clicktext":
            region, x, y = await window.click_text(args.text, args.dx, args.dy, args.double)
            print(f"clicked {region['text']!r} at ({x},{y})")
        elif c == "click":
            await window.click(args.x, args.y)
        elif c == "dclick":
            await window.double_click(args.x, args.y)
        elif c == "drag":
            await window.drag(args.x1, args.y1, args.x2, args.y2)
        elif c == "key":
            for _ in range(args.times):
                await window.key(args.key)
        elif c == "type":
            await window.type(args.text)
        elif c == "hold":
            await window.hold(args.keys, args.ms)
        elif c == "run":
            title = (await window.call("list_windows", pid=window.pid))["windows"][0]["title"]
            await TakeRunner(window, title, config.extra.get("log_file")).run(Path(args.script))
        elif c == "play":
            return await agent.play(window, args.goal, args.steps, args.dry_run)
        return 0
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        await driver.shutdown()


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run(args, load_config(Path(args.config))))


if __name__ == "__main__":
    sys.exit(main())
