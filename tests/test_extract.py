"""Embedded Canvas file links in submission HTML: only Canvas hosts are fetched."""
from __future__ import annotations

import unittest

from courseforge import canvas_policy
import tempfile
from pathlib import Path

from courseforge.extract import extract_file, rce_file_refs

BASE = "https://mgccc.instructure.com"


class RceFileRefsStayOnCanvas(unittest.TestCase):
    def test_evil_host_is_ignored(self):
        html = '<a href="https://evil.example/files/99">x.docx</a>'
        self.assertEqual(rce_file_refs(html, BASE), [])

    def test_relative_file_is_resolved_to_the_canvas_host(self):
        html = '<a href="/files/99?verifier=abc">notes.docx</a>'
        refs = rce_file_refs(html, BASE)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["file_id"], "99")
        self.assertTrue(refs[0]["url"].startswith(BASE + "/files/99/download"))
        self.assertIn("verifier=abc", refs[0]["url"])
        self.assertEqual(refs[0]["name"], "notes.docx")

    def test_canvas_absolute_url_is_kept(self):
        html = ('<a href="https://mgccc.instructure.com/users/7/files/42/'
                'preview?verifier=tok">Mover.cs</a>')
        refs = rce_file_refs(html, BASE)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["file_id"], "42")
        self.assertIn("mgccc.instructure.com", refs[0]["url"])
        self.assertTrue(refs[0]["url"].startswith("https://"))

    def test_http_is_refused(self):
        html = '<a href="http://mgccc.instructure.com/files/99">x.docx</a>'
        self.assertEqual(rce_file_refs(html, BASE), [])

    def test_protocol_relative_off_host_is_ignored(self):
        html = '<a href="//evil.example/files/99">x.docx</a>'
        self.assertEqual(rce_file_refs(html, BASE), [])

    def test_localhost_is_ignored(self):
        html = '<a href="https://127.0.0.1/files/99">x.docx</a>'
        self.assertEqual(rce_file_refs(html, BASE), [])

    def test_rfc1918_is_ignored(self):
        html = '<a href="https://10.0.0.8/files/99">x.docx</a>'
        self.assertEqual(rce_file_refs(html, BASE), [])

    def test_img_on_canvas_is_kept(self):
        html = ('<img src="https://mgccc.instructure.com/files/5/preview'
                '?verifier=v" alt="shot.png">')
        refs = rce_file_refs(html, BASE)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["tag"], "img")
        self.assertEqual(refs[0]["name"], "shot.png")

    def test_an_empty_link_uses_the_title_filename(self):
        """Canvas file links often have no visible text. The filename is
        the title attribute, and without it the file is saved with no
        extension and the extractor reports that it cannot read it."""
        html = ('<a class="instructure_file_link" title="ColorTrigger.cs" '
                'href="https://mgccc.instructure.com/users/7/files/111136056'
                '?verifier=tok"></a>')
        refs = rce_file_refs(html, BASE)
        self.assertEqual(refs[0]["name"], "ColorTrigger.cs")
        self.assertEqual(refs[0]["file_id"], "111136056")

    def test_visible_filename_wins_over_the_title(self):
        html = ('<a title="other.txt" href="https://mgccc.instructure.com/'
                'files/3?verifier=tok">LampScript.cs</a>')
        refs = rce_file_refs(html, BASE)
        self.assertEqual(refs[0]["name"], "LampScript.cs")


class ExtensionlessText(unittest.TestCase):
    def test_a_text_file_with_no_extension_is_read(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "file_111136056"
        path.write_text("public class Trigger { }\n", encoding="utf-8")
        got = extract_file(path, "ColorTrigger.cs")
        self.assertEqual(got.kind, "text")
        self.assertIn("public class Trigger", got.text)
        self.assertIn("```cs", got.text)
        self.assertNotIn("no text extractor", got.note)

    def test_a_binary_file_with_no_extension_stays_unreadable(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "file_9"
        path.write_bytes(b"\x00\x01\x02\xff" * 40)
        got = extract_file(path)
        self.assertEqual(got.kind, "binary")
        self.assertIn("no text extractor", got.note)


class FileUrlAllowList(unittest.TestCase):
    def test_canvas_host_is_allowed(self):
        self.assertTrue(canvas_policy.file_url_allowed(
            BASE + "/files/1/download", BASE))

    def test_inscloudgate_is_allowed(self):
        self.assertTrue(canvas_policy.file_url_allowed(
            "https://inst-fs-iad-prod.inscloudgate.net/files/x", BASE))

    def test_metadata_ip_is_blocked(self):
        self.assertTrue(canvas_policy.is_blocked_host("169.254.169.254"))
        self.assertFalse(canvas_policy.file_url_allowed(
            "https://169.254.169.254/latest/meta-data", BASE))

    def test_userinfo_cannot_spoof_the_canvas_host(self):
        self.assertFalse(canvas_policy.file_url_allowed(
            "https://mgccc.instructure.com@127.0.0.1/files/1", BASE))


if __name__ == "__main__":
    unittest.main()
