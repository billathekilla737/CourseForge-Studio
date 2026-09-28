"""A document the sync could not read must never turn into a quiet low score.

What happened (2026-09-28, one section, one assignment): opening the assignment
started a sync, and Re-sync started another. The second saw a student's Word
file already on disk, skipped the fetch, and read the half of it the first sync
had written so far: "BadZipFile: File is not a zip file". The one picture inside
the file came out fine a moment later. The grader then scored the picture, gave
28 out of 100 with high confidence, flagged four "missing parts" that were all
in the Word file, and did not ask for review. The record kept the error because
the file "existed", so nothing healed on its own.

Four things now stand in the way of that, each pinned here:

  * a download is written beside its destination and renamed into place, so a
    file that exists is complete, and an empty or short copy is fetched again;
  * one sync per assignment at a time (a lock in the grader, and the job
    registry returns the running sync instead of starting a twin);
  * a document that fails to open is read again a second later before it is
    reported as unreadable;
  * a submission whose document could not be read is not scored from the
    scraps, and one that is scored anyway is held for a person.
"""
import io
import json
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from courseforge import extract, grader, server
from courseforge.canvas import CanvasClient


def minimal_docx(words: int = 60) -> bytes:
    """A Word file with only what the extractor reads: document.xml with text."""
    body = " ".join(f"word{i}" for i in range(words))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body><w:p><w:r><w:t>{body}</w:t></w:r></w:p></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
        zf.writestr("word/document.xml", document)
    return buf.getvalue()


class TheDownloadIsWholeOrAbsent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.client = CanvasClient("https://school.example.edu", "tok", scope="grading")

    def test_the_destination_appears_only_when_complete(self):
        dest = self.tmp / "9_essay.docx"
        seen = {}

        def fetch(url, timeout):
            # while the bytes are being fetched, nothing is at the destination
            seen["existed_during_fetch"] = dest.exists()
            return b"PK" + b"x" * 5000

        with mock.patch.object(self.client, "_fetch_bytes", fetch):
            out = self.client.download("https://school.example.edu/files/1/download", dest)
        self.assertEqual(out, dest)
        self.assertFalse(seen["existed_during_fetch"])
        self.assertEqual(dest.read_bytes(), b"PK" + b"x" * 5000)
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["9_essay.docx"],
                         "no .part file is left beside the download")

    def test_an_empty_body_is_refused_and_leaves_nothing_behind(self):
        dest = self.tmp / "9_essay.docx"
        with mock.patch.object(self.client, "_fetch_bytes", lambda url, timeout: b""):
            with self.assertRaises(RuntimeError):
                self.client.download("https://school.example.edu/files/1/download", dest)
        self.assertEqual(list(self.tmp.iterdir()), [])


class ADocumentIsReadTwiceBeforeItIsGivenUpOn(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_file_that_finishes_arriving_is_read_on_the_second_try(self):
        whole = minimal_docx(60)
        path = self.tmp / "9_MDA_Teardown.docx"
        path.write_bytes(whole[: len(whole) // 2])       # the half a sync could see

        def finish(_seconds):
            path.write_bytes(whole)                        # the writer completes

        with mock.patch.object(extract.time, "sleep", finish):
            part = extract.extract_file(path)
        self.assertEqual(part.kind, "text")
        self.assertEqual(part.words, 60)

    def test_a_truly_broken_file_fails_twice_and_says_so(self):
        path = self.tmp / "9_MDA_Teardown.docx"
        path.write_bytes(b"this was never a zip")
        with mock.patch.object(extract.time, "sleep", lambda _s: None):
            part = extract.extract_file(path)
        self.assertEqual(part.kind, "error")
        self.assertIn("BadZipFile", part.note)
        self.assertIn("read twice", part.note)

    def test_a_good_file_is_read_once(self):
        path = self.tmp / "9_MDA_Teardown.docx"
        path.write_bytes(minimal_docx(12))
        with mock.patch.object(extract.time, "sleep",
                               side_effect=AssertionError("no retry was needed")):
            part = extract.extract_file(path)
        self.assertEqual(part.words, 12)


class OneSyncPerAssignmentAtATime(unittest.TestCase):
    def test_the_lock_is_per_assignment(self):
        self.assertIs(grader.sync_lock("1", "2"), grader.sync_lock(1, 2))
        self.assertIsNot(grader.sync_lock("1", "2"), grader.sync_lock("1", "3"))

    def test_two_syncs_of_one_assignment_do_not_overlap(self):
        inside = {"now": 0, "peak": 0}
        gate = threading.Lock()

        def slow_sync(cfg, client, store, cid, aid, progress, me_id):
            with gate:
                inside["now"] += 1
                inside["peak"] = max(inside["peak"], inside["now"])
            time.sleep(0.05)
            with gate:
                inside["now"] -= 1
            return {"ok": True}

        with mock.patch.object(grader, "_sync_assignment", slow_sync):
            threads = [threading.Thread(target=grader.sync_assignment,
                                        args=(None, None, None, "7", "8"))
                       for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)
        self.assertEqual(inside["peak"], 1)

    def test_the_job_registry_returns_the_running_sync_instead_of_a_twin(self):
        jobs = server.Jobs()
        release = threading.Event()
        first = jobs.start("sync", lambda log: release.wait(5) and {}, key="sync:1:2")
        again = jobs.start("sync", lambda log: {}, key="sync:1:2")
        other = jobs.start("sync", lambda log: {}, key="sync:1:3")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        release.set()
        for _ in range(100):
            if jobs.running("sync:1:2") is None:
                break
            time.sleep(0.01)
        later = jobs.start("sync", lambda log: {}, key="sync:1:2")
        self.assertNotEqual(first, later, "once the sync is done a new one may start")

    def test_jobs_without_a_key_are_never_merged(self):
        jobs = server.Jobs()
        a = jobs.start("grade", lambda log: {})
        b = jobs.start("grade", lambda log: {})
        self.assertNotEqual(a, b)


RUBRIC = [{"id": "c1", "label": "Idea", "points": 10, "detail": "", "ratings": []}]
ASSIGNMENT = {"name": "MDA Teardown", "points_possible": 10, "description": ""}
BROKEN_PART = {"label": "MDA_Teardown.docx", "kind": "error", "path": "",
               "note": "BadZipFile: File is not a zip file"}
IMAGE_PART = {"label": "MDA_Teardown.docx - image1.png", "kind": "image",
              "path": "", "note": "image inside the submitted file"}


class Bare:
    pseudonymize = True
    model = "opus"
    claude_timeout_s = 60
    blend_vision = True
    vision_model = "sonnet"
    max_images_per_student = 8


def entry(text: str, parts: list, images: list) -> dict:
    return {"user_id": "9", "pseudonym": "S-001", "name": "Jane Doe",
            "status": "submitted", "words": len(text.split()), "text": text,
            "parts": parts, "unreadable": [p["label"] for p in parts if p["kind"] == "error"],
            "images": images, "filenames": ["MDA_Teardown.docx"]}


class AnUnreadableDocumentIsNotGradedFromTheScraps(unittest.TestCase):
    def test_unreadable_documents_are_told_apart_from_other_failures(self):
        e = entry("", [BROKEN_PART, IMAGE_PART,
                       {"label": "photo.png", "kind": "error", "note": "OSError: x"},
                       {"label": "download failed: notes.pdf (timeout)", "kind": "error",
                        "note": "attachment could not be fetched from Canvas"}], [])
        found = grader.unreadable_documents(e)
        self.assertEqual(len(found), 2)
        self.assertIn("MDA_Teardown.docx (BadZipFile: File is not a zip file)", found[0])
        self.assertTrue(found[1].startswith("download failed: notes.pdf"))

    def test_a_picture_from_a_broken_word_file_is_not_a_grade(self):
        e = entry("", [BROKEN_PART, IMAGE_PART], ["C:/nowhere/image1.png"])
        with mock.patch.object(grader.llm, "run",
                               side_effect=AssertionError("the model must not be asked")):
            out = grader.grade_one(Bare(), ASSIGNMENT, RUBRIC, e, "")
        self.assertEqual(out["source"], "auto-skip")
        self.assertIsNone(out["total"])
        self.assertTrue(out["needs_human"])
        self.assertIn("MDA_Teardown.docx", out["needs_human_reason"])
        self.assertIn("re-sync", out["needs_human_reason"].lower())
        self.assertIn("document could not be read", out["flags"])

    def test_a_score_drafted_beside_a_broken_file_is_held_for_a_person(self):
        text = " ".join(["word"] * 80)
        e = entry(text, [{"label": "Canvas text entry", "kind": "text"}, BROKEN_PART], [])
        reply = {"criteria": [{"id": "c1", "points": 8, "rationale": "S-001 covered it."}],
                 "comment": "Name the rule.", "flags": [], "confidence": "high",
                 "needs_human": False, "needs_human_reason": ""}

        def fake_run(prompt, **kw):
            return type("R", (), {"text": json.dumps(reply), "data": reply, "cost_usd": 0.0,
                                  "repaired": False, "parse_error": ""})()

        with mock.patch.object(grader.llm, "run", fake_run):
            out = grader.grade_one(Bare(), ASSIGNMENT, RUBRIC, e, "")
        self.assertEqual(out["total"], 8.0)
        self.assertTrue(out["needs_human"], "the push must not send this until someone looks")
        self.assertIn("document could not be read", out["flags"])
        self.assertIn("MDA_Teardown.docx", out["needs_human_reason"])
        self.assertIn("re-grade", out["needs_human_reason"].lower())

    def test_a_readable_submission_is_untouched_by_the_guard(self):
        e = entry(" ".join(["word"] * 80), [{"label": "essay.docx", "kind": "text"}], [])
        self.assertEqual(grader.unreadable_documents(e), [])


if __name__ == "__main__":
    unittest.main()
