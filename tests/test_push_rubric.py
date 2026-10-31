"""A push fills in the Canvas rubric, and only the rubric Canvas really has.

Regression from IST 2824, Fall 2026: Canvas criterion ids on this account look
like "_8947". The push kept only ids made of digits, so every real criterion
was dropped and Canvas got the total with empty rubric rows. The filter was
there to keep Studio's own rows ("_overall" on an assignment with no rubric,
"_quiz_auto" and "q<id>" on a quiz) out of Canvas; it now compares against
the criterion ids of the assignment's Canvas rubric instead.

The rubric rows hold the points as graded, and the score can carry a late
penalty or a curve. Canvas recomputes the score from the rubric when
"use this rubric for grading" is on, so the rubric goes first and the score
second, and the score Canvas keeps is the one the preview showed.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from courseforge import confirm
from courseforge.canvas import CanvasClient
from courseforge.server import canvas_criterion_ids, canvas_rubric_payload
from tests.test_grade_review import bare_app


# What Canvas returns on the assignment: real criterion ids, one of them in
# the underscore form this account uses.
CANVAS_ASSIGNMENT = {
    "id": 2, "name": "Low-Poly Prop", "points_possible": 20,
    "rubric": [{"id": "_8947", "description": "Form", "points": 10},
               {"id": "1234", "description": "Finish", "points": 10}],
}

RUBRIC = [{"id": "_8947", "label": "Form", "points": 10.0, "ratings": []},
          {"id": "1234", "label": "Finish", "points": 10.0, "ratings": []}]


def assignment_dir(app, course_id="1", assignment_id="2") -> Path:
    return app.store.assignment_dir(course_id, assignment_id)


class RecordingCanvas:
    """Enough of CanvasClient for a live push. Records each grade write."""

    def __init__(self):
        self.grades: list[dict] = []

    def post_grade(self, course_id, assignment_id, user_id, score=None, comment=None,
                   rubric=None):
        self.grades.append({"user_id": str(user_id), "score": score,
                            "rubric": dict(rubric or {})})
        return {"score": score, "entered_score": score,
                "posted_at": "2026-10-30T10:00:00Z", "workflow_state": "graded"}

    def __getattr__(self, name):
        raise RuntimeError(f"{name} is not part of this test")


class WhichCriteriaAreCanvasCriteria(unittest.TestCase):
    def test_underscore_and_digit_ids_both_count(self):
        self.assertEqual(canvas_criterion_ids(CANVAS_ASSIGNMENT), {"_8947", "1234"})

    def test_no_rubric_means_no_ids(self):
        self.assertEqual(canvas_criterion_ids({"rubric": None}), set())
        self.assertEqual(canvas_criterion_ids({}), set())
        self.assertEqual(canvas_criterion_ids(None), set())

    def test_made_up_rows_are_left_out(self):
        rubric = RUBRIC + [{"id": "_overall", "points": 20.0},
                           {"id": "_quiz_auto", "points": 5.0},
                           {"id": "q55", "points": 5.0}]
        entry = {"scores": {"_8947": 8, "1234": 6, "_overall": 14,
                            "_quiz_auto": 5, "q55": 3},
                 "rationales": {"_8947": "Clean silhouette."}}
        rows = canvas_rubric_payload(entry, rubric, {"_8947", "1234"})
        self.assertEqual(set(rows), {"_8947", "1234"})
        self.assertEqual(rows["_8947"], {"points": 8, "comments": "Clean silhouette."})
        self.assertEqual(rows["1234"], {"points": 6, "comments": ""})

    def test_an_unscored_criterion_is_not_sent_as_zero(self):
        rows = canvas_rubric_payload({"scores": {"_8947": 8}}, RUBRIC, {"_8947", "1234"})
        self.assertEqual(set(rows), {"_8947"})

    def test_a_total_only_grade_sends_no_rows(self):
        entry = {"total_only": True, "total": 15, "scores": {"_8947": 8, "1234": 7}}
        self.assertEqual(canvas_rubric_payload(entry, RUBRIC, {"_8947", "1234"}), {})


class ThePushPlanCarriesTheRubric(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.client = RecordingCanvas()
        self.app = bare_app(self.tmp, self.client)
        self.app.cfg.base_url = "https://canvas.example"

    def save(self, rubric, students, assignment=CANVAS_ASSIGNMENT):
        self.app.store.save_draft("1", "2", {
            "rubric": rubric, "points_possible": 20, "students": students})
        self.app.store.save_extracted("1", "2", {
            uid: {"user_id": uid, "name": f"Student {uid}", "status": "submitted"}
            for uid in students})
        if assignment is not None:
            self.app.store.write(assignment_dir(self.app) / "assignment.json", assignment)

    def plan(self):
        out = self.app.push("1", "2", dry_run=True)
        return {p["user_id"]: p for p in out["would_post"]}, out

    def test_underscore_canvas_ids_are_pushed(self):
        self.save(RUBRIC, {"7": {"user_id": "7", "source": "claude",
                                 "scores": {"_8947": 8, "1234": 9}, "total": 17}})
        plan, out = self.plan()
        self.assertEqual(set(plan["7"]["rubric"]), {"_8947", "1234"})
        self.assertEqual(plan["7"]["rubric"]["_8947"]["points"], 8)
        self.assertEqual(plan["7"]["score"], 17)
        self.assertNotIn("rubric_differs", plan["7"])
        self.assertEqual(out["rubric_n"], 1)
        self.assertEqual(out["rubric_note"], "")

    def test_the_synthesized_overall_row_is_not_pushed(self):
        # An assignment with no Canvas rubric: the sync made up "_overall".
        no_rubric = {"id": 2, "name": "Reflection", "points_possible": 20, "rubric": None}
        self.save([{"id": "_overall", "label": "Overall", "points": 20.0, "ratings": []}],
                  {"7": {"user_id": "7", "source": "claude",
                         "scores": {"_overall": 16}, "total": 16}},
                  assignment=no_rubric)
        plan, out = self.plan()
        self.assertEqual(plan["7"]["score"], 16)
        self.assertNotIn("rubric", plan["7"])
        self.assertEqual(out["rubric_n"], 0)

    def test_quiz_rows_are_not_pushed(self):
        quiz_rubric = [{"id": "_quiz_auto", "label": "Already scored", "points": 10.0,
                        "ratings": [], "locked": True},
                       {"id": "q55", "label": "Essay", "points": 10.0, "ratings": []}]
        quiz_assignment = {"id": 2, "name": "Quiz", "points_possible": 20,
                           "quiz_id": 9, "rubric": None}
        self.save(quiz_rubric, {"7": {"user_id": "7", "source": "claude",
                                      "scores": {"_quiz_auto": 10, "q55": 7},
                                      "total": 17}},
                  assignment=quiz_assignment)
        plan, _ = self.plan()
        self.assertNotIn("rubric", plan["7"])

    def test_without_the_saved_assignment_no_rows_are_guessed(self):
        self.save(RUBRIC, {"7": {"user_id": "7", "source": "claude",
                                 "scores": {"_8947": 8, "1234": 9}, "total": 17}},
                  assignment=None)
        plan, _ = self.plan()
        self.assertEqual(plan["7"]["score"], 17)
        self.assertNotIn("rubric", plan["7"])

    def test_a_criterion_no_longer_on_the_canvas_rubric_is_not_pushed(self):
        # The draft still names "_old"; Canvas's rubric (as last synced) does not.
        self.save(RUBRIC + [{"id": "_old", "label": "Gone", "points": 5.0, "ratings": []}],
                  {"7": {"user_id": "7", "source": "claude",
                         "scores": {"_8947": 8, "1234": 9, "_old": 3}, "total": 20}})
        plan, _ = self.plan()
        self.assertEqual(set(plan["7"]["rubric"]), {"_8947", "1234"})

    def test_total_only_entries_push_no_rubric(self):
        self.save(RUBRIC, {"7": {"user_id": "7", "source": "canvas", "total": 15.5,
                                 "total_only": True, "scores": {}}})
        plan, _ = self.plan()
        self.assertEqual(plan["7"]["score"], 15.5)
        self.assertNotIn("rubric", plan["7"])

    def test_curved_and_late_totals_are_kept_and_flagged(self):
        self.save(RUBRIC, {
            # Curved: 8 + 6 = 14 earned, +3 flat.
            "7": {"user_id": "7", "source": "claude", "scores": {"_8947": 8, "1234": 6},
                  "total": 14, "curve": {"flat": 3, "by_criterion": {},
                                         "steps": [{"kind": "flat"}]}},
            # Late: 9 + 9 = 18 earned, one day at 10% of 20 is 2 off.
            "8": {"user_id": "8", "source": "claude", "scores": {"_8947": 9, "1234": 9},
                  "total": 18, "late_penalty": {"applied": True, "kind": "percent_per_day",
                                                "percent": 10, "days": 1, "base": 20}},
        })
        plan, out = self.plan()
        self.assertEqual(plan["7"]["score"], 17)
        self.assertEqual(plan["7"]["rubric_total"], 14)
        self.assertTrue(plan["7"]["rubric_differs"])
        self.assertEqual(plan["8"]["score"], 16)
        self.assertEqual(plan["8"]["rubric_total"], 18)
        self.assertTrue(plan["8"]["rubric_differs"])
        self.assertEqual(out["rubric_differs_n"], 2)
        self.assertIn("written after them", out["rubric_note"])

    def test_the_live_push_writes_the_previewed_score_with_canvas_rows(self):
        self.save(RUBRIC,
                  {"7": {"user_id": "7", "source": "claude",
                         "scores": {"_8947": 9, "1234": 9}, "total": 18,
                         "late_penalty": {"applied": True, "kind": "percent_per_day",
                                          "percent": 10, "days": 1, "base": 20}},
                   "8": {"user_id": "8", "source": "canvas", "total": 12.5,
                         "total_only": True, "scores": {}}})
        preview, _ = self.plan()
        token = None
        try:
            self.app.push("1", "2", dry_run=False)
        except confirm.ConfirmRequired as asked:
            token = asked.token
        self.assertIsNotNone(token)
        self.app.push("1", "2", dry_run=False, confirm_token=token)
        sent = {g["user_id"]: g for g in self.client.grades}
        self.assertEqual(sent["7"]["score"], preview["7"]["score"])
        self.assertEqual(sent["7"]["score"], 16)
        self.assertEqual(set(sent["7"]["rubric"]), {"_8947", "1234"})
        self.assertEqual(sent["8"]["score"], 12.5)
        self.assertEqual(sent["8"]["rubric"], {})


class PostGradeWritesTheScoreLast(unittest.TestCase):
    """With "use this rubric for grading" on, Canvas recomputes the score from
    a rubric assessment. The score has to be written after the rubric."""

    def setUp(self):
        self.client = CanvasClient("https://canvas.example", "x" * 40, scope="grading")
        self.sent: list[dict] = []

        def fake_request(method, url, data=None, content_type=None, **_kw):
            self.sent.append({"method": method, "url": url,
                              "fields": urllib.parse.parse_qsl(data.decode("utf-8"))})
            return {"score": 16, "entered_score": 16}, {}

        self.client._request = fake_request

    def test_rubric_then_score_in_two_requests(self):
        out = self.client.post_grade(
            1, 2, 7, score=16, comment="Good work.",
            rubric={"_8947": {"points": 9, "comments": "Clean."},
                    "1234": {"points": 9, "comments": ""}})
        self.assertEqual(len(self.sent), 2)
        first, second = (dict(r["fields"]) for r in self.sent)
        self.assertEqual(first, {"rubric_assessment[_8947][points]": "9",
                                 "rubric_assessment[_8947][comments]": "Clean.",
                                 "rubric_assessment[1234][points]": "9"})
        self.assertEqual(second, {"submission[posted_grade]": "16",
                                  "comment[text_comment]": "Good work."})
        self.assertTrue(all(r["method"] == "PUT" for r in self.sent))
        self.assertTrue(all(r["url"].endswith("/courses/1/assignments/2/submissions/7")
                            for r in self.sent))
        self.assertEqual(out["score"], 16)

    def test_no_rubric_is_one_request(self):
        self.client.post_grade(1, 2, 7, score=12.5)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(dict(self.sent[0]["fields"]), {"submission[posted_grade]": "12.5"})

    def test_nothing_to_write_is_refused(self):
        with self.assertRaises(ValueError):
            self.client.post_grade(1, 2, 7)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
