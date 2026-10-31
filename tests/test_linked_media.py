"""Work that lives in Canvas, not in a file: a Canvas Studio recording, the
older media recorder, or another outside tool. It used to come through as an
empty submission, "Nothing readable was extracted"."""
import unittest
from types import SimpleNamespace

from courseforge import extract, grader

STUDIO = ("https://mgccc.instructuremedia.com/lti/launch?custom_arc_media_id=abc"
          "&custom_arc_launch_type=embed")


def studio_sub(uid="7", **extra):
    return {"user_id": uid, "submission_type": "basic_lti_launch",
            "submitted_at": "2026-10-07T03:32:43Z", "attempt": 1,
            "url": "https://canvas.example/courses/1/external_tools/retrieve?url=x",
            "external_tool_url": STUDIO, **extra}


def empty_entry(uid="7"):
    return {"user_id": uid, "name": "Pat", "status": "submitted",
            "filenames": [], "parts": [], "text": "", "words": 0,
            "images": [], "videos": [], "sheets": []}


class ARecordingIsNamed(unittest.TestCase):
    def test_studio_recording(self):
        part = extract.linked_media_part(studio_sub())
        self.assertEqual(part["label"], "Canvas Studio recording")
        self.assertTrue(part["data"]["video"])
        self.assertIn("SpeedGrader", part["note"])

    def test_media_recorder(self):
        part = extract.linked_media_part(
            {"user_id": "7", "submission_type": "media_recording", "attempt": 1,
             "media_comment": {"media_id": "m-1"}})
        self.assertEqual(part["label"], "Canvas media recording")

    def test_other_tool_is_not_called_a_video(self):
        part = extract.linked_media_part(
            studio_sub(external_tool_url="https://tool.example/launch"))
        self.assertEqual(part["label"], "Work turned in through an outside tool")
        self.assertFalse(part["data"]["video"])

    def test_a_lookalike_host_is_not_studio(self):
        part = extract.linked_media_part(
            studio_sub(external_tool_url="https://instructuremedia.com.evil.example/x"))
        self.assertFalse(part["data"]["video"])

    def test_uploads_and_unsubmitted_are_left_alone(self):
        self.assertIsNone(extract.linked_media_part(
            studio_sub(attachments=[{"filename": "a.mp4"}])))
        self.assertIsNone(extract.linked_media_part(
            {"user_id": "7", "submission_type": "basic_lti_launch"}))
        self.assertIsNone(extract.linked_media_part(
            {"user_id": "7", "submission_type": "online_upload", "attempt": 1}))


class AnOlderSyncPicksItUp(unittest.TestCase):
    def test_page_shows_the_recording(self):
        stored = {"7": empty_entry()}
        out = extract.with_linked_media(stored, [studio_sub()])
        self.assertEqual([p["label"] for p in out["7"]["parts"]], ["Canvas Studio recording"])
        self.assertEqual(out["7"]["videos"], ["Canvas Studio recording"])
        self.assertEqual(stored["7"]["parts"], [], "the stored extract is not modified")

    def test_added_once(self):
        once = extract.with_linked_media({"7": empty_entry()}, [studio_sub()])
        twice = extract.with_linked_media(once, [studio_sub()])
        self.assertEqual(len(twice["7"]["parts"]), 1)
        self.assertEqual(len(twice["7"]["videos"]), 1)

    def test_present_text_entries_adds_it(self):
        out = extract.present_text_entries({"7": empty_entry()}, [studio_sub()],
                                           None, "1", "2")
        self.assertEqual(out["7"]["parts"][0]["kind"], extract.LINKED_KIND)

    def test_unsubmitted_entry_untouched(self):
        entry = {**empty_entry(), "status": "unsubmitted"}
        out = extract.with_linked_media({"7": entry}, [studio_sub()])
        self.assertIs(out["7"], entry)


class GradingLeavesItToYou(unittest.TestCase):
    cfg = SimpleNamespace(pseudonymize=False)

    def grade(self, sub):
        entry = extract.with_linked_media({"7": empty_entry()}, [sub])["7"]
        return grader.grade_one(self.cfg, {}, [], entry, "")

    def test_recording_is_a_video_to_watch(self):
        r = self.grade(studio_sub())
        self.assertEqual(r["unscored_reason"], "1 video to watch - grade it yourself")
        self.assertEqual(r["needs_human_reason"], r["unscored_reason"])
        self.assertIsNone(r["total"])

    def test_other_tool_is_not_called_empty(self):
        r = self.grade(studio_sub(external_tool_url="https://tool.example/launch"))
        self.assertIn("outside tool", r["unscored_reason"])
        self.assertNotIn("scores zero", r["needs_human_reason"])


class FakeClient:
    base = "https://canvas.example"

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer if answer is not None else {
            "url": "https://canvas.example/courses/1/external_tools/sessionless_launch?verifier=v"}

    def get(self, path, **params):
        self.calls.append((path, params))
        return self.answer


class FakeStore:
    def __init__(self, subs):
        self.subs = subs

    def assignment_dir(self, course_id, assignment_id):
        from pathlib import Path
        return Path("nowhere")

    def read(self, path, default=None):
        return self.subs


class ItPlaysHere(unittest.TestCase):
    def test_asks_canvas_for_a_launch_of_the_students_recording(self):
        from courseforge import studiomedia
        client = FakeClient()
        out = studiomedia.launch(client, FakeStore([studio_sub()]), "1", "2", "7")
        self.assertTrue(out["url"].startswith("https://canvas.example/"))
        path, params = client.calls[0]
        self.assertEqual(path, "/courses/1/external_tools/sessionless_launch")
        self.assertEqual(params["url"], STUDIO)

    def test_only_a_studio_recording(self):
        from courseforge import studiomedia
        client = FakeClient()
        for subs in ([studio_sub(external_tool_url="https://tool.example/x",
                                 url="https://tool.example/x")],
                     [{"user_id": "7", "submission_type": "online_upload", "attempt": 1}],
                     []):
            with self.assertRaises(ValueError):
                studiomedia.launch(client, FakeStore(subs), "1", "2", "7")
        with self.assertRaises(ValueError):
            studiomedia.launch(client, FakeStore([studio_sub()]), "1", "2", "../7")
        self.assertEqual(client.calls, [], "Canvas is not asked")

    def test_a_link_off_the_canvas_host_is_refused(self):
        from courseforge import studiomedia
        for answer in ({"url": "https://elsewhere.example/launch"},
                       {"url": "http://canvas.example/launch"}, {}):
            with self.assertRaises(RuntimeError):
                studiomedia.launch(FakeClient(answer), FakeStore([studio_sub()]),
                                   "1", "2", "7")


if __name__ == "__main__":
    unittest.main()
