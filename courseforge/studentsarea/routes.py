"""Routes for the per-student page."""
from __future__ import annotations

from .. import nicknames, statesync, watchnotes
from ..confirm import ConfirmRequired, ConfirmStale
from ..routing import HTTPError, route
from . import assemble, risk
# Attendance is its own package. Importing it here registers its routes
# without a new line in the area list.
from ..attendance import routes as _attendance_routes  # noqa: F401

AREA = "student"
MAX_NOTE = 8000


def install(app) -> None:  # noqa: ARG001
    return None


# RISK-INDEX: these two routes, plus studentsarea/risk.py. See docs/RISK-INDEX.md.
@route("GET", "/api/students/risk", area=AREA)
def risk_saved(req):
    """The last scan, if there is one. Does not call Canvas."""
    saved = risk.load_for_use(req.app)
    return risk.public_view(risk.refresh_attendance(req.app, saved))


@route("POST", "/api/students/risk", area=AREA)
def risk_scan(req):
    """Read this term's classes and score who needs a look. A job, so the
    page can show which class it is on."""

    def job(log):
        return risk.scan(req.app, log)

    return req.job("student.risk", job)


@route("GET", "/api/nicknames", area=AREA)
def nick_list(req):
    """Every nickname this instructor has set. Display only."""
    try:
        book = nicknames.reconcile(req.app)
    except Exception:  # noqa: BLE001
        root = getattr(getattr(req.app, "store", None), "root", None)
        book = nicknames.Book(root).read() if root else {}
    return {"by_id": nicknames.plain(book)}


@route("POST", "/api/student/{uid}/nickname", area=AREA)
def nick_save(req):
    uid = str(req.params["uid"])
    if not uid.isdigit():
        raise HTTPError(400, "That is not a Canvas user id.")
    body = req.body if isinstance(req.body, dict) else {}
    try:
        saved = nicknames.save(req.app, uid, body.get("nickname"))
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    legal = ""
    try:
        legal = assemble.person(req.app, uid).get("name") or ""
    except Exception:  # noqa: BLE001
        legal = ""
    saved["legal_name"] = legal
    saved["display_name"] = nicknames.format_name(legal, saved.get("nickname") or "")
    return saved


@route("GET", "/api/students", area=AREA)
def find(req):
    """This term's master list, narrowed when a query is present."""
    term = req.q("term")
    return assemble.search(req.app, req.q("q") or "", term=term or None)


@route("GET", "/api/student/{uid}", area=AREA)
def show(req):
    return assemble.dossier(req.app, req.params["uid"], req.q("course"))


@route("POST", "/api/student/{uid}/live", area=AREA)
def live(req):
    uid = req.params["uid"]

    def job(log):
        return assemble.live(req.app, uid, log)

    return req.job("student.live", job)


@route("POST", "/api/student/{uid}/work", area=AREA)
def student_work(req):
    """Late and missing items in each class this term. Reads only."""
    uid = req.params["uid"]

    def job(log):
        return assemble.course_work(req.app, uid, log)

    return req.job("student.work", job)


@route("POST", "/api/student/{uid}/remind", area=AREA)
def remind_student(req):
    """One Canvas Inbox note listing this student's missing work in one class.

    The first call is refused until the message has been shown and confirmed.
    """
    body = req.body if isinstance(req.body, dict) else {}
    titles = body.get("items") if isinstance(body.get("items"), list) else []
    try:
        return req.app.remind_one_student(
            req.params["uid"], body.get("course_id"), body.get("course_name") or "",
            titles, body.get("confirm"))
    except ConfirmRequired as exc:
        raise HTTPError(409, exc.summary, **exc.payload())
    except ConfirmStale as exc:
        raise HTTPError(409, str(exc), confirm_stale=True)


@route("POST", "/api/student/{uid}/notes", area=AREA)
def save_notes(req):
    body = req.body if isinstance(req.body, dict) else {}
    text = str(body.get("notes") or "")
    if len(text) > MAX_NOTE:
        raise HTTPError(400, "That note is longer than this page will keep.")
    uid = str(req.params["uid"])
    sync = getattr(req.app, "state_sync", None)
    if sync is None or not hasattr(sync, "put"):
        raise HTTPError(
            503,
            "Canvas state sync is not running, so the note would only live on this computer.",
        )
    saved = assemble._sync_payload(req.app, uid)
    payload = {
        "user_id": uid,
        "notes": text,
        "history": saved.get("history") or [],
    }
    try:
        return sync.put(f"students/{uid}.json", payload)
    except statesync.Conflict:
        raise HTTPError(
            409,
            "Canvas has a newer copy of this note. Reload the page, then save again.",
        ) from None


@route("GET", "/api/student/{uid}/watch", area=AREA)
def watch_list(req):
    """The instructor's notes on this student, newest first."""
    return {"notes": watchnotes.for_student(req.app.store.root, req.params["uid"])}


@route("POST", "/api/student/{uid}/watch", area=AREA)
def watch_add(req):
    """A note on this student, usually written from the grader on one assignment."""
    body = req.body if isinstance(req.body, dict) else {}
    try:
        return watchnotes.add(
            req.app, req.params["uid"], body.get("text"),
            kind=str(body.get("kind") or "note"),
            course_id=str(body.get("course_id") or ""),
            assignment_id=str(body.get("assignment_id") or ""),
            assignment_name=str(body.get("assignment_name") or ""),
            about_user_id=str(body.get("about_user_id") or ""),
            about_name=str(body.get("about_name") or ""),
            user_name=str(body.get("user_name") or ""))
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None


@route("POST", "/api/student/{uid}/watch/{nid}", area=AREA)
def watch_change(req):
    body = req.body if isinstance(req.body, dict) else {}
    try:
        return watchnotes.change(
            req.app, req.params["uid"], req.params["nid"],
            text=body.get("text"),
            resolved=body.get("resolved") if "resolved" in body else None,
            delete=bool(body.get("delete")))
    except KeyError as exc:
        raise HTTPError(404, str(exc.args[0] if exc.args else exc)) from None
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
