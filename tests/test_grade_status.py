"""The light grading-status check behind To Do and the assignment list.

Canvas's needs-grading count stays up after a gradebook post, and the only
other signal was extracted.json, which a full sync writes. So To Do listed
finished work until each assignment was opened. The check reads submission
rows (no files) and judges them with gradesync's rule. These tests use fake
Canvas clients; nothing here reaches Canvas.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from courseforge import canvas_policy, gradestatus, gradesync
from courseforge.canvas import CanvasClient
from courseforge.hub import routes as hub
from courseforge.store import Store

POSTED = "2026-10-01T12:00:00Z"


def sub(aid, uid, **kw):
    row = {"assignment_id": aid, "user_id": uid, "workflow_state": "graded",
           "submitted_at": "2026-09-30T12:00:00Z", "score": 9.0,
           "posted_at": POSTED, "graded_at": POSTED}
    row.update(kw)
    return row


class FakeClient:
    def __init__(self, subs=(), lists=None):
        self.subs = list(subs)
        self.lists = lists or {}
        self.asked = []
        self.list_reads = []

    def grading_states(self, course_id, assignment_ids):
        ids = {str(a) for a in assignment_ids}
        self.asked.append(sorted(ids))
        return [s for s in self.subs if str(s["assignment_id"]) in ids]

    def assignments(self, course_id):
        self.list_reads.append(course_id)
        return self.lists.get(course_id, [])


class FakeApp:
    def __init__(self, root, client, courses=()):
        self.store = Store(root)
        self.client = client
        self._courses = list(courses)

    def courses(self, refresh=False):
        return [dict(c) for c in self._courses]

    def term_summary(self, refresh=False):
        return {"terms": [], "default": "Fall 2026"}


def row(aid, needs=0, has=True, **kw):
    out = {"id": aid, "name": "A%s" % aid, "needs_grading": needs,
           "has_submissions": has, "graded_submissions_exist": False,
           "due_at": "2020-01-02T18:00:00Z", "submission_types": ["online_upload"]}
    out.update(kw)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = Store(self.tmp)

    def save_list(self, cid, rows):
        self.store.save_assignments(cid, rows)
        return rows


class LightInfos(unittest.TestCase):
    def test_a_posted_zero_for_someone_who_turned_nothing_in_is_unsubmitted(self):
        infos = gradestatus.light_infos([
            sub(1, 7, submitted_at=None, attempt=None, score=0)])
        self.assertEqual(infos["7"]["status"], "unsubmitted")
        self.assertEqual(infos["7"]["canvas_score"], 0)

    def test_an_excused_student_owes_no_grade(self):
        infos = gradestatus.light_infos([sub(1, 7, excused=True, score=None)])
        self.assertEqual(infos, {})

    def test_only_the_lasting_quiz_facts_come_from_an_old_sync(self):
        previous = {"7": {"quiz_auto_score": 6, "quiz_review": [{"id": 1}],
                          "quiz_needs_written": True, "canvas_score": None,
                          "text": "a whole essay"}}
        infos = gradestatus.light_infos([sub(1, 7, score=8)], {}, previous)
        info = infos["7"]
        self.assertEqual(info["quiz_auto_score"], 6)
        self.assertEqual(info["canvas_score"], 8)
        self.assertNotIn("quiz_needs_written", info)
        self.assertNotIn("text", info)


class Judge(unittest.TestCase):
    @staticmethod
    def gw(out):
        return {"graded": out["graded"], "waiting": out["waiting"]}

    def test_a_gradebook_post_is_graded_although_canvas_still_counts_it(self):
        """The bug: needs_grading stays at 2 after the post."""
        infos = gradestatus.light_infos([sub(1, 7), sub(1, 8)])
        self.assertEqual(self.gw(gradestatus.judge(row(1, needs=2), infos)),
                         {"graded": True, "waiting": 0})

    def test_waiting_counts_the_submissions_still_open(self):
        infos = gradestatus.light_infos([
            sub(1, 7),
            sub(1, 8, workflow_state="submitted", score=None, posted_at=None),
            sub(1, 9, workflow_state="pending_review", score=4)])
        self.assertEqual(self.gw(gradestatus.judge(row(1, needs=1), infos)),
                         {"graded": False, "waiting": 2})

    def test_a_score_held_back_from_students_is_not_waiting_to_grade(self):
        infos = gradestatus.light_infos([sub(1, 7, posted_at=None)])
        self.assertTrue(gradestatus.judge(row(1), infos)["graded"])

    def test_nobody_turned_in_but_canvas_counts_some_falls_back_to_the_count(self):
        out = gradestatus.judge(row(1, needs=3), {})
        self.assertEqual(self.gw(out), {"graded": False, "waiting": 3})

    def test_a_resubmission_after_grading_is_waiting_again(self):
        """Canvas keeps the old posted score and turns the state back to
        submitted. grade_matches_current_submission is how it says so."""
        infos = gradestatus.light_infos([
            sub(1, 7),
            sub(1, 8, workflow_state="submitted",
                grade_matches_current_submission=False)])
        self.assertFalse(infos["8"]["canvas_grade_matches"])
        self.assertEqual(self.gw(gradestatus.judge(row(1, needs=1), infos)),
                         {"graded": False, "waiting": 1})

    def test_a_matching_or_unknown_flag_changes_nothing(self):
        for flag in (True, None):
            infos = gradestatus.light_infos([sub(1, 7, grade_matches_current_submission=flag)])
            self.assertTrue(gradestatus.judge(row(1), infos)["graded"], flag)

    def test_the_local_grade_does_not_hide_a_resubmission(self):
        """The draft here can match the old posted score. The new attempt
        still has to be looked at."""
        info = {"user_id": "7", "status": "submitted", "canvas_score": 9.0,
                "canvas_posted_at": POSTED, "canvas_grade_matches": False}
        entry = {"total": 9.0, "scores": {}, "source": "claude"}
        self.assertTrue(gradesync.student_still_open(info, entry, [], 10))
        self.assertFalse(gradesync.assignment_is_graded(
            1, {"7": info}, True, {"7": entry}, [], 10))
        info["canvas_grade_matches"] = True
        self.assertTrue(gradesync.assignment_is_graded(
            1, {"7": info}, True, {"7": entry}, [], 10))

    def test_the_count_and_the_verdict_agree_with_gradesync(self):
        infos = gradestatus.light_infos([
            sub(1, 7), sub(1, 8, workflow_state="submitted", score=None)])
        self.assertFalse(gradesync.assignment_is_graded(0, infos, True))
        self.assertEqual(gradesync.waiting_count(infos), 1)
        del infos["8"]
        self.assertTrue(gradesync.assignment_is_graded(0, infos, True))
        self.assertEqual(gradesync.waiting_count(infos), 0)


class Check(Base):
    def test_the_check_takes_finished_work_off_to_do_without_a_sync(self):
        rows = self.save_list(101, [row(1, needs=2), row(2, needs=1)])
        client = FakeClient([sub(1, 7), sub(1, 8),
                             sub(2, 7, workflow_state="submitted", score=None)])
        app = FakeApp(self.tmp, client)
        before = hub.local_state(app, 101)
        self.assertEqual(before["to_grade"], 2)
        self.assertEqual(before["waiting"], 3)

        out = gradestatus.check(client, self.store, 101, rows)
        self.assertEqual(out["checked"], 2)
        after = hub.local_state(app, 101)
        self.assertEqual([w["id"] for w in after["open_work"]], [2])
        self.assertEqual(after["to_grade"], 1)
        self.assertEqual(after["waiting"], 1)
        # No folder per assignment, and no file was downloaded.
        kids = sorted(p.name for p in (self.tmp / "101").iterdir())
        self.assertEqual(kids, ["assignments.json", "grade_status.json"])

    def test_the_saved_status_names_no_student(self):
        rows = self.save_list(101, [row(1, needs=1)])
        client = FakeClient([sub(1, 4471234, workflow_state="submitted", score=None)])
        gradestatus.check(client, self.store, 101, rows)
        text = (self.tmp / "101" / gradestatus.FILE).read_text(encoding="utf-8")
        self.assertNotIn("4471234", text)
        saved = json.loads(text)["assignments"]["1"]
        self.assertEqual((saved["graded"], saved["waiting"], saved["turned_in"]), (False, 1, 1))

    def test_a_graded_row_that_has_not_moved_is_not_asked_again(self):
        rows = self.save_list(101, [row(1, needs=2), row(2, needs=1), row(3, has=False)])
        client = FakeClient([sub(1, 7), sub(2, 7, workflow_state="submitted", score=None)])
        gradestatus.check(client, self.store, 101, rows)
        self.assertEqual(client.asked, [["1", "2"]])     # 3 has no work in it
        gradestatus.check(client, self.store, 101, rows)
        self.assertEqual(client.asked[-1], ["2"])        # 1 is graded and unchanged
        gradestatus.check(client, self.store, 101, rows, force=True)
        self.assertEqual(client.asked[-1], ["1", "2"])
        rows[0]["needs_grading"] = 3                      # a new submission arrived
        gradestatus.check(client, self.store, 101, rows)
        self.assertEqual(client.asked[-1], ["1", "2"])

    def test_a_row_checked_against_an_older_list_falls_back(self):
        rows = self.save_list(101, [row(1, needs=2)])
        gradestatus.check(FakeClient([sub(1, 7)]), self.store, 101, rows)
        cache = gradestatus.load(self.tmp, 101)
        cdir = self.tmp / "101"
        self.assertTrue(gradestatus.state_for(cdir, rows[0], cache, self.store.read)["graded"])
        moved = dict(rows[0], needs_grading=4)
        state = gradestatus.state_for(cdir, moved, cache, self.store.read)
        self.assertEqual((state["graded"], state["waiting"], state["checked_at"]),
                         (False, 4, None))

    def test_a_sync_after_the_check_wins(self):
        """extracted.json is rewritten by sync, pull and push, and it is
        compared against the local draft. Newer than the check, it decides."""
        rows = self.save_list(101, [row(1, needs=1)])
        gradestatus.check(FakeClient([sub(1, 7)]), self.store, 101, rows)
        cdir = self.tmp / "101"
        (cdir / "1").mkdir()
        ext = cdir / "1" / "extracted.json"
        ext.write_text(json.dumps({"7": {"user_id": "7", "status": "submitted",
                                         "canvas_score": None}}), encoding="utf-8")
        later = time.time() + 5
        os.utime(ext, (later, later))
        cache = gradestatus.load(self.tmp, 101)
        state = gradestatus.state_for(cdir, rows[0], cache, self.store.read)
        self.assertFalse(state["graded"])
        self.assertIsNone(state["checked_at"])

    def test_the_local_draft_still_counts_on_a_pending_review_quiz(self):
        """Posting a quiz total through the gradebook leaves it in pending
        review. The total held here matching what students see is graded."""
        rows = self.save_list(101, [row(1, needs=1, submission_types=["online_quiz"])])
        draft = {"students": {"7": {"total": 10, "scores": {"q1": 4}, "source": "claude"}},
                 "rubric": [], "points_possible": 10}
        (self.tmp / "101" / "1").mkdir()
        (self.tmp / "101" / "1" / "draft.json").write_text(json.dumps(draft), encoding="utf-8")
        client = FakeClient([sub(1, 7, workflow_state="pending_review", score=10)])
        gradestatus.check(client, self.store, 101, rows)
        cache = gradestatus.load(self.tmp, 101)
        self.assertTrue(cache["assignments"]["1"]["graded"])

    def test_an_assignment_that_left_the_list_leaves_the_cache(self):
        rows = self.save_list(101, [row(1, needs=1), row(2, needs=1)])
        gradestatus.check(FakeClient([sub(1, 7), sub(2, 7)]), self.store, 101, rows)
        gradestatus.check(FakeClient([sub(1, 7)]), self.store, 101, rows[:1])
        self.assertEqual(list(gradestatus.load(self.tmp, 101)["assignments"]), ["1"])


class Freshness(Base):
    def test_is_stale(self):
        now = 1_000_000.0
        self.assertTrue(gradestatus.is_stale({}, now))
        self.assertFalse(gradestatus.is_stale({"checked_ts": now - 60}, now))
        self.assertTrue(gradestatus.is_stale({"checked_ts": now - gradestatus.FRESH_S - 1}, now))
        self.assertTrue(gradestatus.is_stale({"checked_ts": now + 600}, now))

    def test_check_course_rereads_the_list_only_when_stale_or_asked(self):
        client = FakeClient([sub(1, 7)], lists={101: [
            {"id": 1, "name": "One", "needs_grading_count": 2,
             "has_submitted_submissions": True, "submission_types": ["online_upload"]}]})
        app = FakeApp(self.tmp, client)
        out = hub.check_course(app, 101)
        self.assertEqual((out["checked"], client.list_reads), (1, [101]))
        out = hub.check_course(app, 101)
        self.assertTrue(out.get("skipped"))
        self.assertEqual(client.list_reads, [101])
        hub.check_course(app, 101, force=True)
        self.assertEqual(client.list_reads, [101, 101])
        self.assertEqual(hub.local_state(app, 101)["to_grade"], 0)

    def test_the_home_check_asks_only_about_known_stale_courses(self):
        courses = [
            {"id": 101, "title": "Known", "term_label": "Fall 2026"},
            {"id": 202, "title": "Never opened", "term_label": "Fall 2026"},
            {"id": 303, "title": "Fresh", "term_label": "Fall 2026"},
            {"id": 404, "title": "Other term", "term_label": "Spring 2027"},
        ]
        lists = {cid: [{"id": cid * 10, "needs_grading_count": 1,
                        "has_submitted_submissions": True}] for cid in (101, 303, 404)}
        client = FakeClient([sub(1010, 7), sub(3030, 7)], lists=lists)
        app = FakeApp(self.tmp, client, courses)
        for cid in (101, 303, 404):
            self.save_list(cid, [row(cid * 10, needs=1)])
        hub.check_course(app, 303, force=True)
        client.list_reads.clear()

        out = hub.check_todo(app, "Fall 2026")
        self.assertEqual(client.list_reads, [101])
        self.assertFalse((self.tmp / "202").exists())
        self.assertEqual(out["checked"], 1)
        self.assertEqual(out["failed"], [])
        # The other term was not asked about, so only its work is still listed.
        self.assertEqual([b["course_id"] for b in out["behind"]], [404])
        # Fresh now, so the next page load asks nothing.
        out = hub.check_todo(app, "Fall 2026")
        self.assertEqual(out["checked"], 0)
        self.assertEqual(client.list_reads, [101])

    def test_a_course_that_fails_is_named_and_the_rest_still_paint(self):
        class Broken(FakeClient):
            def assignments(self, course_id):
                raise RuntimeError("Canvas did not answer\nsecond line")

        app = FakeApp(self.tmp, Broken(), [{"id": 101, "title": "Known",
                                            "term_label": "Fall 2026"}])
        self.save_list(101, [row(1, needs=2)])
        out = hub.check_todo(app, "__all")
        self.assertEqual(out["failed"][0]["error"], "Canvas did not answer")
        self.assertEqual(out["courses"][0]["to_grade"], 1)


class CanvasRead(unittest.TestCase):
    def test_one_course_wide_read_per_twenty_assignments(self):
        urls = []

        class Recording(CanvasClient):
            def _request(self, method, url, data=None, content_type=None,
                         attempts=4, timeout=None):
                urls.append((method, url))
                return [], {}

        client = Recording("https://canvas.example.edu", "fake-token", scope="grading")
        client.grading_states(101, list(range(1, 46)))
        self.assertEqual(len(urls), 3)
        self.assertTrue(all(m == "GET" for m, _ in urls))
        self.assertTrue(all(u.startswith("/courses/101/students/submissions?") for _, u in urls))
        self.assertIn("student_ids%5B%5D=all", urls[0][1])
        self.assertNotIn("include", urls[0][1])
        # A dropped student's ungraded post is not work to grade: Canvas's own
        # count and the full sync both leave inactive enrollments out.
        self.assertTrue(all("enrollment_state=active" in u for _, u in urls))

    def test_content_work_cannot_make_this_read(self):
        with self.assertRaises(canvas_policy.PolicyDenied):
            canvas_policy.check_scope(
                "content", "GET",
                "/api/v1/courses/101/students/submissions?student_ids[]=all")


class Resubmissions(Base):
    """Graded once (often a placeholder zero), then handed in again. Still to
    grade, but kept off the main To Do list and shown under its own toggle."""

    def test_canvas_flag_decides_and_the_dates_cover_older_rows(self):
        self.assertTrue(gradesync.is_resubmission(
            {"canvas_score": 0, "canvas_grade_matches": False}))
        self.assertFalse(gradesync.is_resubmission(
            {"canvas_score": 0, "canvas_grade_matches": True,
             "canvas_state": "submitted", "submitted_at": "2026-10-18", "canvas_graded_at": "2026-10-08"}))
        # Synced before the flag was kept: back to "submitted", sent after the grade.
        self.assertTrue(gradesync.is_resubmission(
            {"canvas_score": 0, "canvas_state": "submitted",
             "submitted_at": "2026-10-18T16:43:48Z", "canvas_graded_at": "2026-10-08T20:20:44Z"}))
        # A quiz waiting on its essay is not a resubmission.
        self.assertFalse(gradesync.is_resubmission(
            {"canvas_score": 6, "canvas_state": "pending_review",
             "submitted_at": "2026-10-18T16:43:48Z", "canvas_graded_at": "2026-10-08T20:20:44Z"}))
        self.assertFalse(gradesync.is_resubmission({"canvas_score": None, "canvas_grade_matches": False}))

    def test_judge_counts_them_apart_and_keeps_the_oldest_date(self):
        infos = gradestatus.light_infos([
            sub(1, 7, workflow_state="submitted", score=None),
            sub(1, 8, workflow_state="submitted", score=0, submitted_at="2026-10-18T16:43:48Z",
                grade_matches_current_submission=False),
            sub(1, 9, workflow_state="submitted", score=40, submitted_at="2026-10-12T14:45:36Z",
                grade_matches_current_submission=False)])
        out = gradestatus.judge(row(1, needs=3), infos)
        self.assertEqual((out["graded"], out["waiting"], out["resubmitted"]), (False, 3, 2))
        self.assertEqual(out["resubmitted_since"], "2026-10-12T14:45:36Z")

    def test_only_resubmissions_leave_to_do_for_the_regrade_list(self):
        rows = self.save_list(101, [
            row(1, needs=3, name="AI Debate", due_at="2020-01-02T18:00:00Z"),
            row(2, needs=1, name="Essay", due_at="2020-01-03T18:00:00Z")])
        client = FakeClient([
            sub(1, 7), *[sub(1, u, workflow_state="submitted", score=0,
                             submitted_at="2026-10-17T18:04:35Z",
                             grade_matches_current_submission=False) for u in (8, 9, 10)],
            sub(2, 7, workflow_state="submitted", score=None),
            sub(2, 8, workflow_state="submitted", score=5, submitted_at="2026-10-20T00:00:00Z",
                grade_matches_current_submission=False)])
        gradestatus.check(client, self.store, 101, rows)
        app = FakeApp(self.tmp, client, [{"id": 101, "title": "Computer Apps",
                                          "term_label": "Fall 2026"}])
        state = hub.local_state(app, 101)
        self.assertEqual((state["to_grade"], state["waiting"], state["to_regrade"]), (1, 1, 4))
        out = hub.picker_payload(app)
        self.assertEqual([b["name"] for b in out["behind"]], ["Essay"])
        self.assertEqual(out["behind"][0]["needs_grading"], 1)
        self.assertEqual([(r["name"], r["resubmitted"]) for r in out["regrade"]],
                         [("AI Debate", 3), ("Essay", 1)])     # oldest resubmission first
        self.assertNotIn("regrade_work", out["courses"][0])

    def test_the_assignment_list_row_says_graded_with_resubmissions(self):
        from courseforge.server import App
        rows = self.save_list(101, [row(1, needs=2)])
        gradestatus.check(FakeClient([
            sub(1, 7), sub(1, 8, workflow_state="submitted", score=0,
                           grade_matches_current_submission=False)]), self.store, 101, rows)
        holder = type("Holder", (), {"store": self.store})()
        got = App.assignments(holder, 101)[0]
        self.assertEqual((got["graded"], got["waiting"], got["resubmitted"]), (False, 0, 1))


class DroppedStudents(Base):
    """students.json is saved once and kept, so tags stay put. A student who
    dropped since is still on it; Canvas sends them no submission row."""

    def sync(self, rows):
        from courseforge import grader
        from courseforge.config import Config

        class Client:
            def assignment(_s, cid, aid):
                return {"id": 2, "name": "Essay", "points_possible": 10,
                        "submission_types": ["online_text_entry"]}

            def submissions(_s, cid, aid, history=False):
                return rows

        self.store.save_students(1, [{"id": u, "name": "Student %s" % u} for u in (11, 12, 13)])
        cfg = Config()
        cfg.base_url = "https://canvas.example.edu"
        grader.sync_assignment(cfg, Client(), self.store, 1, 2)
        return self.store.extracted(1, 2)

    def text_row(self, uid):
        return {"user_id": uid, "workflow_state": "submitted", "submitted_at": "2026-10-01T00:00:00Z",
                "attempt": 1, "body": "<p>my essay</p>", "attachments": []}

    def test_a_roster_name_canvas_sent_no_row_for_gets_no_card(self):
        extracted = self.sync([self.text_row(11), {"user_id": 12, "workflow_state": "unsubmitted"}])
        self.assertEqual(sorted(extracted), ["11", "12"])
        self.assertEqual(extracted["12"]["status"], "unsubmitted")

    def test_with_no_rows_at_all_everyone_on_the_roster_still_gets_a_card(self):
        self.assertEqual(sorted(self.sync([])), ["11", "12", "13"])


class LateTurnIn(Base):
    """A grading run skipped a student for having nothing in; they turned work
    in later. The old skip must not make the page read as finished."""

    def test_the_old_skip_counts_as_waiting_not_missing_or_unreadable(self):
        from courseforge.server import App
        self.store.save_extracted(101, 1, {
            "7": {"user_id": "7", "name": "Late", "status": "submitted"},
            "8": {"user_id": "8", "name": "Absent", "status": "unsubmitted"},
            "9": {"user_id": "9", "name": "Video", "status": "submitted"},
        })
        self.store.save_draft(101, 1, {"students": {
            "7": {"source": "auto-skip", "total": None, "unscored_reason": "no submission"},
            "8": {"source": "auto-skip", "total": None, "unscored_reason": "no submission"},
            "9": {"source": "auto-skip", "total": None,
                  "unscored_reason": "1 video to watch - grade it yourself"},
        }})
        holder = type("Holder", (), {"store": self.store})()
        out = App._unscored_counts(holder, 101, 1)
        self.assertEqual(out, {"no_submission": ["Absent"], "nothing_readable": ["Video"],
                               "not_graded_yet": ["Late"]})

    def test_ungraded_lists_only_work_that_was_turned_in(self):
        js = (Path(__file__).resolve().parents[1] / "courseforge" / "web" / "js"
              / "grade.js").read_text(encoding="utf-8")
        self.assertIn("return s.status !== 'unsubmitted' && (!liveAtScan(s) || isResubmission(s));", js)

    def test_the_page_relabels_the_old_skip(self):
        js = (Path(__file__).resolve().parents[1] / "courseforge" / "web" / "js"
              / "grade.js").read_text(encoding="utf-8")
        self.assertIn("e.unscored_reason === 'no submission') return LATE_TURN_IN", js)
        self.assertIn("why === LATE_TURN_IN) ? 'not_graded_yet'", js)


class Routes(unittest.TestCase):
    def test_both_checks_are_post_jobs(self):
        from courseforge.routing import ROUTER
        want = {"/api/picker/status", "/api/courses/{cid}/grade-status"}
        hit = {r.pattern: r.method for r in ROUTER.routes if r.pattern in want}
        self.assertEqual(hit, {p: "POST" for p in want})


if __name__ == "__main__":
    unittest.main(verbosity=2)
