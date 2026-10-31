"""A curved total saved before curves were rounded kept its decimal, and the
charts and letters read it: 5.59 of 10 showed as an F where the curve gives
6, a D. Opening the assignment puts the saved number right."""
import inspect
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from courseforge import curve
from courseforge.server import App
from courseforge.store import Store

RUBRIC = [{"id": "_overall", "label": "Overall", "points": 10.0, "ratings": []}]


def bare_app(root: Path) -> App:
    app = App.__new__(App)
    app.store = Store(root)
    app.cfg = SimpleNamespace(grade_scale={"A": 90, "B": 80, "C": 70, "D": 60})
    return app


class AnOldCurvedTotalIsPutRight(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = bare_app(self.tmp)
        old = {"source": "claude", "total": 5.0, "scores": {"_overall": 5.0},
               "final_total": 5.59, "curve": {"flat": 0.59, "by_criterion": {}}}
        plain = {"source": "claude", "total": 9.0, "scores": {"_overall": 9.0}}
        self.app.store.save_draft("1", "2", {"points_possible": 10.0, "rubric": RUBRIC,
                                             "students": {"7": old, "8": plain}})

    def test_the_saved_total_matches_the_curve(self):
        self.app._refresh_totals("1", "2")
        st = self.app.store.draft("1", "2")["students"]
        self.assertEqual(st["7"]["final_total"], 6.0)
        self.assertEqual(curve.letter(100 * st["7"]["final_total"] / 10), "D")
        self.assertNotIn("final_total", st["8"], "an uncurved score is left alone")

    def test_opening_the_assignment_does_it(self):
        src = inspect.getsource(App.workspace)
        self.assertLess(src.index("_refresh_totals"), src.index("_late_policy_for("),
                        "refreshed before the draft is read for the page")


if __name__ == "__main__":
    unittest.main()
