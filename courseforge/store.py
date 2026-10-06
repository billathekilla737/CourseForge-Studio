"""On-disk layout for cached Canvas data and grade drafts.

    data/
      courses.json
      <course_id>/
        assignments.json
        students.json
        <assignment_id>/
          assignment.json      the assignment, including its rubric
          submissions.json     raw submissions
          discussion.json      topic view, for graded discussions
          extracted.json       plain text per student
          instructions.md      your custom grading instructions
          map.json             pseudonym -> identity (local only)
          draft.json           scores, comments, provenance
          files/               downloaded attachments

Everything under data/ is gitignored. draft.json is the single source of truth
for the review UI and for any later push to Canvas.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def safe_id(value) -> str:
    """One path segment that stays inside the data folder.

    Course and assignment ids arrive from the URL. A Canvas id is digits; the
    account-wide record uses the word "account". Anything with a dot or a
    separator in it is not an id and would name a folder somewhere else.
    """
    text = str(value)
    if not _ID.match(text):
        raise ValueError(f"{text!r} is not a course or assignment id")
    return text


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Grading runs concurrently and two overlapping jobs would otherwise each
        # hold a stale snapshot of draft.json and clobber the other's results.
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        """For callers that need to read-modify-write the draft as one step."""
        return self._lock

    # ------------------------------------------------------------- locations
    def course_dir(self, course_id) -> Path:
        path = self.root / safe_id(course_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def assignment_dir(self, course_id, assignment_id) -> Path:
        path = self.course_dir(course_id) / safe_id(assignment_id)
        (path / "files").mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------ primitives
    def read(self, path: Path, default: Any = None) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def write(self, path: Path, payload: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)          # atomic-ish: never leave a half-written draft
        return path

    def read_text(self, path: Path, default: str = "") -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return default

    def write_text(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    # --------------------------------------------------------------- courses
    def courses(self) -> list[dict]:
        return self.read(self.root / "courses.json", []) or []

    def save_courses(self, courses: list[dict]) -> None:
        self.write(self.root / "courses.json", courses)

    def assignments(self, course_id) -> list[dict]:
        return self.read(self.course_dir(course_id) / "assignments.json", []) or []

    def save_assignments(self, course_id, assignments: list[dict]) -> None:
        self.write(self.course_dir(course_id) / "assignments.json", assignments)

    def students(self, course_id) -> list[dict]:
        return self.read(self.course_dir(course_id) / "students.json", []) or []

    def save_students(self, course_id, students: list[dict]) -> None:
        self.write(self.course_dir(course_id) / "students.json", students)

    # ----------------------------------------------------------- assignments
    def assignment(self, course_id, assignment_id) -> dict:
        return self.read(self.assignment_dir(course_id, assignment_id) / "assignment.json", {}) or {}

    def instructions(self, course_id, assignment_id) -> str:
        return self.read_text(self.assignment_dir(course_id, assignment_id) / "instructions.md")

    def save_instructions(self, course_id, assignment_id, text: str) -> None:
        self.write_text(self.assignment_dir(course_id, assignment_id) / "instructions.md", text)

    def extracted(self, course_id, assignment_id) -> dict:
        return self.read(self.assignment_dir(course_id, assignment_id) / "extracted.json", {}) or {}

    def save_extracted(self, course_id, assignment_id, extracted: dict) -> None:
        with self._lock:
            self.write(self.assignment_dir(course_id, assignment_id) / "extracted.json", extracted)

    # --------------------------------------------------------------- drafts
    def draft(self, course_id, assignment_id) -> dict:
        return self.read(self.assignment_dir(course_id, assignment_id) / "draft.json", {}) or {}

    def save_draft(self, course_id, assignment_id, draft: dict) -> dict:
        with self._lock:
            draft["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self.write(self.assignment_dir(course_id, assignment_id) / "draft.json", draft)
            return draft

    def update_student(self, course_id, assignment_id, user_id, **changes) -> dict:
        """Merge changes into one student's draft entry and persist.

        Re-reads the draft inside the lock so a concurrent grading job cannot be
        overwritten by a stale in-memory copy.
        """
        with self._lock:
            draft = self.draft(course_id, assignment_id)
            entries = draft.setdefault("students", {})
            entry = entries.setdefault(str(user_id), {})
            entry.update(changes)
            entry["edited_at"] = datetime.now().isoformat(timespec="seconds")
            return self.save_draft(course_id, assignment_id, draft)

    def put_student(self, course_id, assignment_id, user_id, entry: dict,
                    keep_human: bool = True) -> dict:
        """Replace one student's entry, re-reading the draft under the lock.

        With keep_human, a score the instructor set by hand is never overwritten;
        the incoming result is tucked under "ai" so both are visible. A score
        pulled from Canvas counts as the instructor's too: it was graded on
        another machine, not proposed by a model.
        """
        with self._lock:
            draft = self.draft(course_id, assignment_id)
            entries = draft.setdefault("students", {})
            existing = entries.get(str(user_id)) or {}
            # A Canvas score on a half-graded quiz is only the multiple choice.
            # The incoming result is the written portion added to that, and it
            # has to replace the partial, not hide under it.
            quiz_finish = (bool(entry.get("quiz_submission_id"))
                           and existing.get("source") == "canvas"
                           and existing.get("total_only"))
            if keep_human and existing.get("source") in ("human", "canvas") and not quiz_finish:
                existing.setdefault("ai", entry)
                entries[str(user_id)] = existing
            else:
                entry = dict(entry)
                entry["ai"] = {k: entry.get(k) for k in ("scores", "total", "comment")}
                # A curve is the instructor's decision about the whole class, not
                # a property of this grading run: re-grading one student must not
                # quietly drop them out of it. The total is recomputed after the
                # run, since the earned score underneath has changed.
                if existing.get("curve"):
                    entry["curve"] = existing["curve"]
                    entry.pop("final_total", None)
                # So is how much of the late penalty to apply: a re-grade
                # re-prices the penalty, not the instructor's leniency.
                if existing.get("late_share") is not None:
                    entry["late_share"] = existing["late_share"]
                    entry.pop("final_total", None)
                # human_ok deliberately does NOT carry over: it meant "I read
                # this score", and this is a different score.
                entries[str(user_id)] = entry
            self.save_draft(course_id, assignment_id, draft)
            return draft

    # -------------------------------------------------------------- cleanup
    def clear_assignment(self, course_id, assignment_id, keep_instructions: bool = True) -> None:
        path = self.assignment_dir(course_id, assignment_id)
        saved = self.instructions(course_id, assignment_id) if keep_instructions else ""
        shutil.rmtree(path, ignore_errors=True)
        if saved:
            self.save_instructions(course_id, assignment_id, saved)
