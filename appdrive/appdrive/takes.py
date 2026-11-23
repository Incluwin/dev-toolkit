"""Takes: step scripts that drive a window, optionally while recording it.

A take is a text file, one step per line (# comments). Built-in steps:

  wait <s>                 sleep
  key <key>                a posted key press (return, escape, ...)
  type <text>              type into whatever has focus
  hold <key...> <ms>       real key hold (SendInput)
  click <x> <y>            foreground click at capture pixels
  clicktext <text>         click the one on-screen text matching it (OCR)
  shot <file.png>          save a capture
  park                     rest the mouse in the window corner
  record <out.mp4>         start recording the window
  stop                     stop recording; writes <out.mp4>.steps.txt
  cut                      the finished clip starts here (post.py trims to the last cut)
  caption <seconds> <text> a caption burned in by post.py at this moment
  expectlog <regex>        a line the app/server logged since the take started (needs a log file)

`{name}` in a step is replaced by the runner's variable of that name (TakeRunner(..., me="Alice")
makes "{me}" the camera character), so takes need no hard-coded account or character names.

An app adds its own steps (say, warp, wait-until-online, ...) with `runner.verb("name")`. Every step
is an assertion too: a step that fails stops the take and the recording, so a clip that exists is a
clip whose every beat happened. <out.mp4>.steps.txt holds "<seconds since record> <step>" per line.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .window import AppWindow

RECORD_SCRIPT = Path(__file__).with_name("record-window.ps1")
RECORD_LIMIT_S = 900
EXPECT_LOG_TIMEOUT_S = 10


class Recorder:
    """ffmpeg gdigrab of one window title (record-window.ps1), stopped through its .stop file."""

    def __init__(self, title: str, script: Path = RECORD_SCRIPT, limit_s: int = RECORD_LIMIT_S):
        self.title, self.script, self.limit_s = title, script, limit_s
        self.process, self.out = None, None

    def start(self, out: Path) -> None:
        if self.process:
            raise RuntimeError("already recording")
        out.parent.mkdir(parents=True, exist_ok=True)
        self.out = out
        self.process = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.script),
             "-Title", self.title, "-Out", str(out), "-Seconds", str(self.limit_s)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def stop(self) -> str:
        if not self.process:
            return ""
        Path(f"{self.out}.stop").touch()
        output, _ = self.process.communicate(timeout=30)
        self.process = None
        if not self.out.exists():
            raise RuntimeError(f"recording failed: {output.strip()}")
        return output.strip()


@dataclass
class Step:
    number: int
    line: str
    verb: str
    rest: str
    words: list[str]


@dataclass
class TakeContext:
    window: AppWindow
    recorder: Recorder
    log_file: Path | None = None
    log_offset: int = 0
    recorded_at: float | None = None
    steps: list[str] = field(default_factory=list)
    vars: dict = field(default_factory=dict)   # for app verbs (e.g. the camera character's name)

    def log_since_start(self) -> str:
        if not self.log_file:
            raise RuntimeError("this step needs a log file (TakeRunner(log_file=...))")
        return self.log_file.read_bytes()[self.log_offset:].decode("utf-8", "replace")


Handler = Callable[[TakeContext, Step], Awaitable[None]]


class TakeRunner:
    def __init__(self, window: AppWindow, window_title: str, log_file: Path | None = None, **vars):
        self.ctx = TakeContext(window, Recorder(window_title), log_file, vars=vars)
        self.verbs: dict[str, Handler] = {}
        _register_builtin(self)

    def verb(self, name: str) -> Callable[[Handler], Handler]:
        def add(fn: Handler) -> Handler:
            self.verbs[name] = fn
            return fn
        return add

    async def run(self, script: Path) -> None:
        ctx = self.ctx
        if ctx.log_file and ctx.log_file.exists():
            ctx.log_offset = ctx.log_file.stat().st_size
        try:
            for number, raw in enumerate(Path(script).read_text(encoding="utf-8").splitlines(), 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                for name, value in ctx.vars.items():  # "{me}" -> TakeRunner(..., me="Alice")
                    line = line.replace("{" + name + "}", str(value))
                verb, _, rest = line.partition(" ")
                if verb not in self.verbs:
                    raise RuntimeError(f"line {number}: unknown step {verb!r}")
                print(f"[{number}] {line}", flush=True)
                try:
                    await self.verbs[verb](ctx, Step(number, line, verb, rest, rest.split()))
                except RuntimeError as e:
                    raise RuntimeError(f"line {number}: {e}") from e
                if ctx.recorded_at is not None and verb != "record":
                    ctx.steps.append(f"{time.monotonic() - ctx.recorded_at:.2f} {line}")
        finally:
            if ctx.recorder.process:
                print(ctx.recorder.stop(), flush=True)


def _register_builtin(runner: TakeRunner) -> None:
    @runner.verb("wait")
    async def _wait(ctx, step):
        await asyncio.sleep(float(step.rest))

    @runner.verb("key")
    async def _key(ctx, step):
        await ctx.window.key(step.rest)

    @runner.verb("type")
    async def _type(ctx, step):
        await ctx.window.type(step.rest)

    @runner.verb("hold")
    async def _hold(ctx, step):
        await ctx.window.hold(step.words[:-1], int(step.words[-1]))

    @runner.verb("click")
    async def _click(ctx, step):
        await ctx.window.click(float(step.words[0]), float(step.words[1]))

    @runner.verb("clicktext")
    async def _clicktext(ctx, step):
        await ctx.window.click_text(step.rest)

    @runner.verb("shot")
    async def _shot(ctx, step):
        await ctx.window.shot(Path(step.rest))

    @runner.verb("park")
    async def _park(ctx, step):
        ctx.window.park_cursor()

    @runner.verb("record")
    async def _record(ctx, step):
        ctx.recorder.start(Path(step.rest))
        ctx.recorded_at = time.monotonic()
        ctx.steps.clear()
        await asyncio.sleep(1.0)  # ffmpeg start-up

    @runner.verb("stop")
    async def _stop(ctx, step):
        if ctx.recorded_at is None:
            raise RuntimeError("stop outside a recording")
        ctx.steps.append(f"{time.monotonic() - ctx.recorded_at:.2f} stop")
        out = ctx.recorder.out
        print(ctx.recorder.stop(), flush=True)
        Path(f"{out}.steps.txt").write_text("\n".join(ctx.steps) + "\n", encoding="utf-8")
        ctx.recorded_at = None

    @runner.verb("cut")
    async def _cut(ctx, step):
        if ctx.recorded_at is None:
            raise RuntimeError("cut outside a recording")

    @runner.verb("caption")
    async def _caption(ctx, step):
        float(step.words[0])  # rendered in post from the step log; validate the duration now
        if ctx.recorded_at is None:
            raise RuntimeError("caption outside a recording")

    @runner.verb("expectlog")
    async def _expectlog(ctx, step):
        pattern, deadline = re.compile(step.rest), time.monotonic() + EXPECT_LOG_TIMEOUT_S
        while not pattern.search(ctx.log_since_start()):
            if time.monotonic() > deadline:
                raise RuntimeError(f"the log never matched {step.rest!r}")
            await asyncio.sleep(0.5)
