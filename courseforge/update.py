"""Is there a newer CourseForge Studio, and can this copy fetch it itself.

The tool is installed by cloning a repository, which is fine for the person who
wrote it and a wall for everybody else. An instructor who was handed a folder
has no git, no GitHub account, and no reason to acquire either; told that an
update exists, they cannot get it. So the window has to be able to say "there
is a new version" and then go and get it, with one press and no terminal.

What that has to survive:

  * **Two kinds of install.** A git checkout is updated by git -- fetch, then a
    fast-forward -- so HEAD moves with the files. Laying the zip over a checkout
    left HEAD behind, which made every file read as an uncommitted change and
    every later update refuse until somebody ran git checkout. An unzipped
    folder knows nothing, so the updater stamps it on the way past and reads
    that stamp next time.
  * **A folder somebody is working in.** Overwriting a checkout with
    uncommitted changes destroys work that exists nowhere else, so that case is
    refused with a sentence rather than handled cleverly. "Changed" means the
    content differs from HEAD, not that git status lists it: a file rewritten
    with LF endings over a CRLF checkout is listed but holds nothing to lose.
  * **Being offline, or blocked.** A check that fails must leave the window
    looking exactly as it did, because the point of the app is not updating it.
    Every failure here is reported into the status object and never raised at
    whoever happened to open the laptop.
  * **Not eating the user's own things.** The archive holds tracked files only,
    so `config.json`, `data/` and the token are not in it and cannot be
    clobbered by copying it over the top.

What it deliberately does not do: update itself without being asked, restart
without being asked, or delete anything. Files that disappear upstream are left
behind rather than removed -- a stale module costs a few kilobytes, and a
delete that goes wrong costs the install.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

API = "https://api.github.com"
# The only repository this copy will fetch. A config.json that names anything
# else is refused, not followed: the updater overwrites the install.
ALLOWED_REPO = "billathekilla737/CourseForge-Studio-app"
# The private repo this app replaced. A config.json or an old default that
# still names it must follow the public one, not the archived repository,
# or the launcher says "up to date" forever.
LEGACY_REPOS = {"billathekilla737/courseforge-studio"}
# Written into the install directory whenever an update is applied, so a folder
# that was never a git checkout still knows which revision it is running.
STAMP = "installed.json"
UA = "CourseForge-Studio-Updater"
TIMEOUT = 20
# Anything bigger than this is not our repository and will not be unpacked.
MAX_ZIP_BYTES = 120 * 1024 * 1024
# Files that must exist in a downloaded archive before it is allowed to
# overwrite a working install.
MUST_CONTAIN = ("courseforge/__init__.py", "courseforge/server.py",
                "courseforge/launcher.py")


@dataclass
class Status:
    """Everything the window needs to draw, including the bad cases."""

    checked: bool = False
    available: bool = False
    current: str = ""            # short revision of this copy, if it knows one
    latest: str = ""             # short revision upstream
    latest_sha: str = ""         # full upstream SHA, used to pin the zipball
    version: str = __version__
    notes: list = field(default_factory=list)     # recent commit subjects
    error: str = ""
    can_apply: bool = True
    why_not: str = ""
    source: str = ""

    def headline(self) -> str:
        if self.error:
            return "Could not check for updates"
        if not self.checked:
            return "Checking for updates…"
        return "Update available" if self.available else "Up to date"


# ------------------------------------------------------------- this install
def install_dir() -> Path:
    """The folder the running code lives in (the repository root)."""
    return Path(__file__).resolve().parent.parent


def is_git_checkout(root: Path | None = None) -> bool:
    return (Path(root or install_dir()) / ".git").exists()


def _run_git(root: Path, *args, timeout: int = 15) -> tuple:
    """(returncode, stdout, stderr). -1 when git could not be run at all.

    Never prompts: a credential dialog on a public fetch would hang the update
    thread with nobody watching it.
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                             text=True, timeout=timeout, encoding="utf-8",
                             errors="replace", env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", str(exc)
    return out.returncode, out.stdout, out.stderr


def _git(root: Path, *args) -> str:
    """Raw stdout, deliberately not stripped.

    `git status --porcelain` puts the status in the first two columns and the
    path from the third, so a leading space is data. Stripping the whole output
    shifts the first line by one and hands back a path with its first letter
    missing -- which then reads as a file that does not exist.
    """
    code, out, _err = _run_git(root, *args)
    return out if code == 0 else ""


def dirty_files(root: Path | None = None) -> list:
    """Tracked files whose content differs from HEAD. Empty for a non-checkout.

    `git diff HEAD` rather than `git status`. Status trusts the size it cached
    at checkout, so under core.autocrlf a file rewritten with LF endings is
    listed as modified even though its content is identical; the old zip
    overlay produced exactly that on every file and then refused to update
    again. Diff hashes the file and drops what turns out to be unchanged.
    Staged and unstaged edits both count.
    """
    root = Path(root or install_dir())
    if not is_git_checkout(root):
        return []
    out = _git(root, "diff", "--name-only", "-z", "--no-renames", "HEAD")
    return [p for p in out.split("\0") if p]


def head_sha(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def current_revision(root: Path | None = None) -> str:
    """What this copy is running: git first, then the updater's own stamp."""
    root = Path(root or install_dir())
    if is_git_checkout(root):
        sha = head_sha(root)
        if sha:
            return sha[:7]
    stamp = read_stamp(root)
    return str(stamp.get("revision") or "")[:7]


def read_stamp(root: Path | None = None) -> dict:
    path = Path(root or install_dir()) / STAMP
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def write_stamp(root: Path, revision: str, version: str = __version__) -> None:
    path = Path(root) / STAMP
    body = {"revision": revision, "version": version,
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "Written by the in-app updater. Safe to delete; the app will "
                    "then not know which revision it is running."}
    path.write_text(json.dumps(body, indent=1), encoding="utf-8")


# ----------------------------------------------------------------- the check
def _repo(cfg) -> str:
    """The public app repo, or a ValueError if config.json tried to retarget it."""
    got = (getattr(cfg, "update_repo", "") or ALLOWED_REPO).strip("/")
    got = got[:-4] if got.lower().endswith(".git") else got
    if got.lower() in LEGACY_REPOS or got.lower() == ALLOWED_REPO.lower():
        return ALLOWED_REPO
    raise ValueError(
        f"Updates are pinned to {ALLOWED_REPO}. This copy names {got}, "
        "which will not be fetched.")


def _branch(cfg) -> str:
    return getattr(cfg, "update_branch", "") or "main"


def _token(cfg) -> str:
    """Never read from config.json. The public source needs none."""
    return os.environ.get("GITHUB_TOKEN") or ""


def _get(url: str, token: str = "", accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", accept)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def check(cfg, root: Path | None = None) -> Status:
    """Ask GitHub what the branch head is. Never raises."""
    root = Path(root or install_dir())
    try:
        repo, branch = _repo(cfg), _branch(cfg)
    except ValueError as exc:
        status = Status(current=current_revision(root), error=str(exc))
        _guard_local_edits(status, root)
        return status
    status = Status(current=current_revision(root), source=f"{repo}@{branch}")

    # Before the network, not after it. Asked the other way round, a check that
    # fails to reach GitHub returns a status that still says "safe to apply",
    # and the one guard protecting uncommitted work is the one that did not run.
    _guard_local_edits(status, root)

    try:
        with _get(f"{API}/repos/{repo}/commits/{branch}", _token(cfg)) as resp:
            head = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status.error = (
            f"GitHub said {exc.code}. The repository {repo} is private or does "
            f"not exist, so this copy cannot see updates."
            if exc.code in (401, 403, 404) else f"GitHub said {exc.code}.")
        return status
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        status.error = f"Could not reach GitHub: {exc}"
        return status
    except ValueError:
        status.error = "GitHub sent something this could not read."
        return status

    status.checked = True
    status.latest_sha = str(head.get("sha") or "")
    status.latest = status.latest_sha[:7]
    if not status.latest_sha:
        status.error = "GitHub did not name a version."
        return status
    if not status.current:
        # No git answer and no stamp. That used to be reported as "up to date",
        # so a new computer never showed the Update button. Offer the fetch.
        # After it runs, the stamp matches and the offer goes away.
        status.available = True
        status.why_not = (
            "This copy does not record which version it is, so it cannot tell "
            "whether it is behind. Update will fetch the current public version."
        )
    else:
        status.available = status.latest != status.current
        if status.available and _uses_git(root) and _contains(root, status.latest_sha):
            # A checkout that already has the upstream head -- typically the
            # machine it is written on, with commits not pushed yet. Behind
            # is news; ahead is not.
            status.available = False

    if status.available:
        status.notes = _notes(repo, status.current, status.latest, _token(cfg))
    return status


def _guard_local_edits(status: Status, root: Path) -> bool:
    """Refuse to overwrite a checkout somebody is working in. True if safe."""
    bad = dirty_files(root)
    if not bad:
        return True
    shown = ", ".join(bad[:3]) + (f" and {len(bad) - 3} more" if len(bad) > 3 else "")
    status.can_apply = False
    status.why_not = (
        f"This folder is a git checkout with {len(bad)} uncommitted change(s) "
        f"({shown}). Updating would overwrite work that exists nowhere else, so "
        f"it is left alone. Commit or stash, then update again.")
    return False


def _uses_git(root: Path) -> bool:
    """A checkout git can actually read. A folder with .git copied onto a
    machine without git falls back to the zip path and its stamp."""
    return is_git_checkout(root) and bool(head_sha(root))


def _contains(root: Path, sha: str) -> bool:
    """Is `sha` HEAD or behind it. False when the commit is not here at all."""
    if not sha:
        return False
    code, _out, _err = _run_git(root, "merge-base", "--is-ancestor", sha, "HEAD")
    return code == 0


def _fetch_url(repo: str) -> str:
    """Fetched from the pinned repository by URL, never from `origin`, which
    is whatever the person cloned from or later pointed it at."""
    return f"https://github.com/{repo}.git"


def _notes(repo: str, base: str, head: str, token: str) -> list:
    """What changed between the two revisions, as commit subjects."""
    if not base:
        return []
    try:
        with _get(f"{API}/repos/{repo}/compare/{base}...{head}", token) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            TimeoutError, ValueError):
        return []
    out = []
    for commit in (body.get("commits") or [])[-12:]:
        line = str((commit.get("commit") or {}).get("message") or "").split("\n")[0]
        if line:
            out.append(line)
    return list(reversed(out))


# ----------------------------------------------------------------- applying
def apply(cfg, status: Status, root: Path | None = None,
          log=lambda *_a, **_k: None) -> dict:
    """Download the branch archive and lay it over this install.

    Returns {"ok": bool, "message": str, "restart": bool}. The caller decides
    what to do about restarting; nothing here kills the running app.
    """
    root = Path(root or install_dir())
    # Checked here too, against the disk, rather than trusting the flag on a
    # status object the caller handed in. A destructive step does not get to
    # rely on somebody else having asked the question earlier.
    fresh = Status()
    if not status.can_apply or not _guard_local_edits(fresh, root):
        return {"ok": False, "restart": False,
                "message": status.why_not or fresh.why_not}

    try:
        repo = _repo(cfg)
    except ValueError as exc:
        return {"ok": False, "restart": False, "message": str(exc)}
    sha = status.latest_sha or status.latest
    if not sha:
        return {"ok": False, "restart": False,
                "message": "No revision to fetch."}
    if _uses_git(root):
        return _apply_git(root, repo, _branch(cfg), sha, log)
    url = f"{API}/repos/{repo}/zipball/{sha}"
    with tempfile.TemporaryDirectory(prefix="cfstudio-update-") as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / "update.zip"
        log("downloading the new version…")
        try:
            with _get(url, _token(cfg), accept="application/vnd.github+json") as resp:
                size = 0
                with open(archive, "wb") as fh:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_ZIP_BYTES:
                            return {"ok": False, "restart": False,
                                    "message": "The download was far larger than "
                                               "this app; it was stopped."}
                        fh.write(chunk)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                TimeoutError) as exc:
            return {"ok": False, "message": f"Download failed: {exc}",
                    "restart": False}

        log("checking what was downloaded…")
        try:
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
                # GitHub wraps everything in one top-level folder.
                tops = {n.split("/")[0] for n in names if "/" in n}
                if len(tops) != 1:
                    return {"ok": False, "restart": False,
                            "message": "The archive was not shaped like this app."}
                top = tops.pop()
                if any(_unsafe(n, top) for n in names):
                    return {"ok": False, "restart": False,
                            "message": "The archive tried to write outside the "
                                       "install folder; nothing was changed."}
                have = {n[len(top) + 1:] for n in names}
                missing = [m for m in MUST_CONTAIN if m not in have]
                if missing:
                    return {"ok": False, "restart": False,
                            "message": f"The archive is missing {missing[0]}, so "
                                       f"it is not CourseForge Studio. Nothing "
                                       f"was changed."}
                zf.extractall(tmpdir)
        except (zipfile.BadZipFile, OSError) as exc:
            return {"ok": False, "message": f"The download was not readable: {exc}",
                    "restart": False}

        staged = tmpdir / top
        log("putting the new files in place…")
        try:
            count = _copy_over(staged, root, log)
        except OSError as exc:
            return {"ok": False, "restart": False,
                    "message": f"Could not write the new files: {exc}. The app "
                               f"may be half-updated; re-run the update."}

    write_stamp(root, status.latest or "")
    return {"ok": True, "restart": True,
            "message": f"Updated to {status.latest or 'the latest version'}. "
                       f"{count} file(s) written. Restart to run it."}


def _apply_git(root: Path, repo: str, branch: str, sha: str, log) -> dict:
    """Fetch the pinned repository and fast-forward this checkout to `sha`.

    Fast-forward only: a checkout with its own commits is somebody's work, and
    merging into it or resetting it is a decision for them, not for a button.
    The caller has already confirmed nothing differs from HEAD.
    """
    def fail(message: str) -> dict:
        return {"ok": False, "restart": False, "message": message}

    log("fetching the new version with git…")
    code, _out, err = _run_git(root, "fetch", "--no-tags", _fetch_url(repo), branch,
                               timeout=300)
    if code != 0:
        return fail(f"git could not fetch the update: {_last_line(err)}")

    # The revision the check reported, so the window installs what it showed.
    # If the branch was rewound since and that commit is gone, take the head.
    code, full, _err = _run_git(root, "rev-parse", "--verify", "--quiet",
                                f"{sha}^{{commit}}")
    target = full.strip() if code == 0 and full.strip() else "FETCH_HEAD"

    # Files that only differ from HEAD in their cached size -- LF rewritten over
    # a CRLF checkout -- make the merge refuse as if they were edits. Re-adding
    # them records fresh stat data; their content already equals HEAD, so
    # nothing is staged.
    _run_git(root, "add", "-u")

    log("moving this checkout forward…")
    before = head_sha(root)
    code, out, err = _run_git(root, "merge", "--ff-only", "--no-edit", target,
                              timeout=120)
    if code != 0:
        text = f"{out}\n{err}"
        if "untracked working tree files would be overwritten" in text:
            names = [l.strip() for l in text.splitlines()
                     if l.startswith(("\t", "    ")) and l.strip()]
            shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more"
                                            if len(names) > 3 else "")
            return fail(f"Files that git does not track are in the way of the "
                        f"update ({shown}). Move them out of the folder, then "
                        f"update again. Nothing was changed.")
        if "fast-forward" in text.lower() or "diverg" in text.lower():
            return fail("This checkout has commits of its own that are not on "
                        "GitHub, so it cannot simply move forward. Nothing was "
                        "changed; use git pull and settle it there.")
        return fail(f"git could not apply the update: {_last_line(err or out)}")

    after = head_sha(root)
    if after == before:
        return {"ok": True, "restart": False,
                "message": f"Already at {after[:7]}; nothing to update."}
    return {"ok": True, "restart": True,
            "message": f"Updated to {after[:7]} with git. Restart to run it."}


def _last_line(text: str) -> str:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines[-1] if lines else "no reason given"


def _unsafe(name: str, top: str) -> bool:
    """A zip entry that would escape the install folder."""
    if name.startswith("/") or ".." in Path(name).parts:
        return True
    return not (name == top or name.startswith(top + "/"))


# The updater never touches these, whatever an archive happens to contain.
KEEP = {"config.json", "config.local.json", "secrets.json", STAMP,
        "canvas.token", "canvas.token.enc", "canvas.token.txt"}
KEEP_DIRS = {".git", "data", "__pycache__", ".venv", "venv"}


def _copy_over(src: Path, dst: Path, log) -> int:
    """Copy the staged tree over the install, leaving the user's own files."""
    written = 0
    for path in sorted(src.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(src)
        if set(rel.parts) & KEEP_DIRS or rel.name in KEEP:
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and moved into place, so a file is never
        # left half-written if this stops midway.
        tmp = target.with_suffix(target.suffix + ".new")
        shutil.copy2(path, tmp)
        os.replace(tmp, target)
        written += 1
        if written % 40 == 0:
            log(f"{written} files…")
    return written
