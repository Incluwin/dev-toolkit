"""BotStory steps for the kit's check runner (harness/checks/check.py --plugin botstory_verbs.py).

Server steps (all through the check stack: its admin port, token, database and logs folder):
  gm <operator> <command...>        POST /actions/gm-command as <operator> (headless operator: spawnbot, skillprobe,
                                    combatarena, pvp); brings that GM character online headlessly if it is offline
  online <character>                bring a GM character online headlessly (gm-command with a harmless command)
  queue <character> <command...>    any GM command through the check stack's logs/gm-command-queue.txt, run as that
                                    online GM (ENABLE_GM_TEST_QUEUE)
  post <route> <json>               POST /actions/<route> with that body (e.g. citizens, living-reload, whisper)
  sql <statement>                   a write on the check database (POST /actions/sql/run with allow_mutation)
  expectadmin <route> <jsonpath> <op> <value> [within <s>]      GET /runtime/<route>
  expectsql <query> <op> <value> [within <s>]       first column of the first row
  expectgm <regex> [within <s>]     a line of the latest gm-command run's output
  expectbootlog / rejectbootlog     in a check marked "# log: live" these read the LIVE server's log (read only)
Client steps (only with --client; each runs one maple_client.py command against the open client):
  say <text> | press <keys...> | dialog <answer> | clicktext <text> | shot <name> | login <args...>
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CHECK_ROOT = Path(__file__).resolve().parent
MAPLE_CLIENT = CHECK_ROOT.parent / "cua" / "maple_client.py"
ROOT = Path(os.environ.get("BOTSTORY_ROOT") or CHECK_ROOT.parents[1])
# Read only, and only by expectbootlog/rejectbootlog in a check marked "# log: live": those checks are about what
# the live world did over hours, which a fresh check stack cannot show. Nothing is ever sent to the live server.
LIVE_LOG = ROOT / "FriendServer" / "BotStory Server" / "logs" / "cosmic-log.log"
LIVE_ADMIN_PORT = 8480
HARMLESS_ONLINE_COMMAND = "pvp"  # usage text only; brings the operator online


def register(runner):
    api = runner.api
    CheckFailed = api.CheckFailed

    def on_load(stack):
        if stack.base_url.endswith(f":{LIVE_ADMIN_PORT}"):
            raise SystemExit("stack.json points at the live admin port; refusing")
        if stack.log_file is None and stack.raw.get("logDir"):
            stack.log_file = Path(stack.raw["logDir"]) / "cosmic-log.log"
        elif stack.log_file is not None and stack.log_file.name == "server.log":
            stack.log_file = stack.log_file.with_name("cosmic-log.log")
    runner.on_load = on_load
    runner.uptime_minutes = lambda stack: stack.request("GET", "/runtime/uptime").get("millis", 0) / 60000

    def gm(ctx, operator, command):
        r = ctx.stack.request("POST", "/actions/gm-command", {"operator": operator, "command": command})
        if not r.get("ok"):
            raise CheckFailed(f"gm-command refused: {r}")

    @runner.verb("online")
    def _online(ctx, rest, words):
        gm(ctx, rest, HARMLESS_ONLINE_COMMAND)

    @runner.verb("gm")
    def _gm(ctx, rest, words):
        gm(ctx, words[0], " ".join(words[1:]))

    @runner.verb("queue")
    def _queue(ctx, rest, words):
        with (ctx.stack.log_file.parent / "gm-command-queue.txt").open("a", encoding="utf-8", newline="\n") as q:
            q.write(f"{words[0]}|{' '.join(words[1:])}\n")
        time.sleep(2.0)

    @runner.verb("post")
    def _post(ctx, rest, words):
        route, _, body = rest.partition(" ")
        r = ctx.stack.request("POST", f"/actions/{route}", json.loads(body or "{}"))
        if isinstance(r, dict) and r.get("ok") is False:
            raise CheckFailed(f"POST {route}: {r}")

    def sql(ctx, statement, mutate=False):
        body = {"sql": statement, **({"allow_mutation": "true"} if mutate else {})}
        r = ctx.stack.request("POST", "/actions/sql/run", body)
        if not r.get("ok"):
            raise CheckFailed(f"sql refused: {r}")
        return r

    @runner.verb("sql")
    def _sql(ctx, rest, words):
        sql(ctx, rest, mutate=True)

    @runner.verb("expectadmin")
    def _expectadmin(ctx, rest, words):
        subject, op, want, within = api.split_assert(words)
        route, jpath = subject[0], (subject[1] if len(subject) > 1 else "$")
        route = route if route.startswith("/") else f"/runtime/{route}"
        api.expect_value(within, lambda: api.resolve(ctx.stack.request("GET", route), jpath), op, want,
                         f"{route} {jpath}")

    @runner.verb("expectsql")
    def _expectsql(ctx, rest, words):
        subject, op, want, within = api.split_assert(words)
        query = " ".join(subject)

        def first_value():
            rows = sql(ctx, query).get("rows") or []
            return next(iter(rows[0].values())) if rows else None
        api.expect_value(within, first_value, op, want, f"sql {query!r}")

    @runner.verb("expectgm")
    def _expectgm(ctx, rest, words):
        import re
        text, within = api.split_within(rest)
        pattern = re.compile(text)

        def probe():
            out = "\n".join(ctx.stack.request("GET", "/runtime/gm-command").get("lines") or [])
            return pattern.search(out) is not None, out[-300:]
        api.poll(within, probe, f"expectgm {text!r} never matched within {within:g} s")

    def bootlog(expect):
        def step(ctx, rest, words):
            if ctx.result.meta.get("log") == "live":
                runner.bootlog(ctx, rest, expect, LIVE_LOG.read_bytes().decode("utf-8", "replace"), "live log")
            else:
                runner.bootlog(ctx, rest, expect, where="check-stack log")
        return step
    runner.verb("expectbootlog")(bootlog(True))
    runner.verb("rejectbootlog")(bootlog(False))

    def client(command):
        def step(ctx, rest, words):
            cmd = command(rest, words)
            done = subprocess.run([sys.executable, str(MAPLE_CLIENT), *cmd], capture_output=True, text=True)
            if done.returncode != 0:
                raise CheckFailed(f"maple_client {cmd[0]} failed: {done.stderr.strip()[-300:]}")
        return step
    runner.verb("say", client=True)(client(lambda rest, words: ["chat", rest]))
    runner.verb("press", client=True)(client(lambda rest, words: ["press", *words]))
    runner.verb("dialog", client=True)(client(lambda rest, words: ["dialog", *words]))
    runner.verb("clicktext", client=True)(client(lambda rest, words: ["clicktext", rest]))
    runner.verb("shot", client=True)(client(lambda rest, words: ["shot", "--out", rest]))
    runner.verb("login", client=True)(client(lambda rest, words: ["login", *words]))
