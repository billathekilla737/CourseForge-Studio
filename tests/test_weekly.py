"""The week summary on the term schedule: what this week teaches and tests.

No network: Canvas is a stand-in object and llm.run is faked.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from courseforge import server, weekly
from courseforge.claude_cli import ClaudeResult

ROOT = Path(__file__).resolve().parents[1]


def row(course_id="1", assignment_id="10", **extra) -> dict:
    base = {"course_id": course_id, "assignment_id": assignment_id,
            "course_label": f"Course {course_id}", "course_code": f"MAT {course_id}",
            "course_name": f"202630 MAT {course_id} 001 Course {course_id}",
            "name": f"Item {assignment_id}", "kind_label": "ASSIGNMENT",
            "points": 10, "due_local": "Wednesday, Nov 4, 11:59 PM", "text": ""}
    base.update(extra)
    return base


class ThePlainText(unittest.TestCase):
    def test_markup_goes_and_the_words_stay(self):
        html = "<p>Read <b>Chapter 6</b>&nbsp;on&nbsp;limits.</p><script>x()</script>"
        self.assertEqual(weekly.plain_text(html), "Read Chapter 6 on limits.")

    def test_a_long_description_is_cut_at_a_word(self):
        out = weekly.plain_text("word " * 1000, limit=50)
        self.assertLessEqual(len(out), 52)
        self.assertTrue(out.endswith(" …"))
        self.assertNotIn("wor …", out)


class WhatThePageSent(unittest.TestCase):
    def test_duplicates_and_blanks_drop_out_in_order(self):
        got = weekly.wanted([
            {"course_id": "1", "assignment_id": "10", "due_local": "Mon"},
            {"course_id": "1", "assignment_id": "10", "due_local": "Mon"},
            {"course_id": "", "assignment_id": "11"},
            {"course_id": "2", "assignment_id": "20"},
        ])
        self.assertEqual([(g["course_id"], g["assignment_id"]) for g in got],
                         [("1", "10"), ("2", "20")])

    def test_an_empty_week_says_so(self):
        with self.assertRaisesRegex(RuntimeError, "nothing dated"):
            weekly.wanted([])

    def test_too_many_items_asks_to_narrow(self):
        many = [{"course_id": "1", "assignment_id": str(i)}
                for i in range(weekly.MAX_ITEMS + 1)]
        with self.assertRaisesRegex(RuntimeError, "chips"):
            weekly.wanted(many)


class ThePrompt(unittest.TestCase):
    def test_each_course_and_item_is_there_with_its_facts(self):
        text = weekly.prompt("the week of Nov 2 – Nov 8, 2026", [
            row("1", "10", name="Unit 3 Test", kind_label="TEST", proctored=True,
                text="Covers sections 3.1 to 3.4."),
            row("2", "20", name="Lab 5", points=None),
        ])
        self.assertIn("the week of Nov 2", text)
        self.assertIn("## Course 1: Course 1", text)
        self.assertIn("- Unit 3 Test [TEST, due Wednesday, Nov 4, 11:59 PM, 10 pts, proctored]",
                      text)
        self.assertIn("Covers sections 3.1 to 3.4.", text)
        self.assertIn("- Lab 5 [ASSIGNMENT, due Wednesday", text)
        self.assertIn("(none written)", text)

    def test_the_system_prompt_forbids_guessing(self):
        self.assertIn("Do not invent", weekly.SYSTEM)
        self.assertIn('"course_id"', weekly.SYSTEM)


class TheShape(unittest.TestCase):
    def test_every_course_gets_a_card_even_one_the_model_skipped(self):
        rows = [row("1", "10"), row("2", "20", name="Quiz 4", kind_label="QUIZ")]
        out = weekly.shape({"overview": "A light week.",
                            "courses": [{"course_id": "1", "teaching": "Limits.",
                                         "testing": []}]}, rows)
        self.assertEqual(out["overview"], "A light week.")
        self.assertEqual([c["course_id"] for c in out["courses"]], ["1", "2"])
        self.assertEqual(out["courses"][0]["teaching"], "Limits.")
        self.assertEqual(out["courses"][1]["teaching"], "")
        # The model said nothing about course 2, but its quiz is still listed.
        self.assertEqual(out["courses"][1]["testing"],
                         ["Quiz 4, due Wednesday, Nov 4, 11:59 PM"])

    def test_unparsed_reply_falls_back_to_its_text(self):
        out = weekly.shape(None, [row()], fallback_text="Plain words back.")
        self.assertEqual(out["overview"], "Plain words back.")
        self.assertEqual(len(out["courses"]), 1)


class TheApp(unittest.TestCase):
    """App.week_summary reads each item, calls the model once, writes nothing."""

    def make(self, details, broken=()):
        reads = []

        def schedule_item(course_id, assignment_id):
            reads.append((course_id, assignment_id))
            if (course_id, assignment_id) in broken:
                raise RuntimeError("Canvas said 404")
            return dict(details[(course_id, assignment_id)])

        app = SimpleNamespace(
            cfg=SimpleNamespace(grading_concurrency=2, model="opus",
                                claude_timeout_s=60),
            schedule_item=schedule_item)
        return app, reads

    def test_one_call_for_the_week_with_every_description(self):
        details = {
            ("1", "10"): row("1", "10", name="Unit 3 Test", kind_label="TEST",
                             description="<p>Sections 3.1 to 3.4</p>"),
            ("2", "20"): row("2", "20", name="Essay 2", description="<p>Argument</p>"),
        }
        app, reads = self.make(details)
        calls = []

        def fake_run(prompt, **kw):
            calls.append((prompt, kw))
            return ClaudeResult(text="", data={
                "overview": "Two courses.",
                "courses": [{"course_id": "1", "teaching": "Chapter 3.",
                             "testing": ["Unit 3 Test, due Wednesday"]},
                            {"course_id": "2", "teaching": "Argument essays.",
                             "testing": []}]}, cost_usd=0.012)

        items = [{"course_id": "1", "assignment_id": "10", "due_local": "Wed"},
                 {"course_id": "2", "assignment_id": "20", "due_local": "Fri"}]
        with mock.patch.object(server.llm, "run", fake_run):
            out = server.App.week_summary(app, "the week of Nov 2", items)

        self.assertEqual(len(calls), 1)
        prompt, kw = calls[0]
        self.assertIs(kw["system"], weekly.SYSTEM)
        self.assertIn("Sections 3.1 to 3.4", prompt)
        self.assertIn("due Fri", prompt)
        self.assertEqual(sorted(reads), [("1", "10"), ("2", "20")])
        self.assertEqual(out["overview"], "Two courses.")
        self.assertEqual([c["course_id"] for c in out["courses"]], ["1", "2"])
        self.assertEqual(out["courses"][0]["testing"], ["Unit 3 Test, due Wednesday"])
        self.assertEqual(out["cost_usd"], 0.012)
        self.assertEqual(out["failed"], [])

    def test_an_unreadable_item_is_left_out_not_fatal(self):
        details = {("1", "10"): row("1", "10")}
        app, _reads = self.make(details, broken={("1", "11")})
        items = [{"course_id": "1", "assignment_id": "10"},
                 {"course_id": "1", "assignment_id": "11"}]
        with mock.patch.object(server.llm, "run",
                               lambda *a, **k: ClaudeResult(text="", data={})):
            out = server.App.week_summary(app, "w", items)
        self.assertEqual(len(out["courses"]), 1)
        self.assertEqual(out["failed"][0]["assignment_id"], "11")
        self.assertIn("404", out["failed"][0]["error"])

    def test_nothing_readable_stops_before_the_model(self):
        app, _reads = self.make({}, broken={("1", "10")})
        with mock.patch.object(server.llm, "run",
                               side_effect=AssertionError("model was called")):
            with self.assertRaisesRegex(RuntimeError, "could be read"):
                server.App.week_summary(app, "w", [{"course_id": "1",
                                                    "assignment_id": "10"}])


class ThePageWiresIt(unittest.TestCase):
    def test_route_button_and_modal(self):
        src = (ROOT / "courseforge" / "server.py").read_text(encoding="utf-8")
        self.assertIn('["schedule", "week-summary"]', src)
        js = (ROOT / "courseforge" / "web" / "js" / "grade.js").read_text(encoding="utf-8")
        self.assertIn('data-sum="${key}"', js)
        self.assertIn("What's this week?", js)
        self.assertIn("openWeekSummary(el.dataset.sum)", js)
        self.assertIn("'/schedule/week-summary'", js)
        body = js[js.index("function weekItems"):js.index("function openWeekSummary")]
        # Past items in the week still count; only the course chips narrow it.
        self.assertIn("off.has(it.course_code)", body)
        self.assertNotIn("hidePast", body)
        css = (ROOT / "courseforge" / "web" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".wsCard{", css)


if __name__ == "__main__":
    unittest.main()
