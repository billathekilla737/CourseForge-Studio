"""Studio grades lateness; Canvas's automatic late deduction never does.

Two regressions from Game Theory, Fall 2026:

  * Canvas's late policy cut a pushed 100 to 90, and the next pull copied the
    90 back over the instructor's grade, as if another machine had changed it.
  * The push left Canvas's deduction on, so "ignore late grades" in the
    instructions waived Studio's penalty and Canvas took one anyway.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from courseforge import confirm, gradesync
from courseforge.store import Store
from tests.test_grade_review import bare_app


def canvas_row(entered, shown, deducted, status=None, late=True):
    return {"user_id": 7, "entered_score": entered, "score": shown,
            "points_deducted": deducted, "late": late, "late_policy_status": status,
            "workflow_state": "graded", "graded_at": "2026-10-05T10:00:00Z",
            "posted_at": "2026-10-05T10:00:00Z"}


class APullComparesTheEnteredScore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = Store(self.tmp)

    def test_the_fields_hold_the_entered_score_and_the_cut_beside_it(self):
        f = gradesync.canvas_grade_fields(canvas_row(100, 90, 10), None)
        self.assertEqual(f["canvas_score"], 100)
        self.assertEqual(f["canvas_shown_score"], 90)
        self.assertEqual(f["canvas_points_deducted"], 10)

    def test_canvas_late_cut_does_not_overwrite_the_instructors_grade(self):
        self.store.save_draft("1", "2", {
            "rubric": [], "points_possible": 100,
            "students": {"7": {"user_id": "7", "source": "human", "total": 100,
                               "scores": {}, "synced_score": 100}}})
        info = {"7": {"user_id": "7", "status": "submitted",
                      **gradesync.canvas_grade_fields(canvas_row(100, 90, 10), None)}}
        out = gradesync.merge_canvas_grades(self.store, "1", "2", info)
        entry = self.store.draft("1", "2")["students"]["7"]
        self.assertEqual(entry["total"], 100)
        self.assertEqual(entry["source"], "human")
        self.assertIn("7", out["in_sync"])

    def test_a_score_already_overwritten_comes_back_on_the_next_pull(self):
        # What the old pull left behind: Canvas's 90 adopted as the grade.
        self.store.save_draft("1", "2", {
            "rubric": [], "points_possible": 100,
            "students": {"7": {"user_id": "7", "source": "canvas", "total": 90,
                               "scores": {}, "total_only": True, "synced_score": 90}}})
        info = {"7": {"user_id": "7", "status": "submitted",
                      **gradesync.canvas_grade_fields(canvas_row(100, 90, 10), None)}}
        gradesync.merge_canvas_grades(self.store, "1", "2", info)
        self.assertEqual(self.store.draft("1", "2")["students"]["7"]["total"], 100)


class ThePushTurnsCanvasDeductionOff(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = bare_app(self.tmp)
        self.app._late_cache = {"1": {"kind": "percent_per_day", "canvas_late_on": True,
                                      "canvas_applies": False}}
        self.app.store.save_draft("1", "2", {
            "rubric": [], "points_possible": 100,
            "students": {uid: {"user_id": uid, "source": "human", "total": 80, "scores": {}}
                         for uid in ("7", "8", "9")}})
        self.app.store.save_extracted("1", "2", {
            "7": {"user_id": "7", "name": "Late", "status": "submitted", "late": True},
            "8": {"user_id": "8", "name": "On time", "status": "submitted", "late": False},
            "9": {"user_id": "9", "name": "Already none", "status": "submitted",
                  "late": True, "canvas_late_status": "none"},
        })

    def test_only_late_work_still_open_to_canvas_deduction_is_marked(self):
        plan = self.app.push("1", "2", dry_run=True)
        marked = {p["user_id"] for p in plan["would_post"] if p.get("clear_late")}
        self.assertEqual(marked, {"7"})
        self.assertEqual(plan["late_cleared_n"], 1)
        self.assertIn("not taken twice", plan["late_note"])

    def test_a_course_without_a_canvas_policy_keeps_its_late_labels(self):
        self.app._late_cache = {"1": {"kind": "percent_per_day", "canvas_late_on": False}}
        plan = self.app.push("1", "2", dry_run=True)
        self.assertFalse(any(p.get("clear_late") for p in plan["would_post"]))

    def test_a_deduction_already_taken_is_undone_even_without_the_policy_flag(self):
        self.app._late_cache = {"1": {"kind": "none"}}
        ex = self.app.store.extracted("1", "2")
        ex["8"]["canvas_points_deducted"] = 10
        self.app.store.save_extracted("1", "2", ex)
        plan = self.app.push("1", "2", dry_run=True)
        marked = {p["user_id"] for p in plan["would_post"] if p.get("clear_late")}
        self.assertEqual(marked, {"8"})


class RecordingCanvas:
    """Enough of CanvasClient for a push. Records the order of every write."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def clear_late_status(self, course_id, assignment_id, user_id):
        self.calls.append(("status none", str(user_id)))
        return {}

    def post_grade(self, course_id, assignment_id, user_id, score=None, comment=None,
                   rubric=None):
        self.calls.append(("grade", str(user_id)))
        return {"score": score, "entered_score": score, "points_deducted": 0,
                "late_policy_status": "none", "posted_at": "2026-10-06T10:00:00Z"}

    def release_grades(self, *a, **k):
        raise RuntimeError("not needed here")

    def __getattr__(self, name):
        raise RuntimeError(f"{name} is not part of this test")


class TheLivePushClearsStatusBeforeTheGrade(unittest.TestCase):
    def test_status_none_goes_in_first_and_only_for_late_work(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        client = RecordingCanvas()
        app = bare_app(tmp, client)
        app.cfg.base_url = "https://canvas.example"
        app._late_cache = {"1": {"kind": "percent_per_day", "canvas_late_on": True}}
        app.store.save_draft("1", "2", {
            "rubric": [], "points_possible": 100,
            "students": {uid: {"user_id": uid, "source": "human", "total": 80, "scores": {}}
                         for uid in ("7", "8")}})
        app.store.save_extracted("1", "2", {
            "7": {"user_id": "7", "name": "Late", "status": "submitted", "late": True},
            "8": {"user_id": "8", "name": "On time", "status": "submitted", "late": False}})
        try:
            app.push("1", "2", dry_run=False)
        except confirm.ConfirmRequired as asked:
            self.assertIn("not taken twice", asked.summary)
            token = asked.token
        app.push("1", "2", dry_run=False, confirm_token=token)
        self.assertEqual(client.calls[:2], [("status none", "7"), ("grade", "7")])
        self.assertNotIn(("status none", "8"), client.calls)
        self.assertIn(("grade", "8"), client.calls)
        seen = app.store.extracted("1", "2")["7"]
        self.assertEqual(seen["canvas_late_status"], "none")


class OldCanvasRowsMoveToStudio(unittest.TestCase):
    """Rows graded while "Canvas deducts" was the rule get Studio's on open."""

    POLICY = {"kind": "percent_per_day", "percent": 10.0, "interval": "day",
              "grace_hours": 0.0, "floor_percent": 50.0, "max_days": None,
              "canvas_applies": False, "summary": "10% per day late"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = bare_app(self.tmp)
        canvas_lp = {"kind": "canvas", "applied": False, "points": 0}
        self.app.store.save_draft("1", "2", {
            "rubric": [], "points_possible": 100,
            "late_policy": {"kind": "canvas", "canvas_applies": True},
            "students": {"7": {"user_id": "7", "source": "claude", "total": 80,
                               "scores": {}, "late_penalty": dict(canvas_lp)}}})
        self.app.store.save_extracted("1", "2", {
            "7": {"user_id": "7", "name": "Ada Example", "status": "submitted",
                  "late": True, "seconds_late": 86400}})

    def test_studio_applies_its_own_penalty(self):
        draft = self.app._reprice_late("1", "2", self.app.store.draft("1", "2"), self.POLICY)
        entry = draft["students"]["7"]
        self.assertTrue(entry["late_penalty"]["applied"])
        self.assertEqual(entry["final_total"], 70, "one day late, 10% of 100")

    def test_instructions_that_waive_late_work_waive_it(self):
        self.app.store.save_instructions("1", "2", "Ignore late grades for this assignment.")
        draft = self.app._reprice_late("1", "2", self.app.store.draft("1", "2"), self.POLICY)
        entry = draft["students"]["7"]
        self.assertTrue(entry["late_penalty"]["waived"])
        self.assertEqual(entry["final_total"], 80)

    def test_the_saved_canvas_rule_is_replaced_by_the_course_rule(self):
        self.app._late_cache = {"1": dict(self.POLICY)}
        rule = self.app._late_policy_for("1", self.app.store.draft("1", "2"))
        self.assertEqual(rule["kind"], "percent_per_day")


if __name__ == "__main__":
    unittest.main()
