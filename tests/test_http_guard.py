"""Host allow-list, per-launch API key, and student-file MIME rules."""
from __future__ import annotations

import http.client
import inspect
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from courseforge import server
from courseforge.assistant import routes as _assistant_routes  # noqa: F401
from courseforge.routing import _error_payload
from courseforge.store import Store


class HostAndKeyHelpers(unittest.TestCase):
    def test_only_loopback_with_this_port(self):
        self.assertTrue(server.api_host_allowed("127.0.0.1:8900", 8900))
        self.assertTrue(server.api_host_allowed("localhost:8900", 8900))
        self.assertFalse(server.api_host_allowed("evil.example", 8900))
        self.assertFalse(server.api_host_allowed("127.0.0.1:8901", 8900))
        self.assertFalse(server.api_host_allowed("127.0.0.1", 8900))

    def test_html_and_svg_are_attachments(self):
        ctype, attach = server.student_file_response("x.html")
        self.assertEqual(ctype, "application/octet-stream")
        self.assertTrue(attach)
        ctype, attach = server.student_file_response("icon.svg")
        self.assertEqual(ctype, "application/octet-stream")
        self.assertTrue(attach)

    def test_images_video_glb_are_inline(self):
        self.assertEqual(server.student_file_response("a.png"), ("image/png", False))
        self.assertEqual(server.student_file_response("a.mp4"), ("video/mp4", False))
        self.assertEqual(server.student_file_response("a.glb"), ("model/gltf-binary", False))

    def test_download_flag_forces_octet_stream(self):
        ctype, attach = server.student_file_response("a.png", download=True)
        self.assertEqual(ctype, "application/octet-stream")
        self.assertTrue(attach)

    def test_a_page_image_stays_inside_the_files_folder(self):
        """Scanned PDF pages live in <stem>-pages/. A .. segment must not
        walk out of that folder, and a random subfolder is not a page."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "essay-pages"
            folder.mkdir()
            page = folder / "page-1.png"
            page.write_bytes(b"\x89PNG")
            (root / "pic.png").write_bytes(b"\x89PNG")
            self.assertEqual(server.submission_file(root, "pic.png"),
                             (root / "pic.png").resolve())
            self.assertEqual(server.submission_file(root, "essay-pages/page-1.png"),
                             page.resolve())
            self.assertIsNone(server.submission_file(root, "essay-pages/../pic.png"))
            self.assertIsNone(server.submission_file(root, "../pic.png"))
            (folder / "notes.html").write_text("<script>x</script>", encoding="utf-8")
            self.assertIsNone(server.submission_file(root, "other/page-1.png"))
            self.assertIsNone(server.submission_file(root, "essay-pages/notes.html"))

    def test_set_settings_allow_list_stays_narrow(self):
        src = inspect.getsource(server.App.set_settings)
        self.assertIn(
            'allowed = {"model", "vision_model", "grading_concurrency", "pseudonymize"}',
            src)


class _FakeApp:
    def __init__(self, root: Path, key: str, hook: str):
        self.cfg = SimpleNamespace(port=0, data_dir=str(root), data=Path(root),
                                   base_url="https://x.instructure.com")
        self.studio_key = key
        self.store = Store(root)
        self.jobs = server.Jobs()
        self.area_status = {}
        self.assistant = SimpleNamespace(
            secret=hook,
            permission_request=lambda body: {"decision": "deny", "reason": "test"},
        )

    def health(self):
        return {"app": "CourseForge Studio"}

    def courses(self, refresh=False):
        return [{"id": 1, "name": "Demo"}]


class LiveApiGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.key = "a" * 32
        self.hook = "b" * 32
        self.app = _FakeApp(self.tmp, self.key, self.hook)
        self.httpd = server.ThreadedServer(("127.0.0.1", 0), server.make_handler(self.app))
        self.port = self.httpd.server_address[1]
        self.app.cfg.port = self.port
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        # LIFO: shutdown the loop first, then close the socket.
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

        files = self.app.store.assignment_dir("1", "2") / "files"
        files.mkdir(parents=True, exist_ok=True)
        (files / "x.html").write_text("<script>alert(1)</script>", encoding="utf-8")
        (files / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        page = files / "essay-pages"
        page.mkdir()
        (page / "page-1.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _call(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = dict(headers or {})
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        got_headers = {k.lower(): v for k, v in resp.getheaders()}
        status = resp.status
        conn.close()
        return status, got_headers, raw

    def test_courses_without_key_is_403(self):
        status, _, raw = self._call("GET", "/api/courses")
        self.assertEqual(status, 403)
        self.assertNotIn(b"Demo", raw)

    def test_evil_host_is_403_even_with_the_key(self):
        status, _, _ = self._call("GET", "/api/courses", {
            "Host": "evil.example",
            "X-Studio-Key": self.key,
        })
        self.assertEqual(status, 403)

    def test_courses_with_the_key_is_200(self):
        status, _, raw = self._call("GET", "/api/courses", {
            "X-Studio-Key": self.key,
        })
        self.assertEqual(status, 200)
        self.assertIn(b"Demo", raw)

    def test_health_is_open(self):
        status, headers, raw = self._call("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertIn(b"CourseForge Studio", raw)
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        policy = headers.get("content-security-policy", "")
        self.assertIn("default-src 'self'", policy)
        # The Word viewer compiles a parser in the page. That is not permission
        # to eval script, and the worker that parses the file stays local.
        self.assertIn("'wasm-unsafe-eval'", policy)
        self.assertIn("worker-src 'self' blob: data:", policy)
        self.assertNotIn("unsafe-eval", policy.replace("'wasm-unsafe-eval'", ""))

    def test_html_file_is_attachment_octet_stream(self):
        status, headers, raw = self._call("GET", "/api/a/1/2/file?name=x.html", {
            "X-Studio-Key": self.key,
        })
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "application/octet-stream")
        self.assertIn("attachment", headers.get("content-disposition", ""))
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        self.assertIn(b"<script>", raw)

    def test_a_rendered_pdf_page_is_inline(self):
        status, headers, _ = self._call(
            "GET", "/api/a/1/2/file?name=essay-pages/page-1.png", {
                "Cookie": f"{server.STUDIO_COOKIE}={self.key}",
            })
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "image/png")
        status, _, _ = self._call(
            "GET", "/api/a/1/2/file?name=essay-pages/../pic.png", {
                "X-Studio-Key": self.key,
            })
        self.assertEqual(status, 404)

    def test_png_is_inline(self):
        status, headers, _ = self._call("GET", "/api/a/1/2/file?name=pic.png", {
            "Cookie": f"{server.STUDIO_COOKIE}={self.key}",
        })
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "image/png")
        self.assertNotIn("attachment", headers.get("content-disposition", ""))

    def test_permission_with_hook_secret_skips_the_browser_key(self):
        status, _, raw = self._call("POST", "/api/assistant/permission", body={
            "secret": self.hook,
        })
        self.assertNotEqual(status, 403, raw)
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data.get("decision"), "deny")

    def test_permission_without_secret_or_key_is_403(self):
        status, _, _ = self._call("POST", "/api/assistant/permission", body={
            "secret": "wrong",
        })
        self.assertEqual(status, 403)

    def test_index_carries_the_secret(self):
        status, headers, raw = self._call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'name="cf-secret"', raw)
        self.assertIn(self.key.encode(), raw)
        self.assertIn(server.STUDIO_COOKIE, headers.get("set-cookie", ""))

    def test_job_error_has_no_trace(self):
        jobs = server.Jobs()

        def boom(_log):
            raise RuntimeError("secret path C:\\\\hidden")

        job_id = jobs.start("boom", boom)
        snap = None
        for _ in range(100):
            snap = jobs.get(job_id)
            if snap and snap.get("state") != "running":
                break
            time.sleep(0.02)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["state"], "error")
        self.assertNotIn("trace", snap)
        self.assertEqual(snap["error"], "RuntimeError")
        self.assertNotIn("hidden", json.dumps(snap))


class RoutingFiveHundred(unittest.TestCase):
    def test_unexpected_error_is_type_only(self):
        out = _error_payload(RuntimeError("C:\\secret\\path"))
        self.assertEqual(out["error"], "RuntimeError")
        self.assertNotIn("trace", out)
        self.assertNotIn("secret", json.dumps(out))


if __name__ == "__main__":
    unittest.main()
