"""Finish clips recorded by a take (appdrive.takes).

  post.py clip <raw.mp4> <out.mp4>          trim at the take's last `cut`, burn in its `caption` lines
  post.py combine <out.mp4> <clip.mp4>...   join finished clips (same encoder settings) into one video

Captions come from <raw.mp4>.steps.txt: "<seconds since record> caption <duration> <text>".
Captions share one spot (centered, BOX_TOP px from the top; set APPDRIVE_CAPTION_TOP to move it), so
each waits for the previous one to finish.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "C:/Windows/Fonts/arialbd.ttf"
FONT_SIZE = 30
LINE_WIDTH_CHARS = 64
BOX_TOP = int(os.environ.get("APPDRIVE_CAPTION_TOP", "88"))
BOX_PADDING = 18
BOX_ALPHA = 175
CAPTION_GAP_S = 0.4


def ffmpeg() -> str:
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    found = sorted(Path.home().glob("AppData/Local/Microsoft/WinGet/Packages/*/*/bin/ffmpeg.exe"))
    if not found:
        raise SystemExit("ffmpeg not found (winget install Gyan.FFmpeg)")
    return str(found[0])


def ffprobe() -> str:
    return str(Path(ffmpeg()).with_name("ffprobe.exe"))


def read_steps(raw: Path) -> tuple[float, list[tuple[float, float, str]]]:
    """Returns the cut time and (start, duration, text) captions, relative to the recording."""
    cut, captions = 0.0, []
    for line in Path(f"{raw}.steps.txt").read_text(encoding="utf-8").splitlines():
        at, _, step = line.partition(" ")
        verb, _, rest = step.partition(" ")
        if verb == "cut":
            cut = float(at)
        elif verb == "caption":
            duration, _, text = rest.partition(" ")
            captions.append((float(at), float(duration), text))
    return cut, captions


def render_caption(text: str, size: tuple[int, int], out: Path) -> None:
    font = ImageFont.truetype(FONT, FONT_SIZE)
    lines = textwrap.wrap(text, LINE_WIDTH_CHARS)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    widths = [draw.textlength(line, font=font) for line in lines]
    line_height = FONT_SIZE + 8
    box_w = int(max(widths)) + 2 * BOX_PADDING
    box_h = line_height * len(lines) + 2 * BOX_PADDING - 8
    left = (size[0] - box_w) // 2
    draw.rounded_rectangle([left, BOX_TOP, left + box_w, BOX_TOP + box_h], radius=12, fill=(0, 0, 0, BOX_ALPHA))
    for i, line in enumerate(lines):
        x = (size[0] - widths[i]) / 2
        draw.text((x, BOX_TOP + BOX_PADDING + i * line_height), line, font=font, fill=(255, 255, 255, 255))
    image.save(out)


def video_size(path: Path) -> tuple[int, int]:
    out = subprocess.run([ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    width, height = out.split(",")
    return int(width), int(height)


def clip(raw: Path, out: Path) -> None:
    cut, captions = read_steps(raw)
    size = video_size(raw)
    with tempfile.TemporaryDirectory() as tmp:
        inputs, chain, last = ["-i", str(raw)], [f"[0:v]trim=start={cut:.2f},setpts=PTS-STARTPTS[v0]"], "v0"
        free_at = 0.0  # captions share one spot, so each waits for the previous one to finish
        for n, (at, duration, text) in enumerate(captions, 1):
            png = Path(tmp) / f"caption{n}.png"
            render_caption(text, size, png)
            inputs += ["-i", str(png)]
            start = max(0.0, at - cut, free_at)
            free_at = start + duration + CAPTION_GAP_S
            chain.append(f"[{last}][{n}:v]overlay=0:0:enable='between(t,{start:.2f},{start + duration:.2f})'[v{n}]")
            last = f"v{n}"
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg(), "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chain),
                        "-map", f"[{last}]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-r", "30", str(out)], check=True)
    print(f"{out} (cut {cut:.2f}s, {len(captions)} captions)")


def combine(out: Path, clips: list[Path]) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listing:
        for path in clips:
            listing.write(f"file '{path.resolve().as_posix()}'\n")
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing.name,
                    "-c", "copy", str(out)], check=True)
    Path(listing.name).unlink()
    print(f"{out} ({len(clips)} clips)")


def main() -> None:
    if len(sys.argv) >= 4 and sys.argv[1] == "clip":
        clip(Path(sys.argv[2]), Path(sys.argv[3]))
    elif len(sys.argv) >= 4 and sys.argv[1] == "combine":
        combine(Path(sys.argv[2]), [Path(p) for p in sys.argv[3:]])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
