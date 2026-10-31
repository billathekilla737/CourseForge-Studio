"""A file saved with no name is read by what it is, not left as "no text
extractor". A phone screenshot pasted into the Canvas text box comes down as
file_<id>, and the student was graded as if they had sent nothing."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from courseforge import extract

PASTED = ('<p><img src="https://canvas.example/files/111/download?verifier=v" alt="" '
          'data-api-endpoint="https://canvas.example/api/v1/files/111"></p>')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def nameless(self, data: bytes, name="rce111_file_111") -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def picture(self, fmt: str) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (20, 40), (200, 30, 30)).save(buf, fmt)
        return buf.getvalue()


class TheBytesSayWhatItIs(Fixture):
    def test_pictures(self):
        for fmt, ext in (("JPEG", ".jpg"), ("PNG", ".png"), ("GIF", ".gif"), ("WEBP", ".webp")):
            self.assertEqual(extract.sniff_ext(self.nameless(self.picture(fmt))), ext, fmt)

    def test_pdf_and_office(self):
        self.assertEqual(extract.sniff_ext(self.nameless(b"%PDF-1.7\n...")), ".pdf")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("word/document.xml", "<w/>")
        self.assertEqual(extract.sniff_ext(self.nameless(buf.getvalue())), ".docx")

    def test_phone_photo_and_video(self):
        self.assertEqual(extract.sniff_ext(self.nameless(b"\0\0\0\x18ftypheic" + b"\0" * 20)),
                         ".heic")
        self.assertEqual(extract.sniff_ext(self.nameless(b"\0\0\0\x18ftypqt  " + b"\0" * 20)),
                         ".mov")

    def test_unknown_stays_unknown(self):
        self.assertEqual(extract.sniff_ext(self.nameless(b"\x01\x02\x03 nothing known")), "")


class APastedScreenshotIsGraded(Fixture):
    def test_read_as_an_image(self):
        shot = self.nameless(self.picture("JPEG"))
        sub = extract.extract_submission(PASTED, [shot])
        self.assertEqual(len(sub.images), 1)
        self.assertTrue(Path(sub.images[0].path).is_file())
        self.assertEqual(Path(sub.images[0].path).suffix, ".png", "a copy the model can read")
        notes = " ".join(p.note for p in sub.parts)
        self.assertNotIn("no text extractor", notes)
        self.assertNotIn("could not be downloaded", notes)

    def test_a_named_file_is_read_by_its_name(self):
        named = self.dir / "notes.txt"
        named.write_text("%PDF looks like a pdf but is text", encoding="utf-8")
        part = extract.extract_file(named)
        self.assertEqual(part.kind, "text")
        self.assertIn("looks like a pdf", part.text)


class AnOlderSyncIsPutRight(Fixture):
    """Graded before files were read by their bytes: no re-sync needed."""

    def stored(self, shot: Path) -> dict:
        return {"7": {"user_id": "7", "status": "submitted",
                      "text": "--- Canvas text entry ---\n-", "body_text": "--- Canvas text entry ---\n-",
                      "words": 1, "images": [], "videos": [],
                      "unreadable": ["file_111", "1 inline image(s)"],
                      "parts": [{"label": "Canvas text entry", "kind": "text", "text": "-"},
                                {"label": "file_111", "kind": "binary",
                                 "note": extract.NAMELESS_NOTE, "path": str(shot)},
                                {"label": "1 inline image(s)", "kind": "binary",
                                 "note": extract.INLINE_NOTE + " and they could not be downloaded"}]}}

    def test_the_picture_is_found_and_the_false_notes_go(self):
        stored = self.stored(self.nameless(self.picture("JPEG")))
        out = extract.reread_nameless(stored)["7"]
        self.assertEqual([p["kind"] for p in out["parts"]], ["text", "image"])
        self.assertEqual(len(out["images"]), 1)
        self.assertEqual(out["unreadable"], [])
        self.assertEqual(stored["7"]["parts"][1]["kind"], "binary", "stored copy untouched")

    def test_a_nameless_document_adds_its_text(self):
        buf = io.BytesIO()
        import docx
        d = docx.Document()
        d.add_paragraph("My badge link is here")
        d.save(buf)
        out = extract.reread_nameless(self.stored(self.nameless(buf.getvalue())))["7"]
        self.assertIn("My badge link is here", out["body_text"])
        self.assertEqual(len(out["parts"]), 3, "no picture found, so the inline note stays")

    def test_nothing_to_do_returns_the_same(self):
        stored = self.stored(self.nameless(b"\x01\x02 unknown"))
        self.assertIs(extract.reread_nameless(stored), stored)


if __name__ == "__main__":
    unittest.main()
