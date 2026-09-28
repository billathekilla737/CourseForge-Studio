"""Letter grades and grade curves.

Two rules hold everywhere in this module, and the rest of the tool depends on
them:

1. **A curve never lowers a score.** Every kind is clamped so a student's curved
   score is at least what they earned. A "curve" that took points away from
   someone would be a grade change with no audit trail and no conversation, and
   the instructor almost never means it: they set a target mean below the current
   one by accident, or typed a negative boost. The plan reports how many students
   a curve would have lowered so the mistake is visible instead of silent.

2. **The earned score is never overwritten.** A curve is stored beside the scores
   as a set of deltas, so the original is always recoverable, the adjustment is
   always visible, and removing a curve is exact rather than approximate.
"""
from __future__ import annotations

import math

from . import latepolicy

# Cutoffs are the minimum percent for each letter, highest first. F is whatever
# falls below the lowest cutoff.
DEFAULT_SCALE = {"A": 90.0, "B": 80.0, "C": 70.0, "D": 60.0}
FAIL = "F"

KINDS = ("flat", "target_mean", "scale", "to_top", "sqrt", "floor")

# Entries the grader could not score: nothing was submitted, or what was
# submitted could not be read. They are not zeros.
UNSCORED_SOURCES = ("auto-skip", "no-submission")


def is_unscored_marker(entry: dict | None) -> bool:
    """True only when this entry is explicitly marked as having no score.

    Weaker than `not is_scored(...)`: it says nothing about a row that simply
    carries no total field. Use it where the input may be a bare set of scores
    rather than a full draft entry, so that a partial row is still analysed
    while a real non-submission is still excluded.
    """
    if not entry:
        return False
    if str(entry.get("source") or "") in UNSCORED_SOURCES:
        return True
    return "total" in entry and entry.get("total") is None


def is_scored(entry: dict | None) -> bool:
    """True when this entry carries a real score that belongs in statistics.

    A missing submission is not a zero. Counting it as one drags down every
    average, turns the student into an F in the distribution, and quietly
    rewards a curve aimed at the students who did the work. It is reported as a
    non-submission instead, and left out of the arithmetic.

    Older drafts stored a fabricated 0 for these, so the marker on the entry is
    checked as well as the total: that way existing data behaves correctly
    without rewriting anyone's grades underneath them.
    """
    if not entry:
        return False
    if entry.get("total") is None:
        return False
    return str(entry.get("source") or "") not in UNSCORED_SOURCES


# --------------------------------------------------------------- letter grades
def ladder(scale: dict | None = None) -> list[tuple[str, float]]:
    """The scale as (letter, cutoff) pairs, highest cutoff first."""
    items = [(str(k), float(v)) for k, v in (scale or DEFAULT_SCALE).items()]
    return sorted(items, key=lambda kv: kv[1], reverse=True)


def letter(percent: float | None, scale: dict | None = None) -> str:
    if percent is None:
        return ""
    for name, cutoff in ladder(scale):
        if percent >= cutoff:
            return name
    return FAIL


def distribution(percents: list[float], scale: dict | None = None) -> list[dict]:
    """Counts and shares per letter, highest first, including empty bands.

    Empty bands are kept: "no one got an A" is a finding, and a chart that drops
    the band hides it.
    """
    steps = ladder(scale)
    names = [name for name, _ in steps] + [FAIL]
    counts = {name: 0 for name in names}
    for value in percents:
        counts[letter(value, scale)] += 1
    total = len(percents)
    bands = []
    for index, name in enumerate(names):
        if name == FAIL:
            low, high = 0.0, (steps[-1][1] if steps else 0.0)
        else:
            low = steps[index][1]
            high = steps[index - 1][1] if index else None
        bands.append({
            "letter": name, "count": counts[name],
            "share": (counts[name] / total) if total else 0.0,
            "low": low, "high": high,
        })
    return bands


# ------------------------------------------------------------------- totals
def whole_points(value: float) -> int:
    """Nearest whole point. A half rounds up, which is how a gradebook does it.

    A number the instructor or Canvas already typed with a decimal is left
    alone by the caller. This is only for a score this app worked out.
    """
    return int(math.floor(float(value) + 0.5))


def criterion_max(rubric: list[dict], cid: str) -> float:
    for crit in rubric:
        if str(crit.get("id")) == str(cid):
            return float(crit.get("points") or 0)
    return 0.0


def earned_total(entry: dict, rubric: list[dict]) -> float:
    """The score the student earned, before any curve."""
    scores = entry.get("scores") or {}
    # A grade pulled from Canvas with no rubric breakdown (typed straight into
    # the gradebook) has a total and nothing under it. Summing an empty rubric
    # would turn that grade into a zero.
    if rubric and not entry.get("total_only"):
        return round(sum(float(scores.get(str(c.get("id"))) or 0) for c in rubric), 2)
    return round(float(entry.get("total") or 0), 2)


def final_total(entry: dict, rubric: list[dict], possible: float | None = None) -> float:
    """The score to show and to post: earned, minus a late dock, plus any curve.

    Per-criterion adjustments are capped at that criterion's points, and the
    whole thing at the assignment's points possible, so a curve cannot invent a
    score above full marks. A syllabus late penalty is taken off the earned
    score; the rubric cells themselves stay as graded.
    """
    curve = entry.get("curve") or {}
    by_criterion = curve.get("by_criterion") or {}
    flat = float(curve.get("flat") or 0)
    earned = earned_total(entry, rubric)
    if not curve:
        total = earned
    else:
        scores = entry.get("scores") or {}
        # A typed Canvas total with no rubric cells must not be rebuilt from zeros
        # plus the curve; that posts the delta as the grade.
        if rubric and not entry.get("total_only"):
            total = 0.0
            for crit in rubric:
                cid = str(crit.get("id"))
                top = float(crit.get("points") or 0)
                value = float(scores.get(cid) or 0) + float(by_criterion.get(cid) or 0)
                total += max(0.0, min(value, top))
        else:
            total = float(entry.get("total") or 0)
        total += flat
    total = max(0.0, total - latepolicy.deduction(entry, earned))
    if possible:
        total = min(total, float(possible))
    # A grade pulled straight from the gradebook keeps its decimal. Anything
    # this app graded or curved becomes a whole number.
    if entry.get("total_only") and not curve:
        return round(total, 2)
    return float(whole_points(total))


def _stats(values: list[float], possible: float, scale: dict | None) -> dict:
    if not values:
        return {"n": 0, "mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0,
                "mean_pct": 0.0, "distribution": distribution([], scale)}
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / 2)
    mean = sum(ordered) / len(ordered)
    percents = [(100.0 * v / possible) if possible else 0.0 for v in ordered]
    return {
        "n": len(ordered),
        "mean": round(mean, 2), "median": round(median, 2),
        "min": round(ordered[0], 2), "max": round(ordered[-1], 2),
        "mean_pct": round((100.0 * mean / possible) if possible else 0.0, 1),
        "distribution": distribution(percents, scale),
    }


# --------------------------------------------------------------------- curves
def _curved(kind: str, value: float, top: float, *, amount: float,
            target: float | None, group_max: float, group_mean: float) -> float:
    """One student's curved value for a kind, before the no-lowering clamp."""
    if kind == "flat":
        return value + amount
    if kind == "scale":
        return value * (1.0 + amount / 100.0)
    if kind == "target_mean":
        goal = (target or 0) / 100.0 * top
        return value + (goal - group_mean)
    if kind == "to_top":
        return value + max(0.0, top - group_max)
    if kind == "sqrt":
        if top <= 0:
            return value
        return math.sqrt(max(0.0, value / top)) * top
    if kind == "floor":
        goal = (target or 0) / 100.0 * top
        return max(value, goal)
    raise ValueError(f"unknown curve kind {kind!r}")


def plan(kind: str, *, rubric: list[dict], entries: dict, possible: float,
         scope: str = "total", amount: float = 0.0, target: float | None = None,
         only: list[str] | None = None, scale: dict | None = None) -> dict:
    """Work out what a curve would do, without changing anything.

    `entries` maps user_id to draft entry. Only students with a score take part;
    an ungraded student has nothing to curve. Returns the per-student rows plus
    before/after statistics for the whole graded pool, so the instructor sees the
    effect on the distribution and not just on individuals.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown curve kind {kind!r}; expected one of "
                         f"{', '.join(KINDS)}")
    if kind in ("target_mean", "floor") and target is None:
        raise ValueError(f"the {kind} curve needs a target percent")

    picked = {str(u) for u in (only or [])}
    graded = {uid: e for uid, e in (entries or {}).items() if is_scored(e)}
    taking_part = {uid: e for uid, e in graded.items()
                   if not picked or str(uid) in picked}

    by_criterion = scope != "total"
    top = criterion_max(rubric, scope) if by_criterion else float(possible)
    if by_criterion and top <= 0:
        raise ValueError(f"no rubric criterion with id {scope!r}")

    def base_of(entry: dict) -> float:
        """Where the student stands right now, curves included.

        A second curve has to build on the first. Measuring from the earned
        score instead would double-count the earlier one: a floor of 65% applied
        after a +8 criterion curve would hand out 65 - earned, landing the
        student well above the floor that was asked for.
        """
        existing = (entry.get("curve") or {}).get("by_criterion") or {}
        if by_criterion:
            cid = str(scope)
            value = (float((entry.get("scores") or {}).get(cid) or 0)
                     + float(existing.get(cid) or 0))
            return max(0.0, min(value, top))
        return final_total(entry, rubric, possible)

    values = [base_of(e) for e in taking_part.values()]
    group_max = max(values) if values else 0.0
    group_mean = (sum(values) / len(values)) if values else 0.0

    rows, lowered, capped = [], 0, 0
    for uid, entry in taking_part.items():
        before = base_of(entry)
        raw = _curved(kind, before, top, amount=amount, target=target,
                      group_max=group_max, group_mean=group_mean)
        after = min(max(raw, before), top)      # never lower, never above max
        after = float(whole_points(after))
        if after < before:
            after = before
        if raw < before:
            lowered += 1
        if raw > top:
            capped += 1
        rows.append({"user_id": str(uid), "before": round(before, 2),
                     "after": round(after, 2), "delta": round(after - before, 2)})

    deltas = {row["user_id"]: row["delta"] for row in rows}

    # Before/after on the whole graded pool, so a curve on a selection is shown
    # in the context of the class it is part of.
    before_totals, after_totals = [], []
    for uid, entry in graded.items():
        was = final_total(entry, rubric, possible)
        before_totals.append(was)
        delta = deltas.get(str(uid), 0.0)
        if not delta:
            after_totals.append(was)
            continue
        trial = dict(entry)
        trial["curve"] = _merged(entry.get("curve"), scope, delta)
        after_totals.append(final_total(trial, rubric, possible))

    changed = [r for r in rows if r["delta"]]
    return {
        "kind": kind, "scope": scope, "amount": amount, "target": target,
        "criterion_label": (next((c.get("label") for c in rubric
                                  if str(c.get("id")) == str(scope)), scope)
                            if by_criterion else "whole score"),
        "scope_max": round(top, 2),
        "rows": sorted(rows, key=lambda r: -r["delta"]),
        "n_selected": len(taking_part), "n_changed": len(changed),
        "n_lowered_blocked": lowered, "n_capped": capped,
        "biggest_gain": max((r["delta"] for r in rows), default=0.0),
        "mean_gain": round(sum(r["delta"] for r in rows) / len(rows), 2) if rows else 0.0,
        "ungraded_skipped": sorted(
            set(str(u) for u in picked) - {str(u) for u in taking_part}) if picked
            else [],
        "before": _stats(before_totals, possible, scale),
        "after": _stats(after_totals, possible, scale),
    }


def _merged(existing: dict | None, scope: str, delta: float) -> dict:
    """Add a delta onto an existing curve record. Curves stack."""
    out = {"flat": float((existing or {}).get("flat") or 0),
           "by_criterion": dict((existing or {}).get("by_criterion") or {}),
           "steps": list((existing or {}).get("steps") or [])}
    if scope == "total":
        out["flat"] = round(out["flat"] + delta, 4)
    else:
        key = str(scope)
        out["by_criterion"][key] = round(
            float(out["by_criterion"].get(key) or 0) + delta, 4)
    return out


def apply_to(entry: dict, scope: str, delta: float, note: dict) -> dict:
    """Return the curve record for one student after adding this step."""
    curve = _merged(entry.get("curve"), scope, delta)
    curve["steps"] = (curve.get("steps") or []) + [note]
    return curve
