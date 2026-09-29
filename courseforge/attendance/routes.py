"""HTTP routes for the attendance calendar.

    GET  /api/attendance/{cid}                  the calendar, the class, the counts
    POST /api/attendance/{cid}/roster          re-read the class list (reads only)
    POST /api/attendance/{cid}/pattern         which weekdays this class meets
    POST /api/attendance/{cid}/meet            add or remove one date
    POST /api/attendance/{cid}/marks           present, tardy, absent, excused

Nothing here writes the gradebook. Marks copy to the instructor's Canvas
files the same way nicknames do.
"""
from __future__ import annotations

from ..routing import HTTPError, route
from . import book

AREA = "attendance"


def install(app) -> None:  # noqa: ARG001
    return None


def _cid(req) -> str:
    cid = str(req.params.get("cid") or "").strip()
    if not cid.isdigit():
        raise HTTPError(400, "That is not a course id.")
    return cid


def _body(req) -> dict:
    return req.body if isinstance(req.body, dict) else {}


def _view(app, cid, stored: dict | None = None) -> dict:
    try:
        return book.view(app, cid, stored)
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None


@route("GET", "/api/attendance/{cid}", area=AREA)
def show(req):
    """Open the calendar.

    The first time this course is opened on this computer there is no local
    file, so this waits for the Canvas copy and will not paint a blank one
    over it. After that the file already here is returned at once, and the
    page asks /sync for anything newer.
    """
    cid = _cid(req)
    if book.on_disk(req.app, cid):
        stored = book.load(req.app, cid)
        pulled = False
    else:
        stored = book.reconcile(req.app, cid)
        pulled = True
    out = _view(req.app, cid, stored)
    out["pulled"] = pulled
    return out


@route("POST", "/api/attendance/{cid}/sync", area=AREA)
def sync(req):
    """Pick up the other computer's marks after the calendar is already up."""
    cid = _cid(req)
    stored = book.reconcile(req.app, cid)
    return _view(req.app, cid, stored)


@route("POST", "/api/attendance/{cid}/flush", area=AREA)
def flush(req):
    """Write whatever the page still had, then copy the book to Canvas.

    Used when the person leaves, and a few seconds after an edit. The file
    is saved before the upload is attempted, so a dropped connection does
    not lose the marks. A later pass sends anything Canvas did not confirm.
    """
    cid = _cid(req)
    try:
        book.apply_edit(req.app, cid, _body(req))
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    stored = book.reconcile(req.app, cid)
    out = _view(req.app, cid, stored)
    enabled = bool(getattr(getattr(req.app, "state_sync", None), "enabled", False))
    if not enabled:
        out["sync"] = "local"
    elif cid in book.outstanding(req.app):
        out["sync"] = "error"
    else:
        out["sync"] = "sent"
    return out


@route("POST", "/api/attendance/{cid}/roster", area=AREA)
def reload_roster(req):
    """The class list from Canvas. No grades are read and none are written."""
    cid = _cid(req)
    try:
        book.refresh_roster(req.app, cid)
    except Exception as exc:  # noqa: BLE001
        raise HTTPError(502, "Could not read the class list from Canvas.") from exc
    stored = book.reconcile(req.app, cid)
    return _view(req.app, cid, stored)


def _saved(req) -> dict:
    """Write the edit and answer at once. The Canvas copy follows behind it."""
    cid = _cid(req)
    try:
        stored = book.apply_edit(req.app, cid, _body(req))
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    book.nudge(req.app, cid)
    out = _view(req.app, cid, stored)
    out["sync"] = "local"
    return out


@route("POST", "/api/attendance/{cid}/pattern", area=AREA)
def pattern(req):
    return _saved(req)


@route("POST", "/api/attendance/{cid}/meet", area=AREA)
def meet(req):
    return _saved(req)


@route("POST", "/api/attendance/{cid}/marks", area=AREA)
def marks(req):
    return _saved(req)


def hub_status(app, course_id) -> dict:
    """Local file only. The hub must not call Canvas to paint."""
    stored = book.load(app, course_id)
    people = []
    try:
        people = [p["user_id"] for p in book.roster(app, course_id)]
    except Exception:  # noqa: BLE001
        people = []
    break_days = []
    try:
        break_days = book.breaks_between(app, course_id, stored)
    except Exception:  # noqa: BLE001
        break_days = []
    break_set = {book.parse_day(d) for d in break_days}
    break_set.discard(None)
    meetings = book.meeting_dates(stored, break_set)
    return {
        "lines": [book.summary_line(stored, meetings, people)],
        "badge": None,
        "needs": [],
        "actions": [{"label": "Open the calendar", "href": "#/c/%s/attendance" % course_id}],
    }
