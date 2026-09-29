"""Student files the auto-grader has to be able to read."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from courseforge.extract import (extract_submission, present_text_entries,
                                 unpack_file, xlsx_to_text, pptx_to_text)


def _xlsx(path: Path) -> None:
    shared = """<?xml version="1.0"?>
<sst><si><t>Name</t></si><si><t>Score</t></si><si><t>Ada</t></si></sst>"""
    sheet = """<?xml version="1.0"?>
<worksheet><sheetData>
<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>
<row><c t="s"><v>2</v></c><c><v>18</v></c></row>
</sheetData></worksheet>"""
    book = """<?xml version="1.0"?><workbook><sheets>
<sheet name="Grades" sheetId="1" r:id="rId1"/>
</sheets></workbook>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
        zf.writestr("xl/workbook.xml", book)


def _pptx(path: Path) -> None:
    slide = """<?xml version="1.0"?><p:sld>
<a:t>The game loop</a:t><a:t>update then draw</a:t>
</p:sld>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", slide)


def _docx_with_picture(path: Path) -> None:
    document = """<?xml version="1.0"?><w:document>
<w:p><w:t>See the screenshot.</w:t></w:p></w:document>"""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 5000
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
        zf.writestr("word/media/shot.png", png)


class OfficeAndImages(unittest.TestCase):
    def test_excel_cells_become_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grades.xlsx"
            _xlsx(path)
            text = xlsx_to_text(path)
            self.assertIn("Grades", text)
            self.assertIn("Ada", text)
            self.assertIn("18", text)

    def test_powerpoint_slide_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deck.pptx"
            _pptx(path)
            text = pptx_to_text(path)
            self.assertIn("The game loop", text)
            self.assertIn("update then draw", text)

    def test_a_screenshot_inside_word_is_an_image_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "essay.docx"
            _docx_with_picture(path)
            parts = unpack_file(path)
            kinds = [p.kind for p in parts]
            self.assertIn("text", kinds)
            self.assertIn("image", kinds)
            self.assertIn("See the screenshot.", parts[0].text)
            sub = extract_submission(None, [path])
            self.assertTrue(sub.images)
            self.assertTrue(sub.images[0].path.endswith(".png"))

    def test_a_scanned_pdf_becomes_page_images(self):
        try:
            import pymupdf
        except ImportError:
            self.skipTest("pymupdf is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            blank = Path(tmp) / "blank.pdf"
            doc = pymupdf.open()
            doc.new_page()
            doc.save(blank)
            doc.close()
            parts = unpack_file(blank)
            self.assertTrue(any(p.kind == "image" for p in parts),
                            [ (p.kind, p.note) for p in parts ])

    def test_a_canvas_text_entry_keeps_its_table(self):
        """The work pane used to show the editor as pipes. Grading still gets
        the flat text; the page gets the table."""
        body = (
            "<h2>Part 4</h2><p>The <strong>goal</strong> is the hard part.</p>"
            "<script>alert(1)</script>"
            "<table><tr><th>Case</th><th>Verdict</th></tr>"
            "<tr><td>Walker</td><td>Gray area</td></tr></table>"
            '<img src="https://school.example/files/55/preview" alt="chart">'
        )
        flat = extract_submission(body, []).text
        self.assertIn("|", flat)
        self.assertNotIn("<table>", flat)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            shot = folder / "9_rce55_chart.png"
            shot.write_bytes(b"\x89PNG\r\n\x1a\n")
            shown = present_text_entries(
                {"9": {"parts": [{"label": "Canvas text entry", "text": flat}]}},
                [{"user_id": 9, "body": body}],
                folder, "1", "2")
        html = shown["9"]["parts"][0]["html"]
        self.assertIn("<h2>", html)
        self.assertIn("<strong>", html)
        self.assertIn("<table>", html)
        self.assertIn("<th>", html)
        self.assertIn("<td>Walker</td>", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("alert", html)
        self.assertIn("/api/a/1/2/file?name=9_rce55_chart.png", html)
        self.assertNotIn("school.example", html)
        # The copy handed to the page is not written back onto the extract
        # the grader reads.
        self.assertNotIn("html", extract_submission(body, []).parts[0].__dict__)

    def test_a_png_submission_is_graded_as_an_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screen.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
            sub = extract_submission(None, [path])
            self.assertEqual(len(sub.images), 1)
            self.assertEqual(sub.images[0].kind, "image")
            self.assertTrue(sub.images[0].path.endswith(".png"))


if __name__ == "__main__":
    unittest.main()
