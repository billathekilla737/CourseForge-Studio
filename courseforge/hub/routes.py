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

from .. import audit, gradesync, ledger
from ..areas import AREAS
from ..routing import HTTPError, route

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
    visit fills it in.
    """
    app = req.app
    refresh = req.flag("refresh")
    courses = [dict(c) for c in app.courses(refresh)]
    for course in courses:
        course.update(local_state(app, course.get("id")))
    return {
        "courses": courses,
        "terms": app.term_summary(False),
        "resume": _resume(courses),
        # Past-due work still to grade, most days first. The page keeps
        # five for the term on screen. Disk only, from the same pass as the counts.
        "behind": take_behind(courses, limit=None),
    }


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
        log("Reading the assignment list from Canvas (reads only)", 0, 2)
        rows = app.assignments(cid, refresh=True)
        log(f"{len(rows)} assignments in the list", 1, 2)
        out = build_hub(app, cid)
        log("Hub updated", 2, 2)
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
    for r in rows:
        if not isinstance(r, dict):
            continue
        n = int(r.get("needs_grading") or 0)
        if not n:
            continue
        aid = str(r.get("id") or "")
        extracted = app.store.read(cdir / aid / "extracted.json", {}) or {}
        draft = app.store.read(cdir / aid / "draft.json", {}) or {}
        if not isinstance(extracted, dict):
            extracted = {}
        if not isinstance(draft, dict):
            draft = {}
        if gradesync.assignment_is_graded(
                n, extracted, r.get("has_submissions"),
                draft.get("students"), draft.get("rubric"),
                draft.get("points_possible"),
                graded_submissions_exist=r.get("graded_submissions_exist")):
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
        children = [p for p in cdir.iterdir() if p.is_dir() and p.name.isdigit()]
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
    }


def local_state(app, course_id) -> dict:
    """What this machine already knows about one course. No Canvas, no model.

    The four figures are the same ones the course page shows in its band, so
    the home list and the open course cannot disagree. A course that has never
    been opened here is left alone: reading it would create the folder.
    """
    cdir = Path(app.store.root) / str(course_id)
    if not cdir.is_dir():
        return {"known": False, "waiting": 0, "assignments": 0, "to_grade": 0, "graded": 0,
                "writes_today": 0, "touched_at": None, "last_assignment": None}

    fig = figures(app, course_id, cdir)
    rows = fig["assignments"]
    names = {str(r.get("id")): r.get("name", "") for r in rows if isinstance(r, dict)}

    newest_draft, newest_aid = 0.0, None
    try:
        children = [p for p in cdir.iterdir() if p.is_dir() and p.name.isdigit()]
    except OSError:
        children = []
    for child in children:
        when = _mtime(child / "draft.json")
        if when > newest_draft:
            newest_draft, newest_aid = when, child.name

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
    }


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
    stale = age is None or age > STALE_AFTER_S

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
