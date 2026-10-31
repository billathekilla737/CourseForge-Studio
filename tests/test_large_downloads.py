"""Large submission files: size them first, ask, then stream them to disk.

A class of screen recordings can be many gigabytes. The sync reads Canvas's
own file sizes before fetching anything; over download_confirm_mb it stops and
says how much, from how many students. The answer is "all", "skip" (leave out
each file over the per-file limit) or nothing. Fake Canvas clients only.
"""
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from courseforge import grader
from courseforge.canvas import MAX_DOWNLOAD_BYTES, CanvasClient
from courseforge.config import Config
from courseforge.store import Store

MB = 1_000_000


def video(uid, name, size):
    return {"user_id": uid, "workflow_state": "submitted", "submitted_at": "2026-10-01T00:00:00Z",
            "attempt": 1, "body": "", "attachments": [
                {"filename": name, "size": size, "url": f"https://canvas.example.edu/files/{uid}/download"}]}


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.fetched = []

    def assignment(self, cid, aid):
        return {"id": aid, "name": "Showcase", "points_possible": 10,
                "submission_types": ["online_upload"]}

    def submissions(self, cid, aid, history=False):
        return self.rows

    def download(self, url, dest, max_bytes=None):
        # Where it went under files/: the student's folder, then the name.
        self.fetched.append((f"{Path(dest).parent.name}/{Path(dest).name}", max_bytes))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"x" * 10)
        return dest


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = Store(self.tmp)
        self.store.save_students(1, [{"id": u, "name": "S%s" % u} for u in (11, 12, 13)])
        self.cfg = Config()
        self.cfg.base_url = "https://canvas.example.edu"
        self.cfg.download_confirm_mb = 500

    def sync(self, client, large=None):
        return grader.sync_assignment(self.cfg, client, self.store, 1, 2, large=large)


class ThePlan(Base):
    def test_files_already_on_disk_are_not_counted(self):
        rows = [video(11, "a.mp4", 400 * MB), video(12, "b.mp4", 200 * MB)]
        adir = self.store.assignment_dir(1, 2)
        plan = grader.download_plan(rows, adir)
        self.assertEqual((plan["files"], plan["students"], plan["bytes"]), (2, 2, 600 * MB))
        self.assertEqual((plan["over_limit"], plan["largest"]), (2, 400 * MB))
        (adir / "files" / "11_a.mp4").write_bytes(b"x")    # short: fetched again
        self.assertEqual(grader.download_plan(rows, adir)["files"], 2)
        rows[1]["attachments"][0]["size"] = 1
        (adir / "files" / "12_b.mp4").write_bytes(b"x")    # complete: left alone
        self.assertEqual(grader.download_plan(rows, adir)["files"], 1)


class TheQuestion(Base):
    def rows(self):
        return [video(11, "a.mp4", 600 * MB), video(12, "b.mp4", 900 * MB),
                video(13, "notes.docx", 2 * MB)]

    def test_over_the_line_it_asks_and_downloads_nothing(self):
        client = FakeClient(self.rows())
        out = self.sync(client)
        check = out["download_check"]
        self.assertEqual((check["files"], check["students"], check["over_limit"]), (3, 3, 2))
        self.assertEqual(check["bytes"], 1502 * MB)
        self.assertIn("free_bytes", check)
        self.assertEqual(client.fetched, [])
        self.assertFalse((self.tmp / "1" / "2" / "extracted.json").exists())

    def test_under_the_line_it_just_downloads_even_one_big_file(self):
        client = FakeClient([video(11, "a.mp4", 300 * MB)])
        out = self.sync(client)
        self.assertNotIn("download_check", out)
        self.assertEqual(client.fetched, [("11 - S11/a.mp4", 301 * MB)])

    def test_all_fetches_each_big_file_with_its_own_limit(self):
        client = FakeClient(self.rows())
        self.sync(client, large="all")
        self.assertEqual(sorted(client.fetched), [("11 - S11/a.mp4", 601 * MB), ("12 - S12/b.mp4", 901 * MB),
                                                  ("13 - S13/notes.docx", None)])

    def test_skip_leaves_out_the_big_files_says_so_and_is_remembered(self):
        client = FakeClient(self.rows())
        self.sync(client, large="skip")
        self.assertEqual(client.fetched, [("13 - S13/notes.docx", None)])
        extracted = self.store.extracted(1, 2)
        notes = [p["label"] for p in extracted["11"]["parts"] if p.get("kind") == "error"]
        self.assertTrue(any("skipped" in n and "Download all" in n for n in notes), notes)
        self.assertEqual(self.store.draft(1, 2).get("large_downloads"), "skip")
        # Opening the assignment again (no answer given) keeps the skip ...
        again = FakeClient(self.rows())
        self.assertNotIn("download_check", self.sync(again))
        self.assertEqual([f for f in again.fetched if f[0].endswith(".mp4")], [])
        # ... and Re-sync asks again.
        self.assertIn("download_check", self.sync(FakeClient(self.rows()), large="ask"))

    def test_all_refuses_when_the_disk_cannot_hold_it(self):
        from unittest import mock
        client = FakeClient(self.rows())
        with mock.patch.object(grader.shutil, "disk_usage",
                               return_value=mock.Mock(free=1000 * MB)):
            with self.assertRaises(RuntimeError) as err:
                self.sync(client, large="all")
        self.assertIn("Not enough free disk space", str(err.exception))
        self.assertEqual(client.fetched, [])


class Streaming(unittest.TestCase):
    """The bytes go to disk as they arrive; the limit can be raised per file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def client(self, body):
        class Resp(io.BytesIO):
            status = 200
            headers = {"Content-Type": "video/mp4"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class C(CanvasClient):
            def _open_url(self, url, use_token, timeout, cookies=None):
                return Resp(body)

            def get(self, path, **kw):
                # A failed fetch asks Canvas for a fresh link. Not in a test.
                raise RuntimeError("no network in tests")

        return C("https://canvas.example.edu", "fake-token", scope="grading")

    def test_a_file_is_written_whole_and_the_part_file_is_gone(self):
        dest = self.tmp / "files" / "11_a.mp4"
        self.client(b"v" * 300_000).download("https://canvas.example.edu/files/1/download", dest)
        self.assertEqual(dest.stat().st_size, 300_000)
        self.assertEqual([p.name for p in dest.parent.iterdir()], ["11_a.mp4"])

    def test_the_default_limit_still_stops_a_file_and_leaves_nothing(self):
        import courseforge.canvas as canvas
        dest = self.tmp / "files" / "11_a.mp4"
        old = canvas.MAX_DOWNLOAD_BYTES
        canvas.MAX_DOWNLOAD_BYTES = 1000
        self.addCleanup(setattr, canvas, "MAX_DOWNLOAD_BYTES", old)
        client = self.client(b"v" * 5000)
        with self.assertRaises(RuntimeError):
            client._fetch_bytes("https://canvas.example.edu/files/1/download", 5,
                                limit=1000, sink=self.tmp / "x.part")
        self.assertFalse(dest.exists())

    def test_a_raised_limit_lets_a_big_file_through(self):
        dest = self.tmp / "files" / "big.mp4"
        size = MAX_DOWNLOAD_BYTES // 1000      # stand-in: the limit is passed through
        self.client(b"v" * size).download("https://canvas.example.edu/files/1/download",
                                          dest, max_bytes=MAX_DOWNLOAD_BYTES * 2)
        self.assertEqual(dest.stat().st_size, size)


if __name__ == "__main__":
    unittest.main(verbosity=2)
