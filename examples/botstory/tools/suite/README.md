# BotStory suite groups

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ..\..\..\..\harness\suite\run-split-suite.ps1 -Worktree <server worktree> -GroupsFile groups.json -MavenRepo "<server>\.m2\repository" -WaitWhileProcess '*graphify*update*' -StartFreeGB 4.5 -MinFreeGB 1.5
```

Three groups (about 6,200 + 2,400 + 6,100 tests), each in its own 2 GB surefire fork, on a 16 GB host that also
runs the live game server. `-WaitWhileProcess` waits out the scheduled code-graph refresh, which takes up to
11 GB of commit for 10-50 minutes.
