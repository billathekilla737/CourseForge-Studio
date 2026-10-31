"""Rename the data folders to the readable layout in folders.py, in place.

    python -m courseforge folders            what would change (changes nothing)
    python -m courseforge folders --apply    do it; the app must be closed
    python -m courseforge folders --undo     put every name back from the log

Nothing is copied. Every step is a rename inside the data folder, which
OneDrive syncs as a rename, and every one is written to data/renames.json
before the next, so --undo can reverse a run that stopped part way.

Three kinds of move, deepest first so a path is never stale:
  files/<uid>_<name>            -> files/<uid> - <Last, First>/<name>
  <course>/<assignment_id>      -> <course>/<assignment_id> - <name>
  <course_id>                   -> <course_id> - <title> (<term>)

A name is cut so the longest path below it stays under Windows' 260. The
course name is given room first, then the assignment, then the student; one
that does not fit at all keeps the bare number, which is no longer than today.
Saved JSON is updated after the moves: extracted.json's paths and served
names, and assignments.json's _dir.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime
from pathlib import Path

from . import folders

LOG = "renames.json"
# Room kept back for the levels below when the path is tight, so the course
# name cannot take it all: an assignment name of this length, then a student's.
RESERVE_ASSIGNMENT = len(" - ") + 16
RESERVE_STUDENT = len(" - ") + 12
_OLD_FILE = folders._OLD_FILE


def _read(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, payload) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _deepest(base: Path) -> int:
    """Length of the longest path under base, relative, with its leading separator."""
    longest = 0
    base_len = len(str(base))
    for dirpath, dirnames, filenames in os.walk(base):
        for name in filenames + dirnames:
            longest = max(longest, len(dirpath) - base_len + 1 + len(name))
    return longest


def _student_moves(files: Path, known: set[str]) -> dict[str, list[tuple[Path, str]]]:
    """uid -> [(flat entry, name inside the student's folder)] for files/."""
    moves: dict[str, list[tuple[Path, str]]] = {}
    try:
        entries = sorted(files.iterdir())
    except OSError:
        return moves
    for entry in entries:
        match = _OLD_FILE.match(entry.name)
        if match and match.group(1) in known:
            moves.setdefault(match.group(1), []).append((entry, match.group(2)))
    return moves


def _rest_len(entry: Path, rest: str) -> int:
    """How long the deepest path from the student's folder down will be."""
    inner = _deepest(entry) if entry.is_dir() else 0
    return 1 + len(rest) + inner


def plan(root: Path) -> dict:
    """Every rename the migration would make, with the longest path after it."""
    root = Path(root)
    courses = {str(c.get("id")): c for c in (_read(root / "courses.json", []) or [])
               if isinstance(c, dict)}
    out = {"root": str(root), "courses": [], "moves": [], "too_long": [], "longest": 0,
           "kept_bare": 0}
    for cdir in folders.children(root):
        cid = folders.id_of(cdir)
        roster = {str(s.get("id")): s for s in (_read(cdir / "students.json", []) or [])
                  if isinstance(s, dict)}
        listed = {str(a.get("id")): a for a in (_read(cdir / "assignments.json", []) or [])
                  if isinstance(a, dict)}
        adirs = folders.children(cdir)

        # Per assignment: the moves into student folders and the deepest path
        # under the assignment once they are made, with bare student ids.
        per: list[dict] = []
        for adir in adirs:
            aid = folders.id_of(adir)
            files = adir / "files"
            extracted = _read(adir / "extracted.json", {}) or {}
            names = {str(k): (v or {}).get("name") for k, v in extracted.items()
                     if isinstance(v, dict)}
            known = set(roster) | set(names)
            moves = _student_moves(files, known)
            existing = {folders.id_of(p): p for p in folders.children(files)}
            deepest_files = 0
            for entry in (files.iterdir() if files.is_dir() else []):
                match = _OLD_FILE.match(entry.name)
                if match and match.group(1) in moves:
                    continue
                deepest_files = max(deepest_files, 1 + len(entry.name)
                                    + (_deepest(entry) if entry.is_dir() else 0))
            rest = {uid: max(_rest_len(e, r) for e, r in items) for uid, items in moves.items()}
            for uid in rest:
                deepest_files = max(deepest_files, 1 + len(uid) + rest[uid])
            other = 0
            for entry in adir.iterdir():
                if entry.name != "files":
                    other = max(other, 1 + len(entry.name)
                                + (_deepest(entry) if entry.is_dir() else 0))
            deepest = max(other, len("/files") + deepest_files)
            per.append({"adir": adir, "aid": aid, "moves": moves, "rest": rest,
                        "existing": existing, "names": names, "deepest": deepest})

        deepest_course = 0
        for entry in cdir.iterdir():
            if entry.is_file():
                deepest_course = max(deepest_course, 1 + len(entry.name))
        for item in per:
            deepest_course = max(deepest_course, 1 + len(item["aid"]) + item["deepest"])
        for entry in cdir.iterdir():
            if entry.is_dir() and not folders.leading_id(entry.name):
                deepest_course = max(deepest_course, 1 + len(entry.name) + _deepest(entry))

        # The course name first, leaving the levels below some room when it
        # can. folders.room() counts the " - " too.
        has_students = any(item["moves"] for item in per)
        reserve = RESERVE_ASSIGNMENT + (RESERVE_STUDENT if has_students else 0)
        budget = folders.room(root, cid, deepest_course + reserve, 200)
        if budget < 8:
            budget = folders.room(root, cid, deepest_course, 200)
        new_course = _course_name(courses.get(cid), cid, budget)
        new_cdir = root / new_course
        course_row = {"id": cid, "from": cdir.name, "to": new_course, "assignments": []}

        for item in per:
            aid = item["aid"]
            name = (listed.get(aid) or {}).get("name") or (
                _read(item["adir"] / "assignment.json", {}) or {}).get("name") or ""
            reserve = RESERVE_STUDENT if item["moves"] else 0
            cap = folders.room(new_cdir, aid, item["deepest"] + reserve, folders.ASSIGNMENT_CAP)
            if cap < 8:
                cap = folders.room(new_cdir, aid, item["deepest"], folders.ASSIGNMENT_CAP)
            new_assignment = folders.label(aid, name, cap)
            new_adir = new_cdir / new_assignment
            students = []
            for uid, items in item["moves"].items():
                if uid in item["existing"]:
                    folder = item["existing"][uid].name
                else:
                    who = roster.get(uid) or {"name": item["names"].get(uid) or ""}
                    scap = folders.room(new_adir / "files", uid, item["rest"][uid],
                                        folders.STUDENT_CAP)
                    folder = folders.student_label(who, uid, scap)
                    out["kept_bare"] += folder == uid
                for entry, rest_name in items:
                    out["moves"].append({"kind": "file", "from": str(entry),
                                         "folder": folder, "name": rest_name})
                    final = (new_adir / "files" / folder / rest_name)
                    longest = len(str(final)) + (_deepest(entry) if entry.is_dir() else 0)
                    out["longest"] = max(out["longest"], longest)
                    if longest > folders.MAX_PATH:
                        out["too_long"].append(str(final))
                students.append(folder)
            course_row["assignments"].append({"id": aid, "from": item["adir"].name,
                                              "to": new_assignment,
                                              "students": sorted(set(students))})
            out["longest"] = max(out["longest"], len(str(new_adir)) + item["deepest"])
            if len(str(new_adir)) + item["deepest"] > folders.MAX_PATH:
                out["too_long"].append(str(new_adir))
        out["courses"].append(course_row)
    return out


def _course_name(course: dict | None, cid: str, budget: int) -> str:
    """The course label that fits in budget characters after "<id> - "."""
    course = course or {}
    term = course.get("term_label") or ""
    tail = f" ({term})" if term and term != folders.OTHER_LABEL else ""
    title = folders.segment(course.get("title") or course.get("name") or "", "", 500)
    title_room = min(folders.COURSE_CAP, budget - len(tail))
    # The whole title beats a cut one with the term: the id already tells
    # two runs of a class apart, the cut title tells nobody anything.
    cut = len(title) > title_room
    if title_room < 8 or (cut and len(title) <= min(folders.COURSE_CAP, budget)):
        return folders.label(cid, course.get("title") or course.get("name") or "",
                             min(folders.COURSE_CAP, budget))
    return folders.course_label(course, cid, title_room)


def summary(p: dict) -> str:
    courses = [c for c in p["courses"] if c["from"] != c["to"]]
    assignments = [a for c in p["courses"] for a in c["assignments"] if a["from"] != a["to"]]
    lines = [f"Data folder: {p['root']}",
             f"{len(courses)} course folder(s), {len(assignments)} assignment folder(s) and "
             f"{len(p['moves'])} student file(s) or folder(s) to move.",
             f"Longest path afterwards: {p['longest']} characters (Windows limit 259)."]
    if p["too_long"]:
        lines.append(f"{len(p['too_long'])} path(s) would be too long; nothing is moved while any are:")
        lines += ["  " + t for t in p["too_long"][:10]]
    if p["kept_bare"]:
        lines.append(f"{p['kept_bare']} student folder(s) keep the bare number: no room for a name.")
    lines.append("")
    for c in p["courses"]:
        lines.append(f"{c['from']}  ->  {c['to']}")
        for a in c["assignments"]:
            moved = f"  ({len(a['students'])} student folder(s))" if a["students"] else ""
            if a["from"] != a["to"] or moved:
                lines.append(f"    {a['from']}  ->  {a['to']}{moved}")
    return "\n".join(lines)


# --------------------------------------------------------------------- apply
def app_running(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def _move(src: Path, dst: Path, log: list, log_path: Path) -> None:
    if dst.exists():
        raise FileExistsError(f"{dst} is already there; nothing was overwritten")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.rename(src, dst)
    log.append({"from": str(src), "to": str(dst)})
    _write(log_path, {"at": datetime.now().isoformat(timespec="seconds"), "moves": log})


def apply(root: Path, p: dict | None = None) -> dict:
    """Make the moves in plan(). Stops at the first failure; --undo reverses."""
    root = Path(root)
    p = p or plan(root)
    if p["too_long"]:
        raise RuntimeError("Some paths would be too long for Windows; nothing was moved.")
    log_path = root / LOG
    previous = _read(log_path, {}) or {}
    log: list = list(previous.get("moves") or [])
    by_from = {m["from"]: m for m in p["moves"]}
    for c in p["courses"]:
        cdir = root / c["from"]
        renamed: dict[str, dict[str, str]] = {}
        for a in c["assignments"]:
            adir = cdir / a["from"]
            files = adir / "files"
            renamed[a["id"]] = {}
            for entry in sorted(files.iterdir()) if files.is_dir() else []:
                move = by_from.get(str(entry))
                if not move:
                    continue
                _move(entry, files / move["folder"] / move["name"], log, log_path)
                renamed[a["id"]][entry.name] = f"{move['folder']}/{move['name']}"
            new_adir = cdir / a["to"]
            if new_adir != adir:
                _move(adir, new_adir, log, log_path)
        new_cdir = root / c["to"]
        if new_cdir != cdir:
            _move(cdir, new_cdir, log, log_path)
        folders.forget()
        # Saved JSON last, once every folder has its final name.
        for a in c["assignments"]:
            _fix_json(new_cdir / a["to"], c["id"], a["id"], renamed[a["id"]])
        _fix_assignment_list(new_cdir)
    folders.forget()
    return {"moved": len(log) - len(previous.get("moves") or []), "log": str(log_path)}


def _rename_names(obj, renamed: dict[str, str]):
    """Served names ("534361_x.sheet.png") swapped for where the file is now."""
    if isinstance(obj, dict):
        return {k: _rename_names(v, renamed) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rename_names(v, renamed) for v in obj]
    if isinstance(obj, str) and obj in renamed:
        return renamed[obj]
    return obj


def _fix_json(adir: Path, cid: str, aid: str, renamed: dict[str, str]) -> None:
    """extracted.json: absolute paths rebased, flat served names renamed."""
    path = adir / "extracted.json"
    raw = _read(path, None)
    if not isinstance(raw, dict):
        return
    fixed = _rename_names(folders.rebase(raw, adir, cid, aid,
                                         folders.student_dirs(adir / "files")), renamed)
    if fixed != raw:
        _write(path, fixed)


def _fix_assignment_list(cdir: Path) -> None:
    path = cdir / "assignments.json"
    rows = _read(path, None)
    if not isinstance(rows, list):
        return
    changed = False
    for row in rows:
        if isinstance(row, dict) and row.get("_dir"):
            adir = folders.find(cdir, str(row.get("id")))
            if adir is not None and row["_dir"] != str(adir):
                row["_dir"] = str(adir)
                changed = True
    if changed:
        _write(path, rows)


def undo(root: Path) -> dict:
    """Reverse every move in the log, newest first. Saved JSON is read through
    folders.rebase, so the old paths work again without rewriting it."""
    root = Path(root)
    log_path = root / LOG
    record = _read(log_path, {}) or {}
    moves = list(record.get("moves") or [])
    undone = 0
    # "<uid> - <name>/<file>" back to "<uid>_<file>" in served names.
    back = {}
    for move in moves:
        src, dst = Path(move["to"]), Path(move["from"])
        if src.parent.parent.name == "files" and folders.leading_id(src.parent.name):
            back[f"{src.parent.name}/{src.name}"] = dst.name
    while moves:
        move = moves[-1]
        src, dst = Path(move["to"]), Path(move["from"])
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src, dst)
            try:
                src.parent.rmdir()      # an emptied student folder
            except OSError:
                pass
        moves.pop()
        undone += 1
        _write(log_path, {**record, "moves": moves})
    folders.forget()
    for cdir in folders.children(root):
        for adir in folders.children(cdir):
            path = adir / "extracted.json"
            raw = _read(path, None)
            if isinstance(raw, dict):
                fixed = _rename_names(raw, back)
                if fixed != raw:
                    _write(path, fixed)
    return {"undone": undone}
