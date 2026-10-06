"""What planning an extension costs in Canvas reads.

The overrides used to be read one assignment at a time, so a course with 80
assignments was 80 more requests before the page could say anything, and a
Move pays for that read three times: the plan, the refused call that asks, and
the confirmed call that writes. They now come back inside the assignment list.
These pin that, and the two things the faster read must not change: an
assignment Canvas sent without its overrides is still asked about on its own,
and the confirmed call still agrees with the call that asked.
"""
import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from courseforge import confirm
from courseforge.extendarea import routes
from courseforge.routing import Request

TZ = "America/Chicago"
# 11:59 pm Central on Sep 5, 8, 10 and 20 2026, and on Dec 1.
SEP5 = "2026-09-06T04:59:00Z"
SEP8 = "2026-09-09T04:59:00Z"
SEP10 = "2026-09-11T04:59:00Z"
SEP20 = "2026-09-21T04:59:00Z"
DEC1 = "2026-12-02T05:59:00Z"

# Jane is away Sep 7 to 11. She is in the first two courses, and not the third.
BODY = {"user_ids": ["7"], "days": 3, "start": "2026-09-07", "end": "2026-09-11",
        "course_ids": ["1", "2", "3"]}


def assignment(aid, name, due_at, kinds=("online_upload",), overrides=()):
    return {"id": aid, "name": name, "due_at": due_at, "lock_at": None,
            "submission_types": list(kinds),
            "html_url": f"https://canvas.test/assignments/{aid}",
            "overrides": list(overrides)}


def courses():
    """Three courses as Canvas sends them.

    The first has 80 assignments, like the course that made this slow. Three
    of them land in the absence: two on the class date, and one only because
    of an extension Jane already has.
    """
    eng = [
        assignment(10, "Essay 2", SEP8),
        assignment(11, "Quiz 3", SEP8, kinds=["online_quiz"]),
        assignment(12, "Reading log", SEP5, overrides=[
            {"id": 9, "assignment_id": 12, "title": "Extension: Jane Doe",
             "student_ids": [7], "due_at": SEP10}]),
    ] + [assignment(100 + n, f"Week {n} journal", DEC1) for n in range(77)]
    his = [
        assignment(20, "Discussion 1", SEP20, kinds=["discussion_topic"], overrides=[
            {"id": 5, "assignment_id": 20, "title": "Section 200",
             "course_section_id": 200, "due_at": SEP10}]),
        assignment(21, "Final project", DEC1),
    ]
    return {
        "1": {"roster": [{"id": 7, "enrollments": [{"course_section_id": 100}]}],
              "assignments": eng},
        "2": {"roster": [{"id": 7, "enrollments": [{"course_section_id": 200}]}],
              "assignments": his},
        "3": {"roster": [{"id": 8, "enrollments": [{"course_section_id": 300}]}],
              "assignments": [assignment(30, "Lab 1", SEP8)]},
    }


class FakeCanvas:
    """Canvas for those three courses, writing down every call it is asked.

    `can_manage=False` is what Canvas does for a token without the right to
    manage assignments: the list comes back with no `overrides` key at all,
    and only the per-assignment endpoint will say what they are.
    """

    def __init__(self, can_manage=True):
        self.courses = courses()
        self.can_manage = can_manage
        self.calls = []
        self.written = []

    def asked(self, name):
        return [call[1:] for call in self.calls if call[0] == name]

    def students_with_sections(self, cid):
        self.calls.append(("students_with_sections", cid))
        return copy.deepcopy(self.courses[cid]["roster"])

    def course_detail(self, cid):
        self.calls.append(("course_detail", cid))
        return {"id": cid, "time_zone": TZ}

    def assignments_with_overrides(self, cid, order_by=None):
        self.calls.append(("assignments_with_overrides", cid, order_by))
        rows = copy.deepcopy(self.courses[cid]["assignments"])
        if not self.can_manage:
            for row in rows:
                del row["overrides"]
        return rows

    def assignment_overrides(self, cid, aid):
        self.calls.append(("assignment_overrides", cid, str(aid)))
        row = next(a for a in self.courses[cid]["assignments"] if str(a["id"]) == str(aid))
        return copy.deepcopy(row["overrides"])

    def submitted_pairs(self, cid, user_ids, assignment_ids):
        self.calls.append(("submitted_pairs", cid))
        return set()

    def create_override(self, cid, aid, student_ids, title, dates=None):
        self.written.append(("create", cid, aid, None, dates))
        return {"id": 900 + len(self.written)}

    def update_override(self, cid, aid, override_id, dates=None,
                        student_ids=None, title=None):
        self.written.append(("update", cid, aid, override_id, dates))
        return {"id": override_id}


class FakeApp:
    """The parts of the App the extensions area reaches."""

    LABELS = {"1": "ENG 1113", "2": "HIS 1163", "3": "MAT 1313"}

    def __init__(self, root, client):
        self.root = Path(root)
        self.client = client
        self.confirm = confirm.ConfirmGate()

    def _cached_course_label(self, cid):
        return self.LABELS.get(str(cid))

    def taught_students(self, refresh=False):
        return {"students": [{"user_id": "7", "name": "Jane Doe",
                              "courses": ["ENG 1113", "HIS 1163"]}]}

    def course_dir(self, cid):
        path = self.root / str(cid)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _gate(self, kind, payload, summary, token, detail="", what="this"):
        # The App's own confirmation without the config lock in front of it,
        # so a fingerprint that moved between the two calls is refused here
        # the way it would be live.
        self.confirm.require(kind, payload, summary, token, detail)


def quiet(*_args, **_kw):
    return None


def run(route, app, **extra):
    """Call a route as the server does, and run the job it starts right here."""
    req = Request(app=app, handler=None, method="POST", path="/api/extend",
                  body={**BODY, **extra})
    return route(req).fn(quiet)


class Base(unittest.TestCase):
    can_manage = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.client = FakeCanvas(self.can_manage)
        self.app = FakeApp(self.tmp, self.client)

    def targets(self):
        req = Request(app=self.app, handler=None, method="POST",
                      path="/api/extend/plan", body=dict(BODY))
        return routes._targets(self.app, routes._args(req), quiet)


class OneReadACourse(Base):
    def test_a_course_is_one_assignments_read_however_many_it_holds(self):
        """Eighty assignments in the first course and two in the second: two
        reads, and not one more for the overrides on any of them. The third
        course, which Jane is not in, is not read at all."""
        _, failed, seen = self.targets()
        self.assertEqual([cid for cid, _ in self.client.asked("assignments_with_overrides")],
                         ["1", "2"])
        self.assertEqual(self.client.asked("assignment_overrides"), [])
        self.assertEqual(failed, [])
        self.assertEqual(seen, {"7"})

    def test_the_overrides_that_came_with_the_list_decide_the_window(self):
        """For the class, the reading log and the discussion are both due
        outside the absence. Jane's own extension and her section's date put
        them inside it, and only the overrides say so."""
        targets, _, _ = self.targets()
        found = {t["assignment_id"]: t for t in targets}
        self.assertEqual(sorted(found), ["10", "11", "12", "20"])
        self.assertEqual([o["id"] for o in found["12"]["overrides"]], [9])
        self.assertEqual([o["id"] for o in found["20"]["overrides"]], [5])

    def test_the_order_is_the_one_it_always_asked_for(self):
        """Rows that tie in the plan's sort -- same course, same date, same
        student, like Essay 2 and Quiz 3 -- keep the order Canvas listed the
        assignments in, and that order is inside the confirmation fingerprint.
        The old read asked for due-date order; this one still does, and the
        targets come out in the order Canvas sent them."""
        targets, _, _ = self.targets()
        self.assertEqual({order for _, order in self.client.asked("assignments_with_overrides")},
                         {"due_at"})
        self.assertEqual([t["assignment_id"] for t in targets], ["10", "11", "12", "20"])


class AMissingKeyIsNotAnEmptyList(Base):
    """A token that cannot manage assignments gets the list without overrides.
    Reading that as "none" would measure Jane's reading log from the class
    date and miss the extension she already has."""

    can_manage = False

    def test_each_assignment_is_then_asked_on_its_own(self):
        targets, _, _ = self.targets()
        # Every assignment in the two courses Jane is in, the old way.
        self.assertEqual(len(self.client.asked("assignment_overrides")), 80 + 2)
        found = {t["assignment_id"]: t for t in targets}
        self.assertEqual(sorted(found), ["10", "11", "12", "20"])
        self.assertEqual([o["id"] for o in found["12"]["overrides"]], [9])


class AMoveStillAgreesWithItself(Base):
    def test_plan_ask_and_write_each_read_a_course_once(self):
        """The plan, the refused apply and the confirmed apply each read the
        courses afresh. The confirmed call has to reach the same rows in the
        same order, or the gate says the confirmation was for a different
        change and nothing is written."""
        planned = run(routes.plan_, self.app)
        keys = [routes._key(r) for r in planned["rows"]]
        self.assertEqual(len(keys), 4)

        with self.assertRaises(confirm.ConfirmRequired) as asked:
            run(routes.apply_, self.app, keys=keys)
        self.assertEqual(self.client.written, [])

        done = run(routes.apply_, self.app, keys=keys,
                   confirm=asked.exception.token)
        self.assertEqual(done["failed_writes"], [])
        self.assertEqual(len(done["applied"]), 4)
        # The extension she already had is moved, not collided with.
        self.assertIn(("update", "1", "12", 9),
                      [w[:4] for w in self.client.written])

        # Two courses, three times each, and never one assignment at a time.
        self.assertEqual(len(self.client.asked("assignments_with_overrides")), 3 * 2)
        self.assertEqual(self.client.asked("assignment_overrides"), [])


if __name__ == "__main__":
    unittest.main()
