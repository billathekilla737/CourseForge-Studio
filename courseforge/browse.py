"""A readable way into downloaded submissions: data/Browse/<term>/<course>/<assignment>.

The real folders stay data/<course_id>/<assignment_id>/. Saved extracted.json
files hold absolute paths into them, the ledger takes the course id from the
folder name, and every area package builds data/<course_id>/... on its own, so
renaming them would break all of that until each assignment is synced again.
A course renamed in Canvas would also mean moving gigabytes inside OneDrive.

Beside them, Browse/ holds one shortcut per synced assignment, opening its
files/ folder:

    data/Browse/Fall 2026/Computer Applications I/AI Debate.lnk

Shortcuts rather than junctions: OneDrive syncs a .lnk as a tiny file, and
deleting one can never delete the work it points at. A junction may be followed,
uploading everything twice, and a delete on another device would go through it.

The tree is rebuilt from the current names after every sync and at startup. Only
shortcuts this module made (listed in Browse/.shortcuts.json) are ever removed,
so a file the instructor drops into Browse/ is left alone.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from . import folders, terms
from .folders import MAX_SEGMENT, segment  # noqa: F401  re-exported

FOLDER = "Browse"
MANIFEST = ".shortcuts.json"
SUFFIX = ".lnk" if os.name == "nt" else ""

# A deeper data folder gets shorter class and assignment names (see _cap).
MAX_PATH = 259
MIN_SEGMENT = 16

_lock = threading.Lock()


def _unique(rows: list[dict], key: str, extras, cap: int = MAX_SEGMENT) -> None:
    """Give every row a distinct rows[key], compared the way Windows does.

    `extras` lists ways to tell two rows apart, tried in order. A row that is
    still tied after all of them gets its Canvas id, which never changes.
    """
    for extra in [*extras, lambda r: f"[{r['id']}]"]:
        seen: dict[str, list[dict]] = {}
        for row in rows:
            seen.setdefault(row[key].casefold(), []).append(row)
        tied = [r for group in seen.values() if len(group) > 1 for r in group]
        if not tied:
            return
        for row in tied:
            more = extra(row)
            if more:
                base = row[key][:max(1, cap - len(more) - 1)].rstrip(" .")
                row[key] = f"{base} {more}"


def _section(course: dict) -> str:
    words = course["code"].split()
    if not words:
        return ""
    return f"({segment(words[-1] if len(words) >= 3 else course['code'], '')})"


def _course_meta(course: dict | None, course_id: str) -> dict:
    course = course or {}
    title = course.get("title") or course.get("name") or f"Course {course_id}"
    term = course.get("term_label")
    if not term:
        canvas_term = course.get("term")
        try:
            term = terms.term_of({
                "name": course.get("name") or "",
                "course_code": course.get("code") or course.get("course_code") or "",
                "term": canvas_term if isinstance(canvas_term, dict) else {"name": canvas_term or ""},
            })["label"]
        except Exception:  # noqa: BLE001  a missing term is not worth failing over
            term = terms.OTHER_LABEL
    code = re.sub(r"\s*\(online\)\s*", " ", str(course.get("code") or ""), flags=re.I).strip()
    return {"id": course_id, "title": title, "term": term, "code": code}


def _cap(root: Path, term_len: int) -> int:
    """How long a class or assignment name may be under this data folder.

    Windows refuses a shortcut whose full path reaches 260 characters, and the
    class and assignment names are the two parts that can be long.
    """
    room = MAX_PATH - len(str(Path(root).absolute() / FOLDER)) - term_len - 3 - len(SUFFIX)
    return max(MIN_SEGMENT, min(MAX_SEGMENT, room // 2))


def plan(root: Path) -> dict[str, str]:
    """Relative shortcut path (posix, no suffix) -> the files folder it opens.

    Only assignments that have been synced are listed: the assignment list
    makes an empty folder for every assignment it shows, and a tree of empty
    folders would be no help.
    """
    root = Path(root)
    try:
        courses = json.loads((root / "courses.json").read_text(encoding="utf-8")) or []
    except (OSError, json.JSONDecodeError):
        courses = []
    known = {str(c.get("id")): c for c in courses if isinstance(c, dict)}

    found: list[dict] = []
    try:
        children = folders.children(root)
    except OSError:
        children = []
    for cdir in children:
        cid = folders.id_of(cdir)
        synced = [a for a in folders.children(cdir) if (a / "extracted.json").is_file()]
        if not synced:
            continue
        meta = _course_meta(known.get(cid), cid)
        term_name = segment(meta["term"], terms.OTHER_LABEL)
        cap = _cap(root, len(term_name))
        try:
            listed = json.loads((cdir / "assignments.json").read_text(encoding="utf-8")) or []
        except (OSError, json.JSONDecodeError):
            listed = []
        names = {str(a.get("id")): a.get("name") for a in listed if isinstance(a, dict)}
        rows = []
        for adir in synced:
            aid = folders.id_of(adir)
            name = names.get(aid)
            if not name:
                try:
                    saved = json.loads((adir / "assignment.json").read_text(encoding="utf-8"))
                    name = (saved or {}).get("name")
                except (OSError, json.JSONDecodeError, AttributeError):
                    name = None
            rows.append({"id": aid, "name": segment(name, f"Assignment {aid}", cap),
                         "target": str(adir / "files")})
        found.append({**meta, "name": segment(meta["title"], f"Course {cid}", cap),
                      "term_name": term_name, "cap": cap, "assignments": rows})

    # Two sections of one class in the same term: the section (FOA, FOB, the
    # last word of "CSC 1123 FOB") tells them apart, and the Canvas id settles
    # anything left.
    by_term: dict[str, list[dict]] = {}
    for course in found:
        by_term.setdefault(course["term_name"].casefold(), []).append(course)
    for group in by_term.values():
        _unique(group, "name", [_section],
                min(c["cap"] for c in group))

    out: dict[str, str] = {}
    for course in found:
        _unique(course["assignments"], "name", [], course["cap"])
        for row in course["assignments"]:
            out[f"{course['term_name']}/{course['name']}/{row['name']}"] = row["target"]
    return out


def where(root: Path, course_id, assignment_id) -> dict:
    """The readable folder and shortcut for one assignment, or {} if there is
    no shortcut for it on disk (not synced, or browse_links is off)."""
    cdir = folders.find(Path(root), course_id)
    adir = folders.find(cdir, assignment_id) if cdir else None
    if adir is None:
        return {}
    want = str(adir / "files")
    for rel, target in plan(root).items():
        if target == want:
            base = Path(root) / FOLDER
            if not _is_ours(base.joinpath(*rel.split("/")[:-1], rel.split("/")[-1] + SUFFIX)):
                return {}
            return {"folder": str(base.joinpath(*rel.split("/")[:-1])),
                    "shortcut": str(base.joinpath(*rel.split("/"))) + SUFFIX,
                    "label": " › ".join([FOLDER, *rel.split("/")])}
    return {}


# ------------------------------------------------------------------ writing
def _write_windows(links: list[tuple[Path, str]]) -> None:
    """Every shortcut in one PowerShell call; a spawn costs about a second."""
    payload = [{"lnk": str(p), "target": t} for p, t in links]
    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        script = ("$ws = New-Object -ComObject WScript.Shell; "
                  "foreach ($i in (Get-Content -Raw -Encoding UTF8 -LiteralPath $env:CF_LINKS"
                  " | ConvertFrom-Json)) { try { $s = $ws.CreateShortcut($i.lnk); "
                  "$s.TargetPath = $i.target; $s.Save() } catch { } }")
        subprocess.run(  # noqa: S603
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            env={**os.environ, "CF_LINKS": tmp}, capture_output=True, timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _write_symlinks(links: list[tuple[Path, str]]) -> None:
    for path, target in links:
        try:
            if path.is_symlink():
                path.unlink()
            os.symlink(target, path, target_is_directory=True)
        except OSError:
            continue


def default_writer():
    return _write_windows if sys.platform.startswith("win") else _write_symlinks


def _is_ours(path: Path) -> bool:
    """A shortcut or link, never a real folder or someone's document."""
    return path.is_symlink() or (path.suffix.lower() == ".lnk" and path.is_file())


def _prune(base: Path, emptied: set[Path]) -> None:
    """Drop the folders a removed shortcut left empty, and nothing else."""
    for folder in sorted(emptied, key=lambda p: len(p.parts), reverse=True):
        while folder != base and base in folder.parents:
            try:
                folder.rmdir()      # only succeeds when empty
            except OSError:
                break
            folder = folder.parent


def refresh(root: Path, writer=None) -> dict:
    """Bring Browse/ in line with the synced assignments and their current names."""
    root = Path(root)
    base = root / FOLDER
    writer = writer or default_writer()
    with _lock:
        want = {rel + SUFFIX: target for rel, target in plan(root).items()}
        try:
            made = json.loads((base / MANIFEST).read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            made = {}
        if not isinstance(made, dict):
            made = {}

        removed = 0
        emptied: set[Path] = set()
        for rel, target in made.items():
            if want.get(rel) == target:
                continue
            path = base.joinpath(*rel.split("/"))
            if _is_ours(path):
                try:
                    path.unlink()
                    removed += 1
                    emptied.add(path.parent)
                except OSError:
                    pass

        todo = []
        for rel, target in want.items():
            path = base.joinpath(*rel.split("/"))
            if made.get(rel) == target and _is_ours(path):
                continue
            if path.exists() and not _is_ours(path):
                continue            # something of the instructor's is in the way
            path.parent.mkdir(parents=True, exist_ok=True)
            todo.append((path, target))
        if todo:
            writer(todo)

        now = {rel: target for rel, target in want.items()
               if _is_ours(base.joinpath(*rel.split("/")))}
        _prune(base, emptied)
        if now or made:
            base.mkdir(parents=True, exist_ok=True)
            tmp = base / (MANIFEST + ".tmp")
            tmp.write_text(json.dumps(now, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(base / MANIFEST)
        return {"made": sum(1 for p, _ in todo if _is_ours(p)), "removed": removed,
                "total": len(now), "missing": len(want) - len(now)}


def clear(root: Path) -> int:
    """Remove every shortcut this module made, and the folders left empty."""
    base = Path(root) / FOLDER
    with _lock:
        try:
            made = json.loads((base / MANIFEST).read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            return 0
        removed, emptied = 0, set()
        for rel in made if isinstance(made, dict) else {}:
            path = base.joinpath(*rel.split("/"))
            if _is_ours(path):
                try:
                    path.unlink()
                    removed += 1
                    emptied.add(path.parent)
                except OSError:
                    pass
        _prune(base, emptied)
        try:
            (base / MANIFEST).unlink()
            base.rmdir()            # only if nothing of the instructor's is left
        except OSError:
            pass
        return removed


def refresh_quietly(cfg, root: Path) -> dict | None:
    """refresh(), or clear() when switched off; never stops the caller."""
    try:
        if not getattr(cfg, "browse_links", False):
            clear(root)
            return None
        return refresh(root)
    except Exception:  # noqa: BLE001  the readable tree is a convenience
        return None


def refresh_in_background(cfg, root: Path) -> None:
    threading.Thread(target=refresh_quietly, args=(cfg, root), daemon=True,
                     name="browse-shortcuts").start()
