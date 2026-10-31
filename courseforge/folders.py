"""Folder names a person can read, found by the number at the front.

    data/
      734736 - 3D Game Engine I (Fall 2026)/
        16404855 - Practice 6 Score Keeper/
          files/
            534361 - Simpson, William/ScoreKeeper.cs

Canvas ids stay the key. Every lookup goes by the leading number, so the bare
"734736" an older install made and "734736 - <any name>" are the same course,
and a course or assignment renamed in Canvas keeps working under its old name
until `migrate` renames the folder (always with the app closed: a rename under
a running job would make it write to a folder that is no longer there).

Windows still refuses paths of 260 characters or more here (LongPathsEnabled is
off), and the data folder sits ~90 characters deep inside OneDrive, so names are
cut to fit. A name that cannot fit at all falls back to the bare number.

Saved JSON holds absolute paths into these folders (extracted.json's parts,
images and _files_dir). `rebase` points them at wherever the folder is now, so
nothing has to be re-synced after a rename.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from pathlib import Path

from .terms import OTHER_LABEL

SEP = " - "
MAX_PATH = 259
COURSE_CAP = 30         # the title; the term is added after it
ASSIGNMENT_CAP = 30
STUDENT_CAP = 24

# One name segment never longer than this, so three of them plus a data folder
# inside OneDrive still leave room under Windows' 260.
MAX_SEGMENT = 60

_FORBIDDEN = re.compile(r'[<>"?*\x00-\x1f\x7f]')
_SEPARATORS = re.compile(r"[/\\|]")
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def segment(text, fallback: str, cap: int = MAX_SEGMENT) -> str:
    """One folder or file name that Windows, macOS and OneDrive all accept."""
    name = unicodedata.normalize("NFC", str(text or ""))
    name = name.replace(":", " -")
    name = _SEPARATORS.sub("-", name)
    name = re.sub(r"\s+", " ", name)
    name = _FORBIDDEN.sub("", name).strip()
    if len(name) > cap:
        name = name[:cap]
    # Windows drops a trailing dot or space without saying so, and the name on
    # disk then differs from the one asked for.
    name = name.rstrip(" .").lstrip(" ")
    if not name or set(name) <= {".", "-", " "}:
        name = fallback
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name


_LEAD = re.compile(r"^(\d+)(?: - .*)?$")
_OLD_FILE = re.compile(r"^(\d+)_(.+)$")

_cache: dict[tuple[str, str], Path] = {}
_cache_lock = threading.Lock()


def leading_id(name) -> str | None:
    """"734736 - 3D Game Engine I" -> "734736". None for anything else."""
    match = _LEAD.match(str(name or ""))
    return match.group(1) if match else None


def id_of(path) -> str:
    """The Canvas id a folder stands for, or its plain name if it has none."""
    name = Path(path).name
    return leading_id(name) or name


def find(parent: Path, ident) -> Path | None:
    """The existing folder for this id under parent, whatever its name says."""
    parent, ident = Path(parent), str(ident)
    exact = parent / ident
    if exact.is_dir():
        return exact
    key = (str(parent), ident)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None and hit.is_dir():
        return hit
    found = None
    try:
        for child in parent.iterdir():
            if child.name.startswith(ident + SEP) and child.is_dir():
                found = child
                break
    except OSError:
        return None
    with _cache_lock:
        if found is None:
            _cache.pop(key, None)
        else:
            _cache[key] = found
    return found


def under(parent: Path, ident) -> Path:
    """find(), or where a bare-id folder would be. Creates nothing."""
    return find(parent, ident) or Path(parent) / str(ident)


def forget(parent: Path | None = None) -> None:
    """Drop cached lookups, after a rename or in a test."""
    with _cache_lock:
        if parent is None:
            _cache.clear()
        else:
            for key in [k for k in _cache if k[0] == str(parent)]:
                _cache.pop(key, None)


def children(parent: Path) -> list[Path]:
    """Every id folder directly under parent, bare or named."""
    try:
        return sorted((p for p in Path(parent).iterdir() if p.is_dir() and leading_id(p.name)),
                      key=lambda p: p.name)
    except OSError:
        return []


def label(ident, name, cap: int) -> str:
    """"<id> - <name>", the name cut to cap; the bare id when nothing fits."""
    ident = str(ident)
    if cap < 4 or not str(name or "").strip():
        return ident
    return f"{ident}{SEP}{text}" if (text := trim(name, cap)) else ident


def trim(name, cap: int) -> str:
    """segment(), cut at a word where it can be: "Game Analysis", not "Game Anal"."""
    full = segment(name, "", 500)
    if len(full) <= cap:
        return full
    cut = full[:cap]
    space = cut.rfind(" ")
    if space >= cap * 0.6:
        cut = cut[:space]
    return segment(cut.rstrip(" -—–&,(:;."), "", cap)


def course_label(course: dict | None, course_id, cap: int = COURSE_CAP) -> str:
    """"734736 - 3D Game Engine I (Fall 2026)". Sandboxes have no term worth naming."""
    course = course or {}
    title = course.get("title") or course.get("name") or ""
    term = course.get("term_label") or ""
    if not title:
        return str(course_id)
    tail = f" ({term})" if term and term != OTHER_LABEL else ""
    head = trim(title, max(4, cap))
    return label(course_id, head + tail, cap + len(tail))


def student_label(student: dict | None, user_id, cap: int = STUDENT_CAP) -> str:
    """"534361 - Simpson, William": sorted by surname, findable by number."""
    student = student or {}
    name = student.get("sortable_name") or student.get("name") or ""
    return label(user_id, name, cap)


def room(base: Path, ident, deepest: int, cap: int) -> int:
    """How long a name may be at this level so the deepest path below still fits.

    `deepest` is the length of the longest path under the new folder, relative
    to it and including its leading separator.
    """
    used = len(str(Path(base).absolute())) + 1 + len(str(ident)) + len(SEP) + deepest
    return max(0, min(cap, MAX_PATH - used))


# ------------------------------------------------------------------ rebasing
def _split(text: str) -> list[str]:
    return [p for p in re.split(r"[\\/]+", text) if p != ""]


def rebase_path(text: str, adir: Path, course_id, assignment_id,
                students: dict[str, Path] | None = None) -> str:
    """A saved absolute path into some assignment folder, pointed at adir.

    Finds the "<course>/<assignment>" pair by id wherever the data folder was,
    keeps what comes after it, and maps an old flat "files/<uid>_<name>" to the
    student's folder when `students` says where that is now.
    """
    if not isinstance(text, str) or len(text) < 4 or not ("\\" in text or "/" in text):
        return text
    parts = _split(text)
    cid, aid = str(course_id), str(assignment_id)
    for i in range(len(parts) - 1, 0, -1):
        if leading_id(parts[i]) == aid and leading_id(parts[i - 1]) == cid:
            tail = parts[i + 1:]
            break
    else:
        return text
    if students is not None and len(tail) >= 2 and tail[0] == "files":
        old = _OLD_FILE.match(tail[1])
        uid = leading_id(tail[1])
        if old and old.group(1) in students:
            tail = ["files", students[old.group(1)].name, old.group(2), *tail[2:]]
        elif uid in students:
            tail = ["files", students[uid].name, *tail[2:]]
        elif uid and SEP in tail[1] and len(tail) >= 3:
            # The student's folder is gone (a migration undone): flat again.
            tail = ["files", f"{uid}_{tail[2]}", *tail[3:]]
    return str(Path(adir).joinpath(*tail))


def student_dirs(files_dir: Path) -> dict[str, Path]:
    """uid -> that student's folder under files/, for the ones that have one."""
    return {leading_id(p.name): p for p in children(files_dir)}


def rebase(obj, adir: Path, course_id, assignment_id, students: dict[str, Path] | None = None):
    """Every saved path in obj pointed at the assignment's current folder."""
    if isinstance(obj, dict):
        return {k: rebase(v, adir, course_id, assignment_id, students) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rebase(v, adir, course_id, assignment_id, students) for v in obj]
    if isinstance(obj, str):
        return rebase_path(obj, adir, course_id, assignment_id, students)
    return obj
