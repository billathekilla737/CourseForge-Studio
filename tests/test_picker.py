"""The course list: what it knows without asking Canvas, and what it offers.

The picker opens on the course you were last grading in. Everything it draws
comes off this disk, because a list that made five Canvas calls to tell you how
many submissions are waiting would be slower than clicking into the course.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from courseforge.hub import routes as hub


class FakeStore:
    def __init__(self, root):
        self.root = Path(root)

    def assignments(self, course_id):
        path = self.root / str(course_id) / "assignments.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def read(self, path, default=None):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default


class FakeApp:
    def __init__(self, root):
        self.store = FakeStore(root)


class LocalState(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = FakeApp(self.tmp)

    def course(self, cid, assignments=None, drafts=(), when=None):
        cdir = self.tmp / str(cid)
        cdir.mkdir(parents=True, exist_ok=True)
        if assignments is not None:
            (cdir / "assignments.json").write_text(json.dumps(assignments), encoding="utf-8")
        for aid in drafts:
            adir = cdir / str(aid)
            adir.mkdir(exist_ok=True)
            (adir / "draft.json").write_text("{}", encoding="utf-8")
            if when:
                os.utime(adir / "draft.json", (when, when))
        return cdir

    def test_a_course_nobody_has_opened_says_so(self):
        """Not a zero. "0 waiting" reads as "nothing to do", which is a
        different and possibly wrong answer."""
        state = hub.local_state(self.app, "999")
        self.assertFalse(state["known"])
        self.assertIsNone(state["touched_at"])

    def test_waiting_comes_from_the_cached_assignment_list(self):
        self.course(101, assignments=[
            {"id": "1", "name": "Essay", "needs_grading": 12},
            {"id": "2", "name": "Quiz", "needs_grading": 3},
            {"id": "3", "name": "Reading", "needs_grading": 0}])
        state = hub.local_state(self.app, 101)
        self.assertTrue(state["known"])
        self.assertEqual(state["waiting"], 15)
        self.assertEqual(state["assignments"], 3)
        self.assertEqual(state["to_grade"], 2)

    def test_graded_here_counts_drafts_and_names_the_newest(self):
        self.course(101, assignments=[{"id": "7", "name": "Project 2"},
                                      {"id": "8", "name": "Project 3"}],
                    drafts=[7], when=time.time() - 3600)
        self.course(101, drafts=[8], when=time.time() - 60)
        state = hub.local_state(self.app, 101)
        self.assertEqual(state["graded"], 0)
        self.assertEqual(state["last_assignment"], {"id": "8", "name": "Project 3"})

    def test_a_synced_course_is_touched_but_not_worked(self):
        """Opening a course writes its assignment list. That is not the same as
        having graded in it, and only the second is worth carrying on with."""
        self.course(101, assignments=[{"id": "1", "needs_grading": 4}])
        state = hub.local_state(self.app, 101)
        self.assertIsNotNone(state["touched_at"])
        self.assertIsNone(state["worked_at"])

    def test_open_work_is_what_is_still_ungraded(self):
        self.course(101, assignments=[
            {"id": "1", "name": "Essay", "needs_grading": 12, "due_at": "2026-09-01T05:00:00Z"},
            {"id": "2", "name": "Quiz", "needs_grading": 3, "due_at": "2026-09-02T05:00:00Z"},
        ])
        state = hub.local_state(self.app, 101)
        self.assertEqual([row["name"] for row in state["open_work"]], ["Essay", "Quiz"])

    def test_a_posted_grade_is_not_still_behind(self):
        cdir = self.course(101, assignments=[
            {"id": "1", "name": "Essay", "needs_grading": 12, "due_at": "2026-09-01T05:00:00Z"},
            {"id": "2", "name": "Quiz", "needs_grading": 3, "due_at": "2026-09-02T05:00:00Z"},
        ])
        (cdir / "1").mkdir()
        (cdir / "1" / "extracted.json").write_text(json.dumps({
            "7": {"user_id": "7", "status": "submitted",
                  "canvas_score": 10, "canvas_posted_at": "2026-09-03T00:00:00+00:00"},
        }), encoding="utf-8")
        state = hub.local_state(self.app, 101)
        self.assertEqual([row["id"] for row in state["open_work"]], ["2"])
        self.assertEqual(state["to_grade"], 1)
        self.assertEqual(state["waiting"], 3)


class Behind(unittest.TestCase):
    def row(self, cid, n, due, name="Work"):
        return {
            "id": cid, "title": "Course %s" % cid, "code": "C%s" % cid,
            "name": "Course %s" % cid, "open_work": [{
                "id": 100 + cid, "name": name, "needs_grading": n, "due_at": due,
            }],
        }

    def on(self, day):
        """Noon on this computer, so the calendar day does not depend on UTC."""
        from datetime import datetime
        local = datetime.now().astimezone().tzinfo
        return datetime(day.year, day.month, day.day, 12, 0, tzinfo=local).isoformat()

    def test_days_past_due_come_first_and_only_five_are_kept(self):
        from datetime import date
        today = date(2026, 9, 29)
        courses = [
            self.row(1, 1, self.on(date(2026, 9, 10)), "One"),
            self.row(2, 9, self.on(date(2026, 9, 11)), "Nine"),
            self.row(3, 4, self.on(date(2026, 9, 12)), "Four early"),
            self.row(4, 4, self.on(date(2026, 9, 13)), "Four later"),
            self.row(5, 2, self.on(date(2026, 9, 14)), "Two"),
            self.row(6, 8, "", "Eight undated"),
            self.row(7, 3, self.on(date(2026, 9, 1)), "Three"),
        ]
        courses.append({
            "id": 8, "excluded": True, "title": "Hidden", "code": "HID",
            "open_work": [{"id": 99, "name": "Skip", "needs_grading": 40,
                           "due_at": self.on(date(2026, 8, 1))}],
        })
        out = hub.take_behind(courses, today=today)
        self.assertEqual([row["name"] for row in out],
                         ["Three", "One", "Nine", "Four early", "Four later"])
        self.assertEqual([row["days_behind"] for row in out], [28, 19, 18, 17, 16])
        self.assertEqual(out[0]["course_code"], "C7")
        self.assertEqual(len(out), 5)
        self.assertNotIn("open_work", courses[0])
        self.assertFalse(any(row["assignment_id"] == 99 for row in out))
        self.assertEqual(set(out[0]), {
            "course_id", "course_title", "course_code", "assignment_id",
            "name", "needs_grading", "due_at", "days_behind",
        })

    def test_undated_and_not_yet_due_are_not_behind(self):
        from datetime import date
        today = date(2026, 9, 29)
        out = hub.take_behind([
            self.row(1, 4, "", "Undated"),
            self.row(2, 40, self.on(date(2026, 10, 1)), "Later"),
            self.row(3, 1, self.on(today), "Today"),
            self.row(4, 1, self.on(date(2026, 9, 28)), "Yesterday"),
        ], today=today)
        self.assertEqual([row["name"] for row in out], ["Yesterday"])
        self.assertEqual(out[0]["days_behind"], 1)


class Resume(unittest.TestCase):
    def row(self, cid, worked=None, **extra):
        base = {"id": cid, "name": "2026 ABC 1234 001 A Course", "title": "A Course",
                "code": "ABC 1234", "term_label": "Fall 2026", "waiting": 5,
                "worked_at": worked, "last_assignment": {"id": "9", "name": "Essay"}}
        base.update(extra)
        return base

    def test_nothing_to_carry_on_with_is_a_real_answer(self):
        """A first run gets no band at all rather than a band about nothing."""
        self.assertIsNone(hub._resume([self.row(1), self.row(2)]))

    def test_the_newest_piece_of_work_wins(self):
        out = hub._resume([
            self.row(1, worked="2026-09-10T10:00:00+00:00"),
            self.row(2, worked="2026-09-12T22:15:01+00:00"),
            self.row(3, worked="2026-09-11T08:00:00+00:00")])
        self.assertEqual(out["course_id"], 2)

    def test_it_names_the_assignment_so_the_button_can_go_straight_there(self):
        out = hub._resume([self.row(1, worked="2026-09-12T22:15:01+00:00")])
        self.assertEqual(out["assignment_id"], "9")
        self.assertEqual(out["assignment_name"], "Essay")
        self.assertTrue(out["ago"], "the band has nothing to say about when")

    def test_an_excluded_course_is_never_offered(self):
        self.assertIsNone(hub._resume(
            [self.row(1, worked="2026-09-12T22:15:01+00:00", excluded=True)]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
