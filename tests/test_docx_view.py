"""The Word viewer shipped with the page stays local and intact."""
from __future__ import annotations

import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "courseforge" / "web"
OOXML = WEB / "vendor" / "ooxml"


class TheWordViewerIsVendored(unittest.TestCase):
    def test_the_docx_bundle_is_present(self):
        dist = OOXML / "dist"
        self.assertTrue((dist / "docx.mjs").is_file())
        wasm = dist / "docx_parser_bg.wasm"
        self.assertTrue(wasm.is_file())
        self.assertGreater(wasm.stat().st_size, 100_000)
        self.assertEqual(wasm.read_bytes()[:4], b"\x00asm")
        notice = (OOXML / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", notice)
        self.assertIn("Yuki Yokotani", notice)

    def test_our_page_does_not_ask_for_remote_fonts(self):
        """The viewer library knows about substitute font hosts. We never turn
        that on, and the page is not allowed to connect anywhere but itself."""
        page = (WEB / "js" / "docview.js").read_text(encoding="utf-8")
        self.assertIn("useGoogleFonts: false", page)
        self.assertNotIn("fonts.googleapis", page)
        server = (WEB.parents[1] / "courseforge" / "server.py").read_text(encoding="utf-8")
        self.assertIn("connect-src 'self'", server)


if __name__ == "__main__":
    unittest.main()
