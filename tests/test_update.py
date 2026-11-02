"""The in-app updater: what it refuses, and what it leaves alone.

An updater is the one feature that can destroy the install it runs in, and it
runs on machines whose owner cannot recover from that. So most of this is about
the refusals: a folder somebody is working in, an archive that is not this app,
an archive that tries to write outside the folder, and the user's own config
and data, which must survive an update that replaces everything around them.
"""
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from courseforge import update


class Cfg:
    update_repo = "billathekilla737/CourseForge-Studio-app"
    update_branch = "main"
    update_token = ""
    check_updates = True


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()
        return False


def archive(top="repo-abc123", files=None, extra=None) -> bytes:
    """A zip shaped the way GitHub's zipball is: everything under one folder."""
    files = files if files is not None else {
        "courseforge/__init__.py": '__version__ = "9.9.9"\n',
        "courseforge/server.py": "# new server\n",
        "courseforge/launcher.py": "# new launcher\n",
        "README.md": "# new readme\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{top}/", "")
        for name, body in files.items():
            zf.writestr(f"{top}/{name}", body)
        for name, body in (extra or {}).items():
            zf.writestr(name, body)          # deliberately outside `top`
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = Cfg()
        # An install that is not a git checkout: the colleague's case.
        (self.tmp / "courseforge").mkdir()
        (self.tmp / "courseforge" / "__init__.py").write_text(
            '__version__ = "1.0.0"\n', encoding="utf-8")
        (self.tmp / "courseforge" / "server.py").write_text("# old\n", encoding="utf-8")
        (self.tmp / "courseforge" / "launcher.py").write_text("# old\n", encoding="utf-8")

    def serve(self, payload: bytes):
        """Point the updater's one network seam at bytes we control."""
        def fake(url, token="", accept=""):
            return FakeResponse(payload)
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake

    def ready(self) -> update.Status:
        return update.Status(checked=True, available=True, current="aaaaaaa",
                             latest="bbbbbbb")


class WhatItRefusesToOverwrite(Base):
    def test_an_archive_that_is_not_this_app(self):
        self.serve(archive(files={"README.md": "# something else\n"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertIn("not CourseForge Studio", out["message"])
        self.assertEqual((self.tmp / "courseforge" / "server.py").read_text(), "# old\n")

    def test_an_archive_that_writes_outside_the_folder(self):
        """A zip entry with .. in it, the oldest trick there is.

        Written INSIDE the top-level folder, so the one-top-level check
        passes and the traversal guard is the thing actually under test.
        """
        self.serve(archive(extra={"repo-abc123/../evil.py": "pwned\n"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertIn("outside", out["message"])
        self.assertFalse((self.tmp.parent / "evil.py").exists())

    def test_an_absolute_entry_is_refused(self):
        self.serve(archive(extra={"/etc/passwd": "no\n"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])

    def test_the_traversal_check_itself(self):
        self.assertTrue(update._unsafe("top/../evil.py", "top"))
        self.assertTrue(update._unsafe("/abs.py", "top"))
        self.assertTrue(update._unsafe("elsewhere/x.py", "top"))
        self.assertFalse(update._unsafe("top/courseforge/server.py", "top"))

    def test_an_archive_with_several_top_level_folders(self):
        self.serve(archive(extra={"other/thing.py": "x\n"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertFalse(out["restart"])

    def test_something_that_is_not_a_zip_at_all(self):
        self.serve(b"<html>404</html>")
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertIn("not readable", out["message"])

    def test_a_status_that_says_do_not(self):
        self.serve(archive())
        blocked = update.Status(can_apply=False, why_not="because I said so")
        out = update.apply(self.cfg, blocked, root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertEqual(out["message"], "because I said so")


class TheGuardOnALocalCheckout(Base):
    def test_a_clean_folder_is_safe(self):
        status = update.Status()
        self.assertTrue(update._guard_local_edits(status, self.tmp))
        self.assertTrue(status.can_apply)

    def test_the_guard_runs_before_the_network(self):
        """A check that cannot reach GitHub must still report the refusal.

        Asked the other way round, an offline machine gets a status saying it
        is safe to overwrite a folder full of uncommitted work.
        """
        src = (Path(update.__file__)).read_text(encoding="utf-8")
        body = src[src.index("def check("):src.index("def _guard_local_edits(")]
        self.assertLess(body.index("_guard_local_edits(status, root)"),
                        body.index("_get("),
                        "the guard moved after the network call")

    def test_apply_checks_the_disk_itself(self):
        """It must not trust a can_apply flag handed in by a caller."""
        src = (Path(update.__file__)).read_text(encoding="utf-8")
        body = src[src.index("def apply("):src.index("def _unsafe(")]
        self.assertIn("_guard_local_edits(fresh, root)", body)


class WhatSurvivesAnUpdate(Base):
    def test_the_token_file_is_not_replaced(self):
        (self.tmp / "canvas.token").write_text("mine", encoding="utf-8")
        self.serve(archive(files={
            "courseforge/__init__.py": "x\n", "courseforge/server.py": "x\n",
            "courseforge/launcher.py": "x\n", "canvas.token": "theirs"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertTrue(out["ok"], out["message"])
        self.assertEqual((self.tmp / "canvas.token").read_text(), "mine")

    def test_the_config_is_not_replaced(self):
        (self.tmp / "config.json").write_text('{"port": 8900}', encoding="utf-8")
        self.serve(archive(files={
            "courseforge/__init__.py": "x\n", "courseforge/server.py": "x\n",
            "courseforge/launcher.py": "x\n", "config.json": '{"port": 1}'}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertTrue(out["ok"], out["message"])
        self.assertEqual(json.loads((self.tmp / "config.json").read_text())["port"], 8900)

    def test_the_data_directory_is_not_touched(self):
        (self.tmp / "data" / "734975").mkdir(parents=True)
        (self.tmp / "data" / "734975" / "draft.json").write_text("mine", encoding="utf-8")
        self.serve(archive(files={
            "courseforge/__init__.py": "x\n", "courseforge/server.py": "x\n",
            "courseforge/launcher.py": "x\n", "data/734975/draft.json": "theirs"}))
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertTrue(out["ok"], out["message"])
        self.assertEqual((self.tmp / "data" / "734975" / "draft.json").read_text(), "mine")

    def test_the_git_folder_is_not_touched(self):
        (self.tmp / ".git").mkdir()
        (self.tmp / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        self.serve(archive(files={
            "courseforge/__init__.py": "x\n", "courseforge/server.py": "x\n",
            "courseforge/launcher.py": "x\n", ".git/HEAD": "clobbered"}))
        update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertEqual((self.tmp / ".git" / "HEAD").read_text(), "ref: refs/heads/main")


class AGoodUpdate(Base):
    def test_the_files_are_replaced(self):
        self.serve(archive())
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertTrue(out["ok"], out["message"])
        self.assertTrue(out["restart"])
        self.assertEqual((self.tmp / "courseforge" / "server.py").read_text(),
                         "# new server\n")
        self.assertEqual((self.tmp / "README.md").read_text(), "# new readme\n")

    def test_no_half_written_files_are_left_behind(self):
        self.serve(archive())
        update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertEqual(list(self.tmp.rglob("*.new")), [])

    def test_the_folder_learns_which_version_it_is(self):
        """Without git there is nothing else to compare against next time."""
        self.serve(archive())
        update.apply(self.cfg, self.ready(), root=self.tmp)
        stamp = update.read_stamp(self.tmp)
        self.assertEqual(stamp["revision"], "bbbbbbb")
        self.assertEqual(update.current_revision(self.tmp), "bbbbbbb")

    def test_a_folder_with_no_stamp_and_no_git_knows_nothing(self):
        self.assertEqual(update.current_revision(self.tmp), "")


class WhatTheWindowIsToldWhenItGoesWrong(Base):
    def test_a_private_repository_is_explained_not_raised(self):
        import urllib.error

        def fake(url, token="", accept=""):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        status = update.check(self.cfg, root=self.tmp)
        self.assertFalse(status.checked)
        self.assertFalse(status.available)
        self.assertIn("private", status.error)
        self.assertEqual(status.headline(), "Could not check for updates")

    def test_being_offline_is_explained_not_raised(self):
        import urllib.error

        def fake(url, token="", accept=""):
            raise urllib.error.URLError("no network")
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        status = update.check(self.cfg, root=self.tmp)
        self.assertIn("Could not reach GitHub", status.error)

    def test_a_matching_revision_is_not_an_update(self):
        def fake(url, token="", accept=""):
            return FakeResponse(json.dumps({"sha": "c4925f6aaaa"}).encode())
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        update.write_stamp(self.tmp, "c4925f6")
        status = update.check(self.cfg, root=self.tmp)
        self.assertTrue(status.checked)
        self.assertFalse(status.available)
        self.assertEqual(status.headline(), "Up to date")

    def test_a_different_revision_is_an_update(self):
        def fake(url, token="", accept=""):
            return FakeResponse(json.dumps({"sha": "9999999bbbb"}).encode())
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        update.write_stamp(self.tmp, "c4925f6")
        status = update.check(self.cfg, root=self.tmp)
        self.assertTrue(status.available)
        self.assertEqual(status.latest, "9999999")
        self.assertEqual(status.headline(), "Update available")


class TheSourceIsPinned(Base):
    def test_a_foreign_repo_cannot_apply(self):
        self.cfg.update_repo = "attacker/malware"
        self.serve(archive())
        out = update.apply(self.cfg, self.ready(), root=self.tmp)
        self.assertFalse(out["ok"])
        self.assertIn("pinned", out["message"].lower())
        self.assertEqual((self.tmp / "courseforge" / "server.py").read_text(), "# old\n")

    def test_the_old_private_repo_name_follows_the_public_one(self):
        self.cfg.update_repo = "billathekilla737/CourseForge-Studio"
        called = []

        def fake(url, token="", accept=""):
            called.append(url)
            return FakeResponse(json.dumps({"sha": "abcdef0aaaa"}).encode())

        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        status = update.check(self.cfg, root=self.tmp)
        self.assertTrue(status.checked)
        self.assertTrue(any("CourseForge-Studio-app" in url for url in called))
        self.assertFalse(any(url.rstrip("/").endswith("CourseForge-Studio/commits/main")
                             for url in called))
        self.assertTrue(status.available)

    def test_a_copy_that_does_not_know_its_version_offers_an_update(self):
        def fake(url, token="", accept=""):
            return FakeResponse(json.dumps({"sha": "abcdef0aaaa"}).encode())

        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        status = update.check(self.cfg, root=self.tmp)
        self.assertTrue(status.available)
        self.assertIn("does not record", status.why_not)

    def test_a_foreign_repo_cannot_check(self):
        self.cfg.update_repo = "attacker/malware"
        called = []

        def fake(url, token="", accept=""):
            called.append(url)
            raise AssertionError("must not hit the network")

        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        status = update.check(self.cfg, root=self.tmp)
        self.assertFalse(status.checked)
        self.assertIn("pinned", status.error.lower())
        self.assertEqual(called, [])

    def test_apply_fetches_the_commit_not_the_branch(self):
        seen = []

        def fake(url, token="", accept=""):
            seen.append(url)
            return FakeResponse(archive())

        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        st = self.ready()
        st.latest_sha = "deadbeefcafebabe0123456789abcdef01234567"
        out = update.apply(self.cfg, st, root=self.tmp)
        self.assertTrue(out["ok"], out["message"])
        self.assertTrue(any("deadbeefcafebabe0123456789abcdef01234567" in u for u in seen))
        self.assertFalse(any(u.rstrip("/").endswith("/zipball/main") for u in seen))

    def test_config_update_token_is_not_sent(self):
        import os
        self.cfg.update_token = "ghp_this_must_not_be_used"
        old = os.environ.pop("GITHUB_TOKEN", None)

        def restore():
            if old is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = old
        self.addCleanup(restore)
        seen = []

        def fake(url, token="", accept=""):
            seen.append(token)
            return FakeResponse(json.dumps({"sha": "c4925f6aaaa"}).encode())

        self.addCleanup(setattr, update, "_get", update._get)
        update._get = fake
        update.write_stamp(self.tmp, "c4925f6")
        update.check(self.cfg, root=self.tmp)
        self.assertEqual(seen, [""])


def git(cwd, *args) -> str:
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                          "-c", "commit.gpgsign=false", *args],
                         cwd=str(cwd), capture_output=True, text=True, check=True)
    return out.stdout


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class AGitCheckout(unittest.TestCase):
    """A checkout is updated by git, so HEAD moves with the files.

    The zip overlay left HEAD where it was. The next check compared the old
    HEAD to GitHub and offered the update again, and the guard then saw every
    overlaid file as an uncommitted change and refused it -- until somebody ran
    git checkout and threw the update away. These are real repositories; a
    local one stands in for GitHub.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.up = self.tmp / "up"
        self.up.mkdir()
        git(self.up, "init", "-q", "-b", "main")
        git(self.up, "config", "core.autocrlf", "false")
        for name in update.MUST_CONTAIN + ("README.md",):
            path = self.up / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"one\ntwo\nthree\n")
        git(self.up, "add", "-A")
        git(self.up, "commit", "-qm", "first")
        git(self.tmp, "-c", "core.autocrlf=false", "clone", "-q", str(self.up), "work")
        self.work = self.tmp / "work"
        git(self.work, "config", "core.autocrlf", "false")
        self.cfg = Cfg()

        original = update._fetch_url
        self.addCleanup(setattr, update, "_fetch_url", original)
        update._fetch_url = lambda repo: str(self.up)
        self.upstream_head = self.head(self.up)
        self.addCleanup(setattr, update, "_get", update._get)
        update._get = self.github

    def github(self, url, token="", accept=""):
        """The commits API answers with the stand-in's head; a zip must never
        be asked for, because a checkout is not updated by overlay."""
        if "/zipball/" in url:
            raise AssertionError("a git checkout downloaded the zip")
        return FakeResponse(json.dumps({"sha": self.upstream_head}).encode())

    def head(self, repo) -> str:
        return git(repo, "rev-parse", "HEAD").strip()

    def commit_upstream(self, name="courseforge/server.py", body=b"new\n") -> str:
        path = self.up / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        git(self.up, "add", "-A")
        git(self.up, "commit", "-qm", f"change {name}")
        self.upstream_head = self.head(self.up)
        return self.upstream_head

    def test_head_moves_with_the_files(self):
        new = self.commit_upstream()
        status = update.check(self.cfg, root=self.work)
        self.assertTrue(status.available and status.can_apply, status.why_not)
        out = update.apply(self.cfg, status, root=self.work)
        self.assertTrue(out["ok"], out["message"])
        self.assertTrue(out["restart"])
        self.assertEqual(self.head(self.work), new)
        self.assertEqual((self.work / "courseforge" / "server.py").read_bytes(), b"new\n")
        self.assertEqual(git(self.work, "status", "--porcelain"), "")
        self.assertFalse((self.work / update.STAMP).exists())

    def test_the_next_check_says_up_to_date_and_could_update_again(self):
        """The loop that needed git checkout to break."""
        self.commit_upstream()
        update.apply(self.cfg, update.check(self.cfg, root=self.work), root=self.work)
        status = update.check(self.cfg, root=self.work)
        self.assertFalse(status.available)
        self.assertTrue(status.can_apply, status.why_not)
        self.assertEqual(status.headline(), "Up to date")

    def test_a_line_ending_rewrite_is_not_an_edit(self):
        """The state the old overlay left behind: content equal to HEAD, LF
        endings over a CRLF checkout. git status lists it; there is nothing
        in it to lose, so it neither blocks the update nor makes merge refuse."""
        git(self.work, "config", "core.autocrlf", "true")
        readme = self.work / "README.md"
        readme.unlink()
        git(self.work, "checkout", "--", "README.md")
        self.assertIn(b"\r\n", readme.read_bytes())
        readme.write_bytes(b"one\ntwo\nthree\n")
        self.assertIn("README.md", git(self.work, "status", "--porcelain"))
        self.assertEqual(update.dirty_files(self.work), [])

        new = self.commit_upstream("README.md", b"one\ntwo\nthree\nfour\n")
        out = update.apply(self.cfg, update.check(self.cfg, root=self.work),
                           root=self.work)
        self.assertTrue(out["ok"], out["message"])
        self.assertEqual(self.head(self.work), new)

    def test_a_real_edit_still_blocks(self):
        self.commit_upstream()
        edited = self.work / "courseforge" / "launcher.py"
        edited.write_bytes(b"my work\n")
        status = update.check(self.cfg, root=self.work)
        self.assertFalse(status.can_apply)
        self.assertIn("courseforge/launcher.py", status.why_not)
        before = self.head(self.work)
        out = update.apply(self.cfg, update.Status(latest_sha=self.upstream_head),
                           root=self.work)
        self.assertFalse(out["ok"])
        self.assertEqual(self.head(self.work), before)
        self.assertEqual(edited.read_bytes(), b"my work\n")

    def test_a_staged_edit_counts(self):
        (self.work / "README.md").write_bytes(b"staged\n")
        git(self.work, "add", "README.md")
        self.assertEqual(update.dirty_files(self.work), ["README.md"])

    def test_a_checkout_with_its_own_commits_is_not_forced(self):
        (self.work / "README.md").write_bytes(b"local\n")
        git(self.work, "commit", "-qam", "local")
        mine = self.head(self.work)
        self.commit_upstream()
        out = update.apply(self.cfg, update.check(self.cfg, root=self.work),
                           root=self.work)
        self.assertFalse(out["ok"])
        self.assertIn("commits of its own", out["message"])
        self.assertEqual(self.head(self.work), mine)

    def test_a_checkout_ahead_of_github_is_not_offered_an_update(self):
        (self.work / "README.md").write_bytes(b"not pushed yet\n")
        git(self.work, "commit", "-qam", "local")
        status = update.check(self.cfg, root=self.work)
        self.assertTrue(status.checked)
        self.assertFalse(status.available)

    def test_an_untracked_file_in_the_way_is_left_alone(self):
        self.commit_upstream("docs/new.md", b"theirs\n")
        (self.work / "docs").mkdir()
        (self.work / "docs" / "new.md").write_bytes(b"mine\n")
        before = self.head(self.work)
        out = update.apply(self.cfg, update.check(self.cfg, root=self.work),
                           root=self.work)
        self.assertFalse(out["ok"])
        self.assertIn("docs/new.md", out["message"])
        self.assertEqual((self.work / "docs" / "new.md").read_bytes(), b"mine\n")
        self.assertEqual(self.head(self.work), before)

    def test_it_fetches_the_pinned_repo_not_origin(self):
        src = Path(update.__file__).read_text(encoding="utf-8")
        body = src[src.index("def _apply_git("):src.index("def _last_line(")]
        self.assertIn("_fetch_url(repo)", body)
        self.assertNotIn('"origin"', body)


class TheFetchUrl(unittest.TestCase):
    def test_it_is_the_public_repo(self):
        self.assertEqual(update._fetch_url(update.ALLOWED_REPO),
                         "https://github.com/billathekilla737/CourseForge-Studio-app.git")


class TheLauncherShowsIt(unittest.TestCase):
    def setUp(self):
        self.src = (Path(__file__).resolve().parent.parent
                    / "courseforge" / "launcher.py").read_text(encoding="utf-8")

    def test_the_check_runs_on_a_thread(self):
        """A window that blocks on GitHub is a window that will not open."""
        self.assertIn("threading.Thread(target=self._check_update", self.src)

    def test_the_badge_is_hidden_until_there_is_news(self):
        self.assertIn("self.update_btn.pack(side=\"left\"", self.src)
        # Created, but not packed, at build time.
        build = self.src[self.src.index("def _build("):self.src.index("def _set_state(")]
        self.assertIn("self.update_btn = tk.Button(", build)
        self.assertNotIn("self.update_btn.pack(", build)

    def test_it_can_be_turned_off(self):
        self.assertIn('getattr(self.cfg, "check_updates", True)', self.src)

    def test_restarting_starts_a_new_process(self):
        """The running process imported the old code; only a new one is updated."""
        self.assertIn("subprocess.Popen", self.src)
        self.assertIn("courseforge.launcher", self.src)


if __name__ == "__main__":
    unittest.main()
