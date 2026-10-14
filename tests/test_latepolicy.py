"""Late-work rules read from a syllabus and applied after auto-grading."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from courseforge import curve, latepolicy
from courseforge.config import Config
from courseforge.grader import build_prompt, grade_assignment
from courseforge.store import Store


SYLLABUS = """
Course outcomes. Students will identify the parts of a game loop.

Late Work
Late assignments are accepted for up to 7 days. A 10% penalty is taken
per day. Work more than 7 days late is not accepted.

Academic integrity. Do not copy another student's file.
"""


class ParseSyllabus(unittest.TestCase):
    def test_percent_per_day_is_the_section_not_the_opening(self):
        passage = latepolicy.extract_late_passages(SYLLABUS)
        self.assertIn("10% penalty", passage)
        self.assertNotIn("game loop", passage[:40])
        policy = latepolicy.parse(SYLLABUS)
        self.assertEqual(policy["kind"], "percent_per_day")
        self.assertEqual(policy["percent"], 10.0)
        self.assertEqual(policy["interval"], "day")

    def test_no_late_work(self):
        policy = latepolicy.parse("Late work will not be accepted in this course.")
        self.assertEqual(policy["kind"], "none_accepted")

    def test_flat_half_credit(self):
        policy = latepolicy.parse("If it is late, the student receives 50% credit.")
        self.assertEqual(policy["kind"], "flat_percent")
        self.assertEqual(policy["percent"], 50.0)

    def test_grace_period_and_hourly(self):
        policy = latepolicy.parse(
            "There is a 24 hour grace period. After that, 5% per hour late.")
        self.assertEqual(policy["kind"], "percent_per_hour")
        self.assertEqual(policy["percent"], 5.0)
        self.assertEqual(policy["grace_hours"], 24.0)

    def test_no_mention_is_empty(self):
        policy = latepolicy.parse("Office hours are Tuesday at 2.")
        self.assertEqual(policy["kind"], "none")

    def test_letter_grade_per_day_is_ten_percent(self):
        policy = latepolicy.parse("Late work loses one letter grade per day.")
        self.assertEqual(policy["kind"], "percent_per_day")
        self.assertEqual(policy["percent"], 10.0)


class InstructionsOverrideTheDock(unittest.TestCase):
    def test_a_class_sentence_waives_everyone(self):
        text = "Ignore late grades for this assignment."
        self.assertTrue(latepolicy.waives_everyone(text))
        self.assertIn("assignment", latepolicy.waiver(text, {"name": "Jane Doe"}))

    def test_a_named_sentence_waives_only_that_student(self):
        text = "Ignore Jane Doe's tardy submission for this assignment."
        self.assertFalse(latepolicy.waives_everyone(text))
        self.assertIn("this student", latepolicy.waiver(text, {
            "name": "Jane Doe", "sortable_name": "Doe, Jane"}))
        self.assertEqual(latepolicy.waiver(text, {"name": "Alex Kim"}), "")

    def test_ordinary_instructions_do_not_waive(self):
        text = "Be lenient if they used a game that was not on the list."
        self.assertEqual(latepolicy.waiver(text, {"name": "Jane Doe"}), "")

    def test_wave_and_a_first_name_waive_that_student(self):
        text = "Wave Jane's late submission. Do not dock points for it."
        student = {"name": "Jane Doe", "sortable_name": "Doe, Jane"}
        self.assertFalse(latepolicy.waives_everyone(text))
        self.assertIn("this student", latepolicy.waiver(text, student))
        self.assertEqual(latepolicy.waiver(text, {"name": "Alex Kim"}), "")

    def test_this_student_waives_only_a_picked_regrade(self):
        text = "I told this student I would wave the late penalty."
        student = {"name": "Jane Doe", "sortable_name": "Doe, Jane"}
        self.assertFalse(latepolicy.waives_everyone(text))
        self.assertEqual(latepolicy.waiver(text, student), "")
        self.assertIn("this student", latepolicy.waiver(text, student, addressed=True))


class ApplyToAScore(unittest.TestCase):
    def test_one_day_at_ten_percent(self):
        policy = latepolicy.parse("10% per day late.")
        graded = {"total": 20, "scores": {"c1": 20}, "flags": []}
        out = latepolicy.attach(
            graded, {"late": True, "seconds_late": 86400}, policy, 20)
        self.assertTrue(out["late_penalty"]["applied"])
        self.assertEqual(out["late_penalty"]["days"], 1)
        self.assertEqual(out["late_penalty"]["points"], 2.0)
        entry = {**out, "scores": {"c1": 20}}
        self.assertEqual(curve.final_total(entry, [{"id": "c1", "points": 20}], 20), 18)

    def test_half_a_day_on_a_hundred_points_is_five_points(self):
        policy = latepolicy.parse("10% per day late.")
        out = latepolicy.attach(
            {"total": 80, "scores": {"c1": 80}, "flags": []},
            {"late": True, "seconds_late": int(0.5 * 86400)}, policy, 100)
        self.assertEqual(out["late_penalty"]["days"], 0.5)
        self.assertEqual(out["late_penalty"]["points"], 5)
        entry = {**out, "scores": {"c1": 80}}
        self.assertEqual(curve.final_total(entry, [{"id": "c1", "points": 100}], 100), 75)

    def test_point_seven_days_is_seven_points(self):
        policy = latepolicy.parse("10% per day late.")
        out = latepolicy.attach(
            {"total": 90, "flags": []},
            {"late": True, "seconds_late": int(0.7 * 86400)}, policy, 100)
        self.assertEqual(out["late_penalty"]["days"], 0.7)
        self.assertEqual(out["late_penalty"]["points"], 7)

    def test_the_roster_tenth_is_what_comes_off(self):
        # 0.74 days prints as 0.7, so the cut is 7 points, not 7.4.
        policy = latepolicy.parse("10% per day late.")
        out = latepolicy.attach(
            {"total": 100, "flags": []},
            {"late": True, "seconds_late": int(0.74 * 86400)}, policy, 100)
        self.assertEqual(out["late_penalty"]["days"], 0.7)
        self.assertEqual(out["late_penalty"]["points"], 7)

    def test_a_day_and_a_half_is_not_rounded_up_to_two(self):
        policy = latepolicy.parse("10% per day late.")
        out = latepolicy.attach(
            {"total": 100, "flags": []},
            {"late": True, "seconds_late": int(1.5 * 86400)}, policy, 100)
        self.assertEqual(out["late_penalty"]["days"], 1.5)
        self.assertEqual(out["late_penalty"]["points"], 15)

    def test_the_cut_is_a_percent_of_the_assignment_not_the_score(self):
        policy = latepolicy.parse("10% per day late.")
        out = latepolicy.attach(
            {"total": 50, "flags": []},
            {"late": True, "seconds_late": 86400}, policy, 100)
        self.assertEqual(out["late_penalty"]["points"], 10)
        # A later edit of the earned score does not resize the late cut.
        edited = latepolicy.refresh(out, 40, 100)
        self.assertEqual(edited["points"], 10)

    def test_a_few_seconds_is_not_a_day(self):
        policy = latepolicy.parse("10% per day late.")
        out = latepolicy.attach(
            {"total": 80, "flags": []},
            {"late": True, "seconds_late": 30}, policy, 100)
        self.assertFalse(out["late_penalty"]["applied"])
        self.assertEqual(out["late_penalty"]["days"], 0)
        self.assertEqual(curve.final_total(
            {**out, "scores": {"c1": 80}}, [{"id": "c1", "points": 100}], 100), 80)

    def test_grace_period_does_not_dock(self):
        policy = latepolicy.parse("24 hour grace period. Then 10% per day late.")
        out = latepolicy.attach(
            {"total": 20, "flags": []},
            {"late": True, "seconds_late": 3600}, policy)
        self.assertFalse(out["late_penalty"]["applied"])

    def test_not_accepted_zeros_the_score(self):
        policy = latepolicy.parse("No late work is accepted.")
        out = latepolicy.attach(
            {"total": 16, "flags": []},
            {"late": True, "seconds_late": 100}, policy)
        self.assertTrue(out["late_penalty"]["applied"])
        self.assertEqual(out["late_penalty"]["points"], 16)
        self.assertEqual(curve.final_total(out, [], 20), 0)

    def test_canvas_already_deducts_so_studio_does_not(self):
        policy = latepolicy.parse("10% per day late.")
        policy["canvas_applies"] = True
        policy["kind"] = "canvas"
        policy["summary"] = "Canvas already deducts."
        out = latepolicy.attach(
            {"total": 20, "flags": []},
            {"late": True, "seconds_late": 86400}, policy)
        self.assertFalse(out["late_penalty"]["applied"])
        self.assertEqual(curve.final_total(
            {**out, "scores": {"c1": 20}}, [{"id": "c1", "points": 20}], 20), 20)

    def test_on_time_is_untouched(self):
        policy = latepolicy.parse("10% per day late.")
        graded = {"total": 20, "flags": []}
        out = latepolicy.attach(graded, {"late": False, "seconds_late": 0}, policy)
        self.assertNotIn("late_penalty", out)

    def test_a_curve_sits_on_top_of_the_late_dock(self):
        entry = {
            "total": 10, "scores": {"c1": 10},
            "curve": {"flat": 2, "by_criterion": {}},
            "late_penalty": {"applied": True, "kind": "percent_per_day",
                             "percent": 10, "units": 1, "interval": "day",
                             "floor_percent": 0, "points": 1},
        }
        self.assertEqual(curve.final_total(entry, [{"id": "c1", "points": 20}], 20), 11)


class LoadFromAFakeCourse(unittest.TestCase):
    def test_syllabus_body_is_enough(self):
        class Client:
            def course_detail(self, cid, include=None):
                return {"syllabus_body": "<p>" + SYLLABUS + "</p>"}

            def pages(self, cid):
                return []

            def course_late_policy(self, cid):
                return None

        policy = latepolicy.load(Client(), "1", lambda html: html)
        self.assertEqual(policy["kind"], "percent_per_day")
        self.assertFalse(policy["canvas_applies"])

    def _client(self, syllabus):
        class Client:
            def course_detail(self, cid, include=None):
                return {"syllabus_body": syllabus}

            def pages(self, cid):
                return []

            def course_late_policy(self, cid):
                return {"late_submission_deduction_enabled": True,
                        "late_submission_deduction": 10,
                        "late_submission_interval": "day",
                        "late_submission_minimum_percent_enabled": True,
                        "late_submission_minimum_percent": 50}
        return Client()

    def test_studio_grades_lateness_even_when_canvas_has_a_policy(self):
        policy = latepolicy.load(self._client("10% per day late."), "1", lambda html: html)
        self.assertFalse(policy["canvas_applies"], "Studio applies it; Canvas's is switched off per student")
        self.assertTrue(policy["canvas_late_on"])
        self.assertEqual(policy["kind"], "percent_per_day")
        self.assertIn("never taken twice", policy["summary"])

    def test_with_no_syllabus_rule_canvas_rule_is_the_one_studio_applies(self):
        policy = latepolicy.load(self._client("Welcome to class."), "1", lambda html: html)
        self.assertEqual(policy["kind"], "percent_per_day")
        self.assertEqual(policy["percent"], 10.0)
        self.assertEqual(policy["floor_percent"], 50.0)
        self.assertEqual(policy["source"], "canvas")


class LatenessSurvivesCanvasStatusNone(unittest.TestCase):
    def test_a_late_submission_is_late(self):
        self.assertEqual(latepolicy.true_lateness({"late": True, "seconds_late": 90}), (True, 90))

    def test_status_none_still_late_by_the_clock(self):
        sub = {"late": False, "late_policy_status": "none",
               "submitted_at": "2026-09-10T05:59:00Z", "cached_due_date": "2026-09-09T04:59:00Z"}
        self.assertEqual(latepolicy.true_lateness(sub), (True, 25 * 3600))

    def test_status_none_on_time_is_on_time(self):
        sub = {"late": False, "late_policy_status": "none",
               "submitted_at": "2026-09-08T05:59:00Z", "cached_due_date": "2026-09-09T04:59:00Z"}
        self.assertEqual(latepolicy.true_lateness(sub), (False, 0))

    def test_an_extension_is_respected(self):
        sub = {"late": False, "late_policy_status": "extended",
               "submitted_at": "2026-09-10T05:59:00Z", "cached_due_date": "2026-09-09T04:59:00Z"}
        self.assertEqual(latepolicy.true_lateness(sub), (False, 0))


class OneStudentUsesTheInstructions(unittest.TestCase):
    def test_the_prompt_for_that_student_carries_the_waiver(self):
        prompt = build_prompt(
            {"name": "Essay", "points_possible": 20, "description": ""},
            [{"id": "c1", "label": "Idea", "points": 20, "detail": "", "ratings": []}],
            {"user_id": "9", "name": "Jane Doe", "sortable_name": "Doe, Jane",
             "status": "submitted", "late": True, "words": 12, "text": "A game has rules."},
            "Wave Jane's late submission. Do not dock points for it.",
            "Jane Doe", addressed=True)
        self.assertIn("Wave Jane's late submission.", prompt)
        self.assertIn("re-grade of one student", prompt)
        self.assertIn("penalty waived", prompt.lower())
        self.assertNotIn("Studio applies the syllabus", prompt)

    def test_regrading_one_student_replaces_a_canvas_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            cid, aid = "1", "2"
            adir = store.assignment_dir(cid, aid)
            store.write(adir / "assignment.json",
                        {"name": "Essay", "points_possible": 10, "rubric": []})
            store.write(adir / "extracted.json", {
                "9": {"user_id": "9", "name": "Jane Doe",
                      "sortable_name": "Doe, Jane", "status": "submitted",
                      "late": True, "seconds_late": 90000, "text": "", "words": 0}})
            store.write(adir / "draft.json", {
                "points_possible": 10, "rubric": [],
                "students": {"9": {"user_id": "9", "source": "canvas", "total": 4,
                                   "scores": {}, "total_only": True,
                                   "late_penalty": {"applied": True, "points": 4}}}})
            store.save_instructions(cid, aid, "Wave Jane's late submission.")
            grade_assignment(Config(), store, cid, aid, only=["9"], late_policy={
                "kind": "percent_per_day", "percent": 10, "interval": "day",
                "summary": "10% a day",
            })
            entry = store.draft(cid, aid)["students"]["9"]
        self.assertNotEqual(entry.get("source"), "canvas")
        self.assertTrue(entry["late_penalty"]["waived"])
        self.assertFalse(entry["late_penalty"]["applied"])


if __name__ == "__main__":
    unittest.main()
