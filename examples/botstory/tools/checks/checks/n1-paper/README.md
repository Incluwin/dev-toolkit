# N1 town paper live checks (docs/WORLD_SYSTEMS_PLAN.md N1, branch feat/world-paper)

Written 2026-11-22, not run. Every check needs the check stack started from a worktree of `feat/world-paper` (or a
branch that has it) whose `config.yaml` has `population.PAPER_ENABLED: true` (local edit, not committed: the switch
ships off). The issue time is `PAPER_DAY` `PAPER_HOUR`:00 in the server's zone (the JVM runs in GMT): Sunday 00:00.

| Check | Needs | What |
|---|---|---|
| P1-issue.check | headless | seed one row per section in `cosmic_check`, `!paper issue`, every section printed and one `living_paper` row |
| P1b-board.check | client | the Bulletin Board's "The paper": this week's and last week's pages |
| P2-arena-name.check | client | a test account wins a forced tournament; `!paper <this week>` names it in Arena |
| P3-one-note.check | client | log in after an issue: one Paperboy note; log in again: none |
| P4-town-talk.check | client | a GM in Henesys on issue day: a citizen says a headline (recent chatter) |
| P5a-issue-hour.check, P5b-after-restart.check | headless | on a Sunday: the boot issues the week once; after `stop-check-stack.ps1` + `start-check-stack.ps1 -SkipBuild`, P5b: no second issue |

P1 and P4 seed `cosmic_check` rows dated the day before the last Sunday (`at` is UTC there, as the server writes it).
