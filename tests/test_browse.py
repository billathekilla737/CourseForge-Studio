"""data/Browse: shortcuts into each synced assignment's files folder by name.

The numbered folders stay the real storage. These tests build a scratch data
folder; nothing here reaches Canvas or touches the real data/.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from courseforge import browse
from courseforge.config import Config
from courseforge.store import Store

S = browse.SUFFIX


def fake_writer(calls):
    """Writes a stand-in shortcut holding its target, and records each batch."""
    def write(links):
        calls.append(list(links))
        for path, target in links:
            if S:
                path.write_text(target, encoding="utf-8")
            else:
                os.symlink(target, path, target_is_directory=True)
    return write


class Segment(unittest.TestCase):
    def test_windows_forbidden_characters(self):
        self.assertEqual(browse.segment('Unit 1: "Why?" <draft>*', "x"), "Unit 1 - Why draft")
        self.assertEqual(browse.segment("Lab 2/3 | Part A\\B", "x"), "Lab 2-3 - Part A-B")

    def test_trailing_dots_and_spaces_are_dropped(self):
        self.assertEqual(browse.segment("Week 4...  ", "x"), "Week 4")

    def test_reserved_device_names(self):
        self.assertEqual(browse.segment("CON", "x"), "_CON")
        self.assertEqual(browse.segment("aux.txt", "x"), "_aux.txt")
        self.assertEqual(browse.segment("Console", "x"), "Console")

    def test_empty_or_punctuation_only_uses_fallback(self):
        self.assertEqual(browse.segment("", "Assignment 9"), "Assignment 9")
        self.assertEqual(browse.segment(" ?*. ", "Assignment 9"), "Assignment 9")
        self.assertEqual(browse.segment(None, "Course 1"), "Course 1")

    def test_long_names_are_cut(self):
        out = browse.segment("A" * 200, "x")
        self.assertEqual(len(out), browse.MAX_SEGMENT)

    def test_control_characters_removed(self):
        self.assertEqual(browse.segment("Tab\there\x00", "x"), "Tab here")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-browse-"))
        self.root = self.tmp / "data"
        self.store = Store(self.root)
        self.courses = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def course(self, cid, title, term="Fall 2026", code="CSC 1123 FOB (Online)"):
        self.courses.append({"id": cid, "title": title, "code": code,
                             "name": f"202630 {code} {title}", "term_label": term})
        self.store.save_courses(self.courses)

    def assignment(self, cid, aid, name, synced=True, listed=True):
        adir = self.store.assignment_dir(cid, aid)
        if synced:
            self.store.write(adir / "extracted.json", {"1": {"text": "x"}})
            (adir / "files" / "1_work.docx").write_bytes(b"student work")
        if listed:
            rows = [r for r in self.store.assignments(cid) if str(r["id"]) != str(aid)]
            rows.append({"id": aid, "name": name})
            self.store.save_assignments(cid, rows)
        else:
            self.store.write(adir / "assignment.json", {"id": aid, "name": name})
        return adir


class Plan(Fixture):
    def test_term_course_assignment(self):
        self.course(735790, "Computer Applications I")
        adir = self.assignment(735790, 16105230, "AI Debate")
        self.assertEqual(browse.plan(self.root), {
            "Fall 2026/Computer Applications I/AI Debate": str(adir / "files")})

    def test_unsynced_assignments_are_left_out(self):
        self.course(735790, "Computer Applications I")
        self.assignment(735790, 1, "Synced")
        self.assignment(735790, 2, "Only listed", synced=False)
        self.assertEqual(list(browse.plan(self.root)),
                         ["Fall 2026/Computer Applications I/Synced"])

    def test_same_assignment_name_gets_canvas_ids(self):
        self.course(735790, "Computer Applications I")
        self.assignment(735790, 11, "Quiz 1")
        self.assignment(735790, 12, "quiz 1")    # Windows sees these as one name
        self.assignment(735790, 13, "Quiz 2")
        self.assertEqual(sorted(browse.plan(self.root)), [
            "Fall 2026/Computer Applications I/Quiz 1 [11]",
            "Fall 2026/Computer Applications I/Quiz 2",
            "Fall 2026/Computer Applications I/quiz 1 [12]",
        ])

    def test_two_sections_in_one_term_get_their_code(self):
        self.course(1, "Computer Applications I", code="CSC 1123 FOA (Online)")
        self.course(2, "Computer Applications I", code="CSC 1123 FOB (Online)")
        self.course(3, "Computer Applications I", term="Spring 2027", code="CSC 1123 FOA (Online)")
        for cid in (1, 2, 3):
            self.assignment(cid, 100 + cid, "AI Debate")
        self.assertEqual(sorted(browse.plan(self.root)), [
            "Fall 2026/Computer Applications I (FOA)/AI Debate",
            "Fall 2026/Computer Applications I (FOB)/AI Debate",
            "Spring 2027/Computer Applications I/AI Debate",
        ])

    def test_identical_sections_fall_back_to_course_id(self):
        self.course(1, "Sandbox", code="Sandbox")
        self.course(2, "Sandbox", code="Sandbox")
        for cid in (1, 2):
            self.assignment(cid, 100 + cid, "Test")
        self.assertEqual(sorted(browse.plan(self.root)), [
            "Fall 2026/Sandbox (Sandbox) [1]/Test", "Fall 2026/Sandbox (Sandbox) [2]/Test"])

    def test_missing_metadata_falls_back(self):
        # No courses.json entry and no assignments.json row for these.
        self.assignment(900, 901, "From assignment.json", listed=False)
        self.store.assignment_dir(900, 902)
        self.store.write(self.store.assignment_dir(900, 902) / "extracted.json", {})
        self.assertEqual(sorted(browse.plan(self.root)), [
            "Sandboxes and other/Course 900/Assignment 902",
            "Sandboxes and other/Course 900/From assignment.json",
        ])

    def test_canvas_term_string_without_label(self):
        self.store.save_courses([{"id": 5, "name": "Sandbox_Z", "term": "Default Term"}])
        self.assignment(5, 50, "Try")
        self.assertEqual(list(browse.plan(self.root)), ["Sandboxes and other/Sandbox_Z/Try"])

    def test_browse_folder_itself_is_not_a_course(self):
        self.course(735790, "Computer Applications I")
        self.assignment(735790, 1, "One")
        browse.refresh(self.root, writer=fake_writer([]))
        self.assertEqual(len(browse.plan(self.root)), 1)


class DeepDataFolder(Fixture):
    def test_names_shrink_to_stay_under_windows_path_limit(self):
        deep = self.tmp / ("d" * 120)
        self.root = deep / "data"
        self.store = Store(self.root)
        self.course(1, "Computer Applications I " + "x" * 50)
        self.assignment(1, 11, "Unit 2: Spreadsheets " + "y" * 50)
        self.assignment(1, 12, "Unit 2: Spreadsheets " + "y" * 60)
        rels = list(browse.plan(self.root))
        self.assertEqual(len(rels), 2)
        base = self.root.absolute() / browse.FOLDER
        for rel in rels:
            self.assertLessEqual(len(str(base / rel)) + len(S), browse.MAX_PATH)
        self.assertTrue(all(rel.endswith(("[11]", "[12]")) for rel in rels), rels)

    def test_short_data_folder_keeps_full_names(self):
        self.course(1, "C" * 60)
        self.assignment(1, 11, "A" * 60)
        self.assertEqual(list(browse.plan(self.root)), [f"Fall 2026/{'C' * 60}/{'A' * 60}"])


class Refresh(Fixture):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.write = fake_writer(self.calls)
        self.course(735790, "Computer Applications I")
        self.adir = self.assignment(735790, 16105230, "AI Debate")
        self.base = self.root / browse.FOLDER

    def link(self, *parts):
        return self.base.joinpath(*parts[:-1], parts[-1] + S)

    def test_makes_shortcut_and_manifest(self):
        out = browse.refresh(self.root, writer=self.write)
        path = self.link("Fall 2026", "Computer Applications I", "AI Debate")
        self.assertTrue(path.is_file() or path.is_symlink())
        self.assertEqual(out, {"made": 1, "removed": 0, "total": 1, "missing": 0})
        manifest = json.loads((self.base / browse.MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest, {
            f"Fall 2026/Computer Applications I/AI Debate{S}": str(self.adir / "files")})

    def test_second_run_writes_nothing(self):
        browse.refresh(self.root, writer=self.write)
        browse.refresh(self.root, writer=self.write)
        self.assertEqual(len(self.calls), 1)

    def test_rename_in_canvas_moves_the_shortcut(self):
        browse.refresh(self.root, writer=self.write)
        self.store.save_assignments(735790, [{"id": 16105230, "name": "AI Debate (final)"}])
        self.courses[0]["title"] = "Computer Apps I"
        self.store.save_courses(self.courses)
        out = browse.refresh(self.root, writer=self.write)
        self.assertEqual(out["removed"], 1)
        self.assertTrue(self.link("Fall 2026", "Computer Apps I", "AI Debate (final)").exists())
        # The old course folder was emptied and pruned.
        self.assertFalse((self.base / "Fall 2026" / "Computer Applications I").exists())

    def test_empty_folder_of_the_instructors_is_kept(self):
        browse.refresh(self.root, writer=self.write)
        mine = self.base / "Fall 2026" / "To sort"
        mine.mkdir()
        self.store.save_assignments(735790, [{"id": 16105230, "name": "Renamed"}])
        browse.refresh(self.root, writer=self.write)
        self.assertTrue(mine.is_dir())

    def test_case_only_rename(self):
        browse.refresh(self.root, writer=self.write)
        self.store.save_assignments(735790, [{"id": 16105230, "name": "AI DEBATE"}])
        browse.refresh(self.root, writer=self.write)
        names = os.listdir(self.base / "Fall 2026" / "Computer Applications I")
        self.assertEqual(names, ["AI DEBATE" + S])

    def test_never_touches_the_instructors_own_files(self):
        browse.refresh(self.root, writer=self.write)
        mine = self.base / "Fall 2026" / "notes.txt"
        mine.write_text("mine", encoding="utf-8")
        folder = self.base / "Fall 2026" / "Computer Applications I" / "kept"
        folder.mkdir()
        (folder / "a.txt").write_text("a", encoding="utf-8")
        shutil.rmtree(self.adir)          # the assignment goes away entirely
        browse.refresh(self.root, writer=self.write)
        self.assertEqual(mine.read_text(encoding="utf-8"), "mine")
        self.assertTrue((folder / "a.txt").is_file())
        self.assertFalse(self.link("Fall 2026", "Computer Applications I", "AI Debate").exists())

    def test_a_real_file_in_the_way_is_left_alone(self):
        if not S:
            self.skipTest("only .lnk shortcuts can collide with a file of the same name")
        path = self.link("Fall 2026", "Computer Applications I", "AI Debate")
        path.mkdir(parents=True)          # a folder named like the shortcut
        out = browse.refresh(self.root, writer=self.write)
        self.assertTrue(path.is_dir())
        self.assertEqual(out["missing"], 1)

    def test_stored_work_is_unchanged(self):
        def snapshot():
            return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.root.rglob("*") if p.is_file()
                    and browse.FOLDER not in p.relative_to(self.root).parts}
        before = snapshot()
        browse.refresh(self.root, writer=self.write)
        self.store.save_assignments(735790, [{"id": 16105230, "name": "Renamed"}])
        listed = (self.store.course_dir(735790) / "assignments.json").relative_to(self.root).as_posix()
        before[listed] = snapshot()[listed]
        browse.refresh(self.root, writer=self.write)
        self.assertEqual(snapshot(), before)

    def test_writer_failure_leaves_it_out_of_the_manifest(self):
        out = browse.refresh(self.root, writer=lambda links: None)
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["missing"], 1)
        # And the next run tries again.
        browse.refresh(self.root, writer=self.write)
        self.assertEqual(len(self.calls), 1)

    def test_where(self):
        browse.refresh(self.root, writer=self.write)
        place = browse.where(self.root, 735790, 16105230)
        self.assertEqual(place["folder"],
                         str(self.base / "Fall 2026" / "Computer Applications I"))
        self.assertEqual(place["label"],
                         "Browse › Fall 2026 › Computer Applications I › AI Debate")
        self.assertEqual(browse.where(self.root, 735790, 1), {})
        browse.clear(self.root)
        self.assertEqual(browse.where(self.root, 735790, 16105230), {})
        self.assertFalse(self.base.exists())

    def test_switched_off(self):
        cfg = Config()
        cfg.browse_links = False
        self.assertIsNone(browse.refresh_quietly(cfg, self.root))
        self.assertFalse(self.base.exists())

    def test_errors_never_reach_the_caller(self):
        def boom(links):
            raise RuntimeError("no shell")
        original = browse.default_writer
        browse.default_writer = lambda: boom
        try:
            cfg = Config()
            cfg.browse_links = True
            self.assertIsNone(browse.refresh_quietly(cfg, self.root))
        finally:
            browse.default_writer = original


class SyncHook(Fixture):
    def test_sync_refreshes_the_tree(self):
        from courseforge import grader
        src = Path(grader.__file__).read_text(encoding="utf-8")
        body = src.split("def _sync_assignment(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("browse.refresh_quietly(cfg, store.root)", body)


@unittest.skipUnless(sys.platform.startswith("win"), "Windows shortcuts")
class RealShortcut(Fixture):
    def test_powershell_writes_a_lnk(self):
        self.course(735790, "Computer Applications I")
        self.assignment(735790, 16105230, "Débat: IA")
        out = browse.refresh(self.root)
        path = (self.root / browse.FOLDER / "Fall 2026" / "Computer Applications I"
                / "Débat - IA.lnk")
        self.assertTrue(path.is_file(), out)
        self.assertEqual(out["total"], 1)


if __name__ == "__main__":
    unittest.main()
