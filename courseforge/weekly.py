"""One week of the term, read back as "what am I teaching, and what is tested".

Prompt and shaping only: the assignments are fetched by server.App and the
model is called through llm.run there, so everything here is testable without
Canvas or Claude.

**The browser picks the week.** Weeks are bucketed client-side because only the
browser knows its timezone (see schedule.py), so the page sends the items in the
week it drew, each with its due date already written out in local time. The
server never re-derives which week an item belongs to.

**The model gets words, not markup, and not all of them.** A term's assignment
descriptions run to hundreds of kilobytes of HTML. Each is reduced to plain text
and cut to a few hundred words; the name, the kind and the points carry most of
what a summary needs, and the description fills in the topic.

**Read-only.** Nothing here writes to Canvas, so no confirmation is asked.
"""
from __future__ import annotations

import re

# A week of five courses is rarely more than twenty dated items. Forty leaves
# room for a heavy week without letting one request fetch a whole term.
MAX_ITEMS = 40

# Per item. Enough for the topic and the instructions' first paragraph.
DESCRIPTION_CHARS = 1500

SYSTEM = """You summarize one week of a community college instructor's teaching
across all of their courses. The instructor reads this at the start of the week
to remember what each class is on.

Rules:
- Work only from the assignments you are given: their names, types, due dates,
  points and descriptions. Do not invent chapters, topics, readings or tests
  that are not in them. If a course's items say little about the topic, say so
  plainly rather than guessing.
- "Teaching" is the subject matter the week's work is about. "Testing" is only
  the items you were told are a test, exam, final or quiz; name each one with
  its due day.
- Plain sentences, written to the instructor ("you"). No emoji, no exclamation
  marks, no motivational lines, no headings inside the text.
- Keep it short: an overview of two or three sentences, then one or two
  sentences per course.

Reply with JSON only:
{"overview": "...",
 "courses": [{"course_id": "...", "teaching": "...",
              "testing": ["Unit 3 Test, due Wednesday", "..."]}]}
Use the course_id exactly as given. "testing" is an empty list when nothing in
that course is assessed this week."""

ASSESSED = ("FINAL", "EXAM", "TEST", "QUIZ")


def plain_text(html: str, limit: int = DESCRIPTION_CHARS) -> str:
    """The words of an assignment description, trimmed at a word boundary."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", str(html or ""), flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">"))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + " …"


def wanted(items: list[dict]) -> list[dict]:
    """The items the page sent, de-duplicated, in the order they were sent.

    Raises when there is nothing to summarize or more than one request should
    fetch; the message is what the page shows.
    """
    seen, out = set(), []
    for item in items or []:
        key = (str(item.get("course_id") or ""), str(item.get("assignment_id") or ""))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        out.append({"course_id": key[0], "assignment_id": key[1],
                    "due_local": str(item.get("due_local") or "")[:60]})
    if not out:
        raise RuntimeError("There is nothing dated in that week to summarize.")
    if len(out) > MAX_ITEMS:
        raise RuntimeError(
            f"That week has {len(out)} items, more than the {MAX_ITEMS} one "
            "summary reads. Turn some courses off with the chips at the top, "
            "then try again.")
    return out


def prompt(week_label: str, rows: list[dict]) -> str:
    """The user message: the week, then each course and its items."""
    courses: dict[str, dict] = {}
    for row in rows:
        entry = courses.setdefault(row["course_id"], {
            "label": row.get("course_label") or row.get("course_code") or "",
            "name": row.get("course_name") or "",
            "items": []})
        entry["items"].append(row)

    lines = [f"The week: {week_label or 'this week'}.",
             f"{len(courses)} course(s), {len(rows)} dated item(s).", ""]
    for course_id, entry in courses.items():
        lines.append(f"## Course {course_id}: {entry['label']}"
                     + (f" ({entry['name']})" if entry["name"]
                        and entry["name"] != entry["label"] else ""))
        for row in entry["items"]:
            points = row.get("points")
            facts = [row.get("kind_label") or "ASSIGNMENT"]
            if row.get("due_local"):
                facts.append(f"due {row['due_local']}")
            if points not in (None, ""):
                facts.append(f"{points:g} pts" if isinstance(points, (int, float))
                             else f"{points} pts")
            if row.get("proctored"):
                facts.append("proctored")
            lines.append(f"- {row.get('name') or '(untitled)'} [{', '.join(facts)}]")
            text = row.get("text") or ""
            lines.append(f"  Description: {text}" if text
                         else "  Description: (none written)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def shape(data, rows: list[dict], fallback_text: str = "") -> dict:
    """The page's payload, from whatever the model sent back.

    Every course in the week gets a card, in the order the week listed them,
    even if the model skipped one. Its tests come from the classification as
    well as from the model, so a test the model left out is still on the card.
    """
    data = data if isinstance(data, dict) else {}
    said = {str(c.get("course_id")): c for c in (data.get("courses") or [])
            if isinstance(c, dict)}
    cards, order = [], []
    for row in rows:
        if row["course_id"] not in order:
            order.append(row["course_id"])
    for course_id in order:
        mine = [r for r in rows if r["course_id"] == course_id]
        first = mine[0]
        reply = said.get(course_id, {})
        testing = [str(t) for t in (reply.get("testing") or []) if str(t).strip()]
        if not testing:
            testing = [f"{r.get('name')}" + (f", due {r['due_local']}"
                                             if r.get("due_local") else "")
                       for r in mine if r.get("kind_label") in ASSESSED]
        cards.append({
            "course_id": course_id,
            "course_label": first.get("course_label") or first.get("course_code") or "",
            "course_name": first.get("course_name") or "",
            "teaching": str(reply.get("teaching") or "").strip(),
            "testing": testing,
            "items": [{"assignment_id": r["assignment_id"], "name": r.get("name"),
                       "kind_label": r.get("kind_label"), "due_local": r.get("due_local"),
                       "points": r.get("points")} for r in mine],
        })
    overview = str(data.get("overview") or "").strip() or fallback_text.strip()
    return {"overview": overview, "courses": cards}
