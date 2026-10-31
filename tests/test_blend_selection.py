"""Blender pass for the selected students only.

Rendering is minutes per file, so the roster's shift/ctrl-click selection and
right-click menu offer the pass for just those students. The server already
took `only`; these check that the page sends it and that the server honours it.
"""
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

from courseforge import blender
from courseforge.store import Store

GRADE_JS = (Path(__file__).resolve().parents[1] / "courseforge" / "web" / "js"
            / "grade.js").read_text(encoding="utf-8")


class ThePageOffersIt(unittest.TestCase):
    def test_the_menu_and_bulk_bar_share_one_blend_action(self):
        self.assertIn("items.push({ id: 'blend',", GRADE_JS)
        self.assertIn("if (name === 'blend') return doBlend(st.blendIds);", GRADE_JS)

    def test_only_students_with_a_blend_file_are_sent(self):
        self.assertIn("blendIds: ids.filter(uid => hasBlendFile(", GRADE_JS)
        self.assertIn("body: only ? { only } : {}", GRADE_JS)

    def test_the_whole_class_button_still_sends_nobody_in_particular(self):
        self.assertIn("bb.onclick = () => doBlend(null);", GRADE_JS)


class TheServerHonoursOnly(unittest.TestCase):
    def test_only_the_named_students_files_are_read(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        store = Store(tmp)
        adir = store.assignment_dir(1, 2)
        extracted = {}
        for uid in ("11", "12", "13"):
            f = adir / "files" / f"{uid}_scene.blend"
            f.write_bytes(b"BLENDER-v300")
            extracted[uid] = {"user_id": uid, "status": "submitted", "filenames": ["scene.blend"],
                              "parts": [{"label": "scene.blend", "kind": "binary", "path": str(f)}]}
        store.save_extracted(1, 2, extracted)
        seen = []

        def fake_parts(entry):
            seen.append(entry["user_id"])
            return []

        cfg = type("Cfg", (), {"blender_path": "", "blend_concurrency": 1})()
        with mock.patch.object(blender, "find_blender", return_value="blender.exe"), \
                mock.patch.object(blender, "blend_parts", side_effect=fake_parts):
            blender.process_assignment(cfg, store, 1, 2, only=["12", "13"])
        self.assertEqual(sorted(seen), ["12", "13"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
