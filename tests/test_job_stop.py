"""Stop on the progress chip: an auto-grade started before the instructions
were written can be ended, keeping only the students it already finished."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from courseforge import claude_cli, grader
from courseforge.config import Config
from courseforge.server import Jobs
from courseforge.store import Store


class Sink:
    """Stands in for the page's JobSink. Stop is pressed after the first student."""

    def __init__(self, stop_after: int):
        self.stop_after = stop_after
        self.stopped = False
        self.job_id = "job-test"
        self.lines: list[str] = []

    def __call__(self, message, done=None, total=None):
        self.lines.append(message)
        if done is not None and done >= self.stop_after and message.startswith("graded"):
            self.stopped = True

    def item(self, *args, **kwargs):
        pass


def graded(cfg, assignment, rubric, entry, *rest, **kw):
    return {"user_id": entry["user_id"], "source": "claude", "scores": {},
            "total": 7, "comment": "fine"}


class StoppingAnAutoGrade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        adir = self.store.assignment_dir("1", "2")
        self.store.write(adir / "assignment.json",
                         {"name": "Essay", "points_possible": 10, "rubric": []})
        self.store.write(adir / "extracted.json", {
            uid: {"user_id": uid, "name": "S" + uid, "status": "submitted",
                  "text": "work", "words": 1}
            for uid in ("1", "2", "3", "4")})
        self.store.write(adir / "draft.json", {
            "points_possible": 10, "rubric": [],
            "students": {"4": {"user_id": "4", "source": "claude", "total": 3,
                               "scores": {}, "comment": "from before"}}})
        self.cfg = Config()
        self.cfg.grading_concurrency = 1

    def test_only_the_students_finished_before_the_stop_are_saved(self):
        sink = Sink(stop_after=1)
        with mock.patch.object(grader, "grade_one", side_effect=graded):
            with self.assertRaises(grader.JobStopped) as caught:
                grader.grade_assignment(self.cfg, self.store, "1", "2", progress=sink)
        students = self.store.draft("1", "2")["students"]
        fresh = [u for u, e in students.items() if e.get("comment") == "fine"]
        self.assertEqual(len(fresh), 1, "one student finished before Stop")
        self.assertEqual(students["4"]["comment"], "from before",
                         "a student the run never reached is left as it was")
        self.assertIn("1 student of 4", str(caught.exception))

    def test_a_run_nobody_stops_still_finishes(self):
        sink = Sink(stop_after=99)
        with mock.patch.object(grader, "grade_one", side_effect=graded):
            grader.grade_assignment(self.cfg, self.store, "1", "2", progress=sink)
        self.assertEqual(sink.lines[-1], "done")


class TheJobRegistry(unittest.TestCase):
    def wait(self, jobs, job_id):
        for _ in range(200):
            if jobs.get(job_id)["state"] != "running":
                return jobs.get(job_id)
            time.sleep(0.01)
        self.fail("job never finished")

    def test_a_job_that_did_not_promise_to_stop_cleanly_refuses(self):
        jobs = Jobs()
        job_id = jobs.start("push", lambda log: time.sleep(0.3))
        out = jobs.stop(job_id)
        self.assertFalse(out["ok"])
        self.assertIn("cannot be stopped", out["error"])
        self.assertEqual(self.wait(jobs, job_id)["state"], "done")

    def test_a_cancellable_job_ends_as_stopped_not_failed(self):
        jobs = Jobs()

        def work(log):
            while not log.stopped:
                time.sleep(0.01)
            raise grader.JobStopped("Stopped. 0 students of 3 finished.")

        job_id = jobs.start("grade", work, cancellable=True)
        self.assertTrue(jobs.get(job_id)["cancellable"])
        self.assertTrue(jobs.stop(job_id)["ok"])
        info = self.wait(jobs, job_id)
        self.assertEqual(info["state"], "stopped")
        self.assertIn("0 students of 3", info["message"])
        self.assertFalse(jobs.stop(job_id)["ok"], "a finished job cannot be stopped again")


class ClaudeCallsFiledUnderAJob(unittest.TestCase):
    def test_a_call_after_stop_never_launches(self):
        claude_cli.stop_owned("job-x")
        with mock.patch.object(claude_cli.subprocess, "Popen") as popen:
            with claude_cli.owned_by("job-x"):
                with self.assertRaises(claude_cli.ClaudeError):
                    claude_cli._invoke("hi", [], "opus", 5, None, None, False)
        popen.assert_not_called()

    def test_stop_kills_only_that_jobs_processes(self):
        mine, theirs = mock.Mock(), mock.Mock()
        with claude_cli._ACTIVE_LOCK:
            claude_cli._OWNED["job-a"] = {mine}
            claude_cli._OWNED["job-b"] = {theirs}
        self.assertEqual(claude_cli.stop_owned("job-a"), 1)
        mine.kill.assert_called_once()
        theirs.kill.assert_not_called()
        with claude_cli._ACTIVE_LOCK:
            claude_cli._OWNED.pop("job-b", None)


if __name__ == "__main__":
    unittest.main()
