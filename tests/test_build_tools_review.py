"""Regression tests from the Build and Tools review. Standard library only, no
network, no model call.

    python -m unittest tests.test_build_tools_review -v

Each class pins one thing that was wrong and is now fixed:

  - a due-date table asked for Saturday or Sunday kept every date, instead of
    quietly sliding all of them to Monday as if the weekend were a holiday
  - a manifest whose only content is an inline syllabus body is the project
    shape with something to push, not a pages manifest missing its pages
  - the manifest screen carries the two module switches the server's
    module-wipe gate reads, so a course that already has modules can be pushed
    from the page at all
  - the due-date screen lets a hand-edited proposed date be applied even when
    the computed table already matched the course
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from courseforge.content import manifest as mf, verify_slots  # noqa: E402
from courseforge.courseops import due_dates  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "courseforge" / "web" / "js"
START = "2026-08-24"
FINALS = "2026-12-11"
BREAKS = ["2026-09-07", "2026-10-12..2026-10-13", "2026-11-23..2026-11-27"]


# ------------------------------------------------------------ due dates
class WeekendDueDayTest(unittest.TestCase):
    def _days(self, weekday):
        rows = due_dates.compute(START, 6, FINALS, BREAKS, weekday=weekday)
        return [date.fromisoformat(r["due_date"]) for r in rows[:-1]]

    def test_a_saturday_due_day_stays_on_saturday(self):
        for d in self._days("Saturday"):
            self.assertEqual(d.weekday(), 5, d)

    def test_a_sunday_due_day_stays_on_sunday(self):
        for d in self._days("Sunday"):
            self.assertEqual(d.weekday(), 6, d)

    def test_a_weekend_table_carries_no_invented_holiday_note(self):
        rows = due_dates.compute(START, 4, FINALS, BREAKS, weekday="Sunday")
        for r in rows[:-1]:
            self.assertEqual(r["moved"], "", r)

    def test_a_weekday_due_day_still_moves_off_a_holiday_and_never_onto_a_weekend(self):
        rows = due_dates.compute(START, 4, FINALS, BREAKS, weekday="Monday")
        # Week 2 would be due on Labor Day; it moves to the Tuesday.
        self.assertEqual(rows[1]["due_date"], "2026-09-08")
        self.assertIn("moved off a holiday", rows[1]["moved"])
        for r in rows[:-1]:
            self.assertLess(date.fromisoformat(r["due_date"]).weekday(), 5, r)

    def test_a_friday_due_day_is_friday_in_every_ordinary_week(self):
        rows = due_dates.compute(START, 5, FINALS, [], weekday="Friday")
        for r in rows[:-1]:
            self.assertEqual(date.fromisoformat(r["due_date"]).weekday(), 4, r)


# --------------------------------------------------------------- manifest
GOOD_SYLLABUS = ('<div><h2>Course Syllabus</h2><h3>Policies</h3><p>Turn work in on time.</p></div>')


class SyllabusOnlyManifestTest(unittest.TestCase):
    def test_an_inline_syllabus_is_the_project_shape(self):
        self.assertEqual(mf.detect_mode({"syllabus_html": GOOD_SYLLABUS}), "project")
        self.assertEqual(mf.detect_mode({"syllabus_file": "syllabus.html"}), "project")

    def test_an_inline_syllabus_is_something_to_push(self):
        problems = mf.validate({"syllabus_html": GOOD_SYLLABUS})
        self.assertEqual(problems, [])

    def test_a_truly_empty_manifest_is_still_refused(self):
        # With nothing in it the manifest reads as the pages shape and is asked
        # for a page; a project shape with no slots says it has nothing to push.
        self.assertTrue(mf.validate({"course_label": "x"}))
        self.assertIn("The manifest has nothing to push.",
                      mf.validate({"mode": "project", "course_label": "x"}))

    def test_an_inline_syllabus_verifies_without_a_root_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verify_slots.verify({"syllabus_html": GOOD_SYLLABUS}, Path(tmp))
        self.assertTrue(result["ok"], result)
        self.assertEqual([r["kind"] for r in result["rows"]], ["syllabus"])

    def test_the_pages_shape_is_still_asked_for_a_module(self):
        problems = mf.validate({"pages": [{"title": "Week 1", "html": "<div><h2>Week 1</h2></div>"}]})
        self.assertTrue(any("names no module" in p for p in problems), problems)


# ------------------------------------------------------------- the pages
class ManifestScreenTest(unittest.TestCase):
    """The browser scripts have no test runner, so the load-bearing strings are
    pinned by reading the source."""

    def setUp(self):
        self.js = (WEB / "content.js").read_text(encoding="utf-8")

    def test_the_manifest_push_sends_both_module_switches(self):
        for key in ("skip_modules", "rebuild_modules"):
            self.assertIn(key, self.js)
        # Both switches exist on the screen.
        self.assertIn('id="bdMfSkip"', self.js)
        self.assertIn('id="bdMfRebuild"', self.js)
        # And the dry run and the push send the same body.
        self.assertIn("const mfBody = apply =>", self.js)
        self.assertEqual(self.js.count("mfBody("), 2, "the dry run and the push both call it")

    def test_the_refusal_text_no_longer_points_at_the_manifest_file(self):
        self.assertNotIn("in the manifest itself", self.js)

    def test_the_module_picker_carries_no_em_dash(self):
        self.assertNotIn("— unpublished", self.js)

    def test_a_remembered_plan_is_tied_to_its_course(self):
        self.assertIn("mfPlanFor", self.js)

    def test_the_review_preview_is_a_canvas_page_not_the_app_paper(self):
        """Navy headings on the dark --paper surface disappear. Students read
        on white; How it will read has to use that surface in both themes."""
        review = self.js[self.js.index("How it will read"):]
        review = review[:review.index("The body")]
        self.assertIn("paperFrame canvasPage", review)
        css = (WEB.parent / "css" / "content.css").read_text(encoding="utf-8")
        self.assertIn("color-scheme:light", css)
        self.assertIn(".bdPanes .paperFrame", css)


class CrumbNamesThisCourseTest(unittest.TestCase):
    """Only the hub used to call ensureCourse, so a tab opened straight on a
    Build or Tools route named whatever course S.course last held."""

    def test_both_area_openers_resolve_the_course_before_the_crumb(self):
        for name, opener in (("content.js", "openBuild"), ("courseops.js", "openTools")):
            js = (WEB / name).read_text(encoding="utf-8")
            body = js[js.index("async function " + opener):]
            with self.subTest(file=name):
                self.assertLess(body.index("await ensureCourse("), body.index("crumbs(["))

    def test_a_task_opened_directly_names_the_course(self):
        core = (WEB / "core.js").read_text(encoding="utf-8")
        helper = core[core.index("async function courseCrumbs"):core.index("const LAST_ASSIGNMENT_KEY")]
        self.assertIn("await ensureCourse(", helper)
        self.assertIn("courseTitle(", helper)
        grade = (WEB / "grade.js").read_text(encoding="utf-8")
        body = grade[grade.index("async function openAssignment"):grade.index("function stopAutoPull")]
        self.assertIn("courseCrumbs(", body)
        self.assertIn("id=\"instrMention\"", grade)
        self.assertIn("wireInstructionMentions", grade)
        self.assertIn("/roster", grade[grade.index("function wireInstructionMentions"):])


class DueDateScreenTest(unittest.TestCase):
    def test_editing_a_proposed_date_wakes_the_apply_button(self):
        js = (WEB / "courseops.js").read_text(encoding="utf-8")
        block = re.search(r"querySelectorAll\('\.dtProposed'\)\.forEach\((.*?)\n    \}\);", js, re.S)
        self.assertIsNotNone(block, "the proposed-date inputs get an input handler")
        self.assertIn("applyBtn.disabled = false", block.group(1))


if __name__ == "__main__":
    unittest.main()
