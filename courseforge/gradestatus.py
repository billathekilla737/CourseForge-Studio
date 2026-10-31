"""Whether each assignment is graded, from a light Canvas read.

To Do and the assignment list used to judge "graded" from extracted.json,
which only a full sync writes. An assignment nobody had opened here fell back
on Canvas's needs-grading count, and that count stays up after a gradebook
post, so finished work sat on To Do until it was opened and resynced. A full
sync downloads and extracts every file to answer a yes-or-no question.

This reads the submission rows only (workflow state, score, posted_at, the
submit stamps) for one course, one paged call per twenty assignments, and
judges them with gradesync's rules. What is kept, in grade_status.json beside
the course's assignment list, is one line per assignment: graded or not, how
many submissions are still waiting, and when it was checked. No student is
named in that file. Nothing here writes to Canvas, and nothing here touches
extracted.json or a draft.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import gradesync
from . import folders
from .store import safe_id

FILE = "grade_status.json"

# A course checked more recently than this is not asked again when a page
# loads. The Refresh buttons always ask.
FRESH_S = 10 * 60

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _course_lock(course_id) -> threading.Lock:
    """Two pages asking about one course at once read Canvas once."""
    key = str(course_id)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# ----------------------------------------------------------------- the cache
def course_path(root, course_id) -> Path:
    return folders.under(Path(root), safe_id(course_id)) / FILE


def load(root, course_id) -> dict:
    """The saved status for one course. Reading does not create the folder."""
    path = course_path(root, course_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("assignments"), dict):
        raw["assignments"] = {}
    return raw


def fingerprint(row: dict) -> list:
    """What the assignment list says about a row's submissions. A check made
    against a different list row is out of date."""
    return [_int(row.get("needs_grading")), bool(row.get("has_submissions")),
            bool(row.get("graded_submissions_exist"))]


def has_work(row: dict) -> bool:
    """Rows worth asking Canvas about: something has been turned in or scored."""
    return bool(isinstance(row, dict) and row.get("id")
                and (_int(row.get("needs_grading")) > 0 or row.get("has_submissions")
                     or row.get("graded_submissions_exist")))


def is_stale(cache: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    checked = _float(cache.get("checked_ts"))
    return not checked or now - checked > FRESH_S or now < checked


# ------------------------------------------------------------------- judging
def light_infos(subs, assignment: dict | None = None,
                previous: dict | None = None) -> dict:
    """Per-student facts in the shape extracted.json uses, from bare rows.

    Only the parts of an earlier sync that do not go stale are carried over:
    the quiz's question list and its automatic score. Whether the written
    answers are still open is Canvas's workflow state, read fresh here. An
    excused student owes no grade, so that row is left out.
    """
    previous = previous if isinstance(previous, dict) else {}
    out: dict[str, dict] = {}
    for sub in subs or []:
        if not isinstance(sub, dict) or sub.get("user_id") is None or sub.get("excused"):
            continue
        uid = str(sub["user_id"])
        old = previous.get(uid) if isinstance(previous.get(uid), dict) else {}
        info = {key: old[key] for key in ("quiz_review", "quiz_auto_score") if key in old}
        info.update({
            "user_id": uid,
            "status": gradesync.work_status(sub, assignment),
            "submitted_at": sub.get("submitted_at"),
            **gradesync.canvas_grade_fields(sub, None),
        })
        out[uid] = info
    return out


def judge(row: dict, infos: dict, draft: dict | None = None) -> dict:
    """Graded or not, and how many are waiting, by gradesync's one rule.

    `resubmitted` is how many of the waiting were graded once and handed in
    again; `resubmitted_since` is when the oldest of those came in. To Do
    keeps them apart from work nobody has graded yet.
    """
    draft = draft if isinstance(draft, dict) else {}
    students = draft.get("students") if isinstance(draft.get("students"), dict) else {}
    rubric, possible = draft.get("rubric"), draft.get("points_possible")
    graded = gradesync.assignment_is_graded(
        row.get("needs_grading"), infos, row.get("has_submissions"),
        students, rubric, possible,
        graded_submissions_exist=row.get("graded_submissions_exist"))
    waiting, again = 0, []
    if not graded:
        still = gradesync.open_submissions(infos, students, rubric, possible)
        again = [i for i in still if gradesync.is_resubmission(i)]
        waiting = len(still) or _int(row.get("needs_grading"))
    dates = sorted(str(i.get("submitted_at")) for i in again if i.get("submitted_at"))
    return {"graded": bool(graded), "waiting": waiting, "resubmitted": len(again),
            "resubmitted_since": dates[0] if dates else None}


def _split(state: dict) -> dict:
    """`fresh` is the waiting work that has never been graded."""
    state["resubmitted"] = min(_int(state.get("resubmitted")), _int(state.get("waiting")))
    state["fresh"] = _int(state.get("waiting")) - state["resubmitted"]
    return state


def state_for(cdir: Path, row: dict, cache: dict, read) -> dict:
    """{"graded", "waiting", "fresh", "resubmitted", "resubmitted_since",
    "checked_at"} for one assignment-list row.

    The light check is used when it was made against this same list row and
    is newer than the assignment's own extracted.json. Otherwise the answer
    comes from what a sync left on disk, as it always did. `read` is the
    store's JSON reader; nothing here creates a folder.
    """
    aid = str(row.get("id") or "")
    seen = (cache.get("assignments") or {}).get(aid)
    adir = folders.under(cdir, aid)
    extracted_at = _mtime(adir / "extracted.json")
    if (isinstance(seen, dict) and seen.get("print") == fingerprint(row)
            and _float(seen.get("checked_ts")) >= extracted_at):
        return _split({"graded": bool(seen.get("graded")), "waiting": _int(seen.get("waiting")),
                       "resubmitted": seen.get("resubmitted"),
                       "resubmitted_since": seen.get("resubmitted_since"),
                       "checked_at": seen.get("checked_at")})
    extracted = read(adir / "extracted.json", {}) or {}
    draft = read(adir / "draft.json", {}) or {}
    if not isinstance(extracted, dict):
        extracted = {}
    if not isinstance(draft, dict):
        draft = {}
    out = judge(row, extracted, draft)
    # Not checked since the last sync: Canvas's own count, as before. Which of
    # those are resubmissions comes from the sync.
    out["waiting"] = 0 if out["graded"] else _int(row.get("needs_grading"))
    out["checked_at"] = None
    return _split(out)


# ------------------------------------------------------------------ checking
def check(client, store, course_id, rows: list[dict], force: bool = False,
          log=None) -> dict:
    """Ask Canvas how grading stands on the rows that need asking, and save it.

    A row is asked about when it has work in it and either has never been
    checked, was checked against a different list row, or was not graded
    then. A graded row whose list entry has not moved keeps its answer.
    `force` asks about every row with work. Returns {"checked": n, ...}.
    """
    log = log or (lambda *_a, **_k: None)
    with _course_lock(course_id):
        cache = load(store.root, course_id)
        seen = cache["assignments"]
        rows = [r for r in rows or [] if isinstance(r, dict) and r.get("id")]
        ask = []
        for row in rows:
            if not has_work(row):
                continue
            old = seen.get(str(row["id"]))
            if (not force and isinstance(old, dict) and old.get("graded")
                    and old.get("print") == fingerprint(row)):
                continue
            ask.append(row)

        by_assignment: dict[str, list] = {}
        if ask:
            log("Checking grading status in Canvas (reads only)")
            for sub in client.grading_states(course_id, [r["id"] for r in ask]):
                if isinstance(sub, dict):
                    by_assignment.setdefault(str(sub.get("assignment_id")), []).append(sub)

        cdir = folders.under(Path(store.root), safe_id(course_id))
        now = time.time()
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
        for row in ask:
            aid = str(row["id"])
            previous = store.read(folders.under(cdir, aid) / "extracted.json", {}) or {}
            draft = store.read(folders.under(cdir, aid) / "draft.json", {}) or {}
            infos = light_infos(by_assignment.get(aid, []), row, previous)
            seen[aid] = {**judge(row, infos, draft), "turned_in": len(gradesync.turned_in(infos)),
                         "print": fingerprint(row), "checked_ts": now, "checked_at": stamp}
        # An assignment that has left the list leaves the cache with it.
        keep = {str(r["id"]) for r in rows}
        for aid in [a for a in seen if a not in keep]:
            seen.pop(aid, None)
        cache["checked_ts"] = now
        cache["checked_at"] = stamp
        store.write(course_path(store.root, course_id), cache)
        return {"checked": len(ask), "checked_at": stamp}
