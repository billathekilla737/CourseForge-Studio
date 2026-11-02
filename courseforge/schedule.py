"""A term-wide schedule of every dated assignment, with what is waiting to grade.

Shaping and classification only: fetching lives in server.App, and the grouping
into weeks happens in the browser.

**Weeks are grouped client-side on purpose.** Canvas returns due dates in UTC,
and a due date of 04:59Z is 11:59 PM the *previous* day in Central. Grouping
those into weeks server-side would file a Sunday-night deadline under the wrong
week, which is exactly the mistake this view exists to prevent. The browser knows
its own timezone, so it does the bucketing.

**Type is read from the name, not from Canvas's own fields.** In real courses
`is_quiz_assignment` is False for "Unit 1 Test" (delivered through an external
tool) and True for "Test: Chapter 8 - Proctored Exam", so those fields say how an
item is delivered rather than what it is. The name is what the instructor and the
students actually go by.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# A short course code pulled out of a Canvas course name such as
# "202630 MAT 2323 003 Statistics (Online)". Canvas's own course_code field is
# frequently null on these, so the name is the reliable source.
CODE = re.compile(r"\b([A-Z]{2,4})\s*[- ]?\s*(\d{4})\b")

# Colours assigned by code so a course keeps the same badge across refreshes and
# across the whole page. Picked for contrast against white at 11px bold.
PALETTE = ["#C62828", "#1565C0", "#2E7D32", "#6A1B9A", "#E65100",
           "#00796B", "#4527A0", "#558B2F", "#AD1457", "#37474F"]

KIND_LABELS = {
    "final": "FINAL",
    "exam": "EXAM",
    "test": "TEST",
    "quiz": "QUIZ",
    "practice": "PRACTICE",
    "discussion": "DISCUSSION",
    "ungraded": "UNGRADED",
    "assignment": "ASSIGNMENT",
}

# Kinds that count toward "tests and exams left". A quiz is not an exam, and
# neither is a practice test: counting either inflates the number an instructor
# uses to plan proctoring.
EXAM_KINDS = ("final", "exam", "test")

_WORD = r"(?:^|[^a-z])"
# "Review" on its own names a topic, not a practice artifact: "Week 14 Quiz -
# Course Review" is a real graded quiz, and three of them were being written
# off as practice. Only an explicit practice word counts, and not when it is a
# series label on a graded exercise ("Practice 5 - Trigger Test Bench").
RE_PRACTICE = re.compile(r"\b(?:practice|prep|study guide|sample|mock)\b(?!\s*\d)",
                         re.I)
RE_FINAL = re.compile(r"\bfinal\b[^a-z]{0,12}(?:exam|assessment)\b|"
                      r"\b(?:exam|assessment)\b[^a-z]{0,12}\bfinal\b", re.I)
RE_MIDTERM = re.compile(r"\bmidterm\b", re.I)
# "Test" is also half of a compound noun. "Practice 5 - Trigger Test Bench" is
# an upload, not an assessment, and counting it would put it in the proctoring
# plan.
RE_TEST = re.compile(r"\btests?\b(?!\s*[- ]?\s*(?:bench|case|cases|suite|plan|driven|harness|bed))", re.I)
RE_EXAM = re.compile(r"\bexams?\b", re.I)
RE_QUIZ = re.compile(r"\bquiz(?:zes)?\b", re.I)
RE_PROCTORED = re.compile(r"\bproctor\w*\b", re.I)
# "Schedule Proctored Final Exam" is a task to book a seat, not the exam. These
# are uploads of a confirmation and appear six times across this instructor's
# courses; treating them as exams would double the proctored count.
RE_LOGISTICS = re.compile(r"^\s*(?:schedule|sign[ -]?up|register|book)\b", re.I)


def course_code(course: dict) -> str:
    """A short code for badges: "MAT 2323", falling back to the course name."""
    for field in (course.get("course_code"), course.get("name")):
        match = CODE.search(str(field or "").upper())
        if match:
            return f"{match.group(1)} {match.group(2)}"
    name = str(course.get("course_code") or course.get("name") or "").strip()
    return name[:14] or f"course {course.get('id')}"


# The registrar's prefix on a Canvas course name: a term code, the subject and
# catalogue number, then a section. "202630 IMT 1213 001 Game Theory and
# Mechanics" is four pieces of filing followed by the thing a person would
# actually call it.
RE_TERM_PREFIX = re.compile(r"^\s*\d{4,6}\s+")
RE_CODE_PREFIX = re.compile(r"^\s*[A-Z]{2,4}\s*-?\s*\d{3,4}\s+")
# A section is digits (001, 330) or a short all-caps tag (FOB). It is only
# looked for once a catalogue number has been removed, so "HC eSports" keeps its
# "HC" and a sandbox with no prefix at all is left alone.
RE_SECTION = re.compile(r"^(?:\d{2,4}|[A-Z]{2,4})\s+")
# A trailing "(Online)" is delivery, not the course. Only these words are
# removed, so a parenthetical that means something -- "(Lab)", "(Honors)" --
# stays on the name where its author put it.
RE_MODE_SUFFIX = re.compile(
    r"\s*\(\s*(?:online|web|hybrid|blended|remote|virtual|in[- ]?person|"
    r"face[- ]?to[- ]?face|f2f|a?synchronous)\s*\)\s*$", re.I)


def course_title(course: dict) -> str:
    """What a person would call this course: "Game Theory and Mechanics".

    The registrar's prefix and a trailing delivery mode both come off, so
    "202630 MAT 1313 002 College Algebra (Online)" reads "College Algebra".

    Two courses can end up with the same title -- this instructor teaches
    MAT 1313 and MAT 1314, both called College Algebra -- and that is accepted
    rather than papered over with the catalogue code. They keep separate badge
    colours, separate filter chips and separate counts, and the badge's tooltip
    carries the full Canvas name.
    """
    name = str(course.get("name") or "").strip()
    if not name:
        return course_code(course)

    rest = RE_TERM_PREFIX.sub("", name, count=1)
    trimmed = RE_CODE_PREFIX.sub("", rest, count=1)
    if trimmed != rest:
        # A section token only follows a catalogue number, and only strip it if
        # a real title survives: "IMT 2114 360 3D Game Engine I" must keep the
        # "3D", which is part of the name rather than a section.
        candidate = RE_SECTION.sub("", trimmed, count=1)
        if len(candidate.split()) >= 2:
            trimmed = candidate
    trimmed = RE_MODE_SUFFIX.sub("", trimmed)
    trimmed = trimmed.strip(" -\u2013\u2014:")
    return trimmed or name


def course_labels(courses: list[dict]) -> dict[str, str]:
    """course_id -> the name to show.

    Identity stays on the course id and the catalogue code everywhere else in
    the app, so two courses sharing a title still filter, colour and count
    separately; only the words on the badge are the same.
    """
    return {str(c.get("id")): course_title(c) for c in courses}


def colour_for(code: str, order: list[str]) -> str:
    """Stable colour per course code, by position in the course list."""
    try:
        return PALETTE[order.index(code) % len(PALETTE)]
    except ValueError:
        return PALETTE[abs(hash(code)) % len(PALETTE)]


def classify(assignment: dict) -> dict:
    """What kind of thing this is, from its name and how it is submitted."""
    name = str(assignment.get("name") or "")
    types = [str(t) for t in (assignment.get("submission_types") or [])]
    is_quiz = bool(assignment.get("is_quiz_assignment")) or "online_quiz" in types
    proctored = bool(RE_PROCTORED.search(name))

    if "discussion_topic" in types:
        kind = "discussion"
    elif "not_graded" in types:
        kind = "ungraded"
    elif RE_LOGISTICS.search(name):
        # Booking an exam is an errand with a due date, not an assessment.
        kind = "assignment"
    elif RE_PRACTICE.search(name) and (RE_TEST.search(name) or RE_EXAM.search(name)
                                       or RE_QUIZ.search(name)):
        kind = "practice"
    elif RE_FINAL.search(name):
        kind = "final"
    elif RE_MIDTERM.search(name) or RE_TEST.search(name):
        kind = "test"
    elif RE_EXAM.search(name):
        kind = "exam"
    elif RE_QUIZ.search(name) or is_quiz:
        kind = "quiz"
    else:
        kind = "assignment"

    return {"kind": kind, "kind_label": KIND_LABELS[kind],
            "exam": kind in EXAM_KINDS, "proctored": proctored}


def shape(course: dict, assignment: dict, base_url: str = "") -> dict | None:
    """One schedule row, or None when the item has no due date.

    An item with no due date has no place on a schedule; Canvas's own calendar
    leaves them out too, and the footer says so.
    """
    due = assignment.get("due_at")
    if not due:
        return None
    code = course_code(course)
    info = classify(assignment)
    url = assignment.get("html_url") or (
        f"{base_url.rstrip('/')}/courses/{course.get('id')}"
        f"/assignments/{assignment.get('id')}" if base_url else "")
    return {
        "course_id": str(course.get("id")),
        "course_code": code,
        "course_label": course_title(course),
        "course_name": course.get("name") or "",
        "assignment_id": str(assignment.get("id")),
        "name": assignment.get("name") or "(untitled)",
        "due_at": due,
        # The other two dates and the quiz id are here for the instruction
        # planner: "unlock my test today" needs to know what it is changing
        # from, and a classic quiz keeps its own copy of these dates.
        "unlock_at": assignment.get("unlock_at"),
        "lock_at": assignment.get("lock_at"),
        "quiz_id": assignment.get("quiz_id"),
        "points": assignment.get("points_possible"),
        "needs_grading": int(assignment.get("needs_grading_count") or 0),
        "published": bool(assignment.get("published", True)),
        "has_submissions": bool(assignment.get("has_submitted_submissions")),
        "url": url,
        **info,
    }


def _parse(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def stats(items: list[dict], now: datetime | None = None) -> dict:
    """The tiles across the top. Instants are compared in UTC, which is safe:
    "is this deadline behind me" does not depend on which timezone you name it
    in. Only the *display* of a date needs a timezone, and that is the browser's
    job.
    """
    now = now or datetime.now(timezone.utc)
    soon = now + timedelta(days=7)
    remaining = upcoming = exams_left = waiting = graded = 0
    for item in items:
        when = _parse(item.get("due_at"))
        past = bool(when and when < now)
        if not past:
            remaining += 1
            if when and when <= soon:
                upcoming += 1
            if item.get("exam"):
                exams_left += 1
        waiting += int(item.get("needs_grading") or 0)
        # Graded means something came in and nothing is left waiting. An item
        # nobody has submitted to yet is not graded, it is just not started.
        if item.get("has_submissions") and not int(item.get("needs_grading") or 0):
            graded += 1
    return {
        "total": len(items),
        "remaining": remaining,
        "next_7_days": upcoming,
        "exams_left": exams_left,
        "needs_grading": waiting,
        "graded": graded,
    }


def build(courses: list[dict], per_course: dict[str, list[dict]],
          base_url: str = "") -> dict:
    """Assemble the payload: the course legend, every dated item, and the tiles."""
    order = [course_code(c) for c in courses]
    labels = course_labels(courses)
    legend, items = [], []
    for course in courses:
        code = course_code(course)
        rows = [shape(course, a, base_url) for a in per_course.get(str(course.get("id")), [])]
        rows = [r for r in rows if r]
        for row in rows:
            row["colour"] = colour_for(code, order)
            row["course_label"] = labels.get(str(course.get("id")), code)
        items += rows
        legend.append({
            "course_id": str(course.get("id")),
            "code": code,
            "label": labels.get(str(course.get("id")), code),
            "name": course.get("name") or "",
            "colour": colour_for(code, order),
            "count": len(rows),
            "needs_grading": sum(r["needs_grading"] for r in rows),
        })
    items.sort(key=lambda r: (str(r.get("due_at")), r["course_code"], r["name"]))
    legend.sort(key=lambda c: c["label"].lower())
    return {"courses": legend, "items": items, "stats": stats(items)}
