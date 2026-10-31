"""A video is graded from what a model can read: a transcript made on this
computer and still frames on contact sheets. Never watched, and it says so."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from courseforge import grader, videoread
from courseforge.claude_cli import ClaudeResult

SPEECH = [{"start": 1.0, "end": 3.0, "text": "This is my game, Pat Doe speaking."},
          {"start": 65.0, "end": 68.0, "text": "The core loop is fishing."}]


def make_video(path: Path, seconds: int = 3, fps: int = 10) -> Path:
    import av
    import numpy as np
    with av.open(str(path), "w") as out:
        stream = out.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for i in range(seconds * fps):
            img = np.zeros((48, 64, 3), dtype=np.uint8)
            img[:, :, i % 3] = 200
            for packet in stream.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    return path


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.adir = Path(self.tmp.name)
        (self.adir / "files").mkdir()
        self.video = make_video(self.adir / "files" / "7_clip.mp4")
        self.root = self.adir / "video"
        patcher = mock.patch.object(videoread, "_transcribe", return_value=(SPEECH, "en"))
        self.transcribe = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def entry(self, **extra):
        return {"user_id": "7", "name": "Pat Doe", "pseudonym": "S-007",
                "status": "submitted", "text": "", "body_text": "", "words": 0, "images": [],
                "videos": ["clip.mp4"], "sheets": [], "filenames": ["clip.mp4"],
                "_files_dir": str(self.adir / "files"),
                "parts": [{"label": "clip.mp4", "kind": "video", "path": str(self.video),
                           "words": 0, "text": "", "data": {"playable": True}}], **extra}


class AVideoIsRead(Fixture):
    def test_frames_on_sheets_and_a_transcript(self):
        r = videoread.read(self.video, self.root)
        self.assertGreater(r["frames"], 0)
        self.assertEqual(len(r["sheets"]), -(-r["frames"] // 9))
        self.assertTrue(all(Path(s).is_file() for s in r["sheets"]))
        self.assertEqual(r["transcript"], SPEECH)
        self.assertAlmostEqual(r["duration"], 3.0, delta=0.5)

    def test_read_once_until_the_file_changes(self):
        videoread.read(self.video, self.root)
        with mock.patch.object(videoread, "_frames", side_effect=AssertionError("re-read")):
            videoread.read(self.video, self.root)
        self.assertEqual(self.transcribe.call_count, 1)
        time.sleep(0.01)
        make_video(self.video, seconds=2)
        os.utime(self.video)
        self.assertIsNone(videoread.cached(self.video, self.root))
        videoread.read(self.video, self.root)
        self.assertEqual(self.transcribe.call_count, 2)

    def test_not_a_video(self):
        bad = self.adir / "files" / "7_bad.mp4"
        bad.write_bytes(b"not a video")
        with self.assertRaises(videoread.Unreadable):
            videoread.read(bad, self.root)

    def test_transcript_text(self):
        text = videoread.as_text("clip.mp4", videoread.read(self.video, self.root))
        self.assertIn("[1:05] The core loop is fishing.", text)
        self.assertIn("## Video: clip.mp4", text)

    def test_page_gets_the_transcript_only_once_read(self):
        stored = {"7": self.entry()}
        self.assertIs(videoread.attach_cached(stored, self.root), stored)
        videoread.read(self.video, self.root)
        out = videoread.attach_cached(stored, self.root)
        self.assertEqual(out["7"]["parts"][0]["transcript"], SPEECH)
        self.assertNotIn("transcript", stored["7"]["parts"][0])


class GradingReadsIt(Fixture):
    cfg = SimpleNamespace(pseudonymize=True, video_grading=True, blend_vision=True,
                          max_images_per_student=8, model="opus", vision_model="sonnet",
                          claude_timeout_s=60, humanize_auto=False, humanize=False)

    def run_grade(self, entry, reply=None):
        seen = {}

        def fake_run(prompt, **kw):
            seen["prompt"], seen["images"] = prompt, kw.get("images") or []
            data = reply or {"criteria": [{"id": "c1", "points": 8, "rationale": "Fine."}],
                             "comment": "Good.", "flags": [], "confidence": "high",
                             "needs_human": False, "needs_human_reason": ""}
            return ClaudeResult(text="{}", data=data)

        rubric = [{"id": "c1", "label": "Content", "points": 10, "detail": "", "ratings": []}]
        with mock.patch.object(grader.llm, "run", side_effect=fake_run), \
                mock.patch.object(grader.humanize, "enabled", return_value=False):
            out = grader.grade_one(self.cfg, {"name": "Show me", "points_possible": 10},
                                   rubric, entry, "",
                                   students=[{"id": 7, "name": "Pat Doe"}])
        return out, seen

    def test_a_video_only_submission_is_graded_not_skipped(self):
        out, seen = self.run_grade(self.entry())
        self.assertEqual(out["source"], "claude")
        self.assertEqual(out["total"], 8)
        self.assertIn("The core loop is fishing.", seen["prompt"])
        self.assertIn("still frames", seen["prompt"])
        self.assertNotIn("video you cannot watch", seen["prompt"])
        self.assertTrue(seen["images"] and all(p.name.startswith("sheet-") for p in seen["images"]))

    def test_the_spoken_name_is_not_sent(self):
        _, seen = self.run_grade(self.entry())
        self.assertNotIn("Pat Doe", seen["prompt"])

    def test_it_says_it_was_read_not_watched(self):
        out, _ = self.run_grade(self.entry())
        self.assertIn(grader.VIDEO_FLAG, out["flags"])
        self.assertEqual(out["confidence"], "medium")

    def test_screenshots_keep_their_place(self):
        shots = []
        for i in range(8):
            p = self.adir / "files" / f"7_shot{i}.png"
            from PIL import Image
            Image.new("RGB", (10, 10)).save(p)
            shots.append(str(p))
        _, seen = self.run_grade(self.entry(images=shots))
        names = [p.name for p in seen["images"]]
        self.assertEqual(names[:8], [Path(s).name for s in shots])
        self.assertTrue(any(n.startswith("sheet-") for n in names[8:]))

    def test_switched_off_leaves_it_to_you(self):
        cfg = SimpleNamespace(**{**vars(self.cfg), "video_grading": False})
        out = grader.grade_one(cfg, {"name": "Show me"}, [], self.entry(), "")
        self.assertEqual(out["source"], "auto-skip")
        self.assertEqual(self.transcribe.call_count, 0)

    def test_an_unreadable_video_is_still_one_to_watch(self):
        bad = self.adir / "files" / "7_bad.mp4"
        bad.write_bytes(b"not a video")
        entry = self.entry(parts=[{"label": "bad.mp4", "kind": "video", "path": str(bad)}],
                           videos=["bad.mp4"])
        out = grader.grade_one(self.cfg, {"name": "Show me"}, [], entry, "")
        self.assertEqual(out["unscored_reason"], "1 video to watch - grade it yourself")


if __name__ == "__main__":
    unittest.main()
