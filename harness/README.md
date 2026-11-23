# harness

Three independent tools. None needs the others, and only `checks` and `git` are cross-project as they
stand; `suite` is for Maven.

## checks

`checks/check.py` runs plain-text checks against a running test instance of your server.

```
# id: S1
# title: Create two items and read them back
post /items {"name": "first", "qty": 2}
expecthttp /items items.# == 1 within 5
expecthttp /items items[?name=first].qty >= 2
expectlog POST /items
rejectbootlog ERROR|Exception
```

```powershell
python harness\checks\check.py <folder of *.check> --stack stack.json --report report.md
powershell -ExecutionPolicy Bypass -File harness\checks\selftest\run.ps1    # try it against a mock server
```

- **The instance** is described by a JSON file your start script writes: `baseUrl`, `token` (sent as a
  bearer token), `logFile`, and optionally `branch`/`commit` for the report. Start a second instance with
  its own ports and database for this; checks write, break and restart things.
- **Steps**: `wait`, `post`, `expecthttp`, `expectlog`, `rejectlog ... for <s>`, `expectbootlog`,
  `rejectbootlog`. Every expect retries until it holds or its window ends, and a failure prints the last
  value seen. The path syntax and operators are in the docstring of `check.py`.
- **Headers** sort checks that cannot run yet from checks that fail: `needs: client` (skipped without
  `--client`, reported WAITS-CLIENT), `needs: blocked` + `why-blocked` (BLOCKED), `soak: <minutes>`
  (WAITS-SOAK until the instance has been up that long). Only FAIL and ERROR make the exit code 1.
- **Your own steps** come from a plugin file (`--plugin verbs.py`, `def register(runner)`): database
  queries, admin commands, or client steps that call `appdrive`.
  `examples/botstory/tools/checks/botstory_verbs.py` adds `gm`, `sql`, `expectsql`, `expectadmin` and
  client steps in about 130 lines.

A habit that paid off: keep one check that is red on purpose next to the green self-test, so you know the
runner can fail.

## suite

`suite/run-split-suite.ps1 -Worktree <path> [-GroupsFile groups.json]` runs a Maven suite in package
groups, each in a fresh fork with a small heap.

- One suite at a time per machine (lock file; a lock whose process is gone is taken over), so parallel
  agent sessions queue instead of exhausting memory together.
- Waits for `-StartFreeGB` of free commit before each group, and stops **its own** JVM tree if free commit
  drops under `-MinFreeGB` while a group runs, so a live service on the same host keeps running.
- `-WaitWhileProcess '*pattern*'` waits out a scheduled heavy job.
- Writes `split-summary.txt`: one line per group (totals, lowest free memory, elapsed) and a TOTAL line.

Do not run Maven by hand in a worktree while a suite runs there: concurrent builds corrupt `target/`.

## git

`git/rewrite-identity-for-push.pl OUTDIR BASE TIP...` writes rewritten copies of your commits for
publishing and prints the new tip hash; it never moves a ref.

```powershell
$env:REWRITE_OLD_EMAIL = 'me@private.example'
$env:REWRITE_NEW_IDENT = 'Me <12345+me@users.noreply.github.com>'
$env:REWRITE_DROP_DIRS = 'assets/raw,recordings'    # optional
$env:REWRITE_DROP_MEDIA = '1'                       # optional: drop every image and video file
perl harness\git\rewrite-identity-for-push.pl $env:TEMP\rewrite upstream/main my-branch
git push origin <new-sha>:refs/heads/my-branch
```

Local history keeps the private email and the dropped files. The rewrite is deterministic, so the next
push of the same history produces the same hashes and fast-forwards. Git for Windows ships a perl
(`<git>\usr\bin\perl.exe`).
