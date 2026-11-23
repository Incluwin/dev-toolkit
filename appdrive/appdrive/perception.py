"""Read a capture as text/icon regions through the cua-perception extension (OCR + icon detector).

For apps with no accessibility tree (games, custom-drawn UIs) this is the only semantic view. Facts
measured with cua-driver 0.29-0.30 on Windows:

  * only the `cua-driver` binary resolves the installed extension; the in-process Python SDK answers
    not_installed, so a capture is saved as a PNG and parsed with `cua-driver perception parse`
  * each parse cold-starts a worker (~3 s) and parses are serial: two at once fail with
    "extension ... is already being modified". Batch by stacking crops into one image (`mosaic`).
  * the detector shrinks anything wider than ~1280 px, which loses small fonts. Parse wide captures in
    overlapping columns at native resolution (`perceive` does this); upscaling adds nothing.
  * regions are read-only (`action_eligible: false`): click a region's center with the normal click.
"""

from __future__ import annotations

import difflib
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image

PERCEPTION_TIMEOUT_S = 120
PERCEPTION_MAX_EDGE = 1280  # the detector's input size
COLUMN_OVERLAP = 60
TEXT_MATCH_CUTOFF = 0.8
DESKTOP_SOURCE = {"kind": "primary_desktop", "display_id": "primary"}  # for images that are not a live capture


def cua_driver_exe() -> str:
    exe = shutil.which("cua-driver")
    if exe:
        return exe
    import cua_driver  # the SDK's bundled copy is the fallback
    return str(Path(cua_driver.__file__).parent / "bin" / "cua-driver.exe")


def parse_png(png: Path, source: dict | None = None) -> list[dict]:
    """Regions of one PNG. `source` is the capture source a live capture came from
    ({"kind": "window", "pid": ..., "window_id": ...}); any other image uses the desktop source."""
    meta = png.with_suffix(".capture.json")
    meta.write_text(json.dumps({"source": source or DESKTOP_SOURCE}))
    proc = subprocess.run([cua_driver_exe(), "perception", "parse", "--image", str(png), "--capture", str(meta),
                           "--json"], capture_output=True, text=True, timeout=PERCEPTION_TIMEOUT_S)
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        parsed = {"stdout": proc.stdout, "stderr": proc.stderr}
    if "regions" not in parsed:
        raise RuntimeError(f"perception parse failed: {parsed}")
    return parsed["regions"]


def perceive(png: Path, source: dict | None = None, max_edge: int = PERCEPTION_MAX_EDGE) -> list[dict]:
    """Regions of a capture PNG in its own pixels. A capture wider than the detector's input is parsed
    as overlapping columns, each region kept by the column that holds its center."""
    image = Image.open(png)
    if image.width <= max_edge:
        return parse_png(png, source)
    columns = math.ceil(image.width / max_edge)
    width = image.width / columns
    regions = []
    for i in range(columns):
        left, right = round(i * width), round((i + 1) * width)
        x0, x1 = max(0, left - COLUMN_OVERLAP), min(image.width, right + COLUMN_OVERLAP)
        part = png.with_name(f"{png.stem}-x{x0}.png")
        image.crop((x0, 0, x1, image.height)).save(part)
        for region in parse_png(part, source):
            region["bounds"]["x"] += x0
            if left <= region["bounds"]["x"] + region["bounds"]["width"] / 2 < right:
                regions.append(region)
    return regions


def region_center(region: dict) -> tuple[int, int]:
    b = region["bounds"]
    return b["x"] + b["width"] // 2, b["y"] + b["height"] // 2


def match_text(regions: list[dict], text: str, cutoff: float = TEXT_MATCH_CUTOFF) -> list[dict]:
    """Regions matching `text`, best tier only: equal, else containing it, else a near miss
    (OCR drops or swaps letters: "Never mind" reads "lever mind")."""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    wanted = norm(text)
    tiers: tuple[list[dict], list[dict], list[dict]] = ([], [], [])
    for region in regions:
        got = norm(region.get("text") or "")
        if not got or not wanted:
            continue
        if got == wanted:
            tiers[0].append(region)
        elif wanted in got:
            tiers[1].append(region)
        elif difflib.SequenceMatcher(None, wanted, got).ratio() >= cutoff:
            tiers[2].append(region)
    return next((tier for tier in tiers if tier), [])


def only_match(matches: list[dict], text: str) -> dict:
    """Clicking by text is only safe when exactly one region matches."""
    if len(matches) != 1:
        found = ", ".join(f"{r['text']!r} at {region_center(r)}" for r in matches) or "nothing"
        raise RuntimeError(f"need exactly one text region matching {text!r}, found {found}")
    return matches[0]


def mosaic(images: list[Image.Image], gap: int = 10) -> tuple[Image.Image, int]:
    """Stack same-width crops into one image for a single parse, with black rows between them so OCR
    never joins two crops. Returns (image, row height): a region's crop index is center_y // row."""
    row = images[0].height + gap
    out = Image.new("RGB", (images[0].width, len(images) * row), "black")
    for i, image in enumerate(images):
        out.paste(image, (0, i * row))
    return out, row
