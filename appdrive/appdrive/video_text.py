"""Read what a viewer sees in a recorded clip: OCR one fixed strip of the frame over time.

  python -m appdrive.video_text transcript <clip.mp4> --strip 0,832,560,862
  python -m appdrive.video_text check <clip.mp4> --strip 0,832,560,862 --leak "(^|\\s)[!@]\\w+|exception|debug"

`--strip x0,y0,x1,y1` is the region to read, in video pixels (a chat line, a status bar, a subtitle
area). It is sampled FPS times a second; the crops are stacked into mosaics at native resolution, so a
60 s clip takes a handful of parses instead of 120 (parses are serial and ~3 s each).

`check` exits 1 when any line matches --leak: use it as a gate after filming, e.g. to prove no debug
output, admin command or stack trace is visible in a clip meant for other people.
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from . import perception, post

GAP = 10
PER_MOSAIC_HEIGHT = 1120   # under the detector's input size, so no downscale
MIN_CONFIDENCE = 0.5
COLLAPSE_CONTAINED = 0.9


def strip_lines(clip: Path, strip: tuple[int, int, int, int], fps: float = 2) -> tuple[int, list[tuple[float, str]]]:
    """(frames read, [(second, text)]): every reading of the strip, in time order."""
    tmp = Path(tempfile.mkdtemp(prefix="video-text-"))
    x0, y0, x1, y1 = strip
    subprocess.run([post.ffmpeg(), "-v", "error", "-i", str(clip),
                    "-vf", f"fps={fps},crop={x1 - x0}:{y1 - y0}:{x0}:{y0}", str(tmp / "f%05d.png")], check=True)
    frames = sorted(tmp.glob("f*.png"))
    per_mosaic = max(1, PER_MOSAIC_HEIGHT // (y1 - y0 + GAP))
    lines = []
    for start in range(0, len(frames), per_mosaic):
        image, row = perception.mosaic([Image.open(f) for f in frames[start:start + per_mosaic]], GAP)
        path = tmp / f"mosaic{start:05d}.png"
        image.save(path)
        parts: dict[int, list[tuple[int, str]]] = {}
        for region in perception.parse_png(path):
            if region["kind"] == "text" and region["confidence"] >= MIN_CONFIDENCE:
                b = region["bounds"]
                parts.setdefault((b["y"] + b["height"] // 2) // row, []).append((b["x"], region["text"]))
        lines += [((start + i) / fps, " ".join(text for _, text in sorted(p))) for i, p in parts.items()]
    shutil.rmtree(tmp, ignore_errors=True)
    return len(frames), sorted(lines)


def collapse(lines: list[tuple[float, str]], skip: re.Pattern | None = None) -> list[tuple[float, str]]:
    """For reading: merge consecutive OCR variants of one line, keeping the most complete. OCR flicker
    drops characters, so a reading nearly contained in its neighbour merges with it."""
    def key(text: str) -> str:
        return re.sub(r"\W", "", text.lower())

    def contained(a: str, b: str) -> float:
        shorter = min(len(a), len(b)) or 1
        return sum(block.size for block in difflib.SequenceMatcher(None, a, b).get_matching_blocks()) / shorter

    out: list[tuple[float, str]] = []
    for second, text in lines:
        if skip and skip.match(text):
            continue
        if out and contained(key(out[-1][1]), key(text)) >= COLLAPSE_CONTAINED:
            out[-1] = (out[-1][0], max(out[-1][1], text, key=lambda t: len(key(t))))
        else:
            out.append((second, text))
    return out


def media_duration_s(path: Path) -> float:
    out = subprocess.run([post.ffprobe(), "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def stamp(second: float) -> str:
    return f"{int(second) // 60}:{int(second) % 60:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["transcript", "check"])
    parser.add_argument("clip")
    parser.add_argument("--strip", required=True, help="x0,y0,x1,y1 in video pixels")
    parser.add_argument("--fps", type=float, default=2)
    parser.add_argument("--leak", help="regex (case-insensitive); a matching line fails `check`")
    args = parser.parse_args()
    strip = tuple(int(v) for v in args.strip.split(","))
    leak = re.compile(args.leak, re.I) if args.leak else None
    frames, lines = strip_lines(Path(args.clip), strip, args.fps)
    if args.command == "transcript":
        lines = collapse(lines)
        print(f"{frames} frames at {args.fps:g} fps, {len(lines)} distinct lines")
        for second, text in lines:
            print(f"  {stamp(second)}  {text}{'   <-- leak?' if leak and leak.search(text) else ''}")
        return 0
    if not leak:
        parser.error("check needs --leak")
    leaks = collapse([(second, text) for second, text in lines if leak.search(text)])
    print(f"{Path(args.clip).name}: {frames} frames at {args.fps:g} fps, {len(collapse(lines))} distinct lines")
    for second, text in leaks:
        print(f"  LEAK {stamp(second)}  {text}")
    print("AUDIT FAILED" if leaks else "AUDIT OK")
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
