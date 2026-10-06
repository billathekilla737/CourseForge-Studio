"""HTTP routes for deadline extensions.

    GET  /api/extend/students                 everyone you teach, deduplicated
    GET  /api/extend/courses                  your courses, for the picker
    POST /api/extend/plan   {...}             a job: what would move, and from where
    POST /api/extend/apply  {..., confirm}    a job: move it, one override each

Every call here reads the roster and writes per-student dates, so all of it goes
through the grading-scoped client; `canvas_policy` refuses these endpoints to
every other area. Nothing is written without the confirm token, and the token is
bound to the exact rows that were shown.
"""
from __future__ import annotations

import json

from .. import audit, extend, schedule, terms
from ..canvas import CanvasError
from ..routing import HTTPError, route

AREA = "extend"
# A run reads every assignment and every override in scope. Past this many
# courses it is not a student's absence any more, and the page should be asking
# a narrower question.
MAX_COURSES = 40


def install(app) -> None:  # noqa: ARG001  (routes register on import)
    return None


# ------------------------------------------------------------------ reading
@route("GET", "/api/extend/students", area=AREA)
def students(req):
    return req.app.taught_students(req.q("refresh") == "1")


@route("GET", "/api/extend/courses", area=AREA)
def courses(req):
    app = req.app
    rows = [c for c in app.courses(False) if not c.get("excluded")]
    summary = terms.summarize(rows)
    return {
        "courses": [{"course_id": str(c.get("id")),
                     "label": app._cached_course_label(str(c.get("id")))
                              or schedule.course_title(c) or str(c.get("id")),
                     "term": c.get("term_label") or ""}
                    for c in rows],
        "terms": summary.get("terms") or [],
        "default_term": summary.get("default") or "",
    }


def _args(req) -> dict:
    body = req.body or {}
    user_ids = [str(u) for u in (body.get("user_ids") or []) if str(u).strip()]
    if not user_ids:
        raise HTTPError(400, "Nobody is selected. Pick at least one student.")
    try:
        days = int(body.get("days") or 0)
    except (TypeError, ValueError):
        raise HTTPError(400, f"{body.get('days')!r} is not a number of days.") from None
    if not 1 <= days <= extend.MAX_DAYS:
        raise HTTPError(400, f"{days} days is outside what this tool will move a "
                             f"deadline (1 to {extend.MAX_DAYS}).")
    start, end = str(body.get("start") or ""), str(body.get("end") or "")
    if not start or not end:
        raise HTTPError(400, "An absence needs a first and a last day.")
    try:
        if extend._day(end) < extend._day(start):
            raise HTTPError(400, "The last day of the absence is before the first.")
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    return {
        "user_ids": user_ids, "days": days, "start": start, "end": end,
        "course_ids": [str(c) for c in (body.get("course_ids") or []) if str(c).strip()],
        "term": str(body.get("term") or ""),
        "include_submitted": bool(body.get("include_submitted")),
    }


# --------------------------------------------------------------- the gather
def _courses_in_scope(app, args) -> list[str]:
    """The chosen courses, or every course in the term being taught.

    Defaulting to the term rather than to everything the token can see is what
    keeps last year's shells and the sandboxes out of a student's extension.
    """
    if args["course_ids"]:
        return args["course_ids"][:MAX_COURSES]
    wanted = args["term"] or terms.summarize(app.courses(False)).get("default")
    return [str(c.get("id")) for c in app.courses(False)
            if not c.get("excluded")
            and (not wanted or c.get("term_label") == wanted)][:MAX_COURSES]


def _targets(app, args, log) -> tuple[list[dict], list[dict], set]:
    """Every assignment due inside the window, with the overrides on it.

    Also returns which of the selected students were actually found in a course
    that was in scope. A student can be perfectly real, and picked on purpose,
    and still appear in none of them -- their course sits in another term, or
    among the sandboxes -- and an empty result with no explanation reads as a
    broken tool rather than as the answer it is.
    """
    want = set(args["user_ids"])
    course_ids = _courses_in_scope(app, args)
    if not course_ids:
        raise HTTPError(400, "No courses in scope. Pick at least one.")
    out: list[dict] = []
    failed: list[dict] = []
    seen: set = set()
    total = len(course_ids)
    log(f"reading {total} course(s)", 0, total)

    for index, cid in enumerate(course_ids, start=1):
        label = app._cached_course_label(cid) or cid
        try:
            roster = app.client.students_with_sections(cid)
        except CanvasError as exc:
            failed.append({"course_id": cid, "course_label": label,
                           "error": str(exc)[:160]})
            log(f"{index}/{total} {label}: could not read the roster", index, total)
            continue
        enrolled, sections = set(), {}
        for person in roster:
            uid = str(person.get("id") or "")
            if not uid:
                continue
            enrolled.add(uid)
            sections[uid] = [str(e.get("course_section_id"))
                             for e in (person.get("enrollments") or [])
                             if e.get("course_section_id") is not None]
        # Nobody selected is in this course, so there is nothing to read here.
        here = want & enrolled
        if not here:
            log(f"{index}/{total} {label}: nobody selected is in it", index, total)
            continue
        seen |= here

        # The course's own timezone, asked for only once the course is known to
        # matter. The Studio's cached course list is a trimmed projection that
        # does not carry it, and a deadline worked out in the wrong zone is
        # wrong by a whole calendar day at 11:59 pm.
        try:
            tz_name = (app.client.course_detail(cid) or {}).get("time_zone") or ""
        except CanvasError:
            tz_name = ""
        try:
            assignments = app.client.assignments(cid)
        except CanvasError as exc:
            failed.append({"course_id": cid, "course_label": label,
                           "error": str(exc)[:160]})
            continue

        kept = 0
        for a in assignments:
            aid = str(a.get("id"))
            try:
                overrides = app.client.assignment_overrides(cid, aid)
            except CanvasError:
                overrides = []
            if not extend.touches_window(a, overrides, here, sections,
                                         args["start"], args["end"], tz_name):
                continue
            kept += 1
            out.append({
                "course_id": cid, "course_label": label, "time_zone": tz_name,
                "assignment_id": aid, "title": a.get("name") or "",
                "kind": _kind(a), "due_at": a.get("due_at"),
                "lock_at": a.get("lock_at"),
                "html_url": a.get("html_url") or "",
                "overrides": overrides, "enrolled": enrolled, "sections": sections,
            })
        log(f"{index}/{total} {label}: {kept} due in the window",
            index, total)
    return out, failed, seen


def _kind(assignment: dict) -> str:
    types = assignment.get("submission_types") or []
    if "online_quiz" in types:
        return "quiz"
    if "discussion_topic" in types:
        return "discussion"
    if "external_tool" in types:
        return "external"
    return "assignment"


def _submitted(app, targets, user_ids, log) -> set:
    """What the selected students have already turned in, one call per course."""
    by_course: dict[str, list[str]] = {}
    for t in targets:
        by_course.setdefault(t["course_id"], []).append(t["assignment_id"])
    found = set()
    for cid, aids in by_course.items():
        try:
            found |= app.client.submitted_pairs(cid, user_ids, aids)
        except CanvasError as exc:
            log(f"could not check what is already turned in for course {cid} ({exc})")
    return found


def _plan(app, args, log) -> dict:
    targets, failed, seen = _targets(app, args, log)
    everyone = {str(s["user_id"]): s for s in
                (app.taught_students(False).get("students") or [])}
    people = [{"user_id": uid, "name": (everyone.get(uid) or {}).get("name") or uid}
              for uid in args["user_ids"]]

    # Picked, but in none of the courses that were searched. Their own course
    # list comes back with them so the page can say where they actually are.
    missing = [{"user_id": p["user_id"], "name": p["name"],
                "courses": (everyone.get(p["user_id"]) or {}).get("courses") or []}
               for p in people if p["user_id"] not in seen]

    submitted = set()
    if targets:
        log("checking what is already turned in")
        submitted = _submitted(app, targets, args["user_ids"], log)

    try:
        # The window goes in again: a target was kept for anybody's date in
        # it, and each student is held to their own.
        result = extend.plan(people, targets, args["days"], submitted,
                             args["include_submitted"],
                             start=args["start"], end=args["end"])
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None

    result["summary"] = extend.describe(result, args["days"])
    result["failed"] = failed
    result["not_in_scope"] = missing
    # Which clock these dates were worked out on. Shown rather than assumed:
    # a window and a shift are both wrong by a day if the zone is.
    zone_names = sorted({t.get("time_zone") or "" for t in targets})
    result["zone"] = extend.zone_note(zone_names[0] if zone_names else "")
    result["zones_differ"] = len([z for z in zone_names if z]) > 1
    result["students"] = people
    result["window"] = {"start": args["start"], "end": args["end"]}
    result["courses_in_scope"] = len({t["course_id"] for t in targets})
    result["assignments_in_window"] = len({(t["course_id"], t["assignment_id"])
                                           for t in targets})
    return result


@route("POST", "/api/extend/plan", area=AREA)
def plan_(req):
    args = _args(req)

    def job(log):
        # No confirmation token is handed out here on purpose. The plan is a
        # screen with a tick box on every row, so the rows that end up being
        # written are not the rows that were planned. A token offered now would
        # be bound to the wrong set; apply asks for its own, against exactly
        # what survived the ticking.
        return _plan(req.app, args, log)
    return req.job("extend.plan", job)


# ---------------------------------------------------------------- the write
@route("POST", "/api/extend/apply", area=AREA)
def apply_(req):
    args = _args(req)
    body = req.body or {}
    # Which rows survived the ticking on the page. Keys are course/assignment/
    # student, never dates: the dates are recomputed here from what Canvas holds
    # right now, so a plan left open in a tab cannot write a stale date.
    keep = {str(k) for k in (body.get("keys") or [])} or None
    token = req.confirm

    def job(log):
        app = req.app
        result = _plan(app, args, log)
        rows = result["rows"]
        if keep is not None:
            rows = [r for r in rows if _key(r) in keep]
        if not rows:
            return {**result, "applied": [], "failed_writes": [],
                    "message": "Nothing was ticked, so nothing was written."}

        summary = extend.describe({"rows": rows}, args["days"])
        # The confirmation screen renders this as one "was -> becomes" line per
        # change, so the dates are read off the dialog rather than trusted.
        detail = json.dumps([
            {"label": f"{r['course_label']} · {r['title']} — {r['name']}",
             "from": extend.pretty(r["from_due"], r.get("time_zone")),
             "to": extend.pretty(r["to_due"], r.get("time_zone"))}
            for r in rows[:14]])
        app._gate("extend", extend.batches(rows), summary, token,
                  detail=detail, what="moving due dates for named students")

        applied, failures = [], []
        total = len(rows)
        log(f"writing {total} date(s)", 0, total)
        for index, row in enumerate(rows, start=1):
            line = f"{row['course_label']} - {row['title']} for {row['name']}"
            log(f"{index}/{total} {line}", index - 1, total)
            dates = {"due_at": row["to_due"]}
            if row["to_lock"]:
                dates["lock_at"] = row["to_lock"]
            try:
                if row["action"] == "update":
                    app.client.update_override(
                        row["course_id"], row["assignment_id"], row["override_id"],
                        dates=dates)
                else:
                    app.client.create_override(
                        row["course_id"], row["assignment_id"], [row["user_id"]],
                        extend.title_for(row["name"], row["user_id"]), dates=dates)
                applied.append(row)
                _record(app, args, row, ok=True)
            except (CanvasError, ValueError) as exc:
                why = f"{type(exc).__name__}: {exc}"[:220]
                failures.append({**{k: row[k] for k in
                                    ("course_label", "title", "name")},
                                 "error": why})
                _record(app, args, row, ok=False, error=why)
            log(f"{index}/{total} done", index, total)

        people = {r["user_id"] for r in applied}
        return {
            **result, "applied": applied, "failed_writes": failures,
            "message": (f"{len(applied)} due date(s) moved {args['days']} day(s) "
                        f"for {len(people)} student(s)"
                        + (f"; {len(failures)} refused by Canvas" if failures else "")),
        }
    return req.job("extend.apply", job)


def _key(row: dict) -> str:
    return f"{row['course_id']}:{row['assignment_id']}:{row['user_id']}"


def _record(app, args, row: dict, ok: bool, error: str = "") -> None:
    """One line per date moved, naming the student.

    A due date that moved for one person and not the rest of the class is
    exactly the kind of thing somebody asks about a year later -- either the
    student saying they were never given the extension, or a colleague asking
    why one grade was not late. Both readings need the same facts: who, which
    assignment, from what date to what date, and on whose say-so.
    """
    audit.record(
        app.course_dir(row["course_id"]), "extend",
        "extended" if ok else "failed",
        (f"Moved the due date on \"{row['title']}\" in {row['course_label']} "
         f"{args['days']} day(s) later for {row['name']}, from "
         f"{extend.pretty(row['from_due'], row.get('time_zone'))} to "
         f"{extend.pretty(row['to_due'], row.get('time_zone'))}. "
         f"Nobody else's date changed."
         if ok else
         f"Tried to move the due date on \"{row['title']}\" in "
         f"{row['course_label']} {args['days']} day(s) later for "
         f"{row['name']}; Canvas refused."),
        students=[audit.person(row["user_id"], row["name"],
                               was=row["from_due"], now=row["to_due"])],
        count=1, course_id=row["course_id"], result="ok" if ok else "failed",
        url=row.get("html_url") or None,
        detail={"assignment_id": row["assignment_id"], "days": args["days"],
                "absence": f"{args['start']} to {args['end']}",
                "measured_from": row.get("source"),
                "lock_from": row.get("from_lock"), "lock_to": row.get("to_lock"),
                "override": row["action"],
                **({"error": error} if error else {})})
