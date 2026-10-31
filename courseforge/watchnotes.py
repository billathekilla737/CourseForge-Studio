"""Notes the instructor keeps on a student while grading.

"I think this one was copied, keep an eye on them." Each note belongs to one
student and, when it was written from the grader, to the assignment that
prompted it. The grader shows a student's open notes beside their work on
every assignment, and the student page lists all of them.

A note can name a second student, the one the instructor thinks the work
was copied from. That pair is then watched: the grader measures the two
against each other on every assignment and flags them at a lower bar than
the rest of the class.

These are the instructor's own notes. They are never sent to a model: a
grade proposed by Claude must not be coloured by a suspicion, and the
suspicion stays a judgement the instructor makes, not one this tool makes.

data/watch-notes.json, copied to the instructor's Canvas files the same way
nicknames are, so another computer signed in as them shows the same notes.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

FILE_NAME = "watch-notes.json"
SYNC_KEY = "watch-notes.json"
MAX_TEXT = 2000
KINDS = ("integrity", "note")

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_row(note_id: str, row) -> dict | None:
    if not isinstance(row, dict) or not str(row.get("user_id") or "").strip():
        return None
    kind = row.get("kind") if row.get("kind") in KINDS else "note"
    return {
        "id": str(note_id),
        "user_id": str(row.get("user_id")).strip(),
        "user_name": str(row.get("user_name") or "")[:200],
        "kind": kind,
        "text": str(row.get("text") or "")[:MAX_TEXT],
        "course_id": str(row.get("course_id") or ""),
        "assignment_id": str(row.get("assignment_id") or ""),
        "assignment_name": str(row.get("assignment_name") or "")[:200],
        # The student this one may have copied from, when the note names one.
        "about_user_id": str(row.get("about_user_id") or ""),
        "about_name": str(row.get("about_name") or "")[:200],
        "created": str(row.get("created") or ""),
        "updated": str(row.get("updated") or ""),
        "resolved": bool(row.get("resolved")),
        # A delete is kept as a marker so the Canvas copy cannot bring it back.
        "deleted": bool(row.get("deleted")),
    }


class Book:
    """data/watch-notes.json. Keyed by note id."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / FILE_NAME

    def read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        rows = raw.get("by_id") if isinstance(raw, dict) else None
        if not isinstance(rows, dict):
            return {}
        out = {}
        for nid, row in rows.items():
            clean = _clean_row(nid, row)
            if clean:
                out[str(nid)] = clean
        return out

    def write(self, by_id: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"by_id": by_id}, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)


def merge_maps(left: dict, right: dict) -> dict:
    """Per note, the copy with the later timestamp. A tie keeps left."""
    out = {}
    for nid in set(left or {}) | set(right or {}):
        a = (left or {}).get(nid)
        b = (right or {}).get(nid)
        if a is None:
            chosen = b
        elif b is None:
            chosen = a
        else:
            chosen = a if str(a.get("updated") or "") >= str(b.get("updated") or "") else b
        clean = _clean_row(nid, chosen)
        if clean:
            out[str(nid)] = clean
    return out


def _remote_map(app) -> dict:
    sync = getattr(app, "state_sync", None)
    if sync is None or not hasattr(sync, "get"):
        return {}
    try:
        env = sync.get(SYNC_KEY) or {}
    except Exception:  # noqa: BLE001
        return {}
    payload = env.get("payload") if isinstance(env, dict) else None
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("by_id") if isinstance(payload.get("by_id"), dict) else {}
    return merge_maps(rows, {})


def _push(app, by_id: dict) -> dict:
    sync = getattr(app, "state_sync", None)
    if sync is None or not hasattr(sync, "put"):
        return {"did": "local"}
    payload = {"by_id": by_id}
    try:
        return sync.put(SYNC_KEY, payload)
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if name != "Conflict" and not name.endswith("Conflict"):
            return {"did": "error", "error": str(exc)[:200]}
    try:
        return sync.put(SYNC_KEY, payload, force=True)
    except Exception as exc:  # noqa: BLE001
        return {"did": "error", "error": str(exc)[:200]}


def reconcile(app) -> dict:
    """Local file and the Canvas copy, merged. Pushes when this side is ahead."""
    with _lock:
        book = Book(app.store.root)
        local = book.read()
        remote = _remote_map(app)
        merged = merge_maps(local, remote)
        if merged != local:
            book.write(merged)
        if merged != remote and (merged or remote):
            _push(app, merged)
        return merged


def _shown(rows) -> list[dict]:
    live = [r for r in rows if not r.get("deleted")]
    live.sort(key=lambda r: str(r.get("created") or ""), reverse=True)
    return live


def for_student(root, user_id) -> list[dict]:
    """Every note on this student, and every note that names them as the
    possible source, newest first. Reads this computer only."""
    uid = str(user_id)
    return _shown(r for r in Book(root).read().values()
                  if uid in (r["user_id"], r["about_user_id"]))


def for_students(root, user_ids) -> dict[str, list[dict]]:
    """Notes for a class at once, for the grader. A note naming a second
    student is listed under both. Students with none are left out."""
    wanted = {str(u) for u in user_ids or []}
    out: dict[str, list[dict]] = {}
    for row in Book(root).read().values():
        for uid in {row["user_id"], row["about_user_id"]} & wanted:
            out.setdefault(uid, []).append(row)
    return {uid: _shown(rows) for uid, rows in out.items()}


def watched_pairs(root) -> set[frozenset]:
    """Pairs named together in an open note: a student and the one the
    instructor thinks they copied from. Resolving the note ends the watch."""
    out = set()
    for row in Book(root).read().values():
        other = row["about_user_id"]
        if other and other != row["user_id"] and not row["resolved"] and not row["deleted"]:
            out.add(frozenset((row["user_id"], other)))
    return out


def add(app, user_id, text, kind: str = "note", course_id="", assignment_id="",
        assignment_name: str = "", about_user_id="", about_name: str = "",
        user_name: str = "") -> dict:
    uid = str(user_id or "").strip()
    if not uid.isdigit():
        raise ValueError("That is not a Canvas user id.")
    about = str(about_user_id or "").strip()
    if about and not about.isdigit():
        raise ValueError("The other student is not a Canvas user id.")
    if about == uid:
        raise ValueError("A student cannot have copied from themselves.")
    body = str(text or "").strip()
    if not body:
        raise ValueError("Write something in the note first.")
    if len(body) > MAX_TEXT:
        raise ValueError(f"Notes are kept to {MAX_TEXT} characters.")
    if kind not in KINDS:
        raise ValueError("Unknown kind of note.")
    now = _now()
    note = _clean_row(secrets.token_hex(6), {
        "user_id": uid, "user_name": user_name, "kind": kind, "text": body,
        "course_id": course_id, "assignment_id": assignment_id,
        "assignment_name": assignment_name, "created": now, "updated": now,
        "about_user_id": about, "about_name": about_name if about else "",
    })
    with _lock:
        book = Book(app.store.root)
        merged = merge_maps(book.read(), _remote_map(app))
        merged[note["id"]] = note
        book.write(merged)
    pushed = _push(app, merged)
    return {"note": note, "sync": pushed}


def change(app, user_id, note_id, text=None, resolved=None, delete: bool = False) -> dict:
    """Edit, resolve or remove one note. The note must be on this student or
    name them, since the grader shows it under both."""
    uid, nid = str(user_id), str(note_id)
    with _lock:
        book = Book(app.store.root)
        merged = merge_maps(book.read(), _remote_map(app))
        note = merged.get(nid)
        if (not note or uid not in (note["user_id"], note["about_user_id"])
                or note.get("deleted")):
            raise KeyError("That note is not on this student.")
        if text is not None:
            body = str(text).strip()
            if not body:
                raise ValueError("A note cannot be empty. Delete it instead.")
            note["text"] = body[:MAX_TEXT]
        if resolved is not None:
            note["resolved"] = bool(resolved)
        if delete:
            note["deleted"] = True
            note["text"] = ""
        note["updated"] = _now()
        merged[nid] = note
        book.write(merged)
    pushed = _push(app, merged)
    return {"note": note, "sync": pushed}
