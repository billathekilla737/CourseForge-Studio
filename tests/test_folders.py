"""Readable data folders: "<id> - <name>", found by the number at the front.

Covers the names, the lookup, the saved-path rebasing, per-student files, the
file route, and the migration of an older bare-id layout (plan, apply, undo).
Scratch folders only; nothing here touches the real data/ or Canvas.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from courseforge import folder_migration as fm
from courseforge import folders, grader, ledger
from courseforge.config import Config
from courseforge.server import submission_file
from courseforge.store import Store


class Names(unittest.TestCase):
    def test_leading_id(self):
        self.assertEqual(folders.leading_id("734736"), "734736")
        self.assertEqual(folders.leading_id("734736 - 3D Game Engine I (Fall 2026)"), "734736")
        self.assertIsNone(folders.leading_id("a11y"))
        self.assertIsNone(folders.leading_id("734736_essay.docx"))
        self.assertIsNone(folders.leading_id("2024 notes"))
        self.assertEqual(folders.id_of(Path("x") / "16404855 - Practice 6"), "16404855")
        self.assertEqual(folders.id_of(Path("x") / "audit"), "audit")

    def test_course_label_has_title_and_term(self):
        course = {"title": "3D Game Engine I", "term_label": "Fall 2026"}
        self.assertEqual(folders.course_label(course, 734736),
                         "734736 - 3D Game Engine I (Fall 2026)")
        sandbox = {"title": "Sandbox_Z", "term_label": "Sandboxes and other"}
        self.assertEqual(folders.course_label(sandbox, 1), "1 - Sandbox_Z")
        self.assertEqual(folders.course_label(None, 5), "5")

    def test_cut_at_a_word_and_never_ending_in_punctuation(self):
        self.assertEqual(folders.label(1, "Week 1 Quiz - What Is a Game (Intro)", 30),
                         "1 - Week 1 Quiz - What Is a Game")
        self.assertEqual(folders.label(1, "Supercalifragilisticexpialidocious", 10),
                         "1 - Supercalif")

    def test_forbidden_characters_and_fallback(self):
        self.assertEqual(folders.label(7, 'Unit 2: "Sheets"?', 30), "7 - Unit 2 - Sheets")
        self.assertEqual(folders.label(7, "???", 30), "7")
        self.assertEqual(folders.label(7, "Anything", 2), "7")

    def test_student_label_is_surname_first(self):
        self.assertEqual(folders.student_label({"sortable_name": "Simpson, William",
                                                "name": "William Simpson"}, 534361),
                         "534361 - Simpson, William")
        self.assertEqual(folders.student_label({}, 534361), "534361")

    def test_room_keeps_paths_under_windows_limit(self):
        base = Path("C:/") / ("d" * 200)
        cap = folders.room(base, "16404855", 40, 30)
        self.assertLessEqual(len(str(base.absolute())) + 1 + 8 + 3 + cap + 40, folders.MAX_PATH)
        self.assertEqual(folders.room(Path("C:/data"), "1", 10, 30), 30)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cf-folders-"))
        self.root = self.tmp / "data"
        self.store = Store(self.root)
        folders.forget()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(folders.forget)

    def known_course(self):
        self.store.save_courses([{"id": 734736, "title": "3D Game Engine I",
                                  "term_label": "Fall 2026"}])


class Lookup(Fixture):
    def test_new_course_and_assignment_get_names(self):
        self.known_course()
        cdir = self.store.course_dir(734736)
        self.assertEqual(cdir.name, "734736 - 3D Game Engine I (Fall 2026)")
        self.store.save_assignments(734736, [{"id": 16404855, "name": "Practice 6"}])
        adir = self.store.assignment_dir(734736, 16404855)
        self.assertEqual(adir.name, "16404855 - Practice 6")
        self.assertTrue((adir / "files").is_dir())

    def test_an_older_bare_folder_is_still_found(self):
        (self.root / "734736" / "16404855" / "files").mkdir(parents=True)
        self.known_course()
        self.assertEqual(self.store.course_dir(734736), self.root / "734736")
        self.assertEqual(self.store.assignment_dir(734736, 16404855).name, "16404855")
        self.assertEqual(len(list(self.root.iterdir())), 2)    # + courses.json

    def test_a_canvas_rename_keeps_the_old_folder_working(self):
        self.known_course()
        cdir = self.store.course_dir(734736)
        self.store.save_courses([{"id": 734736, "title": "Engine One", "term_label": "Fall 2026"}])
        self.assertEqual(self.store.course_dir(734736), cdir)

    def test_find_creates_nothing(self):
        self.assertIsNone(self.store.find_course_dir(1))
        self.assertIsNone(self.store.find_assignment_dir(1, 2))
        self.assertFalse((self.root / "1").exists())

    def test_ids_are_still_checked(self):
        with self.assertRaises(ValueError):
            self.store.course_dir("../x")
        with self.assertRaises(ValueError):
            self.store.assignment_dir(1, "2 - name")

    def test_student_dir(self):
        self.store.save_students(1, [{"id": 11, "name": "Ana Lee", "sortable_name": "Lee, Ana"}])
        path = self.store.student_dir(1, 2, 11)
        self.assertEqual(path.name, "11 - Lee, Ana")
        self.assertEqual(self.store.student_dir(1, 2, 11), path)

    def test_ledger_takes_the_id_from_a_named_folder(self):
        self.known_course()
        cdir = self.store.course_dir(734736)
        from courseforge import audit
        seen = {}
        original = audit.record
        audit.record = lambda *a, **k: seen.update(k)
        try:
            ledger.record(cdir, "grade", "did a thing")
        finally:
            audit.record = original
        self.assertEqual(seen["course_id"], "734736")


class Rebase(Fixture):
    def test_old_absolute_path_points_at_the_current_folder(self):
        adir = self.root / "1 - Course" / "2 - Essay"
        old = r"C:\elsewhere\data\1\2\files\11_essay.docx"
        self.assertEqual(folders.rebase_path(old, adir, 1, 2),
                         str(adir / "files" / "11_essay.docx"))

    def test_flat_file_moves_into_the_students_folder(self):
        adir = self.root / "1 - Course" / "2 - Essay"
        sdir = adir / "files" / "11 - Lee, Ana"
        old = str(self.root / "1" / "2" / "files" / "11_essay-pages" / "page-001.png")
        self.assertEqual(folders.rebase_path(old, adir, 1, 2, {"11": sdir}),
                         str(sdir / "essay-pages" / "page-001.png"))

    def test_a_removed_student_folder_goes_back_to_flat(self):
        adir = self.root / "1" / "2"
        new = str(adir / "files" / "11 - Lee, Ana" / "essay.docx")
        self.assertEqual(folders.rebase_path(new, adir, 1, 2, {}),
                         str(adir / "files" / "11_essay.docx"))

    def test_other_strings_are_left_alone(self):
        adir = self.root / "1" / "2"
        for text in ("plain words", "a/b", r"C:\other\9\9\files\x", "", "11_essay.docx"):
            self.assertEqual(folders.rebase_path(text, adir, 1, 2), text)

    def test_store_extracted_is_rebased(self):
        adir = self.store.assignment_dir(1, 2)
        self.store.write(adir / "extracted.json", {"11": {
            "_files_dir": r"D:\old\data\1\2\files",
            "parts": [{"path": r"D:\old\data\1\2\files\11_essay.docx"}]}})
        got = self.store.extracted(1, 2)["11"]
        self.assertEqual(got["_files_dir"], str(adir / "files"))
        self.assertEqual(got["parts"][0]["path"], str(adir / "files" / "11_essay.docx"))


class FileRoute(Fixture):
    def setUp(self):
        super().setUp()
        self.files = self.store.assignment_dir(1, 2) / "files"
        self.sdir = self.files / "11 - O'Neil, Ana"
        (self.sdir / "essay-pages").mkdir(parents=True)
        (self.sdir / "essay.docx").write_bytes(b"d")
        (self.sdir / "essay-pages" / "page-001.png").write_bytes(b"p")
        (self.files / "12_old.docx").write_bytes(b"o")

    def test_student_folder_files_are_served(self):
        self.assertEqual(submission_file(self.files, "11 - O'Neil, Ana/essay.docx"),
                         (self.sdir / "essay.docx").resolve())
        self.assertEqual(submission_file(self.files, "11 - O'Neil, Ana/essay-pages/page-001.png"),
                         (self.sdir / "essay-pages" / "page-001.png").resolve())

    def test_flat_files_from_an_older_sync_still_served(self):
        self.assertEqual(submission_file(self.files, "12_old.docx"),
                         (self.files / "12_old.docx").resolve())

    def test_escapes_are_refused(self):
        for raw in ("11 - O'Neil, Ana/../12_old.docx", "../2/files/12_old.docx",
                    "11 - x/../../assignment.json", "11 - O'Neil, Ana/essay-pages/../essay.docx",
                    "notanid/essay.docx", "11 - O'Neil, Ana/a/b/c.png"):
            self.assertIsNone(submission_file(self.files, raw), raw)


class Downloads(Fixture):
    def test_new_files_go_to_the_students_folder_and_old_flat_ones_are_reused(self):
        self.store.save_students(1, [{"id": 11, "name": "Ana Lee", "sortable_name": "Lee, Ana"},
                                     {"id": 12, "name": "Bo Kim", "sortable_name": "Kim, Bo"}])
        adir = self.store.assignment_dir(1, 2)
        (adir / "files" / "12_b.txt").write_text("already here", encoding="utf-8")

        class Client:
            fetched = []

            def assignment(self, cid, aid):
                return {"id": aid, "name": "Essay", "submission_types": ["online_upload"]}

            def submissions(self, cid, aid, history=False):
                return [{"user_id": uid, "workflow_state": "submitted", "attempt": 1,
                         "submitted_at": "2026-10-01T00:00:00Z", "body": "",
                         "attachments": [{"filename": name, "size": 12,
                                          "url": f"https://canvas.example.edu/files/{uid}"}]}
                        for uid, name in ((11, "a.txt"), (12, "b.txt"))]

            def download(self, url, dest, max_bytes=None):
                Client.fetched.append(Path(dest))
                Path(dest).write_text("hello there!", encoding="utf-8")
                return dest

        cfg = Config()
        cfg.base_url = "https://canvas.example.edu"
        grader.sync_assignment(cfg, Client(), self.store, 1, 2)
        self.assertEqual(Client.fetched, [adir / "files" / "11 - Lee, Ana" / "a.txt"])
        got = self.store.extracted(1, 2)
        self.assertEqual(got["11"]["parts"][0]["label"], "a.txt")
        self.assertEqual(got["12"]["parts"][0]["label"], "b.txt")
        self.assertTrue(Path(got["11"]["parts"][0]["path"]).is_file())
        self.assertFalse((adir / "files" / "12 - Kim, Bo").exists())


class Migration(Fixture):
    """An install from before: bare ids, files flat as <uid>_<name>."""

    def setUp(self):
        super().setUp()
        self.store.save_courses([{"id": 734736, "title": "3D Game Engine I",
                                  "term_label": "Fall 2026"}])
        old = self.root / "734736"
        adir = old / "16404855"
        files = adir / "files"
        (files / "11_essay-pages").mkdir(parents=True)
        (files / "11_essay.pdf").write_bytes(b"pdf")
        (files / "11_essay-pages" / "page-001.png").write_bytes(b"png")
        (files / "11_scene.sheet.png").write_bytes(b"sheet")
        (files / "99_unknown.txt").write_bytes(b"?")        # not anyone we know
        (adir / "blend" / "11" / "scene").mkdir(parents=True)
        (adir / "blend" / "11" / "scene" / "stats.json").write_text("{}", encoding="utf-8")
        (adir / "draft.json").write_text(json.dumps({"students": {"11": {"total": 9}}}),
                                         encoding="utf-8")
        (old / "a11y").mkdir()
        (old / "a11y" / "state.json").write_text("{}", encoding="utf-8")
        (old / "students.json").write_text(json.dumps(
            [{"id": 11, "name": "Ana Lee", "sortable_name": "Lee, Ana"}]), encoding="utf-8")
        (old / "assignments.json").write_text(json.dumps(
            [{"id": 16404855, "name": "Practice 6: Score Keeper", "_dir": str(adir)}]),
            encoding="utf-8")
        (adir / "extracted.json").write_text(json.dumps({"11": {
            "name": "Ana Lee", "_files_dir": str(files),
            "images": [str(files / "11_essay-pages" / "page-001.png")],
            "sheets": ["11_scene.sheet.png"],
            "parts": [{"path": str(files / "11_essay.pdf"), "data": {"sheet": "11_scene.sheet.png"}}],
        }}), encoding="utf-8")
        self.before = self.snapshot()

    def snapshot(self):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                      if p.is_file() and p.name != fm.LOG)

    def test_plan_changes_nothing(self):
        plan = fm.plan(self.root)
        self.assertEqual(self.snapshot(), self.before)
        course = plan["courses"][0]
        self.assertEqual(course["to"], "734736 - 3D Game Engine I (Fall 2026)")
        self.assertEqual(course["assignments"][0]["to"], "16404855 - Practice 6 - Score Keeper")
        self.assertEqual(course["assignments"][0]["students"], ["11 - Lee, Ana"])
        self.assertEqual(len(plan["moves"]), 3)
        self.assertEqual(plan["too_long"], [])
        self.assertIn("1 course folder(s), 1 assignment folder(s)", fm.summary(plan))

    def test_apply_then_everything_reads(self):
        out = fm.apply(self.root)
        self.assertEqual(out["moved"], 5)
        cdir = self.root / "734736 - 3D Game Engine I (Fall 2026)"
        adir = cdir / "16404855 - Practice 6 - Score Keeper"
        sdir = adir / "files" / "11 - Lee, Ana"
        self.assertTrue((sdir / "essay.pdf").is_file())
        self.assertTrue((sdir / "essay-pages" / "page-001.png").is_file())
        self.assertTrue((adir / "files" / "99_unknown.txt").is_file())
        self.assertTrue((adir / "blend" / "11" / "scene" / "stats.json").is_file())
        self.assertTrue((cdir / "a11y" / "state.json").is_file())
        # Same files, only moved: nothing lost or added.
        self.assertEqual(len(self.snapshot()), len(self.before))

        raw = json.loads((adir / "extracted.json").read_text(encoding="utf-8"))["11"]
        self.assertEqual(raw["parts"][0]["path"], str(sdir / "essay.pdf"))
        self.assertEqual(raw["images"], [str(sdir / "essay-pages" / "page-001.png")])
        self.assertEqual(raw["_files_dir"], str(adir / "files"))
        self.assertEqual(raw["sheets"], ["11 - Lee, Ana/scene.sheet.png"])
        self.assertEqual(raw["parts"][0]["data"]["sheet"], "11 - Lee, Ana/scene.sheet.png")
        self.assertIsNotNone(submission_file(adir / "files", raw["sheets"][0]))
        listed = json.loads((cdir / "assignments.json").read_text(encoding="utf-8"))
        self.assertEqual(listed[0]["_dir"], str(adir))

        store = Store(self.root)
        self.assertEqual(store.assignment_dir(734736, 16404855), adir)
        self.assertEqual(store.draft(734736, 16404855)["students"]["11"]["total"], 9)
        self.assertEqual(store.students(734736)[0]["id"], 11)

    def test_a_second_run_does_nothing(self):
        fm.apply(self.root)
        again = fm.plan(self.root)
        self.assertEqual(again["moves"], [])
        self.assertTrue(all(c["from"] == c["to"] for c in again["courses"]))

    def test_undo_puts_everything_back(self):
        fm.apply(self.root)
        out = fm.undo(self.root)
        self.assertEqual(out["undone"], 5)
        self.assertEqual(self.snapshot(), self.before)
        store = Store(self.root)
        got = store.extracted(734736, 16404855)["11"]
        self.assertTrue(Path(got["parts"][0]["path"]).is_file())
        self.assertEqual(got["sheets"], ["11_scene.sheet.png"])

    def test_refuses_to_overwrite(self):
        (self.root / "734736 - 3D Game Engine I (Fall 2026)").mkdir()
        with self.assertRaises(FileExistsError):
            fm.apply(self.root)
        # What moved before the stop is in the log, and undo reverses it.
        fm.undo(self.root)
        self.assertEqual(self.snapshot(), self.before)

    def test_names_shrink_to_fit_a_deep_data_folder(self):
        deep = self.tmp / ("d" * 120) / "data"
        shutil.copytree(self.root, deep)
        plan = fm.plan(deep)
        self.assertEqual(plan["too_long"], [])
        self.assertLessEqual(plan["longest"], folders.MAX_PATH)
        fm.apply(deep, plan)
        for path in deep.rglob("*"):
            self.assertLessEqual(len(str(path)), folders.MAX_PATH)


class Command(Fixture):
    def test_apply_refuses_while_the_app_runs(self):
        import socket
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        self.addCleanup(listener.close)
        cfg = Config()
        cfg.data_dir = str(self.root)
        cfg.port = listener.getsockname()[1]
        from courseforge.__main__ import cmd_folders
        self.assertEqual(cmd_folders(cfg, apply=True), 2)
        self.assertEqual(cmd_folders(cfg, undo=True), 2)


if __name__ == "__main__":
    unittest.main()
