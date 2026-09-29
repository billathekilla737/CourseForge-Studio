"""The attendance book for one course, and the calendar derived from it.

A class day comes from the weekdays the instructor picked, minus college
breaks when they asked to skip those, plus any single day they added or
removed. A mark is present, tardy, absent, or excused. An empty status is a
clear, kept so a newer clear wins when two computers merge.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

STATUSES = ("present", "tardy", "absent", "excused")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WEEK_NUM = {name: i for i, name in enumerate(WEEKDAYS)}
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UID = re.compile(r"^\d{1,20}$")
MAX_SPAN = 240
MAX_NOTE = 200
MAX_MINUTES = 180

# The lock that guards the file. It must not be held across a Canvas call:
# listing the instructor's files is what made a roll call take minutes.
_disk_lock = threading.Lock()
_sync_lock = threading.Lock()
_pending_guard = threading.Lock()
_pending: set[str] = set()
_wake = threading.Event()
_worker_started = False
_app_ref = None
# Wait until the clicks stop, then copy once, instead of once per student.
_QUIET_S = 1.2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_day(value) -> date | None:
    text = str(value or "").strip()
    if not _DAY.match(text):
        return None
    try:
        found = date.fromisoformat(text)
    except ValueError:
        return None
    if not 2000 <= found.year <= 2100:
        return None
    return found


def _iso(day: date) -> str:
    return day.isoformat()


def sync_key(course_id) -> str:
    return "attendance-%s.json" % course_id


def empty_book() -> dict:
    return {
        "schema": 1,
        "updated": "",
        "pattern_updated": "",
        "weekdays": [],
        "start": "",
        "end": "",
        "skip_breaks": True,
        "meet": {},
        "marks": {},
    }


def _clean_mark(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status") or "").strip().lower()
    if status not in STATUSES and status != "":
        return None
    try:
        minutes = int(raw.get("minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0
    minutes = max(0, min(MAX_MINUTES, minutes))
    if status != "tardy":
        minutes = 0
    note = str(raw.get("note") or "").replace("\r", " ").replace("\n", " ").strip()
    note = re.sub(r"\s+", " ", note)[:MAX_NOTE]
    return {
        "status": status,
        "minutes": minutes,
        "note": note,
        "updated": str(raw.get("updated") or ""),
    }


def clean_book(raw) -> dict:
    """Drop anything that is not a mark or a class-day choice."""
    book = empty_book()
    if not isinstance(raw, dict):
        return book
    book["updated"] = str(raw.get("updated") or "")
    book["pattern_updated"] = str(raw.get("pattern_updated") or "")
    days = []
    for name in raw.get("weekdays") or []:
        key = str(name or "").strip().lower()[:3]
        if key in _WEEK_NUM and key not in days:
            days.append(key)
    book["weekdays"] = days
    start = parse_day(raw.get("start"))
    end = parse_day(raw.get("end"))
    book["start"] = _iso(start) if start else ""
    book["end"] = _iso(end) if end else ""
    book["skip_breaks"] = bool(raw.get("skip_breaks", True))
    meet_in = raw.get("meet") if isinstance(raw.get("meet"), dict) else {}
    for key, row in meet_in.items():
        day = parse_day(key)
        if not day or not isinstance(row, dict):
            continue
        book["meet"][_iso(day)] = {
            "on": bool(row.get("on")),
            "updated": str(row.get("updated") or ""),
        }
    marks_in = raw.get("marks") if isinstance(raw.get("marks"), dict) else {}
    for key, cell in marks_in.items():
        day = parse_day(key)
        if not day or not isinstance(cell, dict):
            continue
        kept = {}
        for uid, mark in cell.items():
            if not _UID.match(str(uid or "").strip()):
                continue
            cleaned = _clean_mark(mark)
            if cleaned:
                kept[str(uid)] = cleaned
        if kept:
            book["marks"][_iso(day)] = kept
    return book


def _stamp(row) -> str:
    return str((row or {}).get("updated") or "") if isinstance(row, dict) else ""


def merge_books(left, right) -> dict:
    """Per field, the later timestamp. A tie keeps left."""
    a = clean_book(left)
    b = clean_book(right)
    pattern = a if a["pattern_updated"] >= b["pattern_updated"] else b
    out = empty_book()
    out["pattern_updated"] = pattern["pattern_updated"]
    out["weekdays"] = list(pattern["weekdays"])
    out["start"] = pattern["start"]
    out["end"] = pattern["end"]
    out["skip_breaks"] = pattern["skip_breaks"]
    out["updated"] = max(a["updated"], b["updated"])
    for day in set(a["meet"]) | set(b["meet"]):
        chosen = a["meet"].get(day) if _stamp(a["meet"].get(day)) >= _stamp(b["meet"].get(day)) else b["meet"].get(day)
        if chosen:
            out["meet"][day] = dict(chosen)
    for day in set(a["marks"]) | set(b["marks"]):
        cell = {}
        la = a["marks"].get(day) or {}
        lb = b["marks"].get(day) or {}
        for uid in set(la) | set(lb):
            chosen = la.get(uid) if _stamp(la.get(uid)) >= _stamp(lb.get(uid)) else lb.get(uid)
            if chosen:
                cell[uid] = dict(chosen)
        if cell:
            out["marks"][day] = cell
    return out


def _course_key(course_id) -> str:
    text = str(course_id).strip()
    if not text.isdigit():
        raise ValueError("That is not a course id.")
    return text


def on_disk(app, course_id) -> bool:
    """True when this course already has a calendar file on this computer."""
    try:
        return _path(app, course_id, mkdir=False).is_file()
    except ValueError:
        return False


def _path(app, course_id, mkdir: bool = False) -> Path:
    key = _course_key(course_id)
    if mkdir:
        return Path(app.course_dir(key)) / "attendance.json"
    return Path(app.store.root) / key / "attendance.json"


def load(app, course_id) -> dict:
    """Read the book. Does not create the course folder."""
    try:
        path = _path(app, course_id, mkdir=False)
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_book()
    return clean_book(raw)


def save(app, course_id, book: dict) -> dict:
    book = clean_book(book)
    path = _path(app, course_id, mkdir=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(book, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return book


def _remote(app, course_id) -> dict:
    sync = getattr(app, "state_sync", None)
    client = getattr(app, "client", None)
    if sync is None or client is None or not getattr(sync, "enabled", False):
        return {}
    try:
        from ..statesync import pull
        env = pull(client, sync_key(course_id), sync.folder)
    except Exception:  # noqa: BLE001
        return {}
    payload = env.get("payload") if isinstance(env, dict) else None
    return payload if isinstance(payload, dict) else {}


def _push(app, course_id, book: dict) -> dict:
    sync = getattr(app, "state_sync", None)
    if sync is None or not hasattr(sync, "put"):
        return {"did": "local"}
    payload = clean_book(book)
    try:
        return sync.put(sync_key(course_id), payload) or {"did": "sent"}
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if name != "Conflict" and not name.endswith("Conflict"):
            return {"did": "error", "error": str(exc)[:200]}
    try:
        return sync.put(sync_key(course_id), payload, force=True) or {"did": "sent"}
    except Exception as exc:  # noqa: BLE001
        return {"did": "error", "error": str(exc)[:200]}


def nudge(app, course_id) -> None:
    """Ask the background copy to run after the clicks go quiet.

    The click itself only writes the file. Canvas is this thread's job, so
    the next student is not waiting on the previous upload.
    """
    global _app_ref, _worker_started
    try:
        cid = _course_key(course_id)
    except ValueError:
        return
    with _pending_guard:
        _pending.add(cid)
        _app_ref = app
        if not _worker_started:
            _worker_started = True
            threading.Thread(
                target=_sync_loop, name="attendance-sync", daemon=True,
            ).start()
    _wake.set()


def _sync_loop() -> None:
    while True:
        _wake.wait()
        _wake.clear()
        while _wake.wait(_QUIET_S):
            _wake.clear()
        app = _app_ref
        if app is None:
            continue
        with _pending_guard:
            batch = sorted(_pending)
            _pending.clear()
        for cid in batch:
            try:
                reconcile(app, cid)
            except Exception:  # noqa: BLE001
                pass
        with _pending_guard:
            more = bool(_pending)
        if more:
            _wake.set()


def reconcile(app, course_id) -> dict:
    """Local file and the Canvas copy, merged. Pushes when this side is ahead.

    The Canvas read and the upload happen outside the file lock, so a mark
    saved while a copy is in flight is still on disk when this merges.
    """
    with _sync_lock:
        return _reconcile(app, course_id)


def _reconcile(app, course_id) -> dict:
    remote = _remote(app, course_id)
    with _disk_lock:
        local = load(app, course_id)
        merged = merge_books(local, remote)
        if merged != local:
            save(app, course_id, merged)
        snapshot = merged
    remote_clean = clean_book(remote) if remote else {}
    should_push = (bool(remote) and snapshot != remote_clean) or (
        not remote and _has_anything(snapshot))
    if not should_push:
        return snapshot
    with _disk_lock:
        current = load(app, course_id)
    if current != snapshot:
        nudge(app, course_id)
        return current
    _push(app, course_id, snapshot)
    with _disk_lock:
        current = load(app, course_id)
    if current != snapshot:
        nudge(app, course_id)
    return current


def _has_anything(book: dict) -> bool:
    return bool(book.get("weekdays") or book.get("meet") or book.get("marks")
                or book.get("start") or book.get("end"))


def _payload_sha(book: dict) -> str:
    from ..statesync import _digest
    return _digest(clean_book(book))


def outstanding(app) -> list[str]:
    """Courses whose calendar file has not been confirmed in Canvas.

    Compared to the sidecar written after a successful upload. A crash or a
    dropped connection leaves the file here so a later pass can send it.
    """
    from ..statesync import load_sidecar
    root = Path(app.store.root)
    found = []
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.name.isdigit() or not (child / "attendance.json").is_file():
            continue
        stored = load(app, child.name)
        if not _has_anything(stored):
            continue
        side = load_sidecar(root, sync_key(child.name))
        if (side.get("sha256") or "") != _payload_sha(stored):
            found.append(child.name)
    return sorted(found)


def push_outstanding(app) -> int:
    """Send every calendar that Canvas does not have yet. One failure does
    not stop the others. Returns how many courses were attempted."""
    sync = getattr(app, "state_sync", None)
    if sync is None or not getattr(sync, "enabled", False):
        return 0
    tried = 0
    for cid in outstanding(app):
        tried += 1
        try:
            reconcile(app, cid)
        except Exception:  # noqa: BLE001
            pass
    return tried


def set_pattern(book: dict, weekdays, start: str, end: str, skip_breaks: bool) -> dict:
    start_d = parse_day(start)
    end_d = parse_day(end)
    if not start_d or not end_d:
        raise ValueError("Class days need a start and an end, as yyyy-mm-dd.")
    if end_d < start_d:
        raise ValueError("The last class day is before the first.")
    if (end_d - start_d).days > MAX_SPAN:
        raise ValueError("That span is longer than a school year. Shorten it.")
    names = []
    for name in weekdays or []:
        key = str(name or "").strip().lower()[:3]
        if key not in _WEEK_NUM:
            raise ValueError("Weekdays are mon, tue, wed, thu, fri, sat, sun.")
        if key not in names:
            names.append(key)
    if not names:
        raise ValueError("Pick at least one day this class meets.")
    book = clean_book(book)
    book["weekdays"] = names
    book["start"] = _iso(start_d)
    book["end"] = _iso(end_d)
    book["skip_breaks"] = bool(skip_breaks)
    stamp = _now()
    book["pattern_updated"] = stamp
    book["updated"] = stamp
    return book


def set_meet(book: dict, day: str, on: bool) -> dict:
    found = parse_day(day)
    if not found:
        raise ValueError("That is not a yyyy-mm-dd date.")
    book = clean_book(book)
    stamp = _now()
    book["meet"][_iso(found)] = {"on": bool(on), "updated": stamp}
    book["updated"] = stamp
    return book


def apply_marks(book: dict, day: str, marks, roster_ids=None, fill: str = "") -> dict:
    """Write marks for one date. `fill` of "present" sets everyone still blank.

    A mark may carry `updated`. An older stamp does not cover a newer one,
    so a slow request cannot undo a click that already landed.
    """
    found = parse_day(day)
    if not found:
        raise ValueError("That is not a yyyy-mm-dd date.")
    if fill and fill != "present":
        raise ValueError("The only fill is present, for people not yet marked.")
    book = clean_book(book)
    iso = _iso(found)
    cell = dict(book["marks"].get(iso) or {})
    changed = False
    last = str(book.get("updated") or "")

    def take(uid: str, status: str, minutes, note, stamp: str) -> None:
        nonlocal changed, last
        cleaned = _clean_mark({
            "status": status, "minutes": minutes, "note": note, "updated": stamp,
        })
        if cleaned is None:
            raise ValueError("A mark is present, tardy, absent, or excused.")
        cell[uid] = cleaned
        changed = True
        if stamp > last:
            last = stamp

    if fill == "present":
        stamp = _now()
        for uid in roster_ids or []:
            uid = str(uid).strip()
            if not _UID.match(uid):
                continue
            current = cell.get(uid) or {}
            if current.get("status"):
                continue
            take(uid, "present", 0, "", stamp)
    for raw in marks or []:
        if not isinstance(raw, dict):
            continue
        uid = str(raw.get("user_id") or "").strip()
        if not _UID.match(uid):
            raise ValueError("A mark needs the student's Canvas user id.")
        status = str(raw.get("status") or "").strip().lower()
        if status in ("clear", "unmarked"):
            status = ""
        if status not in STATUSES and status != "":
            raise ValueError("A mark is present, tardy, absent, or excused.")
        previous = cell.get(uid) or {}
        incoming = str(raw.get("updated") or "")
        prev_stamp = str(previous.get("updated") or "")
        if incoming and prev_stamp and incoming < prev_stamp:
            continue
        note = raw.get("note")
        if note is None:
            note = previous.get("note") or ""
        minutes = raw.get("minutes")
        if minutes is None:
            minutes = previous.get("minutes") or 0
        take(uid, status, minutes, note, incoming or _now())
    if not changed:
        return book
    if cell:
        book["marks"][iso] = cell
    book["updated"] = last or _now()
    # A real mark on a day that is not already a class day makes it one, so
    # the mark is not stored where the calendar will not show it. A clear
    # does not put the day back.
    if any((row.get("status") for row in cell.values())):
        book["meet"][iso] = {"on": True, "updated": book["updated"]}
    return book


def apply_edit(app, course_id, body: dict) -> dict:
    """Save one edit on this computer. Does not call Canvas."""
    if not isinstance(body, dict):
        body = {}
    with _disk_lock:
        stored = load(app, course_id)
        changed = False
        if body.get("weekdays"):
            stored = set_pattern(
                stored, body.get("weekdays") or [],
                str(body.get("start") or ""), str(body.get("end") or ""),
                body.get("skip_breaks", True),
            )
            changed = True
        if body.get("marks") or body.get("fill"):
            people = [p["user_id"] for p in roster(app, course_id)]
            stored = apply_marks(
                stored, str(body.get("date") or ""), body.get("marks") or [],
                roster_ids=people, fill=str(body.get("fill") or ""),
            )
            changed = True
        if (body.get("date") and "on" in body
                and not (body.get("marks") or body.get("fill") or body.get("weekdays"))):
            stored = set_meet(stored, str(body.get("date") or ""), bool(body.get("on")))
            changed = True
        if changed:
            stored = save(app, course_id, stored)
        return stored


def meeting_dates(book: dict, breaks: set[date]) -> list[date]:
    book = clean_book(book)
    start = parse_day(book.get("start"))
    end = parse_day(book.get("end"))
    wanted = {_WEEK_NUM[name] for name in book.get("weekdays") or []}
    found: list[date] = []
    if start and end and start <= end and wanted and (end - start).days <= MAX_SPAN:
        day = start
        while day <= end:
            if day.weekday() in wanted and not (book.get("skip_breaks") and day in breaks):
                found.append(day)
            day += timedelta(days=1)
    have = set(found)
    for iso, row in (book.get("meet") or {}).items():
        day = parse_day(iso)
        if not day:
            continue
        if row.get("on"):
            if day not in have:
                found.append(day)
                have.add(day)
        elif day in have:
            found.remove(day)
            have.remove(day)
    return sorted(found)


def _course_row(app, course_id) -> dict:
    for row in (getattr(app, "store", None) and app.store.courses()) or []:
        if str(row.get("id")) == str(course_id):
            return row
    return {}


def _anchor(course: dict, book: dict) -> date:
    start = parse_day(book.get("start"))
    if start:
        return start
    label = str(course.get("term_label") or course.get("term") or "")
    match = re.search(r"(Fall|Spring|Summer)\s+(\d{4})", label, re.I)
    if match:
        year = int(match.group(2))
        month = {"fall": 9, "spring": 2, "summer": 6}[match.group(1).lower()]
        return date(year, month, 15)
    return date.today()


def breaks_between(app, course_id, book: dict) -> list[str]:
    """College break days that fall inside the span, from the local calendar."""
    try:
        from ..courseops.calendar import expand_breaks, lookup
    except Exception:  # noqa: BLE001
        return []
    course = _course_row(app, course_id)
    anchor = _anchor(course, book)
    try:
        info = lookup(anchor)
    except (OSError, ValueError):
        return []
    if not info:
        return []
    try:
        days = expand_breaks(info.get("breaks"))
    except (ValueError, TypeError):
        return []
    start, end = _span(book, course, info.get("finals_end") or "")
    return sorted(_iso(d) for d in days if start <= d <= end)


def _span(book: dict, course: dict, finals: str) -> tuple[date, date]:
    start = parse_day(book.get("start"))
    end = parse_day(book.get("end"))
    if not start or not end:
        anchor = _anchor(course, book)
        finals_d = parse_day(finals)
        if not start:
            start = date(anchor.year, anchor.month, 1)
        if not end:
            end = finals_d or (start + timedelta(days=120))
    if end < start:
        start, end = end, start
    if (end - start).days > MAX_SPAN:
        end = start + timedelta(days=MAX_SPAN)
    # Marks outside the pattern still have to be visible.
    for iso in (book.get("marks") or {}):
        day = parse_day(iso)
        if not day:
            continue
        if day < start:
            start = day
        if day > end and (day - start).days <= MAX_SPAN:
            end = day
    return start, end


def suggest(app, course_id, book: dict) -> dict:
    course = _course_row(app, course_id)
    finals = ""
    try:
        from ..courseops.calendar import lookup
        info = lookup(_anchor(course, book)) or {}
        finals = str(info.get("finals_end") or "")
    except Exception:  # noqa: BLE001
        finals = ""
    start, end = _span(book, course, finals)
    return {"start": _iso(start), "end": _iso(end), "finals_end": finals}


def public_marks(book: dict) -> dict:
    """Marks the screen draws. Clears stay in the file and drop out here."""
    out = {}
    for day, cell in (clean_book(book).get("marks") or {}).items():
        shown = {}
        for uid, mark in cell.items():
            if not mark.get("status"):
                continue
            shown[uid] = {
                "status": mark["status"],
                "minutes": mark["minutes"],
                "note": mark["note"],
            }
        if shown:
            out[day] = shown
    return out


def totals(book: dict, meetings: list[date], roster_ids: list[str]) -> list[dict]:
    """Counts on class days only. A clear is not an absence."""
    book = clean_book(book)
    meeting = {_iso(d) for d in meetings}
    ids = []
    seen = set()
    for uid in list(roster_ids) + [u for cell in book["marks"].values() for u in cell]:
        uid = str(uid).strip()
        if uid and uid not in seen and _UID.match(uid):
            seen.add(uid)
            ids.append(uid)
    rows = []
    for uid in ids:
        count = {name: 0 for name in STATUSES}
        minutes = 0
        for day in meeting:
            mark = (book["marks"].get(day) or {}).get(uid) or {}
            status = mark.get("status") or ""
            if status in count:
                count[status] += 1
            if status == "tardy":
                minutes += int(mark.get("minutes") or 0)
        rows.append({
            "user_id": uid,
            "present": count["present"],
            "tardy": count["tardy"],
            "absent": count["absent"],
            "excused": count["excused"],
            "minutes": minutes,
        })
    return rows


def roster(app, course_id) -> list[dict]:
    """Legal names for the instructor's screen. Not written into the book.

    Reads the class list already on disk. Does not create the course folder
    and does not call Canvas.
    """
    rows = []
    try:
        path = Path(app.store.root) / _course_key(course_id) / "students.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, ValueError):
        raw = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("id") or row.get("user_id") or "").strip()
        if not _UID.match(uid):
            continue
        rows.append({
            "user_id": uid,
            "name": str(row.get("name") or "").strip(),
            "sortable_name": str(row.get("sortable_name") or row.get("name") or "").strip(),
        })
    rows.sort(key=lambda r: (r["sortable_name"].lower(), r["user_id"]))
    return rows


def refresh_roster(app, course_id) -> list[dict]:
    """Re-read the class list from Canvas. Reads only; the gradebook is not touched."""
    rows = list(app.client.students(course_id) or [])
    app.store.save_students(course_id, rows)
    return roster(app, course_id)


def view(app, course_id, book: dict | None = None) -> dict:
    book = clean_book(book if book is not None else load(app, course_id))
    people = roster(app, course_id)
    break_days = breaks_between(app, course_id, book)
    break_set = {parse_day(d) for d in break_days}
    break_set.discard(None)
    meetings = meeting_dates(book, break_set)
    hint = suggest(app, course_id, book)
    course = _course_row(app, course_id)
    return {
        "course_id": str(course_id),
        "course": course.get("title") or course.get("name") or "",
        "pattern": {
            "weekdays": list(book["weekdays"]),
            "start": book["start"],
            "end": book["end"],
            "skip_breaks": book["skip_breaks"],
        },
        "suggest": hint,
        "range": {"start": hint["start"], "end": hint["end"]},
        "breaks": break_days,
        "meetings": [_iso(d) for d in meetings],
        "marks": public_marks(book),
        "updated": book.get("updated") or "",
        "roster": people,
        "totals": totals(book, meetings, [p["user_id"] for p in people]),
        "note": "Marks stay on this computer and copy to your Canvas files. "
                "The gradebook is not changed.",
    }


def summary_line(book: dict, meetings: list[date], roster_ids: list[str]) -> str:
    rows = totals(book, meetings, roster_ids)
    absent = sum(r["absent"] for r in rows)
    tardy = sum(r["tardy"] for r in rows)
    if not meetings:
        return "No class days chosen yet. The gradebook is not changed."
    return "%d class day%s · %d absence%s · %d tard%s. The gradebook is not changed." % (
        len(meetings), "" if len(meetings) == 1 else "s",
        absent, "" if absent == 1 else "s",
        tardy, "y" if tardy == 1 else "ies",
    )
