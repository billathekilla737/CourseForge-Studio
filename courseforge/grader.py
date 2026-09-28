"""Sync an assignment from Canvas, then grade it with Claude against its rubric."""
from __future__ import annotations

import concurrent.futures
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote_plus

from . import (audit, blender, curve, gradesync, humanize, latepolicy, llm,
               nicknames, overlap, quizgrade, teaching)
from .canvas import CanvasClient
from .config import Config
from .extract import (ARCHIVE_EXT, Extracted, Submission, expand_archive,
                      extract_submission, html_to_text, rce_file_refs)
from .pseudonym import Pseudonymizer
from .store import Store
from .style import HUMANIZE_RULES

MAX_WORK_CHARS = 60_000        # keep one student's work well inside a single turn

# One sync of one assignment at a time. Opening an assignment syncs it, and
# so does the Re-sync button; the two used to run side by side, one writing a
# student's Word file while the other read it half-written, recorded it as
# "not a zip file", and graded that student from the one picture inside it.
# The second sync now waits for the first and then runs, which is cheap: files
# already on disk are not fetched again.
_SYNC_LOCKS: dict[str, threading.Lock] = {}
_SYNC_LOCKS_GUARD = threading.Lock()

# Attachments a student's work lives in. One of these that could not be read
# is a reason to stop, not a detail to grade around.
DOCUMENT_EXT = (".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xlsm", ".xls", ".pdf",
                ".odt", ".pages", ".rtf")


def sync_lock(course_id, assignment_id) -> threading.Lock:
    key = f"{course_id}:{assignment_id}"
    with _SYNC_LOCKS_GUARD:
        lock = _SYNC_LOCKS.get(key)
        if lock is None:
            lock = _SYNC_LOCKS[key] = threading.Lock()
        return lock


def unreadable_documents(entry: dict) -> list[str]:
    """The attachments in an extracted entry that hold a document but could
    not be turned into text: their labels, with the reason where one was kept."""
    out: list[str] = []
    for part in entry.get("parts") or []:
        if not isinstance(part, dict) or part.get("kind") not in ("error", "binary"):
            continue
        label = str(part.get("label") or "")
        name = label.split(" (")[0].strip().lower()
        if name.endswith(DOCUMENT_EXT) or label.startswith("download failed:"):
            note = str(part.get("note") or "").strip()
            out.append(f"{label} ({note})" if note else label)
    if not out:
        for label in entry.get("unreadable") or []:
            if str(label).lower().split(" (")[0].strip().endswith(DOCUMENT_EXT):
                out.append(str(label))
    return out


def _file_name(name: str) -> str:
    """A Canvas link often stores the filename still percent-encoded."""
    name = unquote_plus(name or "").strip()
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._")
    return name[:80]

# Appended to the prompt for the one retry a malformed reply gets. Models fail
# this way occasionally and at random: the same prompt that failed usually
# succeeds on the second ask, so a retry saves a student from being skipped.
RETRY_NUDGE = """

IMPORTANT: your previous reply could not be used. It was not valid JSON.
Return ONLY the JSON object described above: start with { and end with }.
No prose before it, no prose after it, no markdown fence, no trailing commas.
Keep each rationale under 300 characters so the reply finishes cleanly."""


class Progress(Protocol):
    """Job progress sink: a message, plus an optional done/total for a bar."""
    def __call__(self, message: str, done: int | None = None,
                 total: int | None = None) -> None: ...


def _noop(message: str, done: int | None = None, total: int | None = None) -> None:
    return None


def _item(progress: Progress, key: str, state: str, detail: str = "",
          finished: bool = False) -> None:
    """Report live per-unit status, if this sink accepts it.

    The web job sink does; a plain print-style progress function does not, and
    should not be spammed with an update twice a second.
    """
    sink = getattr(progress, "item", None)
    if not sink:
        return
    try:
        sink(key, state, detail, finished)
    except Exception:  # noqa: BLE001  progress must never break a grade
        pass


def _watching(progress: Progress) -> bool:
    return getattr(progress, "item", None) is not None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _brief(chars: int) -> str:
    return f"{round(chars / 1000, 1)}k chars" if chars >= 1000 else f"{chars} chars"


PHASE_WORDS = {"starting": "starting {model}",
               "requesting": "sent to {model}, waiting for the first token",
               "responding": "{model} is reading",
               "thinking": "{model} is thinking",
               "writing": "{model} is writing",
               "finishing": "finishing the reply",
               "parsing": "reading the reply"}


# Rough price order for the aliases the Claude CLI accepts. This exists only to
# decide whether the vision substitution below would be a downgrade, so an id it
# cannot place gets no opinion rather than a guess.
MODEL_COST = {"haiku": 1, "sonnet": 2, "opus": 3}


def _model_alias(name: str) -> str:
    """"claude-sonnet-5" and "sonnet" are the same tier for pricing purposes."""
    low = str(name or "").lower()
    return next((key for key in MODEL_COST if key in low), low)


def vision_model_for(cfg) -> str:
    """Which model reads a student whose work includes images.

    Opus vision costs several times more without judging form any better, so a
    cheaper model is substituted for it. That substitution only ever moves DOWN
    the price list: it exists to stop an expensive default being spent on
    pictures, not to overrule a deliberately cheaper choice. Written as a plain
    swap it did both, and picking haiku was silently upgraded to sonnet for
    every student with an image, which in a Blender course is all of them.
    """
    chosen = cfg.model
    vision = getattr(cfg, "vision_model", "") or chosen
    if vision == chosen:
        return chosen
    want = MODEL_COST.get(_model_alias(vision))
    have = MODEL_COST.get(_model_alias(chosen))
    if want is None or have is None:
        return chosen           # a pinned id with no known tier: respect the choice
    return vision if want < have else chosen


def _activity_sink(progress: Progress, key: str, model: str,
                   words: dict[str, str] | None = None,
                   extra: str = ""):
    """Build an on_activity callback that reports one long Claude turn's phase.

    Returns None when nobody is watching, which keeps the plain CLI path on the
    original non-streaming call.
    """
    if not _watching(progress):
        return None
    words = words or PHASE_WORDS

    def activity(info: dict) -> None:
        bits = []
        if info.get("thinking_tokens"):
            bits.append(f"{info['thinking_tokens']} thinking tokens")
        if info.get("chars"):
            bits.append(_brief(info["chars"]))
        if extra:
            bits.append(extra)
        state = words.get(info["phase"], info["phase"]).format(model=model)
        _item(progress, key, state, ", ".join(bits))

    return activity

SYSTEM_PROMPT = f"""You are grading college coursework for a community college instructor.

You are strict but fair, and you grade only what the rubric asks about. You never
inflate a score to be kind and never dock points for things the rubric does not
mention. You quote or point at specific evidence from the student's work for every
judgment. You never invent content the student did not write.

If the work is missing, unreadable, or too fragmentary to judge, say so and set
needs_human to true rather than guessing at a score.

{HUMANIZE_RULES}

Reply with a single JSON object and nothing else. No preamble, no code fence."""

ASK_SYSTEM = f"""You are helping a community college instructor examine one student's
submission. You are talking to the instructor, not the student.

Answer only from the submission text you are given. If the answer is not in there,
say so plainly instead of guessing. Quote the student's own words when it helps.
Keep it short unless the question needs length.

{HUMANIZE_RULES}

Write prose. No JSON."""


def _rubric_of(assignment: dict) -> list[dict]:
    """Normalize the Canvas rubric, or synthesize one if the assignment has none."""
    rubric = assignment.get("rubric") or []
    out: list[dict] = []
    for crit in rubric:
        ratings = [
            {"points": r.get("points"), "label": r.get("description", ""),
             "detail": html_to_text(r.get("long_description"))}
            for r in (crit.get("ratings") or [])
        ]
        out.append({
            "id": str(crit.get("id")),
            "label": crit.get("description", "") or "Criterion",
            "detail": html_to_text(crit.get("long_description")),
            "points": float(crit.get("points") or 0),
            "ratings": ratings,
        })
    if not out:
        out = [{
            "id": "_overall",
            "label": "Overall",
            "detail": "No Canvas rubric on this assignment; grade against the "
                      "assignment description and any custom instructions.",
            "points": float(assignment.get("points_possible") or 100),
            "ratings": [],
        }]
    return out


# --------------------------------------------------------------------- sync
def sync_assignment(cfg: Config, client: CanvasClient, store: Store,
                    course_id, assignment_id,
                    progress: Progress = _noop,
                    me_id: str | int | None = None) -> dict:
    """Pull everything needed to grade one assignment and cache it locally.

    `me_id` is this token's Canvas user id, used to tell the instructor's own
    submission comments apart from the students' when a grade is pulled back.

    One sync per assignment at a time (see _SYNC_LOCKS): a second one started
    while the first is running waits its turn instead of reading files the
    first is still writing.
    """
    lock = sync_lock(course_id, assignment_id)
    if not lock.acquire(blocking=False):
        progress("another sync of this assignment is running; waiting for it")
        lock.acquire()
    try:
        return _sync_assignment(cfg, client, store, course_id, assignment_id,
                                progress, me_id)
    finally:
        lock.release()


def _sync_assignment(cfg: Config, client: CanvasClient, store: Store,
                     course_id, assignment_id,
                     progress: Progress = _noop,
                     me_id: str | int | None = None) -> dict:
    adir = store.assignment_dir(course_id, assignment_id)

    progress("fetching assignment")
    assignment = client.assignment(course_id, assignment_id)
    store.write(adir / "assignment.json", assignment)

    progress("fetching roster")
    students = store.students(course_id)
    if not students:
        students = client.students(course_id)
        store.save_students(course_id, students)

    progress("fetching submissions")
    submissions = client.submissions(
        course_id, assignment_id,
        history=bool(assignment.get("quiz_id")))
    store.write(adir / "submissions.json", submissions)

    pseud = Pseudonymizer(students, enabled=cfg.pseudonymize)
    store.write(adir / "map.json", pseud.map_json())

    # Graded discussions carry their work in the topic, not the submission body.
    topic_id = (assignment.get("discussion_topic") or {}).get("id")
    discussion: dict = {}
    if topic_id:
        progress("fetching discussion entries")
        view = client.discussion_view(course_id, topic_id)
        store.write(adir / "discussion.json", view)
        discussion = _discussion_by_user(view, {str(s["id"]): s.get("name", "") for s in students})

    names = {str(s["id"]): s.get("name", "") for s in students}
    quiz_pack = None
    quiz_id = assignment.get("quiz_id")
    if quiz_id and quizgrade.is_quiz(assignment):
        progress("reading written questions on the quiz")
        try:
            quiz_pack = quizgrade.prepare(client, course_id, quiz_id, assignment)
        except Exception as exc:  # noqa: BLE001
            progress(f"  could not read the quiz questions: {exc}")
            quiz_pack = None
        if quiz_pack:
            n_written = len(quiz_pack["questions"])
            if n_written:
                progress(f"{n_written} written question(s); "
                         "multiple choice stays as Canvas scored it")
            else:
                progress("automatic quiz; Canvas already graded the questions")
    extracted: dict[str, dict] = {}
    for index, sub in enumerate(submissions, start=1):
        uid = str(sub.get("user_id"))
        name = (sub.get("user") or {}).get("name") or names.get(uid, "")
        progress(f"reading submission {index}/{len(submissions)}", index, len(submissions))

        files: list[Path] = []
        failed: list[str] = []
        # A student who never submitted has a roster row and nothing to fetch.
        # Downloading that row is what used to fill the sync log with errors.
        turned_in = bool(sub.get("submitted_at") or sub.get("attempt") or (sub.get("body") or "").strip()
                         or sub.get("attachments"))
        if turned_in:
            for att in (sub.get("attachments") or []):
                safe = _file_name(att.get("filename") or "") or "file"
                dest = adir / "files" / f"{uid}_{safe}"
                # A file on disk is trusted only when it is complete. Canvas
                # says how big it should be; an empty or short copy (a fetch
                # that died part way) is fetched again, never read as the work.
                have = dest.stat().st_size if dest.is_file() else 0
                want = int(att.get("size") or 0)
                incomplete = dest.is_file() and (have == 0 or (want and have < want))
                if incomplete:
                    progress(f"  {safe} on disk is incomplete ({have} of {want} bytes); fetching it again")
                if (not dest.exists() or incomplete) and att.get("url"):
                    try:
                        client.download(att["url"], dest)
                    except Exception as exc:  # noqa: BLE001
                        progress(f"  could not download {safe} — the rest of the assignment is still read")
                        failed.append(f"{safe} ({exc})")
                        continue
                if dest.exists():
                    files.append(dest)

            # Students often paste a screenshot or a Word file into the text
            # box instead of attaching it. Those live in the student's files
            # and never appear in sub["attachments"].
            for ref in rce_file_refs(sub.get("body"), cfg.base_url,
                                     getattr(cfg, "canvas_hosts", None)):
                safe = _file_name(ref["name"]) or f"file_{ref['file_id']}"
                dest = adir / "files" / f"{uid}_rce{ref['file_id']}_{safe}"
                if dest.exists():
                    files.append(dest)
                    continue
                try:
                    client.download(ref["url"], dest)
                    files.append(dest)
                except Exception as exc:  # noqa: BLE001
                    progress(f"  could not download embedded file {safe}")
                    failed.append(f"{safe} (embedded, {exc})")

        # A zipped folder of scripts is one attachment holding the whole
        # submission, so unpack it and grade the contents rather than reporting
        # "no text extractor for .zip".
        labels: dict[str, str] = {}
        for archive in [f for f in list(files) if f.suffix.lower() in ARCHIVE_EXT]:
            try:
                members = expand_archive(archive, adir / "files",
                                         prefix=f"{uid}_zip{archive.stem[:20]}_")
            except Exception as exc:  # noqa: BLE001
                progress(f"  WARNING could not expand {archive.name}: {exc}")
                failed.append(f"{archive.name} ({exc})")
                continue
            files.remove(archive)
            for member_path, rel in members:
                files.append(member_path)
                labels[str(member_path)] = f"{archive.stem}/{rel}"
            progress(f"  expanded {archive.name}: {len(members)} file(s)")

        parsed: Submission = extract_submission(sub.get("body"), files, labels)
        for part in parsed.parts:
            # Files are stored as "<uid>_<name>" to keep the folder flat; show
            # the student's own filename in the UI and in the prompt.
            if part.label.startswith(f"{uid}_"):
                part.label = part.label[len(uid) + 1:]
                part.label = re.sub(r"^rce\d+_", "", part.label)
        for note in failed:
            # Surface download failures as an unreadable part so the UI and the
            # grading prompt both know something is missing, rather than
            # silently grading an empty submission.
            parsed.add(Extracted(f"download failed: {note}", kind="error",
                                 note="attachment could not be fetched from Canvas"))
        # Canvas reports workflow_state "graded" for a student who never turned
        # anything in but already has a posted zero. Trust attempt/submitted_at
        # for whether work exists, and keep Canvas's own view alongside it.
        really_submitted = bool(sub.get("submitted_at")) or bool(sub.get("attempt"))
        if not really_submitted and quizgrade.was_submitted(sub, assignment):
            really_submitted = True
        entry = {
            "user_id": uid,
            "name": name,
            "pseudonym": pseud.tag(uid),
            "status": sub.get("workflow_state") if really_submitted else "unsubmitted",
            # canvas_score, canvas_posted_at, canvas_rubric and friends: the
            # gradebook side of this row, kept beside the submission so a pull
            # can tell what the other machine has already done.
            **gradesync.canvas_grade_fields(sub, me_id),
            "submitted_at": sub.get("submitted_at"),
            "late": bool(sub.get("late")),
            "seconds_late": sub.get("seconds_late") or 0,
            "attempt": sub.get("attempt"),
            "student_comments": [
                {"text": str(c.get("comment") or ""),
                 "at": c.get("created_at")}
                for c in (sub.get("submission_comments") or [])
                # Only the student's own remarks: the instructor's replies are
                # not feedback about the teaching.
                if str(c.get("author_id") or "") == str(uid)
                and str(c.get("comment") or "").strip()
            ],
            "filenames": [a.get("filename", "") for a in (sub.get("attachments") or [])],
            "parts": [
                {"label": p.label, "kind": p.kind, "note": p.note,
                 "words": p.words, "text": p.text, "path": p.path, "data": p.data}
                for p in parsed.parts
            ],
            "body_text": parsed.text,
            "text": parsed.text,
            "words": parsed.words,
            "unreadable": [p.label for p in parsed.unreadable],
            "images": [p.path for p in parsed.images],
            # Video is listed, never read. The UI plays it; grading it is the
            # instructor's job, and the auto-skip below says so by name.
            "videos": [p.label for p in parsed.videos],
            "sheets": [], "models": [],
            "_files_dir": str(adir / "files"),
        }
        if really_submitted and "external_tool" in (assignment.get("submission_types") or []):
            entry["external_tool"] = True
        if uid in discussion:
            entry["discussion"] = discussion[uid]
            entry["text"] = (entry["text"] + "\n\n" + _discussion_text(discussion[uid])).strip()
            entry["words"] = len(entry["text"].split())
        if quiz_pack and really_submitted:
            try:
                quizgrade.attach(entry, quiz_pack, sub)
            except Exception as exc:  # noqa: BLE001
                progress(f"  could not read written answers: {exc}")
        extracted[uid] = entry

    # Enrolled students with no submission row at all still need a card.
    for student in students:
        uid = str(student["id"])
        if uid in extracted:
            continue
        entry = {
            "user_id": uid, "name": student.get("name", ""), "pseudonym": pseud.tag(uid),
            "status": "unsubmitted", "submitted_at": None, "late": False,
            "filenames": [], "parts": [], "body_text": "", "text": "", "words": 0,
            "unreadable": [], "images": [], "videos": [],
            "sheets": [], "models": [],
            "_files_dir": str(adir / "files"),
        }
        if uid in discussion:
            entry["discussion"] = discussion[uid]
            entry["text"] = _discussion_text(discussion[uid])
            entry["words"] = len(entry["text"].split())
            entry["status"] = "submitted"
        extracted[uid] = entry

    # A finished Blender pass lives in this assignment's own folder, and the
    # loop above rebuilt every entry from Canvas without it. Put it back, or a
    # routine re-sync silently costs the instructor another few minutes of
    # rendering to recover what is already on disk.
    revived = blender.attach_cached(store, course_id, assignment_id, extracted)
    if revived:
        progress(f"reattached {_plural(revived, 'Blender result')} from an earlier pass")

    store.write(adir / "extracted.json", extracted)

    draft = store.draft(course_id, assignment_id)
    draft.update({
        "course_id": str(course_id),
        "assignment_id": str(assignment_id),
        "assignment_name": assignment.get("name", ""),
        "points_possible": assignment.get("points_possible"),
        "rubric": (quiz_pack["criteria"] if quiz_pack and quiz_pack.get("criteria")
                   else _rubric_of(assignment)),
        "quiz_id": str(quiz_id) if quiz_pack else None,
        "synced_at": datetime.now().isoformat(timespec="seconds"),
        "student_count": len(extracted),
    })
    draft.setdefault("students", {})
    store.save_draft(course_id, assignment_id, draft)

    # Grades already in Canvas -- pushed from another machine, or typed into
    # the gradebook -- come into the draft now, so the second PC picks up
    # where the first left off. Local work in progress is never overwritten;
    # a disagreement is recorded as a conflict for the instructor to settle.
    merged = gradesync.merge_canvas_grades(store, course_id, assignment_id, extracted)
    if merged["adopted"]:
        progress(f"pulled {_plural(len(merged['adopted']), 'grade')} already in Canvas")
    if merged["conflicts"]:
        progress(f"WARNING {_plural(len(merged['conflicts']), 'local grade')} disagree"
                 " with Canvas -- see the Conflicts filter")
    progress(f"synced {len(extracted)} students")
    return store.draft(course_id, assignment_id)


def _discussion_by_user(view: dict, names: dict[str, str]) -> dict[str, dict]:
    entries = [e for e in (view.get("view") or []) if e.get("user_id") and not e.get("deleted")]
    out: dict[str, dict] = {}

    def slot(uid) -> dict:
        return out.setdefault(str(uid), {"post": None, "replies": []})

    for entry in entries:
        text = html_to_text(entry.get("message"))
        slot(entry["user_id"])["post"] = {
            "text": text, "words": len(text.split()), "created_at": entry.get("created_at"),
        }
    for entry in entries:
        parent_id = str(entry.get("user_id") or "")
        parent_name = names.get(parent_id, "a classmate")
        parent_text = html_to_text(entry.get("message"))
        for reply in (entry.get("replies") or []):
            if not reply.get("user_id") or reply.get("deleted"):
                continue
            text = html_to_text(reply.get("message"))
            slot(reply["user_id"])["replies"].append({
                "to": parent_name,
                "to_id": parent_id,
                "to_excerpt": parent_text[:400],
                "text": text, "words": len(text.split()),
                "created_at": reply.get("created_at"),
            })
    return out


def _discussion_text(entry: dict, pseud: Pseudonymizer | None = None) -> str:
    """Discussion as one block. With `pseud`, names become tags for a model."""
    def _out(text: str, own_name: str = "") -> str:
        if pseud is None or not text:
            return text or ""
        return pseud.scrub(pseud.scrub_roster(text), own_name=own_name)

    chunks = []
    post = entry.get("post")
    if post:
        chunks.append(f"--- ORIGINAL POST ({post['words']} words) ---\n"
                      f"{_out(post['text'])}")
    else:
        chunks.append("--- ORIGINAL POST ---\n(none: this student never posted)")
    for reply in entry.get("replies") or []:
        if pseud is not None and reply.get("to_id"):
            to = pseud.tag(reply["to_id"])
        else:
            to = reply.get("to") or "a classmate"
        excerpt = _out(reply.get("to_excerpt") or "", own_name=reply.get("to") or "")
        body = _out(reply.get("text") or "")
        chunks.append(
            f"--- REPLY to {to} ({reply['words']} words) ---\n"
            f"[they were replying to: {excerpt}]\n{body}"
        )
    if not entry.get("replies"):
        chunks.append("--- REPLY ---\n(none: this student never replied to a classmate)")
    return "\n\n".join(chunks)


def _hide_nicks(cfg: Config, store: Store, course_id, text: str) -> str:
    """Legal names and nicknames out of instructor text before it is sent."""
    if not text or not cfg.pseudonymize:
        return text or ""
    pseud = Pseudonymizer(store.students(course_id) or [], enabled=True)
    return nicknames.redact(pseud.scrub_roster(text), pseud)


def _work_for_model(entry: dict, pseud: Pseudonymizer) -> str:
    """The copy of a submission that may leave the machine: tags, no PII."""
    work = entry.get("body_text")
    if work is None:
        work = entry.get("text") or ""
        if entry.get("discussion"):
            return pseud.scrub(pseud.scrub_roster(work),
                               own_name=entry.get("name") or "")
    if entry.get("discussion"):
        work = (work + "\n\n" + _discussion_text(entry["discussion"], pseud)).strip()
    return pseud.scrub(pseud.scrub_roster(work or ""),
                       own_name=entry.get("name") or "")


# ------------------------------------------------------------------ prompts
def build_prompt(assignment: dict, rubric: list[dict], entry: dict,
                 instructions: str, label: str,
                 pseud: Pseudonymizer | None = None) -> str:
    lines: list[str] = []
    lines.append(f"# Assignment: {assignment.get('name','(untitled)')}")
    lines.append(f"Points possible: {assignment.get('points_possible')}")
    quiz = entry.get("quiz") or {}
    if quiz.get("questions") and not quiz.get("written_included"):
        lines.append(
            f"This is a quiz. Canvas already scored the multiple-choice and "
            f"other automatic questions at {quiz.get('auto_score')} points. "
            "Score ONLY the written questions below. Do not award points for "
            "the automatic questions and do not fold them into these criteria."
        )
    lines.append("")
    lines.append("## Assignment description as students saw it")
    description = html_to_text(assignment.get("description")) or "(no description in Canvas)"
    lines.append(description[:14_000])
    lines.append("")

    lines.append("## Rubric — score each criterion")
    for crit in rubric:
        lines.append(f"\n### [{crit['id']}] {crit['label']}  (max {crit['points']})")
        if crit["detail"]:
            lines.append(crit["detail"])
        if crit["ratings"]:
            allowed = ", ".join(str(_num(r["points"])) for r in crit["ratings"] if r["points"] is not None)
            lines.append(f"Canvas rating tiers for this criterion: {allowed}")
            for rating in crit["ratings"]:
                if rating["label"] or rating["detail"]:
                    lines.append(f"  - {_num(rating['points'])}: {rating['label']} {rating['detail']}".rstrip())
    lines.append("")

    if instructions.strip():
        lines.append("## Instructor's custom grading instructions")
        lines.append(
            "These OVERRIDE the rubric wording wherever they conflict, and they "
            "govern what you SAY as much as what you score. Anything these tell "
            "you not to grade on is also something not to raise with the student: "
            "do not score it, do not mention it in the comment, do not suggest "
            "improving it. Excluding something from the rubric and then advising "
            "the student to fix it is the same mistake made twice.")
        noted = instructions.strip()
        if pseud is not None:
            noted = nicknames.redact(pseud.scrub_roster(noted), pseud)
        lines.append(noted)
        lines.append("")

    lines.append(f"## Student {label}")
    meta = [f"status: {entry.get('status')}"]
    if entry.get("submitted_at"):
        meta.append(f"submitted: {entry['submitted_at']}")
    if entry.get("late"):
        meta.append("LATE")
        meta.append(
            "Grade the work as if it were on time. Do not dock points for "
            "lateness; the Studio applies the syllabus late policy after you score.")
    meta.append(f"word count: {entry.get('words', 0)}")
    if entry.get("filenames"):
        files = list(entry["filenames"])
        if pseud is not None:
            files = [pseud.scrub(f, own_name=entry.get("name") or "") for f in files]
        meta.append("files: " + ", ".join(files))
    lines.append(" | ".join(meta))
    if entry.get("unreadable"):
        lines.append("NOTE - these parts could not be converted to text: "
                     + "; ".join(entry["unreadable"]))
    if entry.get("images") and len((entry.get("text") or "").split()) < 40:
        lines.append(
            "NOTE - the student's work is in the attached images: screenshots, "
            "photos, scanned PDF pages, or pictures pasted into a Word, "
            "PowerPoint or Excel file. Grade from what is visible in them. A "
            "short text extract is not the whole submission.")
    if entry.get("videos"):
        # A submission that is part write-up, part screen recording still gets
        # graded on the write-up. Without this the model scores the video it
        # never saw, from the filename.
        lines.append(
            "NOTE - this submission includes video you cannot watch: "
            + "; ".join(entry["videos"])
            + ". The instructor watches it separately. Judge only the text and "
            "images below, never the video's contents, and if the rubric turns "
            "on what the video shows, set needs_human true and say which "
            "criteria are waiting on it.")
    lines.append("")
    lines.append("## The student's work")
    work = (_work_for_model(entry, pseud) if pseud is not None
            else (entry.get("text") or ""))
    if len(work) > MAX_WORK_CHARS:
        work = work[:MAX_WORK_CHARS] + "\n\n[...truncated for length...]"
    lines.append(work if work.strip() else "(nothing submitted)")
    lines.append("")

    if entry.get("sheets"):
        lines.append("## About the attached contact sheet")
        lines.append(
            "One image is attached: a contact sheet of the student's 3D model, three "
            "across and two down, read left to right then top to bottom. The tiles are "
            "a 3/4 perspective view, front, right, top, and a wireframe pass.\n"
            "\n"
            "THESE RENDERS WERE PRODUCED BY THIS GRADING TOOL, NOT BY THE STUDENT. We "
            "positioned the camera and used a neutral solid-shading pass with no lights "
            "and no materials. The student did not choose the lighting, the background, "
            "the camera angles, the shading mode or the framing. Never comment on "
            "lighting, render quality, materials, composition or presentation based on "
            "these images, and never deduct points for them. Use the images only to "
            "judge form, proportion, silhouette, symmetry, completeness, and whether "
            "the model is what the assignment asked for.")
        if entry.get("images"):
            lines.append(
                "Any additional images are the student's own screenshots, which they "
                "did choose. Those are fair to comment on.")
        lines.append("")

    ids = ", ".join(f'"{c["id"]}"' for c in rubric)
    lines.append("## Reply format")
    lines.append("The rationale is for the instructor's eyes. The comment is read by "
                 "the student, so write it to them in second person, and keep it "
                 "short: a student reads two sentences and skims anything longer, "
                 "so a long comment is a wasted one. Nothing that belongs in the "
                 "rationale belongs in the comment as well. Work that earns every "
                 "point gets NO comment: return an empty string. Write the "
                 "rationales either way, since those are the instructor's record.")
    lines.append(
        "Return ONLY this JSON object:\n"
        "{\n"
        f'  "criteria": [ {{ "id": <one of {ids}>, "points": <number>, '
        '"rationale": "<2-3 sentences citing specific evidence from the work>" } ],\n'
        '  "comment": "<to the student, second person. TWO sentences. Three only if the work '
        'genuinely needs it, never four. Under 45 words total. Say what cost the '
        'points and the one thing to do differently next time. No opening praise, '
        'no summary of what they did, no sign-off. EMPTY STRING when the work earned '
        'every point: there is nothing to account for, and casting about for advice '
        'to give a perfect submission is what produces advice nobody asked for.>",\n'
        '  "flags": ["<short tags such as late, missing part 4, possible AI text, off-prompt>"],\n'
        '  "confidence": "high" | "medium" | "low",\n'
        '  "needs_human": <true if you could not fairly grade this. A blank, empty, or n/a answer scored 0 is a grade, not a hold>,\n'
        '  "needs_human_reason": "<why, or empty>"\n'
        "}\n"
        "Include every criterion exactly once. Points must not exceed the criterion max. "
        "Where the criterion lists Canvas rating tiers, prefer one of those exact values."
    )
    return "\n".join(lines)


def _num(value) -> str:
    try:
        f = float(value)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(value)


# ------------------------------------------------------------------ grading
def grade_one(cfg: Config, assignment: dict, rubric: list[dict], entry: dict,
              instructions: str, progress: Progress = _noop,
              students: list | None = None) -> dict:
    """Grade a single student. Returns a draft entry; never raises."""
    uid = entry["user_id"]
    label = (entry.get("pseudonym") if cfg.pseudonymize
             else nicknames.shown(entry.get("name") or "", uid)) or uid
    base = {"user_id": uid, "source": "claude",
            "graded_at": datetime.now().isoformat(timespec="seconds")}

    # A .blend-only or screenshot-only submission has no text but is still real
    # work, so "nothing to read" has to account for images too.
    has_work = (bool((entry.get("text") or "").strip())
                or bool(entry.get("sheets")) or bool(entry.get("images")))
    if entry.get("status") == "unsubmitted" or not has_work:
        videos = entry.get("videos") or []
        if entry.get("status") == "unsubmitted":
            reason = "no submission"
        elif videos:
            # Video is never sent to a model: it is the most expensive thing a
            # student can turn in and the cheapest thing to watch. Say that
            # plainly rather than reporting an empty submission.
            reason = f"{_plural(len(videos), 'video')} to watch - grade it yourself"
        else:
            reason = "submission has no readable text"
        _item(progress, label, "skipped", reason, finished=True)
        # No score, not a zero. A fabricated 0 here would show up as an F in the
        # distribution and pull down every mean and every rubric row, which
        # describes the students who did the work rather than the ones who did
        # not. Posting a zero is a policy decision for the instructor to make in
        # Canvas, not something to assume during grading.
        return {
            **base,
            "source": "auto-skip",
            "scores": {},
            "total": None,
            "unscored_reason": reason,
            "rationales": {},
            "comment": "",
            # The reason already names the video, so it is not repeated here.
            "flags": [reason] + ([f"{len(entry.get('images', []))} image(s) to read"]
                                 if entry.get("images") else []),
            "confidence": "low",
            "needs_human": True,
            "needs_human_reason": (reason if videos
                                   else reason + " - decide yourself whether it scores zero"),
        }

    # A document the sync could not read is not "no work": it is work the
    # model cannot see. Grading whatever else was there, typically the one
    # picture pulled out of the same file, produced a 28 out of 100 with high
    # confidence and four "missing part" flags for parts that were sitting in
    # the Word file. So: with little else to read there is no score at all,
    # only a plain reason; with real text beside the broken file the score is
    # drafted but held for a person (see below), and the push will not send it.
    broken = unreadable_documents(entry)
    if broken and len((entry.get("text") or "").split()) < 40:
        reason = ("could not read " + "; ".join(broken)
                  + " - re-sync the assignment, then re-grade this student")
        _item(progress, label, "skipped", reason[:80], finished=True)
        return {
            **base,
            "source": "auto-skip",
            "scores": {},
            "total": None,
            "unscored_reason": reason,
            "rationales": {},
            "comment": "",
            "flags": ["document could not be read"],
            "confidence": "low",
            "needs_human": True,
            "needs_human_reason": reason,
        }

    quiz = entry.get("quiz") or {}
    grade_written = bool(quiz.get("questions")) and not quiz.get("written_included")
    if grade_written:
        rubric = quizgrade.criteria(quiz["questions"])
    _item(progress, label, "reading the submission",
          _brief(len(entry.get("text") or "")))
    prompt = build_prompt(assignment, rubric, entry, instructions, label,
                          pseud=Pseudonymizer(students or [], enabled=cfg.pseudonymize))

    # Student screenshots always go. The Blender contact sheet is optional:
    # blend_vision is about that render, not about whether a PNG submission
    # gets read.
    images: list[Path] = []
    files_dir = Path(entry.get("_files_dir") or "")
    if getattr(cfg, "blend_vision", True):
        for name in (entry.get("sheets") or [])[:1]:
            if files_dir:
                images.append(files_dir / name)
    for path in (entry.get("images") or []):
        images.append(Path(path))
    cap = int(getattr(cfg, "max_images_per_student", 8) or 8)
    images = [p for p in images if p.is_file()][:cap]

    # Vision on opus is several times the price for no better judgment of whether
    # a chair looks like a chair. Never the other way round: see vision_model_for.
    model = vision_model_for(cfg) if images else cfg.model

    # One student is one long Claude turn. Report what it is doing while it runs,
    # otherwise a batch of one sits at 0% for a minute and looks dead.
    words = dict(PHASE_WORDS,
                 responding="{model} is reading the work",
                 writing="{model} is writing feedback")
    activity = _activity_sink(progress, label, model, words,
                              _plural(len(images), "image") if images else "")

    # A malformed reply gets one more ask before the student is given up on.
    # Keep a usable-but-repaired reply as the fallback while trying for a clean
    # one, so a salvaged grade is never thrown away for a worse second answer.
    best: tuple[llm.ClaudeResult, dict, bool] | None = None
    notes: list[str] = []
    spend = 0.0
    last: llm.ClaudeResult | None = None

    for attempt in (1, 2):
        try:
            result = llm.run(prompt if attempt == 1 else prompt + RETRY_NUDGE,
                                    model=model, timeout_s=cfg.claude_timeout_s,
                                    system=SYSTEM_PROMPT, images=images,
                                    on_activity=activity)
        except llm.NotLoggedIn:
            raise
        except Exception as exc:  # noqa: BLE001
            if best:
                break               # attempt 1 gave us something usable; keep it
            _item(progress, label, "failed", str(exc)[:80], finished=True)
            return {**base, "source": "error", "scores": {}, "total": None,
                    "rationales": {}, "comment": "", "flags": ["grading failed"],
                    "confidence": "low", "needs_human": True, "model": model,
                    "cost_usd": round(spend, 4),
                    "needs_human_reason": f"{type(exc).__name__}: {exc}"}

        last = result
        spend += result.cost_usd or 0.0
        data = result.data
        usable = isinstance(data, dict) and bool(data.get("criteria"))

        if usable and not result.repaired:
            best = (result, data, False)
            break
        if usable and best is None:
            best = (result, data, True)

        notes.append(f"attempt {attempt}: " + (
            "reply was malformed but partly salvageable" if usable
            else result.parse_error or "no JSON object in the reply"))
        if attempt == 1:
            _item(progress, label, "reply was malformed, asking once more",
                  result.parse_error[:60])

    if best is None:
        text = (last.text if last else "") or ""
        _item(progress, label, "failed", "no usable JSON after two tries",
              finished=True)
        return {**base, "source": "error", "scores": {}, "total": None,
                "rationales": {}, "comment": "", "flags": ["unparseable reply"],
                "confidence": "low", "needs_human": True, "model": model,
                "cost_usd": round(spend, 4),
                "needs_human_reason": "Claude did not return usable JSON on either "
                                      "of two tries. " + "; ".join(notes),
                "attempts": notes,
                "raw_len": len(text),
                "raw": text[:20_000]}

    result, data, repaired = best

    scores: dict[str, float] = {}
    rationales: dict[str, str] = {}
    by_id = {c["id"]: c for c in rubric}
    for item in data.get("criteria") or []:
        cid = str(item.get("id"))
        crit = by_id.get(cid)
        if not crit:
            continue
        try:
            points = float(item.get("points"))
        except (TypeError, ValueError):
            continue                # no number came back; counted as missing below
        scores[cid] = float(curve.whole_points(max(0.0, min(points, crit["points"]))))
        rationales[cid] = str(item.get("rationale") or "")

    # A criterion the reply never scored used to land on 0 with nothing said
    # about it, which reads in the UI exactly like a deliberate zero. Fill it
    # in, but say so: a missing score is a review item, not a grade.
    missing = [c["id"] for c in rubric if c["id"] not in scores]
    for cid in missing:
        scores[cid] = 0.0

    flags = [str(f) for f in (data.get("flags") or [])]
    needs_human = bool(data.get("needs_human"))
    reason = str(data.get("needs_human_reason") or "")

    if repaired:
        flags.append("reply was malformed and repaired")
        needs_human = True
        reason = ("Claude's reply was malformed and had to be repaired, so these "
                  "scores may be incomplete. Check every criterion. "
                  + "; ".join(notes) + (" " + reason if reason else ""))
    if missing:
        names = ", ".join(by_id[c]["label"] for c in missing if c in by_id)
        flags.append(f"no score returned for: {names}")
        needs_human = True
        reason = reason or (f"Claude returned no score for: {names}. "
                            "Those are showing 0 but were never actually graded.")
    if broken:
        # There was enough other text to draft a score, but part of the work
        # was never read. The draft stays; the push will not send it until a
        # person has looked, and the reason says what to do.
        flags.append("document could not be read")
        needs_human = True
        reason = ("Part of this submission could not be read: " + "; ".join(broken)
                  + ". Re-sync the assignment and re-grade before trusting this score."
                  + (" " + reason if reason else ""))

    total = float(curve.whole_points(sum(scores.values())))
    comment = str(data.get("comment") or "")
    # Full marks are told nothing. With no points to account for, a comment has
    # to find something to improve, and what it reaches for is whatever the
    # instructor excluded from the rubric: "rename your objects and apply scale"
    # on a submission that scored full marks. The prompt asks for this too;
    # enforcing it here means it does not rest on the model having complied.
    top = round(sum(float(c["points"] or 0) for c in rubric), 2)
    if rubric and top > 0 and total >= top - 1e-6:
        comment = ""
    if grade_written:
        folded = quizgrade.fold_auto(
            {"scores": scores, "total": total, "flags": flags}, quiz)
        scores = folded["scores"]
        total = float(curve.whole_points(folded["total"]))
        flags = folded["flags"]

    # The second pass. The prompt already carries the house style, but a model
    # that has just scored a rubric still lets tells through, so the comment
    # and every rationale go back through the humanizer skill before they are
    # stored. The tags are still in place here (S-001), so no name reaches the
    # editor either. A pass that fails keeps the original words: humanize.py
    # checks every rewrite for the same numbers, tags and quoted phrases and
    # never raises, so the score above cannot be lost to the editing.
    edited: dict = {}
    if humanize.enabled(cfg) and (comment.strip()
                                  or any(v.strip() for v in rationales.values())):
        _item(progress, label, f"{humanize.model_for(cfg)} is editing the feedback", "")
        fields = {"comment": comment}
        fields.update({f"rationale:{cid}": text for cid, text in rationales.items()})
        fields, edited = humanize.humanize_fields(fields, cfg, humanize.KIND_GRADE)
        comment = fields.get("comment", comment)
        rationales = {cid: fields.get(f"rationale:{cid}", text)
                      for cid, text in rationales.items()}
        spend += float(edited.get("cost_usd") or 0.0)

    _item(progress, label, "scored", f"{total} pts", finished=True)
    out = {
        **base,
        "scores": scores,
        "total": total,
        "rationales": rationales,
        "comment": comment,
        "flags": flags,
        "confidence": "low" if (repaired or missing)
                      else str(data.get("confidence") or "medium"),
        "needs_human": needs_human,
        "needs_human_reason": reason,
        "cost_usd": round(spend, 4),
        "model": model,
        "images_sent": len(images),
        "humanized": edited,
    }
    if grade_written:
        out["quiz_auto_score"] = quiz.get("auto_score")
        out["quiz_written_score"] = round(
            sum(v for k, v in scores.items() if k != "_quiz_auto"), 2)
        out["quiz_id"] = quiz.get("quiz_id")
        out["quiz_submission_id"] = quiz.get("submission_id")
        out["quiz_attempt"] = quiz.get("attempt") or 1
        out["quiz_question_scores"] = {
            q["id"]: scores.get(f"q{q['id']}")
            for q in (quiz.get("questions") or [])
        }
    if notes:
        out["attempts"] = notes
    if repaired:
        out["raw_len"] = len(result.text or "")
        out["raw"] = (result.text or "")[:20_000]
    return out


def ask_about(cfg: Config, store: Store, course_id, assignment_id, user_id: str,
              question: str, history: list[dict] | None = None,
              progress: Progress = _noop) -> dict:
    """Answer a free-form instructor question about one student's submission."""
    assignment = store.assignment(course_id, assignment_id)
    extracted = store.extracted(course_id, assignment_id)
    entry = extracted.get(str(user_id))
    if not entry:
        raise RuntimeError("That student is not in the synced data. Run Sync first.")

    draft = store.draft(course_id, assignment_id)
    graded = (draft.get("students") or {}).get(str(user_id)) or {}
    rubric = draft.get("rubric") or _rubric_of(assignment)
    label = entry.get("pseudonym") if cfg.pseudonymize else (entry.get("name") or user_id)
    screen = label if cfg.pseudonymize else (nicknames.shown(entry.get("name") or "", user_id) or label)

    lines = [f"# Assignment: {assignment.get('name','(untitled)')}",
             f"Points possible: {assignment.get('points_possible')}", ""]
    lines.append("## Rubric criteria")
    for crit in rubric:
        lines.append(f"- [{crit['id']}] {crit['label']} (max {crit['points']})")
    roster = store.students(course_id)
    pseud = Pseudonymizer(roster or [], enabled=cfg.pseudonymize)
    instructions = store.instructions(course_id, assignment_id)
    if instructions.strip():
        noted = instructions.strip()
        if cfg.pseudonymize:
            noted = nicknames.redact(pseud.scrub_roster(noted), pseud)
        lines += ["", "## Instructor's custom grading instructions", noted]

    lines += ["", f"## Student {label}",
              f"status: {entry.get('status')} | words: {entry.get('words', 0)}"
              + (" | LATE" if entry.get("late") else "")]
    if graded.get("total") is not None:
        got = ", ".join(f"{c['label']}: {(graded.get('scores') or {}).get(c['id'], 0)}/{c['points']}"
                        for c in rubric)
        lines.append(f"current draft score: {graded['total']} ({got})")
    if entry.get("unreadable"):
        lines.append("NOTE - not converted to text: " + "; ".join(entry["unreadable"]))

    work = _work_for_model(entry, pseud) or "(nothing submitted)"
    if len(work) > MAX_WORK_CHARS:
        work = work[:MAX_WORK_CHARS] + "\n\n[...truncated for length...]"
    lines += ["", "## The student's work", work, ""]

    for turn in (history or [])[-6:]:
        role = "Instructor asked" if turn.get("role") == "user" else "You answered"
        text = turn.get("text") or ""
        if cfg.pseudonymize:
            text = pseud.scrub_roster(text)
        lines.append(f"## {role}\n{text}")
    asked = question.strip()
    if cfg.pseudonymize:
        asked = nicknames.redact(pseud.scrub_roster(asked), pseud)
    lines += ["", "## The instructor's question", asked]

    result = llm.run("\n".join(lines), model=cfg.model,
                            timeout_s=cfg.claude_timeout_s, system=ASK_SYSTEM,
                            expect_json=False)
    answer, edited = result.text.strip(), {}
    if humanize.enabled(cfg):
        _item(progress, screen, f"{humanize.model_for(cfg)} is editing the answer", "")
        answer, edited = humanize.humanize_text(answer, cfg, humanize.KIND_ANSWER)
    _item(progress, screen, "done", "", finished=True)
    return {"answer": answer,
            "cost_usd": round(float(result.cost_usd or 0.0)
                              + float(edited.get("cost_usd") or 0.0), 4),
            "model": cfg.model, "humanized": edited,
            "asked_at": datetime.now().isoformat(timespec="seconds")}


CLASS_SYSTEM = f"""You are helping a community college instructor read a whole class's
performance on one assignment. You are talking to the instructor, not to students.

You get per-criterion statistics and a sample of the per-student notes written during
grading. Work from those. Do not invent students, quotes, or numbers.

Your job is to say what actually happened and what to do about it. Be specific about
which criterion cost the class the most and what the evidence suggests the cause was:
a misread instruction, a missing skill, an ambiguous prompt, or nothing wrong at all.
Where the fix belongs in the assignment wording rather than in the students, say so.

Structure it as four short paragraphs, no headings:
1. Where the class landed overall, in plain numbers.
2. The criterion that cost them most, and what the notes suggest went wrong.
3. Anything the class did genuinely well, if there is something.
4. What you would change: what to reteach, reword, or announce before the next one.

{HUMANIZE_RULES}

Write prose. No JSON, no bullet lists, no headings."""


def class_summary(cfg: Config, store: Store, course_id, assignment_id,
                  include_missing: bool = False,
                  progress: Progress = _noop,
                  only: list[str] | None = None) -> dict:
    """Ask Claude to read the class's aggregate performance and write it up.

    With `only`, the read covers just those students. The prompt says so, since
    "the class average" means something different across six students than
    across thirty.
    """
    assignment = store.assignment(course_id, assignment_id)
    extracted = store.extracted(course_id, assignment_id)
    draft = store.draft(course_id, assignment_id)
    rubric = draft.get("rubric") or _rubric_of(assignment)
    entries = draft.get("students") or {}

    picked = {str(u) for u in (only or [])}
    pool = []
    curved = 0
    for uid, entry in entries.items():
        info = extracted.get(uid, {})
        if picked and str(uid) not in picked:
            continue
        if not curve.is_scored(entry):
            continue
        if not include_missing and info.get("status") == "unsubmitted":
            continue
        if entry.get("curve"):
            curved += 1
        pool.append((uid, info, entry))
    if len(pool) < 3:
        raise RuntimeError("Not enough graded students yet to summarize the class. "
                           "Grade at least three, then try again.")

    possible = draft.get("points_possible") or 0
    # The curved score is the one the student will see, so it is the one the
    # written read should describe.
    totals = sorted(curve.final_total(e, rubric, possible) for _u, _i, e in pool)
    mean = sum(totals) / len(totals)
    median = (totals[len(totals) // 2] if len(totals) % 2
              else (totals[len(totals) // 2 - 1] + totals[len(totals) // 2]) / 2)

    enrolled = len(extracted)
    missing = sum(1 for i in extracted.values() if i.get("status") == "unsubmitted")
    submitted = enrolled - missing
    ungraded = submitted - len([1 for u, i, e in pool if i.get("status") != "unsubmitted"])

    lines = [f"# Assignment: {assignment.get('name','(untitled)')}",
             f"Points possible: {possible}",
             "",
             "## Scope of these numbers (read this before quoting any rate)",
             f"- {enrolled} students are enrolled in the section.",
             f"- {submitted} submitted something. {missing} submitted nothing.",
             f"- {len(pool)} are graded so far and are the basis of every statistic below."]
    if ungraded > 0:
        lines.append(f"- {ungraded} submitted but are NOT graded yet, so this is a partial "
                     "picture. Do not describe it as the whole class and do not compute "
                     "participation rates from the graded subset.")
    if include_missing:
        lines.append("- Students who submitted nothing are counted as zero in these numbers.")
    else:
        lines.append("- Students who submitted nothing are excluded from these numbers.")
    lines += ["",
              f"Class mean {mean:.1f}, median {median:.1f}, "
              f"low {totals[0]}, high {totals[-1]}", ""]

    lines.append("## Per-criterion results")
    stats = []
    for crit in rubric:
        vals = [float((e.get("scores") or {}).get(crit["id"]) or 0) for _u, _i, e in pool]
        cmean = sum(vals) / len(vals) if vals else 0
        pct = cmean / crit["points"] if crit["points"] else 0
        stats.append((pct, crit, cmean, min(vals or [0]), max(vals or [0])))
        lines.append(f"- {crit['label']}: mean {cmean:.1f} of {crit['points']} "
                     f"({pct*100:.0f}%), range {min(vals or [0]):.0f} to {max(vals or [0]):.0f}")
        if crit["detail"]:
            lines.append(f"    what it asks: {crit['detail'][:400]}")
    lines.append("")

    # Give it evidence for the two weakest criteria: the notes written while grading.
    stats.sort(key=lambda s: s[0])
    for _pct, crit, _m, _lo, _hi in stats[:2]:
        lines.append(f"## Grading notes for the weakest criterion: {crit['label']}")
        shown = 0
        for uid, info, entry in sorted(pool, key=lambda p: (p[2].get("scores") or {}).get(crit["id"], 0)):
            note = (entry.get("rationales") or {}).get(crit["id"])
            if not note:
                continue
            label = info.get("pseudonym") if cfg.pseudonymize else info.get("name", uid)
            score = (entry.get("scores") or {}).get(crit["id"])
            lines.append(f"- [{label}, scored {score}] {note[:420]}")
            shown += 1
            if shown >= 8:
                break
        lines.append("")

    instructions = store.instructions(course_id, assignment_id)
    if instructions.strip():
        lines += ["## Custom grading instructions that were in force",
                  _hide_nicks(cfg, store, course_id, instructions.strip()), ""]

    if picked:
        lines.append(
            f"NOTE: this is a selected group of {len(pool)} students, not the whole "
            "class. Do not call it the class. Write about this group, and do not "
            "generalise to students who are not in it.")
    if curved:
        lines.append(
            f"NOTE: {curved} of these scores carry a curve applied by the "
            "instructor, and the totals above are the curved ones. Do not "
            "describe a curve as student performance.")
    lines.append("Write the four-paragraph summary now.")
    result = llm.run("\n".join(lines), model=cfg.model,
                            timeout_s=cfg.claude_timeout_s, system=CLASS_SYSTEM,
                            expect_json=False)

    text = result.text.strip()
    # The second pass runs while the tags are still in place, so the editor
    # never sees a name either. A pass that fails keeps the words as written.
    edited: dict = {}
    if humanize.enabled(cfg):
        _item(progress, "class summary",
              f"{humanize.model_for(cfg)} is editing the summary", "")
        text, edited = humanize.humanize_text(text, cfg, humanize.KIND_SUMMARY)

    # Claude only ever saw pseudonyms. The instructor reading this wants names,
    # and the map is local, so swap them back here on the way out.
    if cfg.pseudonymize:
        for uid, info in extracted.items():
            tag, name = info.get("pseudonym"), info.get("name")
            if tag and name:
                # The name is text, not a replacement template: a backslash in
                # it would otherwise be read as an escape and raise at unmask.
                # The instructor reads the nickname spelling. The model saw the tag.
                name = nicknames.shown(name, uid)
                text = re.sub(rf"\b{re.escape(tag)}\b", lambda _m, n=name: n, text)

    summary = {
        "text": text,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": cfg.model,
        "n": len(pool),
        "include_missing": include_missing,
        "cost_usd": round(float(result.cost_usd or 0.0)
                          + float(edited.get("cost_usd") or 0.0), 4),
        "scope": "selection" if picked else "class",
        "humanized": edited,
    }
    _item(progress, "class summary", "done", "", finished=True)
    draft = store.draft(course_id, assignment_id)
    draft["class_summary"] = summary
    store.save_draft(course_id, assignment_id, draft)
    return summary


def grade_assignment(cfg: Config, store: Store, course_id, assignment_id,
                     only: list[str] | None = None,
                     progress: Progress = _noop,
                     late_policy: dict | None = None) -> dict:
    """Grade every student (or just `only`) and merge results into the draft."""
    adir = store.assignment_dir(course_id, assignment_id)
    assignment = store.assignment(course_id, assignment_id)
    extracted = store.extracted(course_id, assignment_id)
    draft = store.draft(course_id, assignment_id)
    rubric = draft.get("rubric") or _rubric_of(assignment)
    instructions = store.instructions(course_id, assignment_id)
    possible = draft.get("points_possible") or assignment.get("points_possible") or 0

    if not extracted:
        raise RuntimeError("Nothing synced for this assignment yet -- run Sync first.")

    targets = [uid for uid in extracted if not only or uid in set(only)]
    done = 0
    total = len(targets)
    # Emit the total up front so the bar appears immediately; the first student
    # can take half a minute and a dead dialog looks like a hang.
    workers = max(1, min(int(cfg.grading_concurrency), 8))
    lane = "one at a time" if workers == 1 else f"{workers} at a time"
    # Say up front if some students will be read by a different model, rather
    # than letting the per-student lines contradict this one.
    vision = vision_model_for(cfg)
    swap = f", {vision} for work with images" if vision != cfg.model else ""
    progress(f"grading {_plural(total, 'student')} with {cfg.model} ({lane}){swap}",
             0, total)
    if late_policy and late_policy.get("summary") and late_policy.get("kind") not in (None, "none"):
        progress(late_policy["summary"], 0, total)

    roster = store.students(course_id)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(grade_one, cfg, assignment, rubric, extracted[uid],
                        instructions, progress, roster): uid
            for uid in targets
        }
        for future in concurrent.futures.as_completed(futures):
            uid = futures[future]
            try:
                result = future.result()
            except llm.NotLoggedIn:
                for pending in futures:
                    pending.cancel()
                raise
            except Exception as exc:  # noqa: BLE001
                result = {"user_id": uid, "source": "error", "scores": {}, "total": None,
                          "needs_human": True, "needs_human_reason": str(exc)}
                info = extracted.get(uid, {})
                label = (info.get("pseudonym") if cfg.pseudonymize
                         else nicknames.shown(info.get("name"), uid)) or uid
                _item(progress, label, "failed", str(exc)[:80], finished=True)
            info = extracted.get(uid) or {}
            reason = latepolicy.waiver(instructions, info)
            if reason:
                result = dict(result)
                result["late_penalty"] = {
                    "applied": False, "waived": True, "points": 0,
                    "summary": reason,
                }
            elif late_policy:
                result = latepolicy.attach(result, info, late_policy, possible)
            # Write through the store, which re-reads under a lock. Holding a
            # local snapshot here let two overlapping jobs erase each other.
            store.put_student(course_id, assignment_id, uid, result)
            done += 1
            progress(f"graded {done}/{total}", done, total)

    draft = store.draft(course_id, assignment_id)
    entries = draft.get("students", {})
    spend = sum(float((e or {}).get("cost_usd") or 0) for e in entries.values())
    # A curve or late dock that survived the re-grade sits on top of a score
    # that just changed, so the posted total has to be worked out again.
    possible = draft.get("points_possible") or 0
    if late_policy:
        draft["late_policy"] = {
            "kind": late_policy.get("kind"),
            "percent": late_policy.get("percent"),
            "interval": late_policy.get("interval"),
            "grace_hours": late_policy.get("grace_hours"),
            "floor_percent": late_policy.get("floor_percent"),
            "max_days": late_policy.get("max_days"),
            "canvas_applies": bool(late_policy.get("canvas_applies")),
            "summary": late_policy.get("summary") or "",
            "source": late_policy.get("source") or "",
            "passage": (late_policy.get("passage") or "")[:1500],
        }
    for entry in entries.values():
        if entry and curve.is_scored(entry) and (
                entry.get("curve") or (entry.get("late_penalty") or {}).get("applied")):
            entry["final_total"] = curve.final_total(entry, rubric, possible)
    draft["last_graded_at"] = datetime.now().isoformat(timespec="seconds")
    draft["estimated_cost_usd"] = round(spend, 4)
    store.save_draft(course_id, assignment_id, draft)

    # The grade was proposed here, not at the push. A year from now the
    # question is which model read this student's work, against which rubric,
    # on what day -- and the push record cannot answer any of it.
    graded = [uid for uid in targets if (entries.get(uid) or {}).get("source") == "claude"]
    failed = [uid for uid in targets if (entries.get(uid) or {}).get("source") == "error"]
    models = sorted({str((entries.get(uid) or {}).get("model") or "")
                     for uid in targets} - {""})
    audit.record(
        store.course_dir(course_id), "grade", "drafted",
        'Claude proposed a grade and a comment for %d student(s) on "%s". '
        "Nothing was sent to Canvas." % (len(graded), assignment.get("name") or assignment_id),
        students=[audit.person(uid, (extracted.get(uid) or {}).get("name", ""),
                               score=(entries.get(uid) or {}).get("total"),
                               model=(entries.get(uid) or {}).get("model"))
                  for uid in targets],
        count=len(graded), course_id=course_id,
        result="failed" if failed and not graded else "ok",
        detail={"assignment_id": str(assignment_id),
                "models": models,
                "rubric": [c.get("label") for c in rubric],
                "points_possible": possible,
                "cost_usd": round(spend, 4),
                "flagged_for_review": sum(
                    1 for uid in graded if (entries.get(uid) or {}).get("needs_human")),
                "failed": len(failed),
                "regrade": bool(only)})
    progress("done", total, total)
    return draft


OVERLAP_SYSTEM = f"""You are helping an instructor triage textual overlap between two
student submissions. You are not deciding whether anyone cheated, and you must not
say that anyone did. Academic misconduct is a determination the instructor makes
through their institution's process, with the students in the room.

Your job is narrower and more useful: say what the overlap looks like, and give the
most likely innocent explanation alongside any reason for concern. Students overlap
legitimately all the time. They quote the same reading. They answer a prompt so
narrow there are only a few sensible sentences. They were told to collaborate and
then wrote up separately. They used the same tutorial, the same wiki page, the same
lecture slide.

Be concrete and quote the text. Never speculate about a student's character or
intent. Never recommend a penalty. If the overlap is unremarkable, say so plainly.

Write plainly, like an experienced colleague looking over a shoulder.

{HUMANIZE_RULES}"""


def overlap_check(cfg: Config, store: Store, course_id, assignment_id,
                  only: list[str] | None = None,
                  progress: Progress = _noop) -> dict:
    """Compare selected submissions for shared wording.

    Two stages. First a local, deterministic pass that finds the shared passages
    and subtracts what the whole group has in common. Then, only if something
    stands out, one Claude call to characterise the strongest pairs. The evidence
    is the passages themselves; the model call only helps read them.
    """
    assignment = store.assignment(course_id, assignment_id)
    extracted = store.extracted(course_id, assignment_id)
    picked = [str(u) for u in (only or [])] or list(extracted)

    texts: dict[str, str] = {}
    no_text: list[dict] = []
    for uid in picked:
        entry = extracted.get(str(uid))
        if not entry:
            continue
        prose = overlap.student_prose(entry)
        if prose.strip():
            texts[str(uid)] = prose
        else:
            why = ("no submission" if entry.get("status") == "unsubmitted"
                   else "nothing the student typed or wrote: only files this tool "
                        "summarised (a .blend scene report, an image)")
            no_text.append({"user_id": str(uid),
                            "label": nicknames.shown(entry.get("name"), uid) or str(uid),
                            "why": why})

    if len(texts) < 2:
        raise RuntimeError(
            f"Need at least two submissions with text the student wrote; got "
            f"{len(texts)}. " + (
                "The others have nothing written to compare: "
                + ", ".join(f"{n['label']} ({n['why']})" for n in no_text[:4])
                if no_text else ""))

    progress(f"comparing {_plural(len(texts), 'submission')} "
             f"({len(texts) * (len(texts) - 1) // 2} pairs)")

    # The prompt, the instructions and the rubric are what everyone shares.
    boiler = "\n".join([
        html_to_text(assignment.get("description") or ""),
        store.instructions(course_id, assignment_id),
        " ".join(f"{c.get('label','')} {c.get('detail','')}"
                 for c in (store.draft(course_id, assignment_id).get("rubric")
                           or _rubric_of(assignment))),
    ])
    report = overlap.compare(texts, boilerplate=boiler)

    names = {str(uid): (nicknames.shown(
        (extracted.get(str(uid)) or {}).get("name"), uid) or str(uid))
             for uid in texts}
    labels = {str(uid): ((extracted.get(str(uid)) or {}).get("pseudonym")
                         if cfg.pseudonymize
                         else (extracted.get(str(uid)) or {}).get("name")) or str(uid)
              for uid in texts}
    for pair in report["pairs"]:
        pair["a_name"], pair["b_name"] = names.get(pair["a"]), names.get(pair["b"])

    notable = [p for p in report["pairs"] if p["notable"]][:6]
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n": len(texts),
        "pairs": report["pairs"],
        "compared": report.get("compared", len(report["pairs"])),
        "notable": len(notable),
        "no_text": no_text,
        "skipped_short": [names.get(u, u) for u in report["skipped"]],
        "window_words": report["k"],
        "read": "",
        "cost_usd": 0.0,
        "model": None,
    }

    if not notable:
        out["read"] = ("Nothing stands out. After removing the wording the whole "
                       "group shares, no pair has enough text in common to be "
                       "worth reading side by side.")
        _save_overlap(store, course_id, assignment_id, out)
        return out

    # Only the strongest pairs go to the model, and only as pseudonyms.
    progress(f"asking {cfg.model} to read {_plural(len(notable), 'pair')}")
    pair_pseud = Pseudonymizer(store.students(course_id) or [],
                               enabled=cfg.pseudonymize)
    lines = [f"# Assignment: {assignment.get('name','(untitled)')}", "",
             f"Overlap was measured on {overlap.DEFAULT_K}-word windows after "
             "removing every window the whole group shares (the prompt, the rubric, "
             "the instructions, and any wording common to more than a third of "
             "these submissions). So the passages below are not the assignment "
             "text: they are wording these two submissions share and the others "
             "do not.", ""]
    for index, pair in enumerate(notable, start=1):
        a, b = labels.get(pair["a"], pair["a"]), labels.get(pair["b"], pair["b"])
        lines.append(f"## Pair {index}: {a} and {b}")
        lines.append(f"Longest shared run: {pair['longest_words']} words. "
                     f"{pair['containment']:.0%} of the shorter submission's "
                     f"distinctive windows appear in the other.")
        for passage in pair["passages"][:3]:
            quoted = passage["text"][:600]
            if cfg.pseudonymize:
                quoted = pair_pseud.scrub_roster(quoted)
            lines.append(f'- ({passage["words"]} words) "{quoted}"')
        lines.append("")
    ids = ", ".join(f'"{index}"' for index in range(1, len(notable) + 1))
    lines.append(
        "For each pair return ONLY this JSON object:\n"
        "{\n"
        f'  "pairs": [ {{ "pair": <one of {ids}>, '
        '"reading": "verbatim overlap" | "close paraphrase" | "shared source or '
        'template" | "ordinary shared phrasing", '
        '"what_i_see": "<2-3 sentences citing the shared wording>", '
        '"innocent_explanation": "<the most likely benign reason>", '
        '"worth_a_conversation": <true or false> } ],\n'
        '  "note": "<one or two sentences for the instructor about the set as a whole>"\n'
        "}")
    result = llm.run("\n".join(lines), model=cfg.model,
                            timeout_s=cfg.claude_timeout_s, system=OVERLAP_SYSTEM,
                            on_activity=_activity_sink(progress, "overlap check",
                                                       cfg.model))
    out["cost_usd"] = round(result.cost_usd or 0.0, 4)
    out["model"] = cfg.model
    data = result.data if isinstance(result.data, dict) else None
    if data:
        by_index = {str(item.get("pair")): item for item in (data.get("pairs") or [])}
        for index, pair in enumerate(notable, start=1):
            item = by_index.get(str(index)) or {}
            pair["reading"] = str(item.get("reading") or "")
            pair["what_i_see"] = str(item.get("what_i_see") or "")
            pair["innocent_explanation"] = str(item.get("innocent_explanation") or "")
            pair["worth_a_conversation"] = bool(item.get("worth_a_conversation"))
        out["read"] = str(data.get("note") or "")
    else:
        # The passages are the evidence; a failed read does not invalidate them.
        out["read"] = ("Claude's reading of these pairs could not be parsed, so "
                       "only the measured overlap is shown below. The passages are "
                       "the evidence either way.")
        out["parse_error"] = result.parse_error

    # The second pass, while the tags are still in place. Every quoted passage
    # is evidence and the editor is told to keep it verbatim; humanize.py
    # checks that it did and keeps the original reading otherwise.
    if data and humanize.enabled(cfg):
        _item(progress, "overlap check",
              f"{humanize.model_for(cfg)} is editing the read", "")
        fields = {"note": out["read"]}
        for index, pair in enumerate(notable, start=1):
            fields[f"pair:{index}:what_i_see"] = pair.get("what_i_see") or ""
            fields[f"pair:{index}:innocent_explanation"] = (
                pair.get("innocent_explanation") or "")
        fields, edited = humanize.humanize_fields(fields, cfg, humanize.KIND_OVERLAP)
        out["read"] = fields.get("note", out["read"])
        for index, pair in enumerate(notable, start=1):
            for field in ("what_i_see", "innocent_explanation"):
                pair[field] = fields.get(f"pair:{index}:{field}", pair[field])
        out["humanized"] = edited
        out["cost_usd"] = round(out["cost_usd"] + float(edited.get("cost_usd") or 0.0), 4)

    if cfg.pseudonymize:
        # The model only ever saw S-0xx. Put the names back for the instructor.
        for pair in notable:
            for field in ("what_i_see", "innocent_explanation"):
                text = pair.get(field) or ""
                for uid, tag in labels.items():
                    if tag and names.get(uid):
                        text = re.sub(rf"\b{re.escape(tag)}\b", lambda _m, n=names[uid]: n, text)
                pair[field] = text
        note = out["read"]
        for uid, tag in labels.items():
            if tag and names.get(uid):
                note = re.sub(rf"\b{re.escape(tag)}\b", lambda _m, n=names[uid]: n, note)
        out["read"] = note

    _save_overlap(store, course_id, assignment_id, out)
    return out


def _save_overlap(store: Store, course_id, assignment_id, out: dict) -> None:
    """Keep the last check on the draft so it can be reopened without paying again."""
    draft = store.draft(course_id, assignment_id)
    draft["overlap"] = out
    store.save_draft(course_id, assignment_id, draft)


TEACHING_SYSTEM = """You are helping an instructor work out what to change about their
teaching and their assignment, based on how one class performed and what the students
said. You are not grading anyone and not writing to students.

The instructor is your audience and they are short on time. Be concrete and specific to
the evidence in front of you. Name the rubric row, quote the students, say what to do in
the next class in a way that fits in ten minutes of class time.

Rules:
- Distinguish "they did not learn this" from "the assignment did not ask for it clearly"
  from "this is a tooling problem". Those need different fixes and the evidence usually
  says which it is.
- If the evidence points at the instructor's own materials, say so plainly and without
  padding. That is what this is for.
- Do not invent evidence. If something is unclear from the data, say it is unclear and
  say what would tell them.
- No praise, no encouragement, no filler. Do not open by summarising what you were given.

{humanize}"""


def teaching_read(cfg: Config, store: Store, course_id, assignment_id,
                  only: list[str] | None = None,
                  progress: Progress = _noop) -> dict:
    """Read one assignment's results as feedback on the teaching.

    The evidence is assembled locally first (item analysis on the rubric, what
    students said in their own words, the grading rationales for the rows that
    went worst). The model turns that into a plan. Everything it is shown is
    either a number computed here or a sentence a student wrote.
    """
    assignment = store.assignment(course_id, assignment_id)
    extracted = store.extracted(course_id, assignment_id)
    draft = store.draft(course_id, assignment_id)
    rubric = draft.get("rubric") or _rubric_of(assignment)
    entries = draft.get("students") or {}
    possible = draft.get("points_possible") or 0

    picked = {str(u) for u in (only or [])}
    graded = {uid: e for uid, e in entries.items()
              if curve.is_scored(e) and (not picked or str(uid) in picked)}
    if len(graded) < 3:
        raise RuntimeError(
            f"Only {len(graded)} graded submission(s) here. Grade at least three "
            "before reading the results as feedback on the teaching: below that "
            "it is individual students, not a pattern.")

    progress(f"reading {_plural(len(graded), 'result')}")
    health = teaching.rubric_health(rubric, list(graded.values()))
    voice = teaching.student_voice(
        {uid: extracted[uid] for uid in graded if uid in extracted},
        overlap.student_prose)

    totals = [curve.final_total(e, rubric, possible) for e in graded.values()]
    mean_pct = round(100.0 * (sum(totals) / len(totals)) / possible, 1) if possible else 0

    # The rows worth talking about, and what the grader said about them for the
    # students who lost the most there.
    weak = [h for h in health if h["mean_pct"] < 100 * teaching.WEAK_MEAN][:3]
    evidence: list[dict] = []
    for row in weak:
        losers = sorted(
            graded.items(),
            key=lambda kv: float((kv[1].get("scores") or {}).get(row["id"]) or 0))
        notes = []
        for uid, entry in losers[:6]:
            note = (entry.get("rationales") or {}).get(row["id"])
            if note:
                notes.append({
                    "label": (extracted.get(uid, {}).get("pseudonym")
                              if cfg.pseudonymize
                              else extracted.get(uid, {}).get("name")) or uid,
                    "scored": float((entry.get("scores") or {}).get(row["id"]) or 0),
                    "why": str(note)[:500],
                })
        evidence.append({"row": row, "notes": notes})

    flags = {}
    for entry in graded.values():
        for flag in (entry.get("flags") or []):
            flags[str(flag)] = flags.get(str(flag), 0) + 1
    common_flags = sorted(flags.items(), key=lambda kv: -kv[1])[:8]

    lines = [f"# Assignment: {assignment.get('name','(untitled)')}",
             f"{len(graded)} graded submissions, class average {mean_pct}% "
             f"of {possible} points.", ""]
    description = html_to_text(assignment.get("description") or "").strip()
    if description:
        lines += ["## What the assignment asked for", description[:2500], ""]
    instructions = store.instructions(course_id, assignment_id)
    if instructions.strip():
        lines += ["## Extra grading instructions in force",
                  _hide_nicks(cfg, store, course_id, instructions.strip())[:1200], ""]

    lines.append("## Rubric rows, worst first")
    for row in health:
        lines.append(
            f"- {row['label']}: mean {row['mean']} of {row['points']} "
            f"({row['mean_pct']}%), spread {row['spread']}, "
            f"{row['at_ceiling']}/{row['n']} at full marks, "
            f"{row['at_floor']}/{row['n']} at almost nothing"
            + (f", correlation with the rest of the grade r={row['r_with_rest']}"
               if row["r_with_rest"] is not None else ""))
        for finding in row["findings"]:
            lines.append(f"    measured signal: {finding['text']}")
    lines.append("")

    if evidence:
        lines.append("## Why points were lost on the weakest rows")
        lines.append("These are the grader's own notes for the students who scored "
                     "lowest on each row. Read them for the pattern, not the "
                     "individuals.")
        for item in evidence:
            lines.append(f"### {item['row']['label']} "
                         f"({item['row']['mean_pct']}% average)")
            for note in item["notes"]:
                lines.append(f"- [scored {_num(note['scored'])}] {note['why']}")
        lines.append("")

    if voice["n_students"]:
        lines.append("## What students said, in their own words")
        lines.append(f"{voice['n_students']} of {len(graded)} students wrote "
                     "something about their own experience, in the submission or "
                     "in a Canvas comment:")
        for band in voice["by_category"]:
            lines.append(f"- {band['count']} × {band['label']}")
        voice_pseud = Pseudonymizer(store.students(course_id) or [],
                                    enabled=cfg.pseudonymize)
        for person in voice["students"][:14]:
            for item in person["items"]:
                quoted = item["text"]
                if cfg.pseudonymize:
                    quoted = voice_pseud.scrub_roster(quoted)
                lines.append(f'  - ({item["category"]}) "{quoted}"')
        lines.append("")
    else:
        lines.append("## What students said")
        lines.append("Nothing: no student wrote anything about their own "
                     "experience of the assignment. Do not invent any.")
        lines.append("")

    if common_flags:
        lines.append("## Flags the grader raised, and how often")
        for flag, count in common_flags:
            lines.append(f"- {count} × {flag}")
        lines.append("")

    lines.append(
        "Return ONLY this JSON object:\n"
        "{\n"
        '  "headline": "<one sentence: the single most useful thing this class '
        'tells the instructor about their own teaching or their assignment>",\n'
        '  "reteach": [ { "what": "<the concept or skill to go back over>", '
        '"evidence": "<the numbers and quotes that say so>", '
        '"cause": "not taught" | "taught but not practised" | "assignment was '
        'unclear" | "tooling or logistics", '
        '"action": "<what to do in the next class, ten minutes or less>", '
        '"students_affected": <number> } ],\n'
        '  "assignment_fixes": [ { "what": "<what to change in the prompt, the '
        'rubric row wording, or the materials>", "why": "<the evidence>" } ],\n'
        '  "worked": "<what this class clearly did learn, from the rows they '
        'cleared; empty string if nothing stands out>",\n'
        '  "watch_next_time": "<the one thing to look for in the next '
        'assignment to tell whether the fix worked>"\n'
        "}\n"
        "Order reteach with the most costly first, at most four items. "
        "Leave assignment_fixes empty rather than inventing changes.")

    progress(f"asking {cfg.model} for a teaching read")
    result = llm.run(
        "\n".join(lines), model=cfg.model, timeout_s=cfg.claude_timeout_s,
        system=TEACHING_SYSTEM.format(humanize=HUMANIZE_RULES),
        on_activity=_activity_sink(progress, "teaching read", cfg.model))

    data = result.data if isinstance(result.data, dict) else None
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_graded": len(graded), "mean_pct": mean_pct,
        "scope": "selection" if picked else "class",
        "health": health, "voice": voice,
        "flags": [{"flag": f, "count": c} for f, c in common_flags],
        "model": cfg.model, "cost_usd": round(result.cost_usd or 0.0, 4),
        "headline": "", "reteach": [], "assignment_fixes": [],
        "worked": "", "watch_next_time": "",
    }
    if data:
        out["headline"] = str(data.get("headline") or "")
        out["worked"] = str(data.get("worked") or "")
        out["watch_next_time"] = str(data.get("watch_next_time") or "")
        for item in (data.get("reteach") or [])[:4]:
            out["reteach"].append({
                "what": str(item.get("what") or ""),
                "evidence": str(item.get("evidence") or ""),
                "cause": str(item.get("cause") or ""),
                "action": str(item.get("action") or ""),
                "students_affected": item.get("students_affected"),
            })
        for item in (data.get("assignment_fixes") or [])[:5]:
            out["assignment_fixes"].append({
                "what": str(item.get("what") or ""),
                "why": str(item.get("why") or "")})
    else:
        out["parse_error"] = result.parse_error
        out["headline"] = ("Claude's reading could not be parsed. The measured "
                           "numbers and the student quotes below are unaffected.")

    # The second pass, while the tags are still in place. Numbers and quotes
    # in the evidence are checked after the rewrite; a field that lost one
    # keeps its original wording.
    if data and humanize.enabled(cfg):
        progress(f"{humanize.model_for(cfg)} is editing the teaching read")
        fields = {"headline": out["headline"], "worked": out["worked"],
                  "watch_next_time": out["watch_next_time"]}
        for i, item in enumerate(out["reteach"]):
            for k in ("what", "evidence", "action"):
                fields[f"reteach:{i}:{k}"] = item[k]
        for i, item in enumerate(out["assignment_fixes"]):
            for k in ("what", "why"):
                fields[f"fix:{i}:{k}"] = item[k]
        fields, edited = humanize.humanize_fields(fields, cfg, humanize.KIND_TEACHING)
        for k in ("headline", "worked", "watch_next_time"):
            out[k] = fields.get(k, out[k])
        for i, item in enumerate(out["reteach"]):
            for k in ("what", "evidence", "action"):
                item[k] = fields.get(f"reteach:{i}:{k}", item[k])
        for i, item in enumerate(out["assignment_fixes"]):
            for k in ("what", "why"):
                item[k] = fields.get(f"fix:{i}:{k}", item[k])
        out["humanized"] = edited
        out["cost_usd"] = round(out["cost_usd"] + float(edited.get("cost_usd") or 0.0), 4)

    if cfg.pseudonymize:
        # The model saw pseudonyms in the grading notes; put names back.
        names = {}
        for uid in graded:
            info = extracted.get(uid) or {}
            if info.get("pseudonym") and info.get("name"):
                names[info["pseudonym"]] = nicknames.shown(info["name"], uid)
        def unmask(text: str) -> str:
            for tag, name in names.items():
                text = re.sub(rf"\b{re.escape(tag)}\b", lambda _m, n=name: n, text)
            return text
        out["headline"] = unmask(out["headline"])
        out["worked"] = unmask(out["worked"])
        out["watch_next_time"] = unmask(out["watch_next_time"])
        for item in out["reteach"]:
            item["evidence"] = unmask(item["evidence"])
            item["action"] = unmask(item["action"])
        for item in out["assignment_fixes"]:
            item["why"] = unmask(item["why"])

    draft = store.draft(course_id, assignment_id)
    draft["teaching"] = out
    store.save_draft(course_id, assignment_id, draft)
    return out
