"""What the Studio knows about a student, with the student taken out of it.

The Assistant could not answer a question about a student, and the reason was
sound: it holds a Canvas client that `canvas_policy` refuses student endpoints
to, so a prompt injected into a course page can never turn into a read of the
gradebook. That guard is worth keeping.

But "it cannot reach Canvas's student endpoints" and "it cannot help you think
about a student" are different sentences, and only the first one had to be
true. Everything below is assembled here, on this machine, out of work the
Studio has already done -- the grading drafts, the accommodation list, the
record of what was pushed -- and handed over with the names removed. Claude
sees `Student-14` and a set of numbers. It never sees a roster, and no Canvas
request is made at all.

Two rules hold, and `assert_clean` enforces the second one rather than trusting
the first:

  * only a tag identifies a student here, in and out;
  * no real name, email, login or SIS id may appear anywhere in the payload,
    including inside free text somebody else wrote.

The second matters because rationales and comments are prose. They were
written from pseudonymised input, so they should already be clean, and they are
scrubbed again on the way out because "should already be" is not a property you
can rely on a year from now.
"""
from __future__ import annotations

from pathlib import Path

from . import audit, folders, identity, pseudonym


class NotOnThisRoster(KeyError):
    """The tag names nobody in this course."""


def _scrub(text: str, name: str = "") -> str:
    """Structured identifiers out, and the student's own name with them."""
    if not text:
        return ""
    out = str(text)
    for pattern, repl in pseudonym.PATTERNS:
        out = pattern.sub(repl, out)
    if name:
        out = pseudonym._strip_name(out, name)
    return out


def assert_clean(payload, names: list[str]) -> None:
    """Refuse to hand over anything with a real name still in it.

    A last gate rather than a comment saying it cannot happen. If this ever
    raises, the answer is to fix what put the name there -- not to relax it.
    """
    import json
    blob = json.dumps(payload, default=str)
    for name in names:
        for part in [name] + [p for p in str(name).split() if len(p) >= 4]:
            if part and part in blob:
                raise RuntimeError(
                    "A real name reached a pseudonymised payload and it was not "
                    "sent. This is a bug in courseforge/students.py.")


# ------------------------------------------------------------------ reading
def _drafts(app, course_id):
    """(assignment id, assignment name, draft) for everything graded here."""
    cdir = folders.under(Path(app.store.root), course_id)
    names = {str(a.get("id")): a.get("name", "")
             for a in (app.store.assignments(course_id) or [])}
    out = []
    try:
        children = folders.children(cdir)
    except OSError:
        return out
    for child in children:
        draft = app.store.draft(course_id, child.name) or {}
        if draft.get("students"):
            out.append((child.name,
                        draft.get("assignment_name") or names.get(child.name, "")
                        or ("assignment " + child.name),
                        draft))
    return out


def _accommodation(app, user_id):
    for student in (app.roster.load() or []):
        if str(student.user_id) == str(user_id):
            return student.label()
    return ""


def class_view(app, course_id) -> dict:
    """Every student in the course as a tag and a line of state.

    The shape of a class, without a class list: how many are graded here, who
    is flagged, who has an accommodation on record.
    """
    names = identity.for_course(app, course_id)
    drafts = _drafts(app, course_id)
    rows = []
    for tag, who in sorted(names.by_tag.items(),
                           key=lambda kv: int(kv[0].split("-")[1])):
        uid = who["user_id"]
        graded, flagged, total = 0, 0, []
        for _aid, _name, draft in drafts:
            entry = (draft.get("students") or {}).get(uid)
            if not entry:
                continue
            if entry.get("total") is not None:
                graded += 1
                if draft.get("points_possible"):
                    total.append(100.0 * float(entry["total"]) / float(draft["points_possible"]))
            if entry.get("needs_human"):
                flagged += 1
        rows.append({
            "tag": tag,
            "graded_here": graded,
            "flagged": flagged,
            "average_percent": round(sum(total) / len(total), 1) if total else None,
            "accommodation": _accommodation(app, uid),
        })
    out = {
        "course_id": str(course_id),
        "students": len(rows),
        "assignments_graded_here": len(drafts),
        "rows": rows,
        "note": "Tags only. The list of who is who never leaves this machine.",
    }
    assert_clean(out, [w.get("name", "") for w in names.by_tag.values()])
    return out


def student_view(app, course_id, tag: str) -> dict:
    """One student, by tag, from what this machine already has.

    Nothing here is fetched. If the Studio has not graded an assignment, that
    assignment simply is not in the answer, and the summary says so rather than
    implying the student did nothing.
    """
    names = identity.for_course(app, course_id)
    tag = str(tag or "").strip()
    who = names.by_tag.get(tag)
    if not who:
        raise NotOnThisRoster(
            "%s is not a student in this course. Tags here run %s-1 to %s-%d."
            % (tag or "(nothing)", identity.PREFIX, identity.PREFIX, len(names)))
    uid, real = who["user_id"], who.get("name", "")

    work, totals = [], []
    for aid, name, draft in _drafts(app, course_id):
        entry = (draft.get("students") or {}).get(uid)
        if not entry:
            continue
        possible = draft.get("points_possible")
        pct = (round(100.0 * float(entry["total"]) / float(possible), 1)
               if entry.get("total") is not None and possible else None)
        if pct is not None:
            totals.append(pct)
        work.append({
            "assignment_id": aid,
            "assignment": name,
            "score": entry.get("total"),
            "out_of": possible,
            "percent": pct,
            "where_it_came_from": entry.get("source") or "",
            "pushed_to_canvas": bool(entry.get("synced_at")),
            "needs_a_person": bool(entry.get("needs_human")),
            "why": _scrub(entry.get("needs_human_reason") or "", real),
            "flags": [_scrub(str(f), real) for f in (entry.get("flags") or [])],
            "comment": _scrub(entry.get("comment") or "", real),
            "rationales": {k: _scrub(str(v), real)
                           for k, v in (entry.get("rationales") or {}).items()},
        })

    # The record names students on purpose -- that is what it is for -- so its
    # sentences are scrubbed on the way here rather than trusted. Scrubbing
    # degrades; assert_clean below would simply refuse, and refusing to answer
    # is a worse outcome than answering without the name.
    cdir = Path(app.course_dir(course_id))
    record = [{"at": r.get("at"), "area": r.get("area"),
               "what": _scrub(r.get("sentence") or "", real)}
              for r in audit.read(cdir, limit=25, student=uid)]

    out = {
        "course_id": str(course_id),
        "tag": tag,
        "accommodation": _accommodation(app, uid),
        "graded_here": len(work),
        "average_percent": round(sum(totals) / len(totals), 1) if totals else None,
        "needs_a_person": sum(1 for w in work if w["needs_a_person"]),
        "work": work,
        "record": record,
        "note": ("Everything here was read from this computer. No Canvas request "
                 "was made and no name was used."),
    }
    if not work:
        out["summary"] = ("Nothing has been graded for %s in the Studio yet, so "
                          "there is nothing to say about their work. This does "
                          "not mean they have submitted nothing." % tag)
    assert_clean(out, [w.get("name", "") for w in names.by_tag.values()])
    return out
