"""Keep the local draft and the Canvas gradebook in step without losing work.

Two machines, one gradebook. A grade pushed from the home PC should be waiting
on the work PC the next time it looks, and a grade half-finished on the work PC
must never be overwritten by that look. So every entry remembers the score Canvas
held the last time this machine and Canvas agreed (``synced_score``), and a pull
is a three-way compare against that baseline:

    local == canvas                    in step: refresh the baseline
    local == baseline, canvas moved    the other machine graded it: take Canvas
    canvas == baseline, local moved    unpushed work here: keep it, say nothing
    both moved, or no baseline yet     conflict: record both sides, change nothing

Comparing against a baseline rather than against clocks means two machines with
drifting clocks, or a grade typed straight into the Canvas gradebook, still
resolve correctly. Nothing in this module writes to Canvas; the only writes are
to draft.json and extracted.json.
"""
from __future__ import annotations

from datetime import datetime

from . import curve, quizgrade
from .canvas import CanvasClient
from .config import Config
from .store import Store

# Local entries that carry no decision anyone would mind losing: a placeholder
# for a missing submission, or a failed grading run.
_PLACEHOLDER_SOURCES = ("auto-skip", "no-submission", "error")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _same(a, b) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < 0.005


# ------------------------------------------------------------------ reading
def canvas_grade_fields(sub: dict, me_id: str | int | None) -> dict:
    """The gradebook-side facts about one submission row, as stored on the
    extracted entry. `posted_at` is Canvas's own word for "visible to the
    student"; null with a score present means the grade is there but hidden."""
    rubric: dict[str, float] = {}
    for cid, cell in (sub.get("rubric_assessment") or {}).items():
        if isinstance(cell, dict) and cell.get("points") is not None:
            rubric[str(cid)] = float(cell["points"])
    comment = ""
    if me_id is not None:
        mine = [c for c in (sub.get("submission_comments") or [])
                if str(c.get("author_id") or "") == str(me_id)
                and str(c.get("comment") or "").strip()]
        mine.sort(key=lambda c: c.get("created_at") or "")
        if mine:
            comment = str(mine[-1]["comment"]).strip()
    # The score the instructor entered, not the one left after Canvas's own
    # late deduction. Comparing against the deducted score made a pull think
    # another machine had lowered the grade, and copy the cut back over the
    # instructor's score. Studio applies late penalties itself.
    entered = sub.get("entered_score")
    return {
        "canvas_score": entered if entered is not None else sub.get("score"),
        "canvas_shown_score": sub.get("score"),
        "canvas_points_deducted": sub.get("points_deducted"),
        "canvas_late_status": sub.get("late_policy_status"),
        "canvas_state": sub.get("workflow_state"),
        "canvas_graded_at": sub.get("graded_at"),
        "canvas_posted_at": sub.get("posted_at"),
        "canvas_grader_id": sub.get("grader_id"),
        "canvas_rubric": rubric,
        "canvas_comment": comment,
    }


# ------------------------------------------------------------------ merging
def _adopted(existing: dict | None, uid: str, info: dict, rubric: list[dict]) -> dict:
    """A draft entry built from what Canvas holds.

    The rubric breakdown comes along when Canvas has one that adds up to the
    score. A total typed straight into the gradebook has no breakdown, and
    inventing one would be worse than admitting it: such an entry is marked
    ``total_only`` and its total is trusted as-is until a slider is moved.
    """
    total = round(float(info["canvas_score"]), 2)
    ids = {str(c.get("id")) for c in rubric}
    cells = info.get("canvas_rubric") or {}
    scores = ({cid: pts for cid, pts in cells.items() if cid in ids} if ids else dict(cells))
    total_only = not scores or not _same(sum(scores.values()), total)
    if total_only:
        scores = {}
    now = _now()
    entry = {
        "user_id": uid,
        "source": "canvas",
        "scores": scores,
        "total": total,
        "total_only": total_only,
        "comment": info.get("canvas_comment") or "",
        "rationales": {},
        "flags": [],
        "needs_human": False,
        "needs_human_reason": "",
        "adopted_at": now,
        "canvas_graded_at": info.get("canvas_graded_at"),
        # This is the score Canvas holds and this machine now agrees with.
        "synced_score": total,
        "synced_at": now,
    }
    if existing:
        # Claude's draft stays visible underneath ("Claude proposed 34") even
        # though the instructor's own grade from the other machine now stands.
        for key in ("ai", "model", "cost_usd", "rationales"):
            if existing.get(key) is not None:
                entry[key] = existing[key]
    return entry


def merge_canvas_grades(store: Store, course_id, assignment_id,
                        extracted: dict) -> dict:
    """Fold the Canvas grades in `extracted` into the draft. See module doc."""
    with store.lock:
        draft = store.draft(course_id, assignment_id)
        rubric = draft.get("rubric") or []
        possible = draft.get("points_possible")
        entries = draft.setdefault("students", {})
        out = {"adopted": [], "in_sync": [], "kept": [], "conflicts": [], "cleared": [],
               "unsubmitted": []}
        now = _now()

        for uid, info in extracted.items():
            uid = str(uid)
            cscore = info.get("canvas_score")
            local = entries.get(uid)

            if cscore is None:
                # Canvas holds nothing for this student. A conflict recorded
                # against a score that has since been removed is moot.
                if local and local.pop("conflict", None) is not None:
                    out["cleared"].append(uid)
                continue

            placeholder = (local is None or local.get("total") is None
                           or local.get("source") in _PLACEHOLDER_SOURCES)

            if placeholder and info.get("status") == "unsubmitted":
                # Canvas holds a score for a student who turned nothing in:
                # almost always the missing-submission policy's zero. This
                # tool keeps non-submissions out of averages unless asked
                # (see README), so the score is shown beside the student but
                # not adopted as a grade. A zero the instructor sets by hand
                # here is a scored entry and takes the normal path below.
                out["unsubmitted"].append(uid)
                continue

            if placeholder and info.get("quiz_needs_written"):
                # Canvas scored the multiple choice and left the essay open.
                # Adopting that partial score would look like the test was
                # finished, and the written answer would never be graded.
                out.setdefault("quiz_open", []).append(uid)
                continue

            if placeholder:
                entries[uid] = _adopted(local, uid, info, rubric)
                out["adopted"].append(uid)
                continue

            lfinal = curve.final_total(local, rubric, possible)
            if _same(lfinal, cscore):
                local["synced_score"] = round(float(cscore), 2)
                local["synced_at"] = info.get("canvas_graded_at") or now
                local.pop("conflict", None)
                out["in_sync"].append(uid)
                continue

            base = local.get("synced_score")
            local_moved = base is None or not _same(lfinal, base)
            canvas_moved = base is None or not _same(cscore, base)
            if canvas_moved and not local_moved:
                entries[uid] = _adopted(local, uid, info, rubric)
                out["adopted"].append(uid)
            elif local_moved and not canvas_moved:
                local.pop("conflict", None)
                out["kept"].append(uid)
            else:
                local["conflict"] = {
                    "canvas_score": cscore,
                    "canvas_rubric": info.get("canvas_rubric") or {},
                    "canvas_comment": info.get("canvas_comment") or "",
                    "canvas_graded_at": info.get("canvas_graded_at"),
                    "canvas_posted_at": info.get("canvas_posted_at"),
                    "local_score": lfinal,
                    "seen_at": now,
                }
                out["conflicts"].append(uid)

        draft["pulled_at"] = now
        store.save_draft(course_id, assignment_id, draft)
        return out


def resolve_conflicts(store: Store, course_id, assignment_id,
                      user_ids: list[str], choice: str) -> dict:
    """Settle recorded conflicts one way or the other.

    ``canvas``  the other side wins: the entry is rebuilt from what Canvas holds.
    ``mine``    this side wins: the local score stays, and Canvas's score becomes
                the acknowledged baseline, so the next pull is quiet and the next
                push overwrites it on purpose.
    """
    if choice not in ("canvas", "mine"):
        raise ValueError("choice must be 'canvas' or 'mine'")
    with store.lock:
        draft = store.draft(course_id, assignment_id)
        rubric = draft.get("rubric") or []
        entries = draft.setdefault("students", {})
        settled, untouched = [], []
        for uid in user_ids:
            uid = str(uid)
            local = entries.get(uid)
            conflict = (local or {}).get("conflict")
            if not conflict:
                untouched.append(uid)
                continue
            if choice == "canvas":
                entries[uid] = _adopted(local, uid, conflict, rubric)
            else:
                local["synced_score"] = round(float(conflict["canvas_score"]), 2)
                local["synced_at"] = _now()
                local.pop("conflict", None)
            settled.append(uid)
        store.save_draft(course_id, assignment_id, draft)
        return {"ok": True, "choice": choice, "settled": settled, "untouched": untouched}


# ------------------------------------------------------------------ pulling
def posting_state(extracted: dict) -> dict:
    """Who has a grade in Canvas, and who can already see it.

    ``live`` is the count the page shows. ``hidden`` is only the leftover
    from an older hold; new grades are posted so students can see them.
    """
    hidden, live = [], []
    for uid, info in extracted.items():
        if not isinstance(info, dict) or info.get("canvas_score") is None:
            continue
        (live if info.get("canvas_posted_at") else hidden).append(str(uid))
    return {"hidden": hidden, "live": live}


def _quiz_still_open(info: dict) -> bool:
    quiz = info.get("quiz") or {}
    return bool(info.get("quiz_needs_written")
                or info.get("status") == "pending_review"
                or info.get("canvas_state") == "pending_review"
                or quiz.get("workflow") == "pending_review")


def whole_grade_posted(info: dict, entry: dict | None = None,
                       rubric: list | None = None, possible=None) -> bool:
    """True when the score students can see is the finished grade.

    Posting the total through the gradebook does not clear a quiz's
    "pending review" flag. The flag only means the essays were not marked
    inside the quiz. If Canvas is showing the same total held here, or a
    total other than the automatic part, the assignment grade is posted.
    """
    score = info.get("canvas_score")
    if score is None or not info.get("canvas_posted_at"):
        return False
    if entry and curve.is_scored(entry):
        held = entry.get("final_total")
        if held is None:
            held = curve.final_total(entry, rubric or [], possible)
        return _same(held, score)
    if _quiz_still_open(info):
        auto = info.get("quiz_auto_score")
        return auto is not None and not _same(score, auto)
    return True


def assignment_is_graded(needs_grading, extracted, has_submissions=False,
                         students=None, rubric=None, possible=None,
                         graded_submissions_exist=False) -> bool:
    """True when every turned-in submission has a finished posted grade.

    A score on the automatic questions is not enough: a quiz still in
    pending review is not graded until the posted total is the whole grade.
    An assignment nobody has turned in is not graded either. Canvas's
    needs-grading count stays up after a gradebook post, so it is not the
    only signal.
    """
    people = [s for s in (extracted or {}).values() if isinstance(s, dict)]
    submitted = [s for s in people if str(s.get("status") or "") not in ("", "unsubmitted")]
    if not submitted:
        try:
            if int(needs_grading or 0) > 0:
                return False
        except (TypeError, ValueError):
            return False
        # Publisher quizzes are graded in an outside tool. Canvas records the
        # scores and sets graded_submissions_exist, but leaves
        # has_submitted_submissions false, so a list refresh never looked graded
        # until the assignment was opened and resynced.
        return bool(has_submissions or graded_submissions_exist)
    entries = students or {}
    for info in submitted:
        uid = str(info.get("user_id") or "")
        entry = entries.get(uid)
        recorded = (quizgrade.written_scores_recorded(entry, info)
                    and info.get("canvas_score") is not None
                    and not quizgrade.hold_for_review(entry, info))
        if whole_grade_posted(info, entry, rubric, possible) or recorded:
            continue
        if _quiz_still_open(info) or info.get("canvas_score") is None:
            return False
    return True


def pull_grades(cfg: Config, client: CanvasClient, store: Store,
                course_id, assignment_id, me_id: str | int | None) -> dict:
    """Refresh the Canvas side of every extracted entry and merge.

    One paged submissions call; no attachments are downloaded, so this is cheap
    enough to run on a timer while the workspace is open.
    """
    extracted = store.extracted(course_id, assignment_id)
    if not extracted:
        raise RuntimeError("Nothing synced for this assignment yet -- run Sync first.")
    for sub in client.submissions(course_id, assignment_id):
        uid = str(sub.get("user_id"))
        if uid in extracted:
            extracted[uid].update(canvas_grade_fields(sub, me_id))
    store.save_extracted(course_id, assignment_id, extracted)
    out = merge_canvas_grades(store, course_id, assignment_id, extracted)
    out.update(posting_state(extracted))
    out["pulled_at"] = _now()
    return out
