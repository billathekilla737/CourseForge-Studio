"""Cross-course student search, and a history that copies to Canvas user files."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from courseforge.store import Store
from courseforge.studentsarea import assemble


class _Sync:
    def __init__(self):
        self.saved = {}

    def get(self, key):
        if key not in self.saved:
            return None
        return {"payload": self.saved[key], "rev": 1, "written_at": "t"}

    def put(self, key, payload, force=False):
        self.saved[key] = payload
        return {"did": "sent", "payload": payload}


class _App:
    def __init__(self, root: Path):
        self.store = Store(root)
        self.roster = type("R", (), {"load": lambda self: []})()
        self.state_sync = _Sync()

    def taught_students(self, refresh=False, term=None):
        return {"count": 2, "students": [
            {"user_id": "7", "name": "Ada Lovelace", "sortable_name": "Lovelace, Ada",
             "sis_user_id": "S100", "login_id": "alovelace",
             "courses": ["Games"], "course_refs": [{"id": "9", "name": "Games"}],
             "on_roster": True},
            {"user_id": "8", "name": "Grace Hopper", "sortable_name": "Hopper, Grace",
             "sis_user_id": "S200", "login_id": "ghopper",
             "courses": ["Compilers"], "course_refs": [], "on_roster": False},
        ]}


class StudentSearch(unittest.TestCase):
    def test_an_empty_search_is_the_whole_list(self):
        app = _App(Path(tempfile.mkdtemp()))
        out = assemble.search(app, "")
        self.assertEqual(out["count"], 2)
        self.assertEqual([s["name"] for s in out["students"]],
                         ["Grace Hopper", "Ada Lovelace"])

    def test_a_name_or_sis_id_finds_one_person(self):
        app = _App(Path(tempfile.mkdtemp()))
        by_name = assemble.search(app, "love")
        self.assertEqual([s["user_id"] for s in by_name["students"]], ["7"])
        by_sis = assemble.search(app, "s200")
        self.assertEqual(by_sis["students"][0]["name"], "Grace Hopper")


class HistoryFollowsTheCanvasCopy(unittest.TestCase):
    def test_a_local_grade_is_copied_into_the_student_file(self):
        root = Path(tempfile.mkdtemp())
        app = _App(root)
        folder = root / "9" / "100"
        folder.mkdir(parents=True)
        (folder / "assignment.json").write_text(
            json.dumps({"name": "Pitch"}), encoding="utf-8")
        (folder / "draft.json").write_text(json.dumps({
            "points_possible": 20,
            "students": {"7": {"total": 18, "graded_at": "2026-09-01T12:00:00+00:00",
                               "source": "claude"}},
        }), encoding="utf-8")
        out = assemble.dossier(app, "7")
        kinds = [h["kind"] for h in out["history"]]
        self.assertIn("grade", kinds)
        saved = app.state_sync.saved["students/7.json"]
        self.assertEqual(saved["history"][0]["title"], "Pitch")
        self.assertIn("18", saved["history"][0]["summary"])

        other = _App(Path(tempfile.mkdtemp()))
        other.state_sync.saved["students/7.json"] = saved
        again = assemble.dossier(other, "7")
        self.assertTrue(any(h.get("title") == "Pitch" for h in again["history"]))


class ExtrasStayInThisTerm(unittest.TestCase):
    def test_past_courses_are_not_opened(self):
        app = _App(Path(tempfile.mkdtemp()))
        calls = []

        class Client:
            def assignments_with_overrides(self, cid):
                calls.append(("assignments", cid))
                return [{"id": "5", "name": "Quiz 1", "overrides": [
                    {"student_ids": [7], "title": "Extension: Ada", "due_at": "2026-10-01"},
                ]}]

            def quizzes(self, cid):
                calls.append(("quizzes", cid))
                return []

        app.client = Client()
        app.courses = lambda refresh=False: [
            {"id": "9", "name": "Games now", "term_label": "Fall 2026",
             "term_code": "202630", "term_sort": 202630, "excluded": False},
            {"id": "3", "name": "Old games", "term_label": "Fall 2024",
             "term_code": "202430", "term_sort": 202430, "excluded": False},
        ]
        app.taught_students = lambda refresh=False: {"students": [{
            "user_id": "7", "name": "Ada Lovelace",
            "course_refs": [
                {"id": "9", "name": "Games now", "term": "Fall 2026"},
                {"id": "3", "name": "Old games", "term": "Fall 2024"},
            ],
        }]}

        # Pin "now" so the test does not depend on the day it runs.
        from courseforge import terms
        original = terms.current_code
        terms.current_code = lambda today=None: "202630"
        try:
            out = assemble.live(app, "7")
        finally:
            terms.current_code = original
        self.assertEqual(out["courses_checked"], 1)
        self.assertEqual(calls, [("assignments", "9"), ("quizzes", "9")])
        self.assertEqual(out["extensions"][0]["title"], "Quiz 1")
        self.assertNotIn("work", out)


class ReminderNote(unittest.TestCase):
    def test_the_note_names_the_student_the_course_and_the_work(self):
        subject, body = assemble.reminder_message(
            "Bourgeois, Andrew", "3D Game Engine I",
            ["Project — Maze Escape", "Week 8 Quiz"], "Ada Lovelace")
        self.assertEqual(subject, "Missing work in 3D Game Engine I")
        self.assertIn("Hi Andrew, you are missing 2 assignments for 3D Game Engine I:", body)
        self.assertIn("- Project — Maze Escape", body)
        self.assertIn("- Week 8 Quiz", body)
        self.assertIn("Please reach out to Ada Lovelace on how to proceed from here.", body)

    def test_one_assignment_stays_singular(self):
        _subject, body = assemble.reminder_message(
            "Robin Vale", "Game Theory", ["Quiz 1"], "")
        self.assertIn("you are missing 1 assignment for Game Theory:", body)
        self.assertIn("your instructor", body)


class LateAndMissing(unittest.TestCase):
    def test_each_class_names_how_long_and_skips_the_rest(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        rows = assemble.work_items([
            {"assignment_id": 1, "missing": True, "submitted_at": None,
             "assignment": {"name": "Pitch", "due_at": "2026-10-08T12:00:00Z",
                            "published": True}},
            {"assignment_id": 2, "late": True, "seconds_late": 2 * 86400 + 3600,
             "submitted_at": "2026-10-18T12:00:00Z",
             "assignment": {"name": "Quiz", "due_at": "2026-10-16T12:00:00Z",
                            "published": True}},
            {"assignment_id": 3, "submitted_at": "2026-10-01T12:00:00Z",
             "assignment": {"name": "On time", "due_at": "2026-10-02T12:00:00Z",
                            "published": True}},
            {"assignment_id": 4, "missing": True, "excused": True,
             "assignment": {"name": "Excused", "due_at": "2026-09-01T12:00:00Z"}},
            {"assignment_id": 5, "missing": True,
             "assignment": {"name": "Draft", "published": False,
                            "due_at": "2026-09-01T12:00:00Z"}},
            {"assignment_id": 6, "submitted_at": None,
             "assignment": {"name": "Still open", "due_at": "2026-11-01T12:00:00Z",
                            "published": True}},
            {"assignment_id": 7, "missing": True,
             "assignment": {"name": "No date", "published": True}},
        ], now)
        self.assertEqual([(row["title"], row["label"]) for row in rows], [
            ("Pitch", "12 days missing"),
            ("No date", "missing"),
            ("Quiz", "2 days late"),
        ])

    def test_the_student_page_reads_only_this_term(self):
        app = _App(Path(tempfile.mkdtemp()))
        calls = []

        class Client:
            def assignments_with_overrides(self, cid):
                return []

            def quizzes(self, cid):
                return []

            def student_submissions(self, cid, uid):
                calls.append((cid, str(uid)))
                return [{
                    "assignment_id": 5, "missing": True,
                    "cached_due_date": "2026-10-01T12:00:00Z",
                    "assignment": {"id": 5, "name": "Pitch", "published": True,
                                   "due_at": "2026-10-01T12:00:00Z"},
                }]

        app.client = Client()
        app.courses = lambda refresh=False: [
            {"id": "9", "name": "Games now", "term_label": "Fall 2026",
             "term_code": "202630", "term_sort": 202630, "excluded": False},
            {"id": "3", "name": "Old games", "term_label": "Fall 2024",
             "term_code": "202430", "term_sort": 202430, "excluded": False},
        ]
        app.taught_students = lambda refresh=False: {"students": [{
            "user_id": "7", "name": "Ada Lovelace",
            "course_refs": [
                {"id": "9", "name": "Games now", "term": "Fall 2026"},
                {"id": "3", "name": "Old games", "term": "Fall 2024"},
            ],
        }]}
        from courseforge import terms
        original = terms.current_code
        terms.current_code = lambda today=None: "202630"
        notes = []
        try:
            out = assemble.course_work(app, "7", lambda msg, done, total: notes.append(msg))
        finally:
            terms.current_code = original
        self.assertEqual(calls, [("9", "7")])
        item = out["work"][0]["items"][0]
        self.assertEqual(item["title"], "Pitch")
        self.assertEqual(item["kind"], "missing")
        self.assertTrue(item["label"].endswith(" missing"))
        self.assertTrue(any(n.startswith("Reading 1 of 1:") for n in notes))
        page = (Path(__file__).resolve().parents[1] / "courseforge" / "web" / "js"
                / "student.js").read_text(encoding="utf-8")
        self.assertIn('class="stCourse"', page)
        self.assertIn("paintCourseWork", page)
        self.assertIn("setCourseBusy", page)
        self.assertIn("/work", page)
        import courseforge.studentsarea.routes  # noqa: F401  registers the route
        from courseforge.routing import ROUTER
        hit = [r for r in ROUTER.routes if r.pattern == "/api/student/{uid}/work"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].method, "POST")


if __name__ == "__main__":
    unittest.main()
