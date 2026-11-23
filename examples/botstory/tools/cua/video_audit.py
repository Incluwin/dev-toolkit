"""Audit a filmed clip by what a viewer sees in it: appdrive.video_text plus this game's two checks.

  video_audit.py transcript <clip.mp4>               timestamped player-view chat lines
  video_audit.py check <clip.mp4> [--raw <raw.mp4>]  leak check + showcase callouts vs the server; exit 1 on findings

Reads the collapsed chat line (bottom left of the 1600x900 recordings). `check` fails when a chat line
looks like a leak (GM command, debug output) or when a skill the server logged as a showcase callout
(`[showcase] <bot> said <skill>`) during the recording never shows up in the clip: said-vs-seen. The
recording window is the raw take's mtime minus its duration (film.sh keeps raws in raw/).
"""

from __future__ import annotations

import argparse
import difflib
import html
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import maple_client as mc
from appdrive import video_text

SKILL_STRINGS = mc.SERVER / "wz" / "String.wz" / "Skill.img.xml"
STRIP = (0, 832, 560, 862)  # x0, y0, x1, y1 of the collapsed chat line
FPS = 2
SKILL_MATCH_CUTOFF = 0.75  # "Infemo" -> Inferno is 0.77; no wrong snaps at 0.7 over the 18 showcase clips
INPUT_LABEL = re.compile(r"^To\s?A\w*\s*\d*$")  # the chat input box's own "To All" label, nothing typed
LEAK = re.compile(r"(^|\s)[!@][a-z]\w*|\[probe\]|0x[0-9a-f]{2}\b|exception|\bnull\b|\bdebug\b|spawnbot|agentpop"
                  r"|(?-i:\bGM\b)", re.I)
SHOWCASE_LINE = re.compile(r"^(\d\d:\d\d:\d\d)\.\d+ .*\[showcase\] (\S+) said (.+) \((\d+)\)$")


def skill_base(name: str) -> str:
    """"Soul Arrow : Bow" -> "Soul Arrow": weapon variants share a base, and OCR often drops the qualifier."""
    return name.split(":")[0].strip()


def skill_names() -> list[str]:
    names = re.findall(r'<string name="name" value="([^"]+)"', SKILL_STRINGS.read_text(encoding="utf-8"))
    return sorted({skill_base(html.unescape(name)) for name in names})


def callouts(lines: list[tuple[float, str]], names: list[str]) -> list[str]:
    """Skill names said in chat ("<bot> : <skill>"), snapped to String.wz base names to absorb OCR noise."""
    seen = []
    for _, text in lines:
        said = skill_base(text.split(":", 1)[1]) if ":" in text else ""
        match = difflib.get_close_matches(said, names, n=1, cutoff=SKILL_MATCH_CUTOFF)
        if match and match[0] not in seen:
            seen.append(match[0])
    return seen


def server_callouts(raw: Path) -> list[str] | None:
    """Showcase callouts the server logged while `raw` was recording; None when the log cannot cover it."""
    if not raw.exists() or not mc.SERVER_LOG.exists():
        return None
    end = datetime.fromtimestamp(raw.stat().st_mtime)
    start = end - timedelta(seconds=video_text.media_duration_s(raw))
    if datetime.fromtimestamp(mc.SERVER_LOG.stat().st_mtime).date() != end.date():
        return None  # the log rolled over; its HH:MM:SS lines belong to another day
    said = []
    for line in mc.SERVER_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        m = SHOWCASE_LINE.match(line)
        if m:
            at = datetime.combine(end.date(), datetime.strptime(m.group(1), "%H:%M:%S").time())
            if start <= at <= end and skill_base(m.group(3)) not in said:
                said.append(skill_base(m.group(3)))
    return said


def cmd_transcript(args) -> int:
    frames, lines = video_text.strip_lines(Path(args.clip), STRIP, FPS)
    lines = video_text.collapse(lines, skip=INPUT_LABEL)
    print(f"{frames} frames at {FPS} fps, {len(lines)} distinct chat lines")
    for second, text in lines:
        print(f"  {video_text.stamp(second)}  {text}{'   <-- leak?' if LEAK.search(text) else ''}")
    return 0


def cmd_check(args) -> int:
    clip = Path(args.clip)
    raw = Path(args.raw) if args.raw else clip.parent / "raw" / clip.name
    frames, lines = video_text.strip_lines(clip, STRIP, FPS)
    seen = callouts(lines, skill_names())
    leaks = video_text.collapse([(second, text) for second, text in lines if LEAK.search(text)], skip=INPUT_LABEL)
    said = server_callouts(raw)
    print(f"{clip.name}: {frames} frames at {FPS} fps, {len(video_text.collapse(lines, skip=INPUT_LABEL))} distinct chat lines")
    print(f"  callouts seen ({len(seen)}): {', '.join(seen) or '-'}")
    failed = bool(leaks)
    for second, text in leaks:
        print(f"  LEAK {video_text.stamp(second)}  {text}")
    if said is None:
        print(f"  server callouts: not checked (no raw take at {raw}, or the server log has rolled over)")
    elif not said:
        print("  server callouts: none logged while recording (not a showcase take, or the server predates the log line)")
    else:
        missed = [name for name in said if name not in seen]
        print(f"  server callouts ({len(said)}); missed in the clip ({len(missed)}): {', '.join(missed) or '-'}")
        failed |= bool(missed)
    print("AUDIT FAILED" if failed else "AUDIT OK")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("transcript").add_argument("clip")
    p = sub.add_parser("check")
    p.add_argument("clip")
    p.add_argument("--raw", help="the raw take (default: raw/<clip name> next to the clip)")
    args = parser.parse_args()
    return {"transcript": cmd_transcript, "check": cmd_check}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
