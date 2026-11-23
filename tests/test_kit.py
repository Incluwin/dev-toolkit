"""Offline tests: no window, no driver session, no server. Run from the kit root:

  python -m unittest discover -s tests -v

(needs the appdrive dependencies: cua-driver wheel and pillow)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "appdrive"), str(ROOT / "harness" / "checks")]

import check  # noqa: E402
from appdrive import agent, perception, takes, video_text  # noqa: E402


def region(text, x, y, confidence=0.9):
    return {"text": text, "kind": "text", "confidence": confidence, "bounds": {"x": x, "y": y, "width": 40, "height": 10}}


class Perception(unittest.TestCase):
    def test_match_tiers(self):
        exact, inside, near = region("Never mind", 0, 0), region("Oh never mind then", 0, 20), region("lever mind", 0, 40)
        self.assertEqual(perception.match_text([inside, near, exact], "never mind"), [exact])
        self.assertEqual(perception.match_text([inside, near], "never mind"), [inside])
        self.assertEqual(perception.match_text([near], "never mind"), [near])
        self.assertEqual(perception.match_text([region("Start", 0, 0)], "never mind"), [])

    def test_only_match_needs_exactly_one(self):
        self.assertEqual(perception.region_center(region("a", 10, 10)), (30, 15))
        with self.assertRaises(RuntimeError):
            perception.only_match([], "x")
        with self.assertRaises(RuntimeError):
            perception.only_match([region("x", 0, 0), region("x", 0, 50)], "x")


class Agent(unittest.TestCase):
    def test_click_candidates_skip_prose_and_duplicates(self):
        regions = [region("OK", 100, 50), region("OK", 300, 50, 0.6), region("this is a long line of prose", 0, 0)]
        table = agent.default_candidates(regions, None)
        self.assertEqual([c.id for c in table], ["click-0", "wait", "done", "give_up"])
        self.assertEqual((table[0].x, table[0].y), (120, 55))


class VideoText(unittest.TestCase):
    def test_collapse_merges_ocr_flicker(self):
        lines = [(0, "PrBishatherup,"), (0.5, "ProbeBish: gatherup, buffs"), (1, "Power Strike"), (1.5, "Power Stance")]
        self.assertEqual(video_text.collapse(lines), [(0, "ProbeBish: gatherup, buffs"), (1, "Power Strike"), (1.5, "Power Stance")])


class Takes(unittest.TestCase):
    def test_builtin_verbs_and_unknown_step(self):
        runner = takes.TakeRunner(None, "title", me="Alice")
        self.assertLessEqual({"wait", "hold", "record", "stop", "cut", "caption", "expectlog"}, set(runner.verbs))


class Checks(unittest.TestCase):
    def test_resolve(self):
        doc = {"items": [{"name": "a", "qty": 2}, {"name": "b", "qty": 5}]}
        self.assertEqual(check.resolve(doc, "items.#"), 2)
        self.assertEqual(check.resolve(doc, "items[?name=b].qty"), 5)
        self.assertEqual(check.resolve(doc, "items[-1].name"), "b")
        self.assertEqual(check.resolve(doc, '=sum(i["qty"]for(i)in(r["items"]))'), 7)
        with self.assertRaises(KeyError):
            check.resolve(doc, "missing.key")

    def test_compare_and_split(self):
        self.assertTrue(check.compare(3, ">=", 3))
        self.assertTrue(check.compare("abc", "contains", "b"))
        self.assertTrue(check.compare(None, "missing", None))
        self.assertFalse(check.compare("up", "==", "down"))
        self.assertEqual(check.split_assert("/s state == up within 5".split()), (["/s", "state"], "==", "up", 5.0))
        self.assertEqual(check.split_assert("/s key exists".split()), (["/s", "key"], "exists", None, check.DEFAULT_WITHIN_S))

    def test_headers_and_plugin(self):
        self.assertEqual(check.read_meta(["# id: B1", "# needs: client"]), {"needs": "client", "id": "B1"})
        runner = check.Runner()
        runner.load_plugin(ROOT / "examples" / "botstory" / "tools" / "checks" / "botstory_verbs.py")
        self.assertLessEqual({"gm", "sql", "expectsql", "expectadmin", "expecthttp"}, set(runner.verbs))
        self.assertTrue(runner.verbs["say"][1])   # a client step
        with self.assertRaises(SystemExit):       # refuses the live admin port
            runner.on_load(check.Stack("http://127.0.0.1:8480", "t", None, {}))


if __name__ == "__main__":
    unittest.main()
