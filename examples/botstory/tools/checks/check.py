"""The kit's check runner with the BotStory steps loaded (so run-live-checks.ps1 can call check.py as before)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "harness" / "checks"))
import check  # noqa: E402  (harness/checks/check.py)

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--stack" not in argv:
        argv += ["--stack", str(HERE / ".stack" / "stack.json")]
    sys.exit(check.main(argv, plugins=[HERE / "botstory_verbs.py"]))
