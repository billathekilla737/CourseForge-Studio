"""The course hub: where each area stands in one course, from local state only.

Three routes. The hub payload is assembled from what is already on disk (the
cached assignment list, draft files, each area's own manifests through its
`hub_status`, the ledger) so it paints in tens of milliseconds and never calls
Canvas. The one Canvas read is the refresh job, which re-reads the assignment
list the way the grader's Refresh button does, then returns a fresh hub.
"""
from __future__ import annotations

import importlib
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .. import audit, folders, gradestatus, ledger
from ..areas import AREAS
from ..routing import HTTPError, route
from ..store import safe_id

# Which hub card each area package reports into. Accessibility is three
# packages (HTML restyling, Office documents, PDFs) and one card.
CARD_OF = {
    "a11y": "a11y", "docs": "a11y", "pdf": "a11y",
    "content": "build", "courseops": "tools", "assistant": "assistant",
}
CARDS = ("grade", "a11y", "build", "tools", "assistant")

# An assignment list older than this is shown at once and refreshed behind it.
STALE_AFTER_S = 6 * 3600


def install(app) -> None:  # noqa: ARG001  (the routes register on import)
    return None


# --------------------------------------------------------------------- routes
@route("GET", "/api/picker", area="hub")
def picker(req):
    """Everything the course list draws, in one call.

    The courses and the term counts were already two round trips; the state
    beside each course is new, and all of it is read off this disk. A picker
    that asked Canvas how many submissions are waiting in five courses would
    take five calls to tell you something you are about to click into anyway.
    So a course that has never been opened here simply says so, and the first
    visit fills it in. To Do is the same disk read. Re-reading it from Canvas
    is the refresh job, which the home page runs only when asked.
    """
    return picker_payload(req.app, req.flag("refresh"))


@route("POST", "/api/picker/refresh", area="hub")
def picker_refresh(req):
    """Re-read the course list and the assignment lists To Do is built from.

    Reads only. A job, because one Canvas call per course in the term can
    take a while. Body `{"term": "Fall 2026"}` limits the assignment reads
    to that term; omit it, or send "__all", to read every course that is
    not excluded. The course list itself is always re-read.
    """
    app = req.app
    body = req.body if isinstance(req.body, dict) else {}
    term = str(body.get("term") or "")

    def job(log):
        return refresh_todo(app, term, log)

    return req.job("picker.refresh", job)


@route("POST", "/api/picker/status", area="hub")
def picker_status(req):
    """Check grading status in Canvas for the courses on screen, then To Do.

    Reads only, and light: the assignment list and the submission rows, no
    files. A course checked in the last few minutes is not asked again, so
    the home page can start this on every load. Body `{"term": ...}` as for
    /api/picker/refresh. The result is the picker payload plus `checked`,
    the number of assignments Canvas was asked about.
    """
    app = req.app
    body = req.body if isinstance(req.body, dict) else {}
    term = str(body.get("term") or "")

    def job(log):
        return check_todo(app, term, log)

    return req.job("picker.status", job)


@route("POST", "/api/courses/{cid}/grade-status", area="hub")
def course_status(req):
    """Check grading status for one course's assignment list. Reads only.

    Skipped when the course was checked in the last few minutes, unless the
    body says `{"force": true}`. Returns the assignment list as
    GET /api/courses/{cid}/assignments draws it, plus `checked`.
    """
    app = req.app
    cid = req.params["cid"]
    body = req.body if isinstance(req.body, dict) else {}
    force = bool(body.get("force"))

    def job(log):
        out = check_course(app, cid, force=force, log=log)
        out["assignments"] = app.assignments(cid)
        return out

    return req.job("grade.status", job)


@route("GET", "/api/courses/{cid}/hub", area="hub")
def hub(req):
    return build_hub(req.app, req.params["cid"])


@route("GET", "/api/courses/{cid}/ledger", area="hub")
def ledger_rows(req):
    cid = req.params["cid"]
    try:
        limit = max(1, min(int(req.q("limit", "50")), 500))
    except ValueError:
        limit = 50
    rows = ledger.read(req.app.course_dir(cid), limit=limit, area=req.q("area") or None)
    return {"course_id": cid, "rows": rows}


# Which install_plan row gets a tool that an "Install X to enable" card names.
# The Python libraries all arrive in one pip call, so several card names lead
# to the same row.
INSTALL_ROW = {
    "tesseract": "tesseract", "java": "java", "verapdf": "verapdf",
    "pymupdf": "python", "pikepdf": "python", "fonttools": "python",
    "pypdf": "python", "python_pptx": "python", "python_docx": "python",
    "pillow": "python", "pdf_engine": "python",
}


@route("POST", "/api/tools/install", area="hub")
def tools_install(req):
    """Install the optional tools named in `{"tools": [...]}`, or all missing.

    A job rather than a plain request, because winget can sit for a minute and
    veraPDF is a 33 MB download. Nothing here touches Canvas, so there is no
    confirm token -- but it does install software on the machine, so the button
    that calls it asks first.
    """
    from .. import tools as _tools
    body = req.body if isinstance(req.body, dict) else {}
    asked = [str(t) for t in (body.get("tools") or []) if str(t)]
    unknown = [t for t in asked if t not in INSTALL_ROW]
    if unknown:
        raise HTTPError(400, "The Studio does not install " + ", ".join(unknown) + ".")
    only = sorted({INSTALL_ROW[t] for t in asked}) or None

    def job(log):
        step = [0]

        def say(line):
            step[0] += 1
            log(line, step[0], step[0] + 1)

        out = _tools.install_stream(req.app.cfg, on_line=say, only=only)
        _tools.wire_env(req.app.cfg)
        found = _tools.detect(req.app.cfg)
        out["tools"] = found
        names = [found.get(t, {}).get("label", t) for t in out["installed"]]
        out["sentence_done"] = ("Installed " + ", ".join(names) + "."
                                if names else "Nothing new was installed.")
        return out

    return req.job("tools.install", job)


@route("POST", "/api/courses/{cid}/hub/refresh", area="hub")
def refresh(req):
    cid = req.params["cid"]
    app = req.app

    def job(log):
        log("Reading the assignment list from Canvas (reads only)", 0, 3)
        rows = app.assignments(cid, refresh=True)
        log(f"{len(rows)} assignments in the list", 1, 3)
        check_course(app, cid, rows=app.store.assignments(cid), force=True)
        log("Grading status checked", 2, 3)
        out = build_hub(app, cid)
        log("Hub updated", 3, 3)
        return out

    return req.job("hub.refresh", job)


# ------------------------------------------------- state beside each course
def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def figures(app, cid, cdir: Path) -> dict:
    """The four numbers on the course page band. Disk only, and it does not
    create the course folder: the home list calls this for courses never opened.
    """
    rows = app.store.read(cdir / "assignments.json", []) or []
    if not isinstance(rows, list):
        rows = []
    waiting = 0
    to_grade = 0
    open_work = []
    # Graded once and handed in again: kept apart, so a regrade done as a
    # favour never outranks work nobody has graded yet.
    regrade_work = []
    to_regrade = 0
    status = gradestatus.load(app.store.root, cid)
    for r in rows:
        if not isinstance(r, dict):
            continue
        state = gradestatus.state_for(cdir, r, status, app.store.read)
        if state["graded"]:
            continue
        if state["resubmitted"]:
            to_regrade += state["resubmitted"]
            regrade_work.append({
                "id": r.get("id"),
                "name": r.get("name") or "",
                "resubmitted": state["resubmitted"],
                "resubmitted_since": state.get("resubmitted_since") or "",
                "due_at": r.get("due_at") or "",
            })
        n = state["fresh"]
        if not n:
            continue
        to_grade += 1
        waiting += n
        open_work.append({
            "id": r.get("id"),
            "name": r.get("name") or "",
            "needs_grading": n,
            "due_at": r.get("due_at") or "",
        })
    graded = 0
    last: str | None = None
    try:
        children = folders.children(cdir)
    except OSError:
        children = []
    for child in children:
        draft = app.store.read(child / "draft.json", None)
        if not isinstance(draft, dict) or not draft:
            continue
        when = draft.get("last_graded_at") or draft.get("updated_at")
        if draft.get("last_graded_at") or draft.get("students"):
            graded += 1
        if when and (last is None or str(when) > str(last)):
            last = str(when)
    # The ledger counts pages, files and settings. Grade pushes are only in
    # the audit, because that record is allowed to name students.
    writes = audit.canvas_writes_today(cdir)
    if (cdir / ledger.FILE).is_file():
        writes += ledger.today_counts(cdir).get("writes", 0)
    return {
        "count": len(rows),
        "to_grade": to_grade,
        "assignments": rows,
        "waiting": waiting,
        "graded": graded,
        "writes_today": writes,
        "last_graded_at": last,
        "open_work": open_work,
        "regrade_work": regrade_work,
        "to_regrade": to_regrade,
        "status_checked_at": status.get("checked_at"),
        "status_stale": gradestatus.is_stale(status),
    }


def local_state(app, course_id) -> dict:
    """What this machine already knows about one course. No Canvas, no model.

    The four figures are the same ones the course page shows in its band, so
    the home list and the open course cannot disagree. A course that has never
    been opened here is left alone: reading it would create the folder.
    """
    cdir = folders.under(Path(app.store.root), course_id)
    if not cdir.is_dir():
        return {"known": False, "waiting": 0, "assignments": 0, "to_grade": 0, "graded": 0,
                "writes_today": 0, "touched_at": None, "last_assignment": None}

    fig = figures(app, course_id, cdir)
    rows = fig["assignments"]
    names = {str(r.get("id")): r.get("name", "") for r in rows if isinstance(r, dict)}

    newest_draft, newest_aid = 0.0, None
    try:
        children = folders.children(cdir)
    except OSError:
        children = []
    for child in children:
        when = _mtime(child / "draft.json")
        if when > newest_draft:
            newest_draft, newest_aid = when, folders.id_of(child)

    touched = max(newest_draft, _mtime(cdir / "assignments.json"),
                  _mtime(cdir / ledger.FILE))
    return {
        "known": True,
        "waiting": fig["waiting"],
        "assignments": fig["count"],
        "to_grade": fig["to_grade"],
        "graded": fig["graded"],
        "writes_today": fig["writes_today"],
        "touched_at": _iso(touched),
        # The work itself, not merely a list that was synced: this is what
        # "carry on" should point at.
        "worked_at": _iso(newest_draft),
        "last_assignment": ({"id": newest_aid, "name": names.get(newest_aid or "", "")}
                            if newest_aid else None),
        "open_work": fig["open_work"],
        "regrade_work": fig["regrade_work"],
        "to_regrade": fig["to_regrade"],
        "status_checked_at": fig["status_checked_at"],
    }


def picker_payload(app, refresh_courses: bool = False) -> dict:
    """The home page, from disk. `refresh_courses` re-reads the course list
    only. Assignment lists, which To Do is built from, stay as saved until
    `refresh_todo`.
    """
    courses = [dict(c) for c in app.courses(refresh_courses)]
    for course in courses:
        course.update(local_state(app, course.get("id")))
    return {
        "courses": courses,
        "terms": app.term_summary(False),
        "resume": _resume(courses),
        # Past-due work still to grade, most days first. The page keeps
        # five for the term on screen.
        "behind": take_behind(courses, limit=None),
        # Graded once and handed in again, oldest resubmission first. The
        # To Do toggle shows these; they never join the list above.
        "regrade": take_regrade(courses),
    }


def todo_courses(courses: list[dict], term: str = "") -> list[dict]:
    """Courses whose assignment lists a To Do refresh should re-read.

    Excluded courses are left alone. A term label limits the read to the
    term on screen. A blank term, or "__all", reads every course.
    """
    label = (term or "").strip()
    out = []
    for course in courses:
        if not isinstance(course, dict) or not course.get("id") or course.get("excluded"):
            continue
        if label and label != "__all" and (course.get("term_label") or "") != label:
            continue
        out.append(course)
    return out


def merge_assignment_rows(raw, previous: dict) -> list[dict]:
    """The assignment-list cache, from a Canvas list plus the stamps already
    saved here. Does not open an assignment folder: a home refresh must not
    create one directory per assignment in a course nobody has graded yet.
    """
    rows = []
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        prev = previous.get(str(item.get("id")), {})
        if not isinstance(prev, dict):
            prev = {}
        row = {
            "id": item["id"],
            "name": item.get("name") or "",
            "points_possible": item.get("points_possible"),
            "due_at": item.get("due_at"),
            "published": item.get("published"),
            "submission_types": item.get("submission_types") or [],
            "has_rubric": bool(item.get("rubric")),
            "is_discussion": bool(item.get("discussion_topic")),
            "needs_grading": item.get("needs_grading_count"),
            "has_submissions": bool(item.get("has_submitted_submissions")),
            "graded_submissions_exist": bool(item.get("graded_submissions_exist")),
            "synced_at": prev.get("synced_at"),
            "graded_at": prev.get("graded_at"),
            "has_instructions": bool(prev.get("has_instructions")),
        }
        if prev.get("_dir"):
            row["_dir"] = prev["_dir"]
        rows.append(row)
    return rows


def cache_assignment_list(app, course_id, raw) -> list[dict]:
    """Store one course's assignment list. The course folder is created when
    the list is saved, and not before, so a Canvas failure leaves no folder.
    """
    path = app.store.course_dir(course_id) / "assignments.json"
    old = app.store.read(path, []) or []
    previous = {}
    if isinstance(old, list):
        previous = {str(row.get("id")): row for row in old
                    if isinstance(row, dict) and row.get("id")}
    rows = merge_assignment_rows(raw, previous)
    app.store.save_assignments(course_id, rows)
    return rows


def _canvas_line(exc: Exception) -> str:
    line = str(exc).splitlines()[0].strip()[:300]
    return line or "Canvas did not answer."


def refresh_todo(app, term: str = "", log=None) -> dict:
    """Re-read courses, then each assignment list To Do is drawn from.

    One course that fails is named and skipped. The others still update.
    If the course list itself cannot be read, nothing is written and the
    page keeps what it was showing.
    """
    log = log or (lambda *_a, **_k: None)
    try:
        courses = [dict(c) for c in app.courses(True)]
    except Exception as exc:  # noqa: BLE001
        return {"error": _canvas_line(exc)}
    targets = todo_courses(courses, term)
    failed = []
    total = max(len(targets), 1)
    log("Reading assignment lists from Canvas (reads only)", 0, total)
    for i, course in enumerate(targets, 1):
        name = course.get("title") or course.get("name") or str(course.get("id"))
        log("Reading assignments in %s" % name, i, total)
        try:
            raw = app.client.assignments(course["id"])
            rows = cache_assignment_list(app, course["id"], raw)
            gradestatus.check(app.client, app.store, course["id"], rows, force=True)
        except Exception as exc:  # noqa: BLE001
            failed.append({
                "course_id": course.get("id"),
                "name": name,
                "error": _canvas_line(exc),
            })
    out = picker_payload(app, False)
    out["failed"] = failed
    if failed:
        log("%d course list(s) could not be read" % len(failed), total, total)
        out["sentence_done"] = ("To Do refreshed. %d course list(s) could not be read."
                                % len(failed))
    else:
        log("To Do updated", total, total)
        out["sentence_done"] = "To Do refreshed from Canvas."
    return out


def check_course(app, course_id, rows=None, force: bool = False, log=None) -> dict:
    """Bring one course's grading status up to date. Reads only.

    Without `rows` the assignment list is re-read first (one call), so the
    counts the check is made against are Canvas's current ones. A course
    checked within gradestatus.FRESH_S is left alone unless `force`.
    """
    log = log or (lambda *_a, **_k: None)
    status = gradestatus.load(app.store.root, course_id)
    if not force and rows is None and not gradestatus.is_stale(status):
        return {"checked": 0, "checked_at": status.get("checked_at"), "skipped": True}
    if rows is None:
        log("Reading the assignment list from Canvas (reads only)")
        rows = cache_assignment_list(app, course_id, app.client.assignments(course_id))
    return gradestatus.check(app.client, app.store, course_id, rows, force=force, log=log)


def check_todo(app, term: str = "", log=None) -> dict:
    """The light To Do check: every stale course on screen, then the picker.

    Uses the saved course list; the Refresh button is what re-reads it. Only
    courses with an assignment list on this disk are checked: one never
    opened here has nothing on To Do to correct. One course that fails is
    named and skipped, as in refresh_todo.
    """
    log = log or (lambda *_a, **_k: None)
    root = Path(app.store.root)
    targets = [c for c in todo_courses([dict(c) for c in app.courses(False)], term)
               if (folders.under(root, safe_id(c["id"])) / "assignments.json").is_file()
               and gradestatus.is_stale(gradestatus.load(root, c["id"]))]
    failed = []
    checked = 0
    total = max(len(targets), 1)
    for i, course in enumerate(targets, 1):
        name = course.get("title") or course.get("name") or str(course.get("id"))
        log("Checking grading status in %s" % name, i - 1, total)
        try:
            checked += check_course(app, course["id"], force=False).get("checked", 0)
        except Exception as exc:  # noqa: BLE001
            failed.append({"course_id": course.get("id"), "name": name,
                           "error": _canvas_line(exc)})
    out = picker_payload(app, False)
    out["checked"] = checked
    out["courses_checked"] = len(targets) - len(failed)
    out["failed"] = failed
    log("Grading status checked", total, total)
    return out


def days_behind(due_at, today: date | None = None) -> int | None:
    """Calendar days from the due date to today, in this computer's time.

    None when the assignment has no due date. Zero means it is due today.
    A negative number is not due yet. An undated assignment is not behind.
    """
    text = str(due_at or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    due_day = when.astimezone().date()
    today = today or datetime.now().astimezone().date()
    return (today - due_day).days


def take_regrade(courses: list[dict], today: date | None = None) -> list[dict]:
    """Assignments with resubmissions to regrade, the longest-waiting first.

    `days_waiting` counts from the oldest resubmission, not the due date: the
    work was late on purpose. The course rows lose `regrade_work` here. No
    student is named.
    """
    rows = []
    for course in courses:
        work = course.pop("regrade_work", None) or []
        if course.get("excluded"):
            continue
        for item in work:
            if not isinstance(item, dict):
                continue
            try:
                n = int(item.get("resubmitted") or 0)
            except (TypeError, ValueError):
                continue
            if n <= 0:
                continue
            since = str(item.get("resubmitted_since") or "")
            rows.append({
                "course_id": course.get("id"),
                "course_title": course.get("title") or course.get("name") or "",
                "course_code": course.get("code") or "",
                "assignment_id": item.get("id"),
                "name": item.get("name") or "",
                "resubmitted": n,
                "resubmitted_since": since,
                "due_at": str(item.get("due_at") or ""),
                "days_waiting": max(days_behind(since, today) or 0, 0),
            })
    rows.sort(key=lambda r: (-r["days_waiting"], r["resubmitted_since"],
                             str(r.get("name") or "").lower()))
    return rows


def take_behind(courses: list[dict], limit: int | None = 5,
                today: date | None = None) -> list[dict]:
    """Assignments still to grade, the ones furthest past their due date first.

    A day is a calendar day on this computer, not a count of submissions.
    Work that is not yet due, or has no due date, is left off: it is not
    behind. The course rows lose `open_work` here. Pass limit=None for the
    full ranking; the home page keeps five for the term on screen.
    No student is named.
    """
    rows = []
    for course in courses:
        if course.get("excluded"):
            course.pop("open_work", None)
            continue
        for item in course.pop("open_work", None) or []:
            if not isinstance(item, dict):
                continue
            try:
                n = int(item.get("needs_grading") or 0)
            except (TypeError, ValueError):
                continue
            if n <= 0:
                continue
            due = str(item.get("due_at") or "")
            days = days_behind(due, today)
            if days is None or days < 1:
                continue
            rows.append({
                "course_id": course.get("id"),
                "course_title": course.get("title") or course.get("name") or "",
                "course_code": course.get("code") or "",
                "assignment_id": item.get("id"),
                "name": item.get("name") or "",
                "needs_grading": n,
                "due_at": due,
                "days_behind": days,
            })

    def key(row: dict):
        return (-row["days_behind"], row.get("due_at") or "",
                str(row.get("name") or "").lower())

    rows.sort(key=key)
    if limit is None:
        return rows
    return rows[:limit]


def _iso(stamp: float) -> str | None:
    if not stamp:
        return None
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")


def _resume(courses: list[dict]) -> dict | None:
    """The course to offer to carry on with, or None on a first run.

    Chosen by when grading was last saved rather than by what the browser last
    opened, so it survives a new browser and never offers a course that was
    only glanced at. None is a real answer: there is nothing to carry on with
    before any work has been done, and a band saying so would be furniture.
    """
    done = [c for c in courses if c.get("worked_at") and not c.get("excluded")]
    if not done:
        return None
    best = max(done, key=lambda c: c["worked_at"])
    last = best.get("last_assignment") or {}
    return {
        "course_id": best.get("id"),
        "course_name": best.get("name", ""),
        "title": best.get("title") or best.get("name", ""),
        "code": best.get("code", ""),
        "term_label": best.get("term_label", ""),
        "assignment_id": last.get("id"),
        "assignment_name": last.get("name") or "",
        "waiting": best.get("waiting", 0),
        "assignments": best.get("assignments", 0),
        "to_grade": best.get("to_grade", 0),
        "graded": best.get("graded", 0),
        "writes_today": best.get("writes_today", 0),
        "known": True,
        "worked_at": best.get("worked_at"),
        "ago": _ago(best.get("worked_at")),
    }


# ------------------------------------------------------------------ the hub
def build_hub(app, cid) -> dict:
    cdir = Path(app.course_dir(cid))
    course = _course(app, cid)
    grade = _grade_status(app, cid, cdir)
    stale = bool(grade.pop("_stale", False))

    modules = {name: _module_status(app, name, cid) for name in AREAS}
    areas: dict[str, dict] = {"grade": grade}
    for card in CARDS[1:]:
        parts = [(name, modules[name]) for name in AREAS if CARD_OF.get(name) == card]
        areas[card] = _merge(card, parts, cid)
    # The raw per-package statuses too, so a sub-area's badge can be read by
    # its own name (the PDF tab asks for hub.pdf.badge).
    for name, status in modules.items():
        areas.setdefault(name, status)

    # The record is not an area -- it has no verbs of its own -- but it gets a
    # card, because a paper trail nobody can find is not a paper trail.
    try:
        from ..record.routes import hub_status as record_status
        areas["record"] = _normalise(record_status(app, cid))
    except Exception as exc:  # noqa: BLE001
        areas["record"] = _not_installed(f"{type(exc).__name__}: {exc}")

    try:
        from ..attendance.routes import hub_status as attendance_status
        areas["attendance"] = _normalise(attendance_status(app, cid))
    except Exception as exc:  # noqa: BLE001
        areas["attendance"] = _not_installed(f"{type(exc).__name__}: {exc}")

    rows = ledger.read(cdir, limit=8)
    needs = sorted({n for a in areas.values() for n in (a.get("needs") or []) if n})
    stats = [
        {"label": "Assignments to Grade", "value": grade.get("to_grade", 0)},
        {"label": "Waiting to grade", "value": grade.get("waiting", 0),
         "kind": "warn" if grade.get("waiting") else ""},
        {"label": "Graded here", "value": grade.get("graded", 0)},
        {"label": "Canvas writes today", "value": grade.get("writes_today", 0)},
    ]
    return {
        "course": course,
        "stats": stats,
        "areas": areas,
        "needs": needs,
        "ledger": rows,
        "stale": stale,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _course(app, cid) -> dict:
    for c in app.store.courses() or []:
        if str(c.get("id")) == str(cid):
            return {
                "id": c.get("id"), "name": c.get("name") or f"Course {cid}",
                "code": c.get("code"), "term_label": c.get("term_label") or c.get("term"),
                "students": c.get("students"),
            }
    return {"id": cid, "name": f"Course {cid}"}


# ----------------------------------------------------------------- grading
def _grade_status(app, cid, cdir: Path) -> dict:
    """The Grade card, from the cached assignment list and the draft files.
    Counts and timestamps only: nothing about a student leaves this function."""
    cache = cdir / "assignments.json"
    fig = figures(app, cid, cdir)
    assignments = fig["assignments"]
    waiting = fig["waiting"]
    graded = fig["graded"]
    last = fig["last_graded_at"]

    lines: list[str] = []
    if cache.is_file():
        lines.append(f"{len(assignments)} assignment{'s' if len(assignments) != 1 else ''} in the list"
                     + (f", {waiting} submission{'s' if waiting != 1 else ''} waiting to grade" if waiting else ""))
    else:
        lines.append("The assignment list has not been read from Canvas yet. "
                     "Open Grade to read it; nothing is written.")
    if graded:
        lines.append(f"{graded} graded here" + (f", last {_ago(last)}" if last else ""))
    else:
        lines.append("Nothing graded here yet. Grades are posted only when you confirm, and students can see them.")

    try:
        age = time.time() - cache.stat().st_mtime if cache.is_file() else None
    except OSError:
        age = None
    stale = (age is None or age > STALE_AFTER_S
             or (bool(assignments) and fig["status_stale"]))

    recent = sorted(assignments, key=lambda a: str(a.get("graded_at") or a.get("synced_at") or ""), reverse=True)
    return {
        "installed": True,
        "lines": lines,
        "badge": waiting or None,
        "badge_title": "submissions Canvas says are waiting to grade",
        "needs": [],
        "actions": [{"label": "Open assignments", "href": f"#/c/{cid}/grade"}],
        "count": fig["count"],
        "to_grade": fig["to_grade"],
        "waiting": waiting,
        "graded": graded,
        "writes_today": fig["writes_today"],
        "last_graded_at": last,
        "assignments": [
            {"id": a.get("id"), "name": a.get("name", ""), "graded_at": a.get("graded_at"),
             "synced_at": a.get("synced_at"), "needs_grading": a.get("needs_grading")}
            for a in recent[:200]
        ],
        "_stale": stale,
    }


# ------------------------------------------------------------------- areas
def _not_installed(detail: str = "") -> dict:
    return {"installed": False, "lines": ["Not installed"], "badge": None, "needs": [],
            "actions": [], **({"detail": detail} if detail else {})}


def _module_status(app, name: str, cid) -> dict:
    status = (getattr(app, "area_status", None) or {}).get(name) or {}
    if not status.get("ok"):
        return _not_installed(status.get("error", ""))
    try:
        module = importlib.import_module(f"courseforge.{name}.routes")
    except Exception as exc:  # noqa: BLE001
        return _not_installed(f"{type(exc).__name__}: {exc}")
    fn = getattr(module, "hub_status", None)
    if fn is None:
        return {"installed": True, "lines": [], "badge": None, "needs": [], "actions": []}
    try:
        raw = fn(app, cid) or {}
    except Exception as exc:  # noqa: BLE001
        return {"installed": True, "lines": [], "badge": None, "needs": [], "actions": [],
                "error": f"Status unavailable: {type(exc).__name__}: {exc}"}
    return _normalise(raw)


def _normalise(raw: dict) -> dict:
    lines = raw.get("lines") or []
    if isinstance(lines, str):
        lines = [lines]
    badge = raw.get("badge")
    if badge in ("", 0, "0"):
        badge = None
    out = {
        "installed": True,
        "lines": [str(x) for x in lines if x],
        "badge": badge,
        "needs": [str(x) for x in (raw.get("needs") or []) if x],
        "actions": [a for a in (raw.get("actions") or []) if isinstance(a, dict) and a.get("label")],
    }
    for key in ("badge_title", "error", "summary"):
        if raw.get(key):
            out[key] = raw[key]
    # Anything else the area reported rides along for its own tab to read.
    for key, value in raw.items():
        if key not in out and key not in ("lines", "badge", "needs", "actions"):
            out[key] = value
    return out


def _merge(card: str, parts: list[tuple[str, dict]], cid) -> dict:
    installed = [(n, p) for n, p in parts if p.get("installed")]
    if not installed:
        return _not_installed()
    lines: list[str] = []
    needs: list[str] = []
    actions: list[dict] = []
    badge_total = 0
    badge_seen = False
    error = None
    for _name, p in installed:
        lines.extend(p.get("lines") or [])
        for n in p.get("needs") or []:
            if n not in needs:
                needs.append(n)
        actions.extend(p.get("actions") or [])
        b = p.get("badge")
        if isinstance(b, (int, float)) and b:
            badge_total += int(b)
            badge_seen = True
        elif b not in (None, "", 0):
            badge_seen = True
            badge_total += 1
        error = error or p.get("error")
    out = {
        "installed": True,
        "lines": lines,
        "badge": badge_total if badge_seen else None,
        "needs": needs,
        "actions": actions or [{"label": f"Open {card.capitalize() if card != 'a11y' else 'Accessibility'}",
                                "href": f"#/c/{cid}/{card}"}],
        "modules": {n: p for n, p in parts},
    }
    if error:
        out["error"] = error
    return out


# ------------------------------------------------------------------ helpers
def _ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    if when.tzinfo is None:
        when = when.astimezone()
    seconds = (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds()
    if seconds < 90:
        return "just now"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = round(minutes / 60)
    if hours < 36:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = round(hours / 24)
    return f"{days} day{'s' if days != 1 else ''} ago"
