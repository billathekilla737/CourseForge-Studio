"""The tiles across the top of the term schedule."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from courseforge import schedule

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def item(due="2026-10-20T04:59:00Z", **extra) -> dict:
    return {"due_at": due, "needs_grading": 0, "has_submissions": False, **extra}


class ItemsGradedThisTerm(unittest.TestCase):
    def test_graded_means_submitted_and_nothing_waiting(self):
        st = schedule.stats([
            item(has_submissions=True),                    # graded
            item(has_submissions=True, needs_grading=3),   # still waiting
            item(),                                        # nobody submitted
            item(due="2026-11-20T04:59:00Z"),              # not due yet
            item(due="2026-11-20T04:59:00Z", has_submissions=True),  # early, graded
        ], now=NOW)
        self.assertEqual(st["graded"], 2)
        self.assertEqual(st["needs_grading"], 3)
        self.assertEqual(st["total"], 5)

    def test_the_page_shows_graded_in_place_of_dated_items(self):
        js = (ROOT / "courseforge" / "web" / "js" / "grade.js").read_text(encoding="utf-8")
        body = js[js.index("function scheduleStats"):js.index("function schedPad")]
        self.assertIn("it.has_submissions && !(+it.needs_grading)", body)
        self.assertIn("'Items graded this term'", js)
        self.assertNotIn("'Dated items this term'", js)


if __name__ == "__main__":
    unittest.main()
