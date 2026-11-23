# Scripted live checks (WORLD_SYSTEMS_PLAN I3)

A second BotStory server, the **check stack**, runs beside the live one and checks are scripts that assert against
it. Nothing here sends anything to the live server (admin 8480, login 8484, database `cosmic`).

| | Live server | Check stack |
|---|---|---|
| Tree | `FriendServer\BotStory Server` (main checkout) | any worktree whose source has `ServerConfig.applySecondServerOverrides` (branch `feat/world-live-checks`, 378667cfe, and anything built on it) |
| Jar | `target\Cosmic.jar` | `target\CheckStack.jar` (a copy, so `start-maple-stack.ps1`, which treats every `Cosmic.jar` java as live, never sees it) |
| Login / channels | 8484 / 7575-7577 | 18484 / 17575-17577 |
| Admin port | 127.0.0.1:8480, token from `.env` | 127.0.0.1:18480, a fresh token per start (in `.stack\stack.json`) |
| Database | `cosmic` | `cosmic_check`, rebuilt from a read-only dump of `cosmic` |
| Logs, GM queue | the main checkout's `logs\` | the worktree's `logs\` (`cosmic-log.log`, `gm-command-queue.txt`, `check-stack-console.log`) |
| Jev | on | off unless `-WithJev` (its calls cost money) |

## Files

| File | What it does |
|---|---|
| `check-stack-common.ps1` | Ports, names, guards (refuses `cosmic`, refuses the live tree), the live server's fingerprint |
| `seed-check-db.ps1` | `mysqldump --single-transaction` of `cosmic` (reads one snapshot, locks and writes nothing), all rows except 4 history tables (economy journal/outbox, admin samplers: structure only), into `cosmic_check`. The dump is reused for 24 h unless `-FreshDump` |
| `start-check-stack.ps1 -Worktree <wt> [-SkipBuild] [-Reseed] [-WithJev]` | Builds (offline), copies the jar, seeds if needed, starts java with a whitelisted environment, waits for login and admin, prints the live pid and ports before and after |
| `stop-check-stack.ps1` | Shutdown through the check stack's own admin port (saves everyone), waits; kills only a java whose command line names `CheckStack.jar`, and only after the timeout |
| `check.py <files or folders> [--client] [--report out.md]` | The runner; step list in its docstring |
| `run-live-checks.ps1 -Feature <folder or all> [-Worktree <wt>] [-Client]` | Runs `checks\<folder>`, writes `reports\<folder>-<stamp>.md`; with `-Worktree` it starts and stops the stack around the run |
| `checks\i3-selftest` | The I3.1 done-test: a GM command through the admin port, read back by `expectadmin`, `expectsql`, `expectgm`, `expectlog` (green) |
| `checks\i3-selftest-red` | The same steps with wrong expectations (red, on purpose; `-Feature all` skips it) |
| `checks\live-checks-2026-11-02` | I3.2: a script per open item of `LIVE_CHECKS_2026-11-02.md` |

## Steps

Server steps talk only to the check stack: `online <GM>` (brings a GM character online headlessly, as the headless
operator), `gm <GM> <command>` (the admin port's allowlisted commands: spawnbot, skillprobe, combatarena, pvp),
`queue <GM> <!command>` (any GM command through the check stack's GM queue; output is read with `expectgm` while that
GM is the headless operator), `post`, `sql` (writes to `cosmic_check`), `wait`, and the asserts `expectadmin <route>
<jsonpath> <op> <value> [within <s>]`, `expectsql <query> <op> <value> [within <s>]`, `expectlog`, `expectgm`,
`expectbootlog`, `rejectbootlog`, `rejectlog <regex> for <s>`.

Header keys: `id`, `title`, `needs: headless|client|blocked`, `why-client`, `why-blocked`, `soak: <minutes>`,
`log: live`, `source`. A `log: live` check's `expectbootlog`/`rejectbootlog` read the **live** server's
`cosmic-log.log` (read only): the A-list items are about what the live world did over hours, which a fresh stack
cannot show. No other step ever reads or writes anything live.

Client steps (`login`, `say`, `press`, `dialog`, `clicktext`, `shot`) run one `maple_client.py` command each and
only with `--client`.

## Running a client check against the check stack (not done yet: waits for the coordinator's go)

The client must log in to the check stack, not the live server: kaentake's `WSPConnect` hook sends the login
connection to `config.ini`'s `host`/`port` (`kaentake/src/system.cpp:137-151`), and channel connections go where the
server says (127.0.0.1:17575+, from `SERVER_HOST`). So use a **separate copy** of `BotStory Client\Game Files` with
`config.ini` `port=18484` (the client writes its preferences back into `config.ini`, so never edit the live client's).
Then `run-live-checks.ps1 -Feature live-checks-2026-11-02 -Client`. `maple_client.py` reads positions from the live
admin port for its own `warp`/`map` steps; the check runner does not use those verbs, so no change to
`maple_client.py` is needed.

## I3.3 design (not built)

**Nightly run.** A Windows scheduled task (`BotStory live checks`, 04:30 daily, the same pattern as the graphify
refresher, with a 2 h limit) runs:

1. `git worktree add --detach %TEMP%\botstory-nightly feat/companion-manager` (or `git -C ... checkout --detach` of the
   new tip when it exists); skip the night if the tip equals last night's and no check file changed.
2. `seed-check-db.ps1 -FreshDump` (the nightly dump), then `run-live-checks.ps1 -Feature all -Worktree <that>`.
3. Wait for `tools\suite\suite.lock` and for 2 GB free memory first (the suite's memory guard rules), and skip if a
   check stack is already running (another session's).
4. Copy the report to `docs\checks\<date>.md` in the nightly worktree and commit it on a `checks/nightly` branch (never
   the main line; the coordinator merges reports when it likes), plus a one-line summary (PASS/FAIL counts and new
   FAILs since the last report) at the top.
5. `stop-check-stack.ps1` in a `finally`, and `git worktree remove`.
   Only headless checks run nightly; client checks stay manual until the desktop is free by schedule.
   Precondition: I3's server change (378667cfe) is on the main line; until then the nightly run must use
   `feat/world-live-checks`.

**Two-client checks.** The client allows a second instance: kaentake hooks `CreateMutexA` and renames
`WvsClientMtx` per process (`kaentake/src/system.cpp:27-39`), and two clients ran side by side on 2026-09-28 (P2a,
two-friends clip; both titled "BotStory", so client 2 was retitled with `SetWindowText` for recording;
`maple_client` picks a client with `MAPLE_PID`). The design: two client copies (each its own `Game Files` copy with
`port=18484`), client A = TestGm (testaccount1), client B = TestFriend (testaccount2); a check gets a `# clients: 2`
header and client steps take a prefix `A:` / `B:` that sets `MAPLE_PID` for that one `maple_client.py` call; the
two are driven in turn, never at once. Candidates: B12 lending, P7B two-owner expeditions (C10), relogin matrix and
two-client save routes (FOLLOW_UPS 3 hardening), B28 Family, Snowball (B2, moved to D). Not verified on this build:
whether both clients stay responsive at once on this host's memory (16 GB with two servers running).
