"""Scripted live checks: assert against a running test instance of your server, from plain text files.

  python check.py <files or folders of *.check> [--stack stack.json] [--plugin verbs.py] [--client]
                  [--report out.md] [--title "Live checks"]

A check is a script, one step per line (# comments). Header comments carry metadata:
  # id: B12            # title: Lending between two owners
  # needs: headless | client | blocked   client checks are skipped unless --client (-> WAITS-CLIENT);
                                         blocked = written but cannot run yet (# why-blocked: ...) -> BLOCKED
  # soak: <minutes>                      the instance must have been up this long (else WAITS-SOAK)

The instance under test is described by a small JSON file your start script writes (--stack, default
.stack/stack.json next to this file, or $CHECK_STACK):
  {"baseUrl": "http://127.0.0.1:18480", "token": "<bearer token>", "logFile": "C:/.../server.log",
   "commit": "abc123", "branch": "feature-x"}
Run checks against a second instance with its own ports and database, never the one people use: a
check is free to write, break and restart things.

Built-in steps:
  wait <seconds>
  post <path> <json>                 POST with that body; fails when the reply has "ok": false
  expecthttp <path> <jsonpath> <op> <value> [within <s>]      GET, then compare one value of the reply
  expectlog <regex> [within <s>]     a log line written since this check started
  rejectlog <regex> for <s>          no such line appears while the check watches for <s> seconds
  expectbootlog <regex>              a line anywhere in the log (since the instance booted)
  rejectbootlog <regex>              no such line anywhere in the log (soak checks: stalls, timeouts)

jsonpath: dotted keys from the response root; `key[3]`, `key[-1]`, `key[?field=value]` (first element
whose field equals value), `key.#` (length), `$` (the whole response), or `=<python expression over r>`
(r = the response; no spaces). Ops: == != > >= < <= contains !contains ~ (regex) exists missing.
A value is read as JSON when it parses (10, true, null, "a b"), else as a plain string.
Every expect step retries until it holds or its window (default 10 s) runs out, so checks need no sleeps.

Your own steps come from a plugin: a Python file with `def register(runner)` that adds verbs
(`@runner.verb("sql")`, `@runner.verb("say", client=True)`; helpers such as CheckFailed, poll and
expect_value are on `runner.api`), and optionally sets
`runner.uptime_minutes = lambda stack: ...` for `# soak:` and `runner.on_load = lambda stack: ...` to
validate the stack file (e.g. refuse the production port). See examples/botstory/tools/checks.

Exit code 0 when every check is PASS, WAITS-CLIENT, WAITS-SOAK or BLOCKED; 1 on any FAIL or ERROR
(ERROR = the check itself is broken).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

DEFAULT_STACK = Path(__file__).resolve().parent / ".stack" / "stack.json"
DEFAULT_WITHIN_S = 10.0
OPS = {"==", "!=", ">", ">=", "<", "<=", "contains", "!contains", "~", "exists", "missing"}
UNARY_OPS = {"exists", "missing"}
META_KEYS = "id|title|needs|source|covers|why-client|why-blocked|soak|log|status-before"
OK_STATUSES = ("PASS", "WAITS-CLIENT", "WAITS-SOAK", "BLOCKED")


class CheckFailed(Exception):
    pass


@dataclass
class Stack:
    base_url: str
    token: str
    log_file: Path | None
    raw: dict

    @staticmethod
    def load(path: Path) -> "Stack":
        if not path.exists():
            raise SystemExit(f"no test instance described at {path}; start one first (see the README)")
        s = json.loads(path.read_text(encoding="utf-8-sig"))
        log = s.get("logFile") or (str(Path(s["logDir"]) / s.get("logName", "server.log")) if s.get("logDir") else None)
        return Stack((s.get("baseUrl") or s["adminUrl"]).rstrip("/"), s.get("token", ""), Path(log) if log else None, s)

    def request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace") or "null")
        except urllib.error.HTTPError as e:
            raise CheckFailed(f"{method} {path}: HTTP {e.code} {e.read()[:200]!r}")

    def read_log(self, offset: int = 0) -> str:
        if not self.log_file:
            raise CheckFailed("this step needs a log file (logFile in the stack file)")
        with self.log_file.open("rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", "replace")


# ------------------------------------------------------------------ assertions

def resolve(doc, path: str):
    if path in ("$", ""):
        return doc
    if path.startswith("="):
        return eval(path[1:], {"__builtins__": {"len": len, "sum": sum, "min": min, "max": max, "abs": abs,
                                                "any": any, "all": all, "sorted": sorted}}, {"r": doc})
    cur = doc
    for part in re.findall(r"[^.\[\]]+|\[[^\]]*\]", path):
        if part == "#":
            cur = len(cur)
        elif part.startswith("[?"):
            key, _, want = part[2:-1].partition("=")
            match = [e for e in cur if isinstance(e, dict) and str(e.get(key)) == want]
            if not match:
                raise KeyError(part)
            cur = match[0]
        elif part.startswith("["):
            cur = cur[int(part[1:-1])]
        else:
            if not isinstance(cur, dict) or part not in cur:
                raise KeyError(part)
            cur = cur[part]
    return cur


def parse_value(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text


def compare(actual, op: str, expected) -> bool:
    if op == "exists":
        return actual is not None
    if op == "missing":
        return actual is None
    if op in ("contains", "!contains"):
        found = (str(expected) in actual) if isinstance(actual, str) else (expected in (actual or []))
        return found if op == "contains" else not found
    if op == "~":
        return re.search(str(expected), json.dumps(actual) if not isinstance(actual, str) else actual) is not None
    if op in (">", ">=", "<", "<="):
        a, e = float(actual), float(expected)
        return {">": a > e, ">=": a >= e, "<": a < e, "<=": a <= e}[op]
    same = actual == expected or str(actual) == str(expected)
    return same if op == "==" else not same


def split_assert(words: list[str]) -> tuple[list[str], str, object, float]:
    """<subject...> <op> [<value>] [within <s>] -> (subject words, op, value, within)."""
    within = DEFAULT_WITHIN_S
    if len(words) >= 2 and words[-2] == "within":
        within, words = float(words[-1]), words[:-2]
    if words and words[-1] in UNARY_OPS:
        return words[:-1], words[-1], None, within
    if len(words) < 3 or words[-2] not in OPS:
        raise CheckFailed(f"cannot read the assertion {' '.join(words)!r}: want <subject> <op> <value>")
    return words[:-2], words[-2], parse_value(words[-1]), within


def split_within(rest: str) -> tuple[str, float]:
    """"<text> within <s>" -> (text, s); the default window otherwise."""
    words = rest.split()
    if len(words) >= 2 and words[-2] == "within":
        return rest.rsplit(" within ", 1)[0], float(words[-1])
    return rest, DEFAULT_WITHIN_S


def poll(within: float, probe, describe) -> None:
    """Retry probe() -> (ok, last seen) until it holds; CheckFailed with the last value when time runs out."""
    deadline = time.monotonic() + within
    last = None
    while True:
        try:
            ok, last = probe()
        except (KeyError, IndexError, TypeError, ValueError) as e:
            ok, last = False, f"<{type(e).__name__}: {e}>"
        if ok:
            return
        if time.monotonic() >= deadline:
            raise CheckFailed(f"{describe} (last seen: {str(last)[:300]})")
        time.sleep(min(1.0, max(0.2, within / 20)))


def expect_value(within: float, fetch: Callable[[], object], op: str, want, describe: str) -> None:
    """The shared shape of expect steps: fetch a value, compare, retry within the window."""
    def probe():
        try:
            actual = fetch()
        except KeyError:
            actual = None
            if op not in UNARY_OPS:
                raise
        return compare(actual, op, want), actual
    poll(within, probe, f"{describe} {op} {want!r} did not hold within {within:g} s")


# ------------------------------------------------------------------ runner

@dataclass
class Result:
    path: Path
    meta: dict
    status: str = "PASS"        # PASS, FAIL, ERROR, WAITS-CLIENT, WAITS-SOAK, BLOCKED
    step: str = ""
    message: str = ""
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class Context:
    stack: Stack
    result: Result
    log_from: int
    allow_client: bool


Handler = Callable[[Context, str, list[str]], None]   # (ctx, rest of the line, its words)


class Runner:
    def __init__(self):
        self.verbs: dict[str, tuple[Handler, bool]] = {}
        self.uptime_minutes: Callable[[Stack], float] | None = None
        self.on_load: Callable[[Stack], None] | None = None
        self.api = sys.modules[__name__]  # CheckFailed, poll, expect_value, split_assert, ... for plugins
        _register_builtin(self)

    def verb(self, name: str, client: bool = False) -> Callable[[Handler], Handler]:
        def add(fn: Handler) -> Handler:
            self.verbs[name] = (fn, client)
            return fn
        return add

    def load_plugin(self, path: Path) -> None:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = module
        spec.loader.exec_module(module)
        module.register(self)

    def run_check(self, stack: Stack, path: Path, allow_client: bool) -> Result:
        lines = path.read_text(encoding="utf-8").splitlines()
        result = Result(path, read_meta(lines))
        if result.meta["needs"] == "client" and not allow_client:
            result.status, result.message = "WAITS-CLIENT", result.meta.get("why-client", "needs the client")
            return result
        if result.meta["needs"] == "blocked":
            result.status, result.message = "BLOCKED", result.meta.get("why-blocked", "")
            return result
        started = time.monotonic()
        try:
            soak = float(result.meta.get("soak", "0") or 0)
            if soak:
                if not self.uptime_minutes:
                    raise RuntimeError("`# soak:` needs a plugin that sets runner.uptime_minutes")
                up_min = self.uptime_minutes(stack)
                if up_min < soak:
                    result.status, result.message = "WAITS-SOAK", f"needs {soak:g} min of uptime, has {up_min:.0f}"
                    return result
            log_from = stack.log_file.stat().st_size if stack.log_file and stack.log_file.exists() else 0
            ctx = Context(stack, result, log_from, allow_client)
            for number, raw in enumerate(lines, 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                result.step = f"line {number}: {line}"
                verb, _, rest = line.partition(" ")
                if verb not in self.verbs:
                    raise CheckFailed(f"unknown step {verb!r}")
                handler, needs_client = self.verbs[verb]
                if needs_client and not allow_client:
                    raise CheckFailed(f"client step {verb!r} in a headless check")
                handler(ctx, rest, rest.split())
            result.step = ""
        except CheckFailed as e:
            result.status, result.message = "FAIL", str(e)
        except Exception as e:  # a broken check, not a failed one
            result.status, result.message = "ERROR", f"{type(e).__name__}: {e}"
        result.seconds = time.monotonic() - started
        return result


def read_meta(lines: list[str]) -> dict:
    meta = {"needs": "headless"}
    for raw in lines:
        m = re.match(rf"#\s*({META_KEYS})\s*:\s*(.*)", raw.strip())
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta


def _register_builtin(runner: Runner) -> None:
    @runner.verb("wait")
    def _wait(ctx, rest, words):
        time.sleep(float(rest))

    @runner.verb("post")
    def _post(ctx, rest, words):
        path, _, body = rest.partition(" ")
        r = ctx.stack.request("POST", path, json.loads(body or "{}"))
        if isinstance(r, dict) and r.get("ok") is False:
            raise CheckFailed(f"POST {path}: {r}")

    @runner.verb("expecthttp")
    def _expecthttp(ctx, rest, words):
        subject, op, want, within = split_assert(words)
        path, jpath = subject[0], (subject[1] if len(subject) > 1 else "$")
        expect_value(within, lambda: resolve(ctx.stack.request("GET", path), jpath), op, want, f"{path} {jpath}")

    @runner.verb("expectlog")
    def _expectlog(ctx, rest, words):
        text, within = split_within(rest)
        pattern = re.compile(text)

        def probe():
            log = ctx.stack.read_log(ctx.log_from)
            hit = pattern.search(log)
            return hit is not None, hit.group(0) if hit else f"{len(log)} bytes of log"
        poll(within, probe, f"expectlog {text!r} never matched within {within:g} s")

    @runner.verb("rejectlog")
    def _rejectlog(ctx, rest, words):
        pattern_text, _, seconds = rest.rpartition(" for ")
        pattern, end = re.compile(pattern_text), time.monotonic() + float(seconds)
        while time.monotonic() < end:
            hit = pattern.search(ctx.stack.read_log(ctx.log_from))
            if hit:
                raise CheckFailed(f"log matched {pattern_text!r}: {hit.group(0)[:250]}")
            time.sleep(1.0)

    def bootlog(ctx, rest, expect: bool, text: str | None = None, where: str = "log") -> None:
        hits = re.findall(f"^.*(?:{rest}).*$", ctx.stack.read_log() if text is None else text, re.MULTILINE)
        if expect and not hits:
            raise CheckFailed(f"no line since boot matched {rest!r}")
        if not expect and hits:
            raise CheckFailed(f"{len(hits)} line(s) since boot matched {rest!r}; first: {hits[0][:250]}")
        ctx.result.notes.append(f"{'expect' if expect else 'reject'}bootlog {rest!r}: {len(hits)} line(s) in the {where}")
    runner.bootlog = bootlog  # plugins may reuse it with another log's text

    @runner.verb("expectbootlog")
    def _expectbootlog(ctx, rest, words):
        bootlog(ctx, rest, True)

    @runner.verb("rejectbootlog")
    def _rejectbootlog(ctx, rest, words):
        bootlog(ctx, rest, False)


def write_report(stack: Stack, results: list[Result], out: Path, title: str) -> None:
    counts = {s: sum(r.status == s for r in results) for s in ("PASS", "FAIL", "ERROR", "WAITS-CLIENT", "WAITS-SOAK", "BLOCKED")}
    rows = [f"# {title}", "",
            f"Instance {stack.raw.get('branch', '?')}@{stack.raw.get('commit', '?')}, {time.strftime('%Y-%m-%d %H:%M:%S %z')}. "
            + ", ".join(f"{v} {k}" for k, v in counts.items()), "",
            "| Check | Title | Needs | Result | Detail |", "|---|---|---|---|---|"]
    for r in results:
        detail = f"{r.step}: {r.message}" if r.step else r.message
        if r.notes:
            detail = (detail + " " if detail else "") + "; ".join(r.notes)
        detail = detail.replace("|", "\\|").replace("\n", " ")
        rows.append(f"| {r.meta.get('id', r.path.stem)} | {r.meta.get('title', '')} | {r.meta['needs']} "
                    f"| **{r.status}** | {detail[:600]} |")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, plugins: list[Path] = ()) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+", help="check files or folders of *.check")
    p.add_argument("--stack", default=os.environ.get("CHECK_STACK", str(DEFAULT_STACK)), help="the instance's JSON file")
    p.add_argument("--plugin", action="append", default=[], help="a Python file with register(runner); repeatable")
    p.add_argument("--client", action="store_true", help="also run client checks (they drive a real client)")
    p.add_argument("--report", help="write a Markdown report here")
    p.add_argument("--title", default="Live checks")
    args = p.parse_args(argv)
    runner = Runner()
    for plugin in [*plugins, *(Path(x) for x in args.plugin)]:
        runner.load_plugin(Path(plugin))
    stack = Stack.load(Path(args.stack))
    if runner.on_load:
        runner.on_load(stack)
    files: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        files += sorted(path.rglob("*.check")) if path.is_dir() else [path]
    results = []
    for f in files:
        r = runner.run_check(stack, f, args.client)
        results.append(r)
        print(f"{r.status:12} {r.meta.get('id', f.stem):8} {f.name}  {r.message[:200]} {'; '.join(r.notes)[:200]}", flush=True)
    if args.report:
        write_report(stack, results, Path(args.report), args.title)
        print(f"report: {args.report}")
    return 0 if all(r.status in OK_STATUSES for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
