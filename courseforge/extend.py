"""Deadline extensions: a student was ill, so their dates move and nobody else's.

A student is in hospital for a week, or loses a parent. What they need is not a
changed assignment -- the rest of the class is on time -- but a different due
date for themselves, on the handful of things that fell during the absence,
in every course they are in. Doing that by hand means opening each assignment
in each course, adding an override, copying the date, adding the days, and
remembering to move the lock date too or the new due date does nothing.

This module is the arithmetic and the judgement, with no Canvas I/O in it, so
it can be tested without a course. The server does the reading and writing.

Three things it is careful about.

**It extends from the date that actually applies to the student.** Canvas can
hold several dates for one assignment: the class date, a section's date, and a
per-student override from an earlier extension. The student is held to their
own override's date if they have one, and otherwise to the most lenient of the
rest, so that is the one +3 days is measured from. Starting from the class
date instead would quietly pull a student's deadline *backwards* when they
already had longer, which is the one outcome nobody would ever intend. And it
is that date, not the class's or a classmate's, that has to fall in the
absence for anything to move.

**It moves the lock date with the due date.** An assignment that locks on the
due date will still refuse the submission at the new one, so the extension
would be a date change that changes nothing. The lock moves by the same number
of days, keeping whatever gap the instructor set.

**It gives each student their own override.** Canvas allows a student into only
one ad-hoc override per assignment, so sharing one between students makes the
next extension for any of them a conflict. One override per student per
assignment costs an extra write and stays composable: a second extension for
the same person on the same assignment updates the override already there
instead of colliding with it.

What it will not do: touch an override it did not make that holds other
students as well. Rewriting that would move dates for people nobody selected.
Those rows are reported and left alone.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# A ceiling on the shift. Not a Canvas limit -- a typo guard. An extension
# longer than this is a withdrawal or an incomplete, and neither is a due date.
MAX_DAYS = 90
# A ceiling on one run, for the same reason: extending 900 dates in one press
# is not an accommodation, it is a rollover, and courseops does those.
MAX_ROWS = 400

# The title every override this tool creates carries. It is how a later run
# recognises its own work, so it must stay stable; the student's name is on the
# end so Canvas's own assignment page says who it is for.
TITLE_PREFIX = "Extension"


def title_for(name: str, user_id) -> str:
    clean = re.sub(r"\s+", " ", str(name or "")).strip()
    return f"{TITLE_PREFIX}: {clean or 'student ' + str(user_id)}"


def is_ours(title: str) -> bool:
    return str(title or "").strip().lower().startswith(TITLE_PREFIX.lower() + ":")


# ------------------------------------------------------------------- dates
def parse_iso(value) -> datetime | None:
    """Canvas hands back '2026-09-09T04:59:00Z'. Anything else is None."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        out = datetime.fromisoformat(text)
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def _zone(tz_name: str | None):
    """The course's timezone, or None meaning "use this machine's".

    Windows ships no IANA database, so `ZoneInfo("America/Chicago")` raises
    here unless the `tzdata` package is installed. The old version of this
    swallowed that and fell back to plain UTC arithmetic, which is wrong twice
    over: it puts an 11:59 pm deadline in the wrong calendar day when deciding
    what is inside the absence window, and it shifts the time by an hour across
    a daylight-saving boundary. Falling back to the machine's own zone is right
    whenever the instructor and the course are in the same one, which is the
    ordinary case, and `zone_note` says out loud when that is what happened.
    """
    if not tz_name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(tz_name))
    except Exception:  # noqa: BLE001  (no database, or a name Canvas made up)
        return None


def zone_note(tz_name: str | None) -> str:
    """Which zone the arithmetic actually used, for the page to show."""
    if tz_name and _zone(tz_name) is not None:
        return str(tz_name)
    local = datetime.now().astimezone().tzname() or "this computer's clock"
    if tz_name:
        return (f"{local} (this PC) — Canvas says the course is in {tz_name}, "
                f"but this machine has no timezone database to read it with. "
                f"Install the tzdata package to use the course's own zone.")
    return f"{local} (this PC)"


def _as_local(when: datetime, zone):
    return when.astimezone(zone) if zone is not None else when.astimezone()


def shift(value, days: int, tz_name: str | None = None) -> str | None:
    """Move an instant by whole days, keeping the wall-clock time.

    Done in the course's own timezone on purpose. A deadline of 11:59 pm that
    steps over the end of daylight saving is still meant to be 11:59 pm; adding
    72 hours to the UTC instant would make it 10:59 pm and start marking work
    late an hour early.
    """
    when = parse_iso(value)
    if when is None:
        return None
    zone = _zone(tz_name)
    naive = (_as_local(when, zone) + timedelta(days=int(days))).replace(tzinfo=None)
    # Re-attaching the zone to the moved wall-clock time is what resolves the
    # new offset; `astimezone()` on a naive value does the same against the
    # machine's zone, DST included.
    moved = naive.replace(tzinfo=zone) if zone is not None else naive.astimezone()
    return moved.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def touches_window(assignment: dict, overrides: list[dict], user_ids,
                   sections: dict, start, end, tz_name: str | None = None) -> bool:
    """True if the class date or any selected student's effective date is in the window.

    Only the cut that decides what is worth planning. Whose dates move is
    `plan`'s call, made per student from that student's own date: one
    student's date being in the window says nothing about another's. The class
    date still counts here so that a student whose own date has moved out of
    the absence is told so, instead of the assignment silently not appearing.
    """
    if day_in_window(assignment.get("due_at"), start, end, tz_name):
        return True
    for uid in user_ids:
        eff = effective(assignment, overrides, uid, sections.get(str(uid)) or [])
        if day_in_window(eff.get("due_at"), start, end, tz_name):
            return True
    return False


def day_in_window(value, start, end, tz_name: str | None = None) -> bool:
    """Is this instant's local calendar day inside [start, end]?

    Compared as days rather than instants because the window is typed as two
    dates. An 11:59 pm deadline on the last day of the absence is inside it,
    which comparing against midnight would get wrong.
    """
    when = parse_iso(value)
    if when is None:
        return False
    day = _as_local(when, _zone(tz_name)).date()
    if start and day < _day(start):
        return False
    if end and day > _day(end):
        return False
    return True


def _day(value):
    if hasattr(value, "year") and not hasattr(value, "hour"):
        return value
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value).strip())
    if not m:
        raise ValueError(f"{value!r} is not a date (yyyy-mm-dd)")
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()


def pretty(value, tz_name: str | None = None) -> str:
    """'Tue 9 Sep, 11:59 pm' -- for a plan somebody has to read and agree to."""
    when = parse_iso(value)
    if when is None:
        return "no date"
    local = _as_local(when, _zone(tz_name))
    hour = local.strftime("%I").lstrip("0") or "12"
    return (f"{local.strftime('%a')} {local.day} {local.strftime('%b')}, "
            f"{hour}:{local.strftime('%M %p').lower()}")


# ------------------------------------------------- which date the student sees
def applicable(overrides: list[dict], user_id, section_ids) -> list[dict]:
    """Every override that governs this student on this assignment."""
    uid = str(user_id)
    sections = {str(s) for s in (section_ids or [])}
    out = []
    for o in overrides or []:
        ids = {str(u) for u in (o.get("student_ids") or [])}
        if uid in ids:
            out.append({**o, "how": "adhoc"})
        elif o.get("course_section_id") is not None \
                and str(o.get("course_section_id")) in sections:
            out.append({**o, "how": "section"})
    return out


def _decides(mine: list[dict], field: str) -> dict | None:
    """The override whose `field` this student is held to; None means the
    assignment's own.

    Canvas settles each date on its own, from only the overrides that set it,
    and leaves the key off an override that does not change that date. That is
    not the same as sending it as null: null takes the date away. Of the ones
    that set it, the student's own override wins outright -- Canvas has
    preferred it to a section's since 2022, even when the section's is later --
    and among the rest no date at all beats every date, then the latest wins.
    """
    setting = [o for o in mine if field in o]
    for o in setting:
        if o.get("how") == "adhoc":
            return o
    if not setting:
        return None
    undated = [o for o in setting if parse_iso(o.get(field)) is None]
    return undated[0] if undated else max(setting, key=lambda o: parse_iso(o.get(field)))


def effective(assignment: dict, overrides: list[dict], user_id, section_ids) -> dict:
    """The due and lock dates this student is actually held to, and from where.

    Each date is settled separately, the way Canvas does it (`_decides`): an
    override that only moves the lock leaves the student on the class due
    date, and a null lock means no lock, not the class's.
    """
    mine = applicable(overrides, user_id, section_ids)
    # Canvas lets a student into one ad-hoc override per assignment, so if they
    # are in one, that is where a new date for them has to go, whether or not
    # it is what sets their due date. A second one would be refused.
    own = next((o for o in mine if o.get("how") == "adhoc"), None)
    if own is not None and own.get("unassign_item"):
        # Taken off this assignment in Canvas: nothing of theirs is due here,
        # and the override that says so is no place to write a date.
        return {"due_at": None, "lock_at": None, "source": "unassigned from them",
                "override_id": None, "override_title": own.get("title") or "",
                "shared_with": 0, "how": "adhoc"}
    due_by = _decides(mine, "due_at")
    lock_by = _decides(mine, "lock_at")
    ids = [str(u) for u in ((own or {}).get("student_ids") or [])]
    return {
        "due_at": due_by.get("due_at") if due_by else assignment.get("due_at"),
        "lock_at": lock_by.get("lock_at") if lock_by else assignment.get("lock_at"),
        "source": ("the class due date" if due_by is None
                   else "their section's date" if due_by.get("how") == "section"
                   else "an extension already on this assignment" if is_ours(due_by.get("title"))
                   else "a date already set just for them"),
        "override_id": own.get("id") if own else None,
        "override_title": (own or {}).get("title") or "",
        # How many OTHER students ride on that same override. Anything above
        # zero is why a row gets left alone rather than rewritten.
        "shared_with": max(0, len(ids) - 1),
        "how": due_by.get("how") if due_by else "everyone",
    }


# ------------------------------------------------------------------ the plan
def plan(students: list[dict], targets: list[dict], days: int,
         submitted: set | None = None, include_submitted: bool = False,
         start=None, end=None) -> dict:
    """Every date change this would make, without making any of them.

    `students` are {user_id, name}; `targets` are the assignments worth
    planning (see `touches_window`), each carrying its course, its overrides,
    the timezone to do the arithmetic in, and `enrolled` / `sections` for the
    course it is in. `start` and `end` are the absence: a student is planned on
    an assignment only where their own date there falls inside it (left out,
    any date does). `submitted` holds (assignment_id, user_id) pairs already
    turned in.
    """
    days = int(days)
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"{days} days is outside what this tool will move a "
                         f"deadline (1 to {MAX_DAYS}).")
    submitted = submitted or set()
    rows: list[dict] = []
    skipped: list[dict] = []

    for target in targets:
        tz_name = target.get("time_zone")
        overrides = target.get("overrides") or []
        enrolled = target.get("enrolled")
        sections = target.get("sections") or {}
        # Did the class's own date fall in the absence? Then a student whose
        # date is somewhere else is told why nothing of theirs moves. If not,
        # the assignment is only here for another student's date, and a line
        # about this one would say no more than one for every assignment
        # that was never in the window at all.
        class_in = day_in_window(target.get("due_at"), start, end, tz_name)
        for student in students:
            uid = str(student.get("user_id"))
            base = {
                "course_id": str(target.get("course_id")),
                "course_label": target.get("course_label") or "",
                "assignment_id": str(target.get("assignment_id")),
                "title": target.get("title") or "",
                "kind": target.get("kind") or "assignment",
                "html_url": target.get("html_url") or "",
                "user_id": uid,
                "name": student.get("name") or uid,
            }
            if enrolled is not None and uid not in enrolled:
                skipped.append({**base, "why": "not in this course", "quiet": True})
                continue

            now = effective(target, overrides, uid, sections.get(uid))
            if not now["due_at"]:
                skipped.append({**base, "why": "no due date applies to them here,"
                                               " so there is nothing to extend",
                                "quiet": not class_in})
                continue
            if not day_in_window(now["due_at"], start, end, tz_name):
                day = _as_local(parse_iso(now["due_at"]), _zone(tz_name)).date()
                side = "before" if start and day < _day(start) else "after"
                skipped.append({
                    **base, "why": f"their date here is {pretty(now['due_at'], tz_name)}, "
                                   f"{side} the absence, so it does not move",
                    "from_due": now["due_at"], "outside": True, "quiet": not class_in})
                continue
            if now["shared_with"]:
                # Their own override is the only place a new date could go, and
                # it carries other students -- whether or not it is what sets
                # their due date.
                held = ("their date here comes from an override" if now["how"] == "adhoc"
                        else "Canvas lets them into only one override here, and "
                             "they are already in one")
                skipped.append({
                    **base, "why": f"{held} shared with {now['shared_with']} other "
                                   f"student(s), \"{now['override_title']}\". Moving it "
                                   f"would move theirs too, so it is left alone.",
                    "from_due": now["due_at"], "blocked": True})
                continue

            already = (base["assignment_id"], uid) in submitted
            if already and not include_submitted:
                skipped.append({**base, "why": "already turned in",
                                "from_due": now["due_at"], "submitted": True})
                continue

            to_due = shift(now["due_at"], days, tz_name)
            to_lock = shift(now["lock_at"], days, tz_name) if now["lock_at"] else None
            # A lock that would still land before the new deadline makes the
            # extension a lie: Canvas stops accepting the work first.
            if to_lock and parse_iso(to_lock) < parse_iso(to_due):
                to_lock = to_due
            rows.append({
                **base,
                "from_due": now["due_at"], "to_due": to_due,
                "from_lock": now["lock_at"], "to_lock": to_lock,
                "source": now["source"],
                "override_id": now["override_id"],
                "action": "update" if now["override_id"] else "create",
                "submitted": already,
                "time_zone": tz_name,
            })

    if len(rows) > MAX_ROWS:
        raise ValueError(
            f"That would move {len(rows)} dates, past the {MAX_ROWS} this tool "
            f"will do at once. Narrow the window or the courses.")

    rows.sort(key=lambda r: (r["course_label"], parse_iso(r["from_due"]) or datetime.max
                             .replace(tzinfo=timezone.utc), r["name"]))
    skipped.sort(key=lambda r: (r["course_label"], r["title"], r["name"]))
    return {"rows": rows, "skipped": skipped, "days": days}


def describe(result: dict, days: int | None = None) -> str:
    """The one line somebody has to agree to before anything is written."""
    rows = result.get("rows") or []
    days = int(days if days is not None else result.get("days") or 0)
    if not rows:
        return "Nothing to move -- no dates in that window need changing."
    people = {r["user_id"] for r in rows}
    courses = {r["course_id"] for r in rows}
    names = sorted({r["name"] for r in rows})
    who = (names[0] if len(names) == 1
           else f"{len(people)} students" if len(names) > 3
           else ", ".join(names[:-1]) + " and " + names[-1])
    return (f"Give {who} {days} more day{'' if days == 1 else 's'} on "
            f"{len(rows)} due date{'' if len(rows) == 1 else 's'} across "
            f"{len(courses)} course{'' if len(courses) == 1 else 's'}. "
            f"Nobody else's dates change.")


def batches(rows: list[dict]) -> list[dict]:
    """One Canvas write per row: an override belongs to one student.

    Grouped only for the confirmation fingerprint and the record, so a plan
    that has drifted since it was shown cannot be applied against the old
    agreement.
    """
    return [{"course_id": r["course_id"], "assignment_id": r["assignment_id"],
             "user_id": r["user_id"], "to_due": r["to_due"], "to_lock": r["to_lock"],
             "action": r["action"], "override_id": r["override_id"]}
            for r in rows]
