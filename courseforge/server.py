"""Local HTTP server: JSON API plus the review UI.

Binds to 127.0.0.1 only. Long operations (sync, grade) run as background jobs
that the page polls, so a class of 30 does not block the request thread.
"""
from __future__ import annotations

import atexit
import concurrent.futures
import csv
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import signal
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from . import (accommodations, areas, audit, blender, confirm, curve, extract,
               grader, humanize,
               latepolicy, llm, gradesync, handoff, htmlclean, instruct,
               nicknames, overlap, quizgrade, quizedit, routing, schedule,
               statesync, teaching, terms)
from .canvas import CanvasClient, CanvasError
from . import config
from .config import Config
from .store import Store, safe_id

WEB_DIR = Path(__file__).resolve().parent / "web"
STUDIO_COOKIE = "cf-studio-key"
# Images, playable video, and .glb may render in the page. Everything else
# (including .html and .svg) is a download, so it cannot run as this origin.
INLINE_STUDENT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".ogv": "video/ogg", ".mov": "video/quicktime",
    ".glb": "model/gltf-binary",
}
def api_host_allowed(host: str, port: int) -> bool:
    """True when Host is exactly this process's loopback address and port."""
    got = (host or "").strip().lower()
    return got in (f"127.0.0.1:{int(port)}", f"localhost:{int(port)}")


def student_file_response(name: str, download: bool = False) -> tuple[str, bool]:
    """Content-Type and attachment flag for a submission file served to the page."""
    ext = Path(name).suffix.lower()
    if ext in INLINE_STUDENT_TYPES and not download:
        return INLINE_STUDENT_TYPES[ext], False
    return "application/octet-stream", True


_PAGE_DIR = re.compile(r"^[A-Za-z0-9._-]{1,160}-(?:pages|media)$")
_PAGE_FILE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def submission_file(files_dir: Path, raw: str) -> Path | None:
    """A file under an assignment's files folder, or None.

    The usual name is one segment. A rendered PDF page or a picture pulled out
    of a Word file lives one folder down, named ``<stem>-pages`` or
    ``<stem>-media``. Anything else, including ``..``, is refused.
    """
    text = (raw or "").replace("\\", "/").strip().strip("/")
    if not text or "\x00" in text:
        return None
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    if len(parts) == 1:
        if not _PAGE_FILE.match(parts[0]):
            return None
    elif (len(parts) == 2 and _PAGE_DIR.match(parts[0])
            and _PAGE_FILE.match(parts[1])
            and Path(parts[1]).suffix.lower() in INLINE_STUDENT_TYPES):
        pass
    else:
        return None
    root = Path(files_dir).resolve()
    target = root.joinpath(*parts).resolve()
    if target != root and root not in target.parents:
        return None
    return target if target.is_file() else None


def keys_match(got: str, expected: str) -> bool:
    if not got or not expected:
        return False
    left, right = got.encode("utf-8"), expected.encode("utf-8")
    if len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def request_studio_key(headers) -> str:
    """X-Studio-Key, or the HttpOnly cookie set on the index page (for <img>/<video>)."""
    got = (headers.get("X-Studio-Key") or "").strip()
    if got:
        return got
    for part in (headers.get("Cookie") or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == STUDIO_COOKIE:
            return value.strip()
    return ""


def log_server_error(exc: BaseException, data_dir: Path | str | None = None) -> None:
    """Tracebacks stay on this machine. HTTP 500 JSON does not carry them."""
    traceback.print_exception(exc)
    if not data_dir:
        return
    try:
        path = Path(data_dir) / "server-error.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n--- %s %s ---\n" % (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                type(exc).__name__))
            traceback.print_exception(exc, file=fh)
    except OSError:
        pass


def build_id() -> str:
    """A fingerprint of the front end as it is on disk right now.

    An open tab keeps running the JavaScript it booted with. After an update the
    server answers with new routes while the page is still asking the old
    questions, and the result is a dialog that contradicts itself: one line from
    the new server, the next from the old script. Every API response carries this
    so the page can notice it has gone stale and say so, instead of leaving
    someone to work out which half to believe.
    """
    parts = []
    names = ["index.html", "style.css", "viewer.js"]
    for sub in ("", "js", "css"):
        folder = WEB_DIR / sub if sub else WEB_DIR
        try:
            names += sorted(f"{sub}/{f.name}" if sub else f.name
                            for f in folder.iterdir()
                            if f.suffix in (".js", ".css") and f.is_file())
        except OSError:
            pass
    for name in dict.fromkeys(names):
        try:
            stat = (WEB_DIR / name).stat()
        except OSError:
            continue
        parts.append(f"{name}:{int(stat.st_mtime)}:{stat.st_size}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]

# Cosmetic, but keeps the browser from guessing on the viewer payload.
mimetypes.add_type("model/gltf-binary", ".glb")
# Video types are not registered the same way on every machine -- on Windows they
# come out of the registry, where .mov is routinely missing or wrong. A <video>
# tag with the wrong Content-Type refuses to play at all, so pin the ones
# students actually submit.
for _ext, _ctype in ((".mov", "video/quicktime"), (".mp4", "video/mp4"),
                     (".m4v", "video/mp4"), (".webm", "video/webm"),
                     (".ogv", "video/ogg"), (".mkv", "video/x-matroska"),
                     (".avi", "video/x-msvideo"), (".wmv", "video/x-ms-wmv"),
                     (".3gp", "video/3gpp"), (".flv", "video/x-flv")):
    mimetypes.add_type(_ctype, _ext)
# The page's own files. On Windows, Python reads these from the registry too,
# and a machine where some program has claimed .js (Notepad, a code editor,
# an old Windows Script Host association) reports it as text/plain. The
# browser then refuses to run a single script, and the first screen sits on
# "checking Canvas…" forever with nothing in any log: the server served every
# file with a 200. Pin every type the page loads so the registry has no say.
for _ext, _ctype in ((".js", "text/javascript"), (".mjs", "text/javascript"),
                     (".css", "text/css"), (".html", "text/html"),
                     (".json", "application/json"), (".map", "application/json"),
                     (".svg", "image/svg+xml"), (".wasm", "application/wasm"),
                     (".woff", "font/woff"), (".woff2", "font/woff2"),
                     (".ttf", "font/ttf"), (".ico", "image/x-icon"),
                     (".png", "image/png"), (".webp", "image/webp")):
    mimetypes.add_type(_ctype, _ext)


class JobSink:
    """Progress sink handed to a job function.

    Calling it appends a log line and optionally moves the done/total bar, which
    is what every job already did. `item` reports the live status of one unit of
    work (a student, the class summary) and is deliberately *not* logged: it
    updates several times a second while a single Claude turn runs.
    """

    def __init__(self, jobs: "Jobs", job_id: str):
        self._jobs = jobs
        self._id = job_id

    def __call__(self, message: str, done: int | None = None,
                 total: int | None = None) -> None:
        self._jobs.log(self._id, message, done, total)

    def item(self, key: str, state: str, detail: str = "",
             finished: bool = False) -> None:
        self._jobs.item(self._id, key, state, detail, finished)


MAX_BODY_BYTES = 16 * 1024 * 1024
# Exceptions whose message is already a sentence for the person.
PLAIN_ERRORS = ("Refused", "PushRefused", "HTTPError", "NotOnThisRoster")
# Finished jobs are kept so a page that closed the dialog can still read the
# result, but not forever: each one holds its plan or draft in memory.
JOB_KEEP_SECONDS = 60 * 60
JOB_KEEP_COUNT = 200


class Jobs:
    """In-memory registry of background jobs."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        """Drop finished jobs that are old, oldest first past the cap. Caller holds the lock."""
        done = [j for j in self._jobs.values() if j.get("state") != "running"]
        done.sort(key=lambda j: j.get("updated", 0))
        stale = {j["id"] for j in done if now - j.get("updated", now) > JOB_KEEP_SECONDS}
        over = len(self._jobs) - len(stale) - JOB_KEEP_COUNT
        for j in done:
            if over <= 0:
                break
            if j["id"] not in stale:
                stale.add(j["id"])
                over -= 1
        for job_id in stale:
            self._jobs.pop(job_id, None)

    def running(self, key: str) -> str | None:
        """The id of the running job started with this key, if there is one."""
        if not key:
            return None
        with self._lock:
            for job in self._jobs.values():
                if job.get("key") == key and job.get("state") == "running":
                    return job["id"]
        return None

    def start(self, kind: str, fn, key: str = "") -> str:
        """Run fn on a thread and return its job id.

        With `key`, a job already running under the same key is returned
        instead of a second one being started: opening an assignment and
        pressing Re-sync in the same minute used to run two syncs of it side
        by side, and the second read files the first was still writing.
        """
        existing = self.running(key)
        if existing:
            return existing
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock:
            self._sweep(now)
            self._jobs[job_id] = {"id": job_id, "kind": kind, "state": "running",
                                  "log": [], "items": {}, "updated": now, "key": key,
                                  "started_at": datetime.now().isoformat(timespec="seconds")}
        sink = JobSink(self, job_id)

        def target() -> None:
            try:
                result = fn(sink)
                with self._lock:
                    self._jobs[job_id].update(state="done", result=result or {},
                                              items={}, updated=time.time())
            except llm.NotLoggedIn as exc:
                with self._lock:
                    self._jobs[job_id].update(state="error", error=str(exc),
                                              needs_login=True, items={},
                                              updated=time.time())
            except confirm.ConfirmRequired as exc:
                # Not a failure: the job reached Canvas's doorstep and stopped
                # to ask. The token has to survive out of the worker thread, or
                # the page has nothing to confirm with.
                with self._lock:
                    self._jobs[job_id].update(state="error", items={},
                                              updated=time.time(),
                                              **exc.payload())
            except Exception as exc:  # noqa: BLE001
                # A refusal an area wrote for a person to read is shown as
                # written; the class name is for the log, not the page.
                traceback.print_exc()
                plain = type(exc).__name__ in PLAIN_ERRORS or isinstance(exc, PermissionError)
                with self._lock:
                    self._jobs[job_id].update(state="error",
                                              error=str(exc) if plain else type(exc).__name__,
                                              items={}, updated=time.time())

        threading.Thread(target=target, daemon=True).start()
        return job_id

    def log(self, job_id: str, message: str, done: int | None = None,
            total: int | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["log"].append(message)
            job["log"] = job["log"][-200:]
            job["message"] = message
            job["updated"] = time.time()
            if total is not None:
                job["done"], job["total"] = done or 0, total

    def item(self, job_id: str, key: str, state: str, detail: str = "",
             finished: bool = False) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            items = job["items"]
            if finished:
                items.pop(key, None)
            else:
                entry = items.setdefault(key, {"key": key, "started": time.time()})
                entry["state"], entry["detail"] = state, detail
                entry["touched"] = time.time()
            job["updated"] = time.time()

    def get(self, job_id: str) -> dict | None:
        """Snapshot of a job, with live items flattened for the page."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            out = {k: v for k, v in job.items() if k not in ("log", "items", "trace")}
            out["log"] = list(job["log"])
            now = time.time()
            out["items"] = [
                {"key": e["key"], "state": e.get("state", ""),
                 "detail": e.get("detail", ""),
                 "elapsed_s": round(now - e["started"], 1),
                 "quiet_s": round(now - e.get("touched", e["started"]), 1)}
                for e in sorted(job["items"].values(), key=lambda e: e["started"])
            ]
            out["idle_s"] = round(now - job.get("updated", now), 1)
            return out


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = Store(cfg.data)
        self.jobs = Jobs()
        # Per-launch key the browser sends as X-Studio-Key. Not the Canvas token
        # and not the Assistant hook secret; it only proves the request came
        # from the page this process served.
        self.studio_key = secrets.token_hex(16)
        self._client: CanvasClient | None = None
        # Assignment pages read from the schedule, kept for this run only.
        self._item_cache: dict[tuple[str, str], dict] = {}
        # Course pages/syllabus about how to take a test (SmarterProctoring).
        self._policy_cache: dict[str, str] = {}
        # Late-work rule from the syllabus (and Canvas, if it already deducts).
        self._late_cache: dict[str, dict] = {}
        self._doctor: dict | None = None
        self._me_id: int | str | None = None
        # Every Canvas write passes through here twice: refused once with a
        # summary, then sent when the same change comes back confirmed.
        self.confirm = confirm.ConfirmGate()
        # Standing accommodations, kept beside the graded work rather than in
        # any one course, because students and their approvals outlast a term.
        self.roster = accommodations.Roster(self.store.root / "accommodations.json")
        nicknames.bind(self.store.root)
        self._quiz_cache: dict[tuple[str, str], dict] = {}
        # Grading in progress, carried between machines through Canvas. The
        # worker only ever looks at assignments that have been handed off once,
        # so nothing is uploaded until it has been asked for.
        self.machine = handoff.machine(config.user_dir())
        self.state_sync = statesync.StateSyncer(
            self, every_s=int(getattr(cfg, "state_sync_s", 45) or 45))
        self.state = self.state_sync
        self.state_sync.start()
        self._handoff_lock = threading.Lock()
        self._handoff_note: dict[str, dict] = {}
        self._handoff_stop = threading.Event()
        self._handoff_worker: threading.Thread | None = None
        # The model seam follows config.json ("cli" locally, "api" when hosted).
        llm.configure(cfg)
        # The account of what was done. Whose account it was is one Canvas call,
        # so it is handed over as something to call later rather than made now.
        audit.set_actor_source(lambda: self.client.whoami() or {}, self.machine)
        self.audit_sync = audit.Syncer(self, getattr(cfg, "audit_sync_s", 180))
        self.audit_sync.start()
        # The humanizer brief keeps itself current from its GitHub repository,
        # once a day, on its own thread. Off in config.json means the skill
        # never leaves this PC to ask.
        self.humanize_refresh = humanize.Refresher(cfg)
        if humanize.auto_update_enabled(cfg):
            self.humanize_refresh.start()
        # The other areas hang their state and routes off the App here.
        self.area_status: dict = {}
        areas.install_all(self)

    @property
    def client(self) -> CanvasClient:
        """The grading client: the one sanctioned reader of student data."""
        if self._client is None:
            self._client = CanvasClient(self.cfg.base_url, self.cfg.token(),
                                        scope="grading", allowed_hosts=self.cfg.canvas_hosts)
        return self._client

    @property
    def content(self) -> CanvasClient:
        """The client every non-grading area uses. It cannot reach submissions,
        grades, enrollments or users; canvas_policy refuses before sending."""
        return self.client.scoped("content")

    def course_dir(self, course_id) -> Path:
        """Where an area keeps its work for one course: data/<course>/<area>/..."""
        path = self.store.root / safe_id(course_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def me_id(self) -> int | str | None:
        """This token's Canvas user id, so a pull can tell the instructor's own
        submission comments from the students'. Best effort; None if offline."""
        if self._me_id is None:
            try:
                self._me_id = (self.client.whoami() or {}).get("id")
            except Exception:  # noqa: BLE001
                return None
        return self._me_id

    # ------------------------------------------------------------------ API
    def health(self) -> dict:
        from . import tools as _tools
        out = {
            "app": "CourseForge Studio",
            "tools": _tools.detect(self.cfg),
            "areas": {k: v.get("ok", False) for k, v in self.area_status.items()},
            "area_errors": {k: v.get("error") for k, v in self.area_status.items() if not v.get("ok")},
            "llm_backend": llm.backend_name(),
            "base_url": self.cfg.base_url,
            "data_dir": str(self.cfg.data),
            "model": self.cfg.model,
            "models": self.cfg.models,
            # What actually reads a submission carrying images. Worth sending
            # even when it equals `model`, so the page can say which students
            # the chosen model will not be used on rather than implying none.
            "vision_model": getattr(self.cfg, "vision_model", "") or self.cfg.model,
            "pseudonymize": self.cfg.pseudonymize,
            "concurrency": self.cfg.grading_concurrency,
            "allow_canvas_writes": self.cfg.allow_canvas_writes,
            "pull_interval_s": self.cfg.pull_interval_s,
            "assignment_max_age_min": self.cfg.assignment_max_age_min,
            "grade_scale": self.cfg.grade_scale,
            "machine": self.machine,
            "state_sync": (self.state_sync.status()
                           if getattr(self, "state_sync", None) else {}),
            "canvas": {"ok": False},
            "claude": self._doctor or {"logged_in": None, "detail": "not checked yet"},
            "blender": blender.probe(self.cfg.blender_path),
        }
        if not self.cfg.has_token():
            # Do not call Canvas. A new computer has no token, and guessing
            # "not connected" sent the course list off to wait on nothing.
            out["canvas"] = {
                "ok": False,
                "reason": "no_token",
                "error": "No Canvas API token is saved on this computer.",
            }
            return out
        try:
            # A stuck address lookup ignores the socket timeout on Windows and
            # holds this request forever. The first screen never gets past
            # "local · not connected" while that is happening.
            me = _within(10, lambda: self.client.get(
                "/users/self/profile", attempts=1, timeout=8))
            out["canvas"] = {"ok": True, "name": me.get("name"), "id": me.get("id")}
        except Exception as exc:  # noqa: BLE001
            out["canvas"] = {"ok": False, "error": str(exc).splitlines()[0][:300]}
        return out

    def check_claude(self) -> dict:
        self._doctor = llm.doctor()
        return self._doctor

    def set_settings(self, changes: dict) -> dict:
        """Change a runtime setting from the UI and persist it to config.json.

        The allow-list is the four keys the page actually edits. Do not add
        update_repo, base_url or canvas_hosts: those are not a dropdown.
        """
        allowed = {"model", "vision_model", "grading_concurrency", "pseudonymize"}
        applied: dict = {}
        for key, value in changes.items():
            if key not in allowed:
                continue
            if key in ("model", "vision_model"):
                value = str(value)
                if value not in self.cfg.models:
                    raise ValueError(
                        f"unknown model {value!r}; choose one of {', '.join(self.cfg.models)} "
                        "or add it to \"models\" in config.json")
            elif key == "grading_concurrency":
                value = max(1, min(int(value), 8))
            elif key == "pseudonymize":
                value = bool(value)
            applied[key] = value
        if applied:
            self.cfg.persist(**applied)
            if "pseudonymize" in applied:
                from . import identity
                identity.forget()
        return {"ok": True, "applied": applied, "model": self.cfg.model,
                "models": self.cfg.models,
                "vision_model": getattr(self.cfg, "vision_model", "") or self.cfg.model,
                "concurrency": self.cfg.grading_concurrency,
                "pseudonymize": self.cfg.pseudonymize}

    def courses(self, refresh: bool = False) -> list[dict]:
        cached = self.store.courses()
        # A cache written by an older version has no term fields; refetch rather
        # than dumping every course into the "other" bucket.
        stale = bool(cached) and any("term_label" not in c for c in cached)
        if cached and not refresh and not stale:
            # Re-evaluate exclusion against the CURRENT config instead of trusting
            # what was cached. Editing excluded_courses should take effect without
            # forcing a refresh from Canvas.
            for c in cached:
                c["excluded"] = self.cfg.is_excluded(
                    {"id": c.get("id"), "name": c.get("name", ""),
                     "course_code": c.get("code", "")})
            return cached
        try:
            raw = _within(25, lambda: self.client.courses(self.cfg.enrollment_types))
        except Exception as exc:  # noqa: BLE001
            # A new computer has no courses.json, so this call is the whole
            # first screen. Say what Canvas did, instead of a bare exception name.
            line = str(exc).splitlines()[0].strip()[:300]
            raise RuntimeError(
                line or "Could not read your courses from Canvas."
            ) from exc
        courses = []
        for c in raw:
            if not c.get("id"):
                continue
            term = terms.term_of(c)
            courses.append({
                "id": c["id"], "name": c.get("name", ""), "code": c.get("course_code", ""),
                # What a person calls it, with the registrar's prefix and the
                # delivery mode off. One parser, on the server, so every screen
                # shortens a name the same way.
                "title": schedule.course_title(c),
                "term": (c.get("term") or {}).get("name", ""),
                "term_code": term["code"], "term_label": term["label"],
                "term_sort": term["sort"],
                "students": c.get("total_students"),
                "excluded": self.cfg.is_excluded(c),
            })
        courses.sort(key=lambda c: (c["term_sort"], c["name"]), reverse=True)
        self.store.save_courses(courses)
        return courses

    def term_summary(self, refresh: bool = False) -> dict:
        """Terms that actually have courses, newest first, plus the default."""
        return terms.summarize(self.courses(refresh))

    def assignments(self, course_id, refresh: bool = False) -> list[dict]:
        cached = self.store.assignments(course_id)
        if not (cached and not refresh):
            raw = self.client.assignments(course_id)
            cached = []
            for a in raw:
                adir = self.store.assignment_dir(course_id, a["id"])
                draft = self.store.draft(course_id, a["id"])
                cached.append({
                    "id": a["id"], "name": a.get("name", ""),
                    "points_possible": a.get("points_possible"),
                    "due_at": a.get("due_at"),
                    "published": a.get("published"),
                    "submission_types": a.get("submission_types") or [],
                    "has_rubric": bool(a.get("rubric")),
                    "is_discussion": bool(a.get("discussion_topic")),
                    "needs_grading": a.get("needs_grading_count"),
                    "has_submissions": bool(a.get("has_submitted_submissions")),
                    "graded_submissions_exist": bool(a.get("graded_submissions_exist")),
                    "synced_at": draft.get("synced_at"),
                    "graded_at": draft.get("last_graded_at"),
                    "has_instructions": bool(self.store.instructions(course_id, a["id"]).strip()),
                    "_dir": str(adir),
                })
            self.store.save_assignments(course_id, cached)
        for row in cached:
            if not isinstance(row, dict):
                continue
            book = self.store.draft(course_id, row.get("id")) or {}
            row["graded"] = gradesync.assignment_is_graded(
                row.get("needs_grading"),
                self.store.extracted(course_id, row.get("id")),
                row.get("has_submissions"),
                book.get("students"), book.get("rubric"),
                book.get("points_possible"),
                graded_submissions_exist=row.get("graded_submissions_exist"))
        return cached

    def workspace(self, course_id, assignment_id) -> dict:
        draft = self.store.draft(course_id, assignment_id)
        policy = draft.get("late_policy") or self.course_late_policy(course_id)
        return {
            "assignment": self.store.assignment(course_id, assignment_id),
            "draft": draft,
            "extracted": self.store.extracted(course_id, assignment_id),
            "instructions": self.store.instructions(course_id, assignment_id),
            "late_policy": policy,
            "late_waiver": latepolicy.waives_everyone(
                self.store.instructions(course_id, assignment_id)),
            "pseudonymize": self.cfg.pseudonymize,
            # Measured locally and cheap, so the teaching bar can say something
            # useful the moment the page opens rather than waiting to be asked.
            "teaching_signal": self.teaching_signal(course_id, assignment_id),
        }

    def export(self, course_id, assignment_id) -> dict:
        adir = self.store.assignment_dir(course_id, assignment_id)
        draft = self.store.draft(course_id, assignment_id)
        extracted = self.store.extracted(course_id, assignment_id)
        rubric = draft.get("rubric") or []

        buf = io.StringIO(newline="")
        writer = csv.writer(buf)
        writer.writerow(["user_id", "student", "status", "earned", "curve", "total",
                         "points_possible", "percent", "letter",
                         "source", "needs_human", "flags",
                         *[c["label"] for c in rubric], "comment"])
        for uid, entry in sorted(draft.get("students", {}).items(),
                                 key=lambda kv: extracted.get(kv[0], {}).get("name", "")):
            info = extracted.get(uid, {})
            possible = draft.get("points_possible") or 0
            if not curve.is_scored(entry):
                earned = adjust = total = pct = letter = ""
            else:
                earned = curve.earned_total(entry, rubric)
                total = curve.final_total(entry, rubric, possible)
                adjust = round(total - earned, 2) or ""
                pct = round(100.0 * total / possible, 1) if possible else ""
                letter = curve.letter(pct if pct != "" else None, self.cfg.grade_scale)
            writer.writerow([
                uid, nicknames.shown(info.get("name", ""), uid), info.get("status", ""),
                earned, adjust, total, possible, pct, letter,
                entry.get("source", ""), entry.get("needs_human", ""),
                "; ".join(entry.get("flags") or []),
                *[(entry.get("scores") or {}).get(c["id"], "") for c in rubric],
                (entry.get("comment") or "").replace("\n", " "),
            ])
        csv_path = adir / "grades.csv"
        csv_path.write_text(buf.getvalue(), encoding="utf-8-sig")
        json_path = adir / "grades.json"
        json_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
        rows = len(draft.get("students", {}))
        audit.record(
            self.course_dir(course_id), "grade", "exported",
            "Wrote %d grade(s) and their comments to a spreadsheet on this "
            "computer. Nothing was sent anywhere." % rows,
            students=[audit.person(uid, extracted.get(uid, {}).get("name", ""))
                      for uid in draft.get("students", {})],
            count=rows, course_id=course_id,
            detail={"assignment_id": str(assignment_id), "csv": str(csv_path),
                    "json": str(json_path)})
        return {"csv": str(csv_path), "json": str(json_path), "rows": rows}

    def curve(self, course_id, assignment_id, body: dict) -> dict:
        """Preview, apply, or remove a grade curve.

        Nothing is written unless `apply` is true, and the response for a preview
        and for an apply have the same shape, so the page shows the instructor
        exactly what they are about to commit to and then the same numbers back.
        """
        draft = self.store.draft(course_id, assignment_id)
        rubric = draft.get("rubric") or []
        possible = draft.get("points_possible") or 0
        entries = draft.get("students") or {}
        extracted = self.store.extracted(course_id, assignment_id)
        only = [str(u) for u in (body.get("only") or [])]

        def named(rows):
            for row in rows:
                row["name"] = (extracted.get(row["user_id"]) or {}).get(
                    "name", row["user_id"])
                row["letter_before"] = curve.letter(
                    100.0 * row["before"] / possible if possible else None,
                    self.cfg.grade_scale)
                row["letter_after"] = curve.letter(
                    100.0 * row["after"] / possible if possible else None,
                    self.cfg.grade_scale)
            return rows

        if body.get("remove"):
            cleared = []
            for uid in (only or list(entries)):
                if (entries.get(str(uid)) or {}).get("curve"):
                    self.store.update_student(course_id, assignment_id, uid,
                                              curve=None)
                    cleared.append((extracted.get(str(uid)) or {}).get("name", uid))
            self._refresh_totals(course_id, assignment_id)
            return {"ok": True, "removed": cleared,
                    "scale": self.cfg.grade_scale,
                    **self.grade_summary(course_id, assignment_id)}

        planned = curve.plan(
            str(body.get("kind") or "flat"),
            rubric=rubric, entries=entries, possible=possible,
            scope=str(body.get("scope") or "total"),
            amount=float(body.get("amount") or 0),
            target=(None if body.get("target") in (None, "")
                    else float(body["target"])),
            only=only, scale=self.cfg.grade_scale)
        planned["rows"] = named(planned["rows"])
        planned["applied"] = False
        planned["scale"] = self.cfg.grade_scale

        if not body.get("apply"):
            return planned

        note = {"kind": planned["kind"], "scope": planned["scope"],
                "amount": planned["amount"], "target": planned["target"],
                "label": str(body.get("label") or ""),
                "at": datetime.now().isoformat(timespec="seconds")}
        for row in planned["rows"]:
            if not row["delta"]:
                continue
            entry = entries.get(row["user_id"]) or {}
            self.store.update_student(
                course_id, assignment_id, row["user_id"],
                curve=curve.apply_to(entry, planned["scope"], row["delta"], note))
        self._refresh_totals(course_id, assignment_id)
        moved = [r for r in planned["rows"] if r["delta"]]
        audit.record(
            self.course_dir(course_id), "grade", "curved",
            "Curved %d grade(s) on this assignment: %s on %s, biggest gain %s. "
            "Nothing was sent to Canvas."
            % (len(moved), planned["kind"], planned.get("criterion_label") or "the whole score",
               planned.get("biggest_gain")),
            students=[audit.person(r["user_id"], r.get("name", ""),
                                   delta=r["delta"], to=r.get("after"))
                      for r in moved],
            count=len(moved), course_id=course_id,
            detail={"assignment_id": str(assignment_id), "kind": planned["kind"],
                    "scope": planned["scope"], "amount": planned["amount"],
                    "target": planned["target"], "label": note["label"]})
        planned["applied"] = True
        return planned

    @staticmethod
    def _said(value) -> str:
        """A score as the record says it, so "None" never appears in a sentence."""
        return "not graded" if value is None else str(value)

    @staticmethod
    def _push_comment(entry: dict, mode: str) -> str:
        """The student-facing comment this push will write, or empty.

        `none` writes scores only. `all` writes every comment. `selected`
        writes only the comments ticked on the student panel — the default
        for a new Claude draft is off, so a push cannot dump every AI
        comment unless someone asked.
        """
        if mode == "none":
            return ""
        if mode == "selected" and not entry.get("post_comment"):
            return ""
        return (entry.get("comment") or "").strip()

    def edit_student(self, course_id, assignment_id, user_id, body: dict) -> dict:
        """A hand edit from the review pane: sliders and the comment."""
        before = ((self.store.draft(course_id, assignment_id).get("students") or {})
                  .get(str(user_id)) or {})
        was_total, was_source = before.get("total"), before.get("source")
        was_comment = before.get("comment") or ""
        changes = {k: v for k, v in body.items() if k != "user_id"}
        if "post_comment" in changes:
            changes["post_comment"] = bool(changes["post_comment"])
        # Ticking "include this comment" is not a regrade.
        flag_only = set(changes) <= {"post_comment"}
        if not flag_only:
            changes.setdefault("source", "human")
        if "scores" in changes:
            rubric_now = (self.store.draft(course_id, assignment_id).get("rubric") or [])
            locked = {str(c.get("id")) for c in rubric_now if c.get("locked")}
            prev = before.get("scores") or {}
            merged_scores = dict(changes["scores"])
            for cid in locked:
                if cid not in merged_scores and cid in prev:
                    merged_scores[cid] = prev[cid]
            changes["scores"] = merged_scores
            changes["total"] = round(sum(float(v or 0) for v in merged_scores.values()), 2)
            # A rubric score by hand replaces a breakdown-less total pulled
            # from Canvas.
            changes["total_only"] = False
        draft = self.store.update_student(course_id, assignment_id, user_id, **changes)
        entry = draft["students"][str(user_id)]
        # The curve and the syllabus late dock sit on top of the earned score.
        # A slider just moved the earned score, so the posted total has to follow.
        rubric = draft.get("rubric") or []
        possible = draft.get("points_possible") or 0
        extra = {}
        if entry.get("late_penalty") and curve.is_scored(entry):
            extra["late_penalty"] = latepolicy.refresh(
                entry, curve.earned_total(entry, rubric))
            entry = {**entry, **extra}
        if curve.is_scored(entry) and (
                entry.get("curve") or (entry.get("late_penalty") or {}).get("applied")):
            extra["final_total"] = curve.final_total(entry, rubric, possible)
        if extra and any(entry.get(k) != v for k, v in extra.items()):
            draft = self.store.update_student(course_id, assignment_id, user_id, **extra)
            entry = draft["students"][str(user_id)]
        if (not flag_only
                and (was_total != entry.get("total")
                     or was_comment != (entry.get("comment") or ""))):
            name = (self.store.extracted(course_id, assignment_id)
                    .get(str(user_id), {}).get("name", ""))
            what = []
            if was_total != entry.get("total"):
                what.append("the score from %s to %s"
                            % (self._said(was_total), self._said(entry.get("total"))))
            if was_comment != (entry.get("comment") or ""):
                what.append("the comment")
            audit.record(
                self.course_dir(course_id), "grade", "edited",
                "Changed %s by hand, over what Claude proposed. Nothing was sent "
                "to Canvas." % " and ".join(what),
                students=[audit.person(user_id, name, score=entry.get("total"))],
                count=1, course_id=course_id,
                detail={"assignment_id": str(assignment_id),
                        "from_total": was_total, "to_total": entry.get("total"),
                        "was_source": was_source,
                        "comment_changed": was_comment != (entry.get("comment") or "")})
        return entry

    def _refresh_totals(self, course_id, assignment_id) -> None:
        """Recompute the posted total on every entry that has a curve or late dock."""
        draft = self.store.draft(course_id, assignment_id)
        rubric = draft.get("rubric") or []
        possible = draft.get("points_possible") or 0
        for uid, entry in (draft.get("students") or {}).items():
            if not entry or not curve.is_scored(entry):
                continue
            if not (entry.get("curve") or (entry.get("late_penalty") or {}).get("applied")):
                continue
            want = curve.final_total(entry, rubric, possible)
            if entry.get("final_total") != want:
                self.store.update_student(course_id, assignment_id, uid,
                                          final_total=want)

    # ---------------------------------------------------------------- setup
    def setup_state(self) -> dict:
        """What setup still needs, without ever revealing the token itself."""
        env = bool((os.environ.get("CANVAS_TOKEN") or "").strip())
        # Saved per user, not inside this copy of the app: a token written next
        # to config.json is lost the next time the folder is re-downloaded, and
        # a second copy on the same machine cannot see it. An explicit
        # token_path in config.json still wins.
        target = (Path(self.cfg.token_path).expanduser() if self.cfg.token_path
                  else config.user_dir() / config.tokenstore.ENC_NAME)
        have = None
        try:
            have = len(self.cfg.token())
        except Exception:  # noqa: BLE001
            have = None
        return {
            "base_url": self.cfg.base_url,
            "token_len": have,                 # a length, never the value
            "token_path": str(target),
            "env_override": env,
            "token_page": self.cfg.base_url.rstrip("/") + "/profile/settings",
            "checked": [str(path) for path in self.cfg.token_candidates()],
        }

    def save_token(self, token: str, base_url: str | None = None,
                   force: bool = False) -> dict:
        """Verify a pasted token against Canvas, then write it to disk.

        Verification comes first on purpose: a mistyped paste must not be able
        to overwrite a token that was working. The token is written, never
        echoed back, and never written to a log.
        """
        token = Config._clean_token(token or "")
        if not token:
            return {"ok": False, "reason": "empty",
                    "error": "Paste the token first."}
        if any(ch.isspace() for ch in token):
            return {"ok": False, "reason": "malformed",
                    "error": "That has a space or a line break inside it, so it "
                             "is not a whole token. Copy it again from Canvas."}
        if len(token) < 20:
            return {"ok": False, "reason": "malformed",
                    "error": f"That is only {len(token)} characters. Canvas "
                             "tokens are much longer, so this looks like a "
                             "partial copy."}

        host = (base_url or self.cfg.base_url or "").strip().rstrip("/")
        if host and not host.startswith(("http://", "https://")):
            host = "https://" + host
        if not host:
            return {"ok": False, "reason": "no_host",
                    "error": "Set your Canvas web address first."}

        # Ask Canvas who this token belongs to before trusting it.
        try:
            who = CanvasClient(host, token).whoami()
        except CanvasError as exc:
            if exc.status in (401, 403):
                return {"ok": False, "reason": "rejected", "status": exc.status,
                        "error": "Canvas rejected that token. Generate a new one "
                                 "under Account -> Settings -> New Access Token, "
                                 "and copy the whole thing."}
            if not force:
                return {"ok": False, "reason": "unreachable", "status": exc.status,
                        "error": f"{host} answered HTTP {exc.status} instead of "
                                 "identifying you. Check the web address. The "
                                 "token itself has not been saved."}
            who = {}
        except Exception:  # noqa: BLE001
            if not force:
                return {"ok": False, "reason": "unreachable",
                        "error": f"Could not reach {host}. Check the web address "
                                 "for a typo, and that you are online. The token "
                                 "itself has not been saved."}
            who = {}

        # Saved per user, not inside this copy of the app: a token written next
        # to config.json is lost the next time the folder is re-downloaded, and
        # a second copy on the same machine cannot see it. An explicit
        # token_path in config.json still wins.
        try:
            if self.cfg.token_path:
                target = Path(self.cfg.token_path).expanduser()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(token + "\n", encoding="utf-8")
            else:
                # Encrypted at rest on Windows (DPAPI, this account only), a
                # 0600 file elsewhere. Never echoed, never logged.
                target = self.cfg.save_token(token)
        except (OSError, config.tokenstore.TokenError) as exc:
            return {"ok": False, "reason": "write_failed",
                    "error": f"Could not save the token: {exc}"}

        if host != self.cfg.base_url:
            self.cfg.persist(base_url=host)
            self.cfg.base_url = host

        # Drop the cached client so the next call uses the new token.
        self._client = None
        self._doctor = None
        return {
            "ok": True,
            "name": who.get("name") or "",
            "user_id": who.get("id"),
            "verified": bool(who),
            "path": str(target),
            "base_url": host,
            "env_override": bool((os.environ.get("CANVAS_TOKEN") or "").strip()),
        }

    # ------------------------------------------------------------- schedule
    SCHEDULE_FILE = "schedule.json"

    def _schedule_cache(self) -> dict:
        return self.store.read(self.store.root / self.SCHEDULE_FILE, {}) or {}

    def schedule(self, term: str | None = None) -> dict:
        """The cached term schedule, with its age so the page can say so.

        This never fetches. The page shows what is on disk straight away and
        asks for a refresh separately, so opening the view is instant even on a
        slow connection and the timestamp is always honest about what you are
        looking at.
        """
        cache = self._schedule_cache()
        term = term or self.default_term()
        saved = (cache.get("terms") or {}).get(term) or {}
        age = None
        fetched = saved.get("fetched_at")
        if fetched:
            try:
                age = round((datetime.now()
                             - datetime.fromisoformat(fetched)).total_seconds())
            except ValueError:
                age = None
        self._label_backfill(saved)
        return {
            "term": term,
            "terms": sorted((cache.get("terms") or {}).keys()),
            "fetched_at": fetched,
            "age_s": age,
            "stale": age is None or age > self.cfg.schedule_max_age_min * 60,
            "max_age_min": self.cfg.schedule_max_age_min,
            "base_url": self.cfg.base_url,
            "courses": saved.get("courses") or [],
            "items": saved.get("items") or [],
            "stats": saved.get("stats") or schedule.stats([]),
            "partial": saved.get("partial") or [],
        }

    @staticmethod
    def _label_backfill(saved: dict) -> None:
        """Give a schedule cached before course labels existed its labels now.

        Cheap, and it means the display changes the moment the app restarts
        rather than waiting for the next refresh from Canvas.
        """
        courses = saved.get("courses") or []
        if not courses or all(c.get("label") for c in courses):
            return
        stand_ins = [{"id": c.get("course_id"), "name": c.get("name"),
                      "course_code": c.get("code")} for c in courses]
        labels = schedule.course_labels(stand_ins)
        for course in courses:
            course["label"] = labels.get(str(course.get("course_id"))) or course.get("code")
        for row in saved.get("items") or []:
            row.setdefault("course_label",
                           labels.get(str(row.get("course_id")))
                           or row.get("course_code"))

    def default_term(self) -> str:
        courses = self.store.courses() or []
        return (terms.summarize(courses).get("default") or "") if courses else ""

    def schedule_refresh(self, term: str | None = None,
                         log=lambda *_a, **_k: None) -> dict:
        """Refetch every dated assignment for one term, straight from Canvas."""
        courses = self.store.courses() or self.client.courses(self.cfg.enrollment_types)
        self.store.save_courses(courses)
        term = term or self.default_term()
        mine = [c for c in courses
                if not term or (c.get("term_label") or "") == term]
        mine = [c for c in mine if not self.cfg.is_excluded(c)]
        if not mine:
            raise RuntimeError(f"No courses in {term or 'this term'} to build a "
                               "schedule from.")

        per_course: dict[str, list[dict]] = {}
        failed: list[dict] = []
        total = len(mine)
        log(f"reading {total} course(s) in {term or 'all terms'}", 0, total)
        for index, course in enumerate(mine, start=1):
            code = schedule.course_code(course)
            log(f"{code}", index - 1, total)
            try:
                per_course[str(course["id"])] = self.client.assignments(course["id"])
            except Exception as exc:  # noqa: BLE001
                # One unreachable course must not cost the whole schedule; the
                # page says which ones are missing.
                per_course[str(course["id"])] = []
                failed.append({"code": code, "error": f"{type(exc).__name__}: {exc}"})
                log(f"  {code} failed: {exc}")
            log(f"read {index}/{total}", index, total)

        payload = schedule.build(mine, per_course, self.cfg.base_url)
        payload["fetched_at"] = datetime.now().isoformat(timespec="seconds")
        payload["partial"] = failed

        cache = self._schedule_cache()
        cache.setdefault("terms", {})[term] = payload
        self.store.write(self.store.root / self.SCHEDULE_FILE, cache)

        log(f"{len(payload['items'])} dated item(s) across {total} course(s)"
            + (f"; {len(failed)} course(s) failed" if failed else ""))
        return self.schedule(term)

    def _cached_course_label(self, course_id) -> str:
        """The label this course carries in whichever cached term holds it."""
        for saved in ((self._schedule_cache().get("terms") or {}).values()):
            # The cache on disk may predate labels, and the backfill in
            # schedule() is not persisted, so derive them here too.
            self._label_backfill(saved)
            for course in saved.get("courses") or []:
                if str(course.get("course_id")) == str(course_id):
                    return str(course.get("label") or "")
        return ""

    # ------------------------------------------------- announcements & plans
    def announce_draft(self, course_id, assignment_id, extra: str = "",
                       log=lambda *_a, **_k: None) -> dict:
        """Draft a reminder announcement for one assignment. Writes nothing."""
        detail = self.schedule_item(course_id, assignment_id)
        item = dict(detail)
        item["course_label"] = detail.get("course_label")
        text = htmlclean.clean(detail.get("description") or "", self.cfg.base_url)
        # The model gets the words, not the markup.
        plain = re.sub(r"<[^>]+>", " ", text)
        plain = re.sub(r"\s+", " ", plain).strip()
        if not plain and detail.get("quiz_id"):
            try:
                quiz = self.client.quiz(course_id, detail["quiz_id"])
                qhtml = htmlclean.clean(quiz.get("description") or "", self.cfg.base_url)
                plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", qhtml)).strip()
            except Exception:  # noqa: BLE001
                pass
        policy = ""
        if instruct.needs_testing_policy(item, extra):
            log("reading the course testing instructions")
            policy = self._testing_policy(course_id)

        prompt = instruct.announce_prompt(item, plain, policy_text=policy, extra=extra)
        if extra.strip():
            prompt += ("\n\nThe instructor also said, and this takes priority "
                       f"over the guidance above:\n{extra.strip()[:600]}")
        log(f"drafting an announcement for {detail.get('name')}")
        result = llm.run(
            prompt, model=self.cfg.model, timeout_s=self.cfg.claude_timeout_s,
            system=instruct.ANNOUNCE_SYSTEM,
            on_activity=grader._activity_sink(log, "announcement", self.cfg.model))
        data = result.data if isinstance(result.data, dict) else {}
        title = str(data.get("title") or f"Reminder: {detail.get('name')}")
        message = str(data.get("message") or result.text.strip())
        # The second pass: students read this on a phone, and a reminder that
        # reads like a chatbot gets skimmed. Every date, time and points value
        # is checked after the rewrite; a draft that lost one keeps its words.
        edited: dict = {}
        if humanize.enabled(self.cfg):
            log(f"{humanize.model_for(self.cfg)} is editing the announcement")
            fields, edited = humanize.humanize_fields(
                {"title": title, "message": message}, self.cfg, humanize.KIND_ANNOUNCE)
            title, message = fields.get("title", title), fields.get("message", message)
        look = getattr(self.cfg, "a11y_look", "hybrid") or "hybrid"
        from .content.generate import load_brand
        brand = load_brand(getattr(self.cfg, "brand_path", None))
        html = instruct.wrap_announcement(message, look=look, brand=brand)
        return {
            "course_id": str(course_id),
            "assignment_id": str(assignment_id),
            "course_label": detail.get("course_label"),
            "name": detail.get("name"),
            "title": title,
            "message": message,
            "html": html,
            "look": look,
            "brand": {"colors": brand["colors"], "fonts": brand["fonts"]},
            "model": self.cfg.model,
            "cost_usd": round(float(result.cost_usd or 0.0)
                              + float(edited.get("cost_usd") or 0.0), 4),
            "parse_error": result.parse_error if not data else "",
            "humanized": edited,
            "writes_enabled": bool(self.cfg.allow_canvas_writes),
        }

    def announce_batch(self, targets: list[dict], extra: str = "",
                       log=lambda *_a, **_k: None) -> dict:
        """Draft a reminder for each of several assignments, in one pass.

        Each draft is its own Claude call, so a week of five tests is five
        calls. They run a few at a time and come back in the order they were
        asked for, which is the order the instructor is reading down the week.
        """
        wanted = [(str(t.get("course_id")), str(t.get("assignment_id")))
                  for t in (targets or [])
                  if t.get("course_id") and t.get("assignment_id")]
        # One assignment twice in a list is one announcement.
        seen, ordered = set(), []
        for pair in wanted:
            if pair not in seen:
                seen.add(pair)
                ordered.append(pair)
        if not ordered:
            raise RuntimeError("Nothing was selected to write about.")
        if len(ordered) > 20:
            raise RuntimeError(
                f"That is {len(ordered)} announcements in one go. Narrow the "
                "filters down first: this makes one Claude call each, and "
                "twenty drafts to read is more than anyone reviews properly.")

        total = len(ordered)
        log(f"drafting {total} announcement(s) with {self.cfg.model}", 0, total)
        drafts: dict[int, dict] = {}
        failed: list[dict] = []
        workers = max(1, min(int(self.cfg.grading_concurrency), 3))

        def one(index: int, course_id: str, assignment_id: str) -> None:
            try:
                drafts[index] = self.announce_draft(course_id, assignment_id,
                                                   extra, log)
            except Exception as exc:  # noqa: BLE001
                failed.append({"course_id": course_id,
                               "assignment_id": assignment_id,
                               "error": f"{type(exc).__name__}: {exc}"})

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(one, index, course_id, assignment_id): index
                for index, (course_id, assignment_id) in enumerate(ordered)
            }
            done = 0
            for future in concurrent.futures.as_completed(futures):
                future.result()
                done += 1
                log(f"drafted {done}/{total}", done, total)

        out = [drafts[i] for i in sorted(drafts)]
        log(f"{len(out)} draft(s) ready"
            + (f", {len(failed)} failed" if failed else ""))
        return {
            "drafts": out,
            "failed": failed,
            "model": self.cfg.model,
            "cost_usd": round(sum(d.get("cost_usd") or 0 for d in out), 4),
            "writes_enabled": bool(self.cfg.allow_canvas_writes),
        }

    def instruct_plan(self, instruction: str, term: str | None = None,
                      now_local: str = "", zone: str = "",
                      log=lambda *_a, **_k: None) -> dict:
        """Read an instruction and return a plan, checked against the schedule.

        Nothing is written here. The plan comes back for the instructor to look
        at, and applying it is a separate call.
        """
        instruction = (instruction or "").strip()
        if not instruction:
            raise RuntimeError("Type what you want to change first.")
        saved = self.schedule(term)
        items, courses = saved.get("items") or [], saved.get("courses") or []
        if not items:
            raise RuntimeError("The schedule has nothing in it yet. Refresh it "
                               "from Canvas first, so there is something to act on.")

        log(f"reading: {instruction[:70]}")
        result = llm.run(
            instruct.plan_prompt(instruction, items, courses, now_local, zone),
            model=self.cfg.model, timeout_s=self.cfg.claude_timeout_s,
            system=instruct.PLAN_SYSTEM,
            on_activity=grader._activity_sink(log, "instruction", self.cfg.model))
        data = result.data if isinstance(result.data, dict) else {}
        plan = instruct.validate(data, items, courses)
        plan.update({
            "instruction": instruction,
            "model": self.cfg.model,
            "cost_usd": round(result.cost_usd or 0.0, 4),
            "writes_enabled": bool(self.cfg.allow_canvas_writes),
            "term": saved.get("term"),
            "parse_error": "" if data else result.parse_error,
        })
        for op in plan["operations"]:
            op["describe"] = instruct.describe(op)
        log(f"{len(plan['operations'])} change(s) to look at")
        return plan

    def instruct_apply(self, operations: list[dict], dry_run: bool = True,
                       term: str | None = None,
                       log=lambda *_a, **_k: None,
                       confirm_token: str | None = None) -> dict:
        """Carry out operations the instructor confirmed.

        The operations are validated again here rather than trusted: the page
        could send anything, and this is the only place that touches a live
        course. A dry run reports exactly what would be sent without sending it.
        """
        saved = self.schedule(term)
        items, courses = saved.get("items") or [], saved.get("courses") or []
        checked = instruct.validate({"operations": operations or []}, items, courses)
        ops = checked["operations"]
        if not ops:
            return {"ok": False, "applied": [], "failed": [],
                    "rejected": checked["rejected"], "dry_run": dry_run,
                    "error": "Nothing in that plan could be applied."}

        if not dry_run:
            # The whole operation, not its one-line description: an
            # announcement is described by its title, and the message it
            # posts has to be part of what the token is bound to.
            self._gate("instruct", [{"line": instruct.describe(op), "op": op} for op in ops],
                       f"{len(ops)} change(s) to your live courses",
                       confirm_token, what="changing your courses")

        applied, failed, planned = [], [], []
        total = len(ops)
        for index, op in enumerate(ops, start=1):
            line = instruct.describe(op)
            # The client converted the local wall clock to UTC; check it here.
            utc = {}
            for key, value in (op.get("dates_utc") or {}).items():
                if key not in instruct.DATE_KEYS:
                    continue
                if value in (None, "", "null"):
                    utc[key] = None
                    continue
                try:
                    datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                except ValueError:
                    failed.append({"describe": line,
                                   "error": f"{key} {value!r} is not a time"})
                    utc = None
                    break
                utc[key] = str(value)
            if utc is None:
                continue
            if op["op"] == "set_dates" and not utc:
                failed.append({"describe": line,
                               "error": "no times came through for this change"})
                continue

            planned.append({"describe": line, "op": op["op"],
                            "course_id": op["course_id"],
                            "assignment_id": op.get("assignment_id"),
                            "sends": utc if op["op"] == "set_dates" else None})
            if dry_run:
                continue

            log(f"{index}/{total}: {line}", index - 1, total)
            try:
                if op["op"] == "announce":
                    look = getattr(self.cfg, "a11y_look", "hybrid") or "hybrid"
                    html = instruct.wrap_announcement(op["message"], look=look)
                    self.client.create_announcement(op["course_id"], op["title"], html)
                elif op["op"] in ("publish", "unpublish"):
                    want = op["op"] == "publish"
                    self.client.update_assignment(op["course_id"],
                                                  op["assignment_id"],
                                                  published=want)
                    if op.get("quiz_id"):
                        self.client.update_quiz(op["course_id"], op["quiz_id"],
                                                published=want)
                else:
                    self.client.update_assignment(op["course_id"],
                                                  op["assignment_id"], dates=utc)
                    # A classic quiz keeps its own dates; writing only the
                    # assignment can leave the quiz still locked.
                    if op.get("quiz_id"):
                        self.client.update_quiz(op["course_id"], op["quiz_id"],
                                                dates=utc)
                applied.append({"describe": line, "course_id": op["course_id"]})
            except Exception as exc:  # noqa: BLE001
                failed.append({"describe": line, "course_id": op["course_id"],
                               "error": f"{type(exc).__name__}: {exc}"})
            log(f"{index}/{total} done", index, total)

        if applied or failed:
            # Publishing an assignment and announcing to a class are changes a
            # student can be affected by, so they are recorded per course
            # rather than as one line about "the schedule".
            touched = {str(r.get("course_id")) for r in applied + failed
                       if r.get("course_id")}
            for cid in sorted(touched):
                mine = [p for p in applied if str(p.get("course_id")) == cid]
                bad = [f for f in failed if str(f.get("course_id")) == cid]
                audit.record(
                    self.course_dir(cid), "tools", "schedule",
                    "Applied %d change(s) from the term schedule%s: %s."
                    % (len(mine), ", %d refused by Canvas" % len(bad) if bad else "",
                       "; ".join(p["describe"] for p in mine[:8])
                       + (" and more" if len(mine) > 8 else "")),
                    count=len(mine), course_id=cid,
                    result="failed" if bad and not mine else "ok",
                    detail={"applied": [p["describe"] for p in mine],
                            "failed": bad})

        if applied:
            # What Canvas now says has changed, so the page stops showing the
            # old dates.
            self._item_cache.clear()
            log("re-reading the schedule from Canvas")
            try:
                self.schedule_refresh(term)
            except Exception as exc:  # noqa: BLE001
                log(f"  could not refresh the schedule: {exc}")

        return {"ok": not failed, "dry_run": dry_run, "planned": planned,
                "applied": applied, "failed": failed,
                "rejected": checked["rejected"],
                "writes_enabled": bool(self.cfg.allow_canvas_writes)}

    # ------------------------------------------------- chasing missing work
    # Canvas's own "Message Students Who -> Haven't submitted yet". One generic
    # message, sent privately to each student who has turned nothing in, with
    # the assignment and its deadline filled in. Nothing here is drafted by a
    # model: it is a stock note the instructor can edit before it goes.

    @staticmethod
    def _due_phrase(due_iso: str | None) -> str:
        """The deadline in the instructor's own timezone, written out.

        strftime's no-pad codes differ between platforms, so the day and hour
        are assembled by hand rather than with %-d / %#I.
        """
        when = schedule._parse(due_iso) if due_iso else None
        if not when:
            return ""
        local = when.astimezone()
        hour = local.hour % 12 or 12
        return (f"{local:%A, %B} {local.day} at {hour}:{local:%M} "
                f"{'AM' if local.hour < 12 else 'PM'}")

    def missing_for(self, course_id, assignment_id) -> dict:
        """Everyone on the roster with nothing turned in, read fresh from Canvas.

        Read live rather than from the local sync: this is offered on the term
        schedule, where an assignment may never have been synced, and a stale
        answer here means messaging a student who did hand something in.
        """
        raw = self.client.assignment(course_id, assignment_id)
        roster = self.client.students(course_id)
        subs = {str(sub.get("user_id")): sub
                for sub in self.client.submission_states(course_id, assignment_id)}

        rows = []
        for student in roster:
            uid = str(student.get("id"))
            sub = subs.get(uid) or {}
            # Excused is the instructor saying this one does not count. A
            # submitted_at is the only proof work exists: Canvas reports
            # workflow_state "graded" for a student who never turned anything
            # in but already has a zero on the books.
            if sub.get("excused") or sub.get("submitted_at"):
                continue
            rows.append({
                "user_id": uid,
                "name": student.get("name") or uid,
                "sortable": student.get("sortable_name") or student.get("name") or uid,
                # Shown beside the name so a student already dealt with can be
                # unticked rather than chased.
                "score": sub.get("score"),
                "graded": bool(sub.get("graded_at")),
            })
        rows.sort(key=lambda r: str(r["sortable"]).lower())

        due = raw.get("due_at")
        when = schedule._parse(due) if due else None
        name = raw.get("name") or "(untitled)"
        phrase = self._due_phrase(due)
        body = (f"{name} was due {phrase} and I do not have a submission from you.\n\n"
                if phrase else
                f"I do not have a submission from you for {name}.\n\n")
        body += ("If you have it finished, upload it in Canvas now. If something got "
                 "in the way, reply to this message and tell me what happened so we "
                 "can work out where to go from here.")
        return {
            "course_id": str(course_id),
            "assignment_id": str(assignment_id),
            "name": name,
            "due_at": due,
            "due_phrase": phrase,
            "past_due": bool(when and when < datetime.now(timezone.utc)),
            "points": raw.get("points_possible"),
            "url": raw.get("html_url") or "",
            "roster": len(roster),
            "missing": rows,
            "subject": f"Missing: {name}",
            "body": body,
        }

    def remind_missing(self, course_id, assignment_id, only: list[str] | None = None,
                       subject: str = "", body: str = "",
                       log=lambda _m: None,
                       confirm_token: str | None = None) -> dict:
        """Message the students who have turned nothing in. Gated like a push."""
        info = self.missing_for(course_id, assignment_id)
        pool = {row["user_id"]: row for row in info["missing"]}
        wanted = [str(u) for u in (only or [])]
        # Re-checked against a fresh read, so a student who submitted between
        # opening the dialog and pressing send is dropped rather than chased.
        ids = [u for u in (wanted or list(pool)) if u in pool]
        if not ids:
            raise ValueError(
                "Nobody to message: every student on the roster has turned "
                "something in, or the ones you picked have since submitted.")

        subject = (subject or info["subject"]).strip()
        body = (body or info["body"]).strip()
        if not body:
            raise ValueError("the message is empty")

        names = [pool[u]["name"] for u in ids]
        self._gate("remind",
                   {"course_id": str(course_id),
                    "assignment_id": str(assignment_id),
                    "students": sorted(ids),
                    "subject": subject, "body": body},
                   f"Send a Canvas message to {len(ids)} student(s) who have "
                   f'turned nothing in for "{info["name"]}"',
                   confirm_token,
                   detail="Each gets a private copy and cannot see who else was "
                          "written to.\n\n" + "\n".join(names[:40])
                          + (f"\n... and {len(names) - 40} more" if len(names) > 40 else ""),
                   what="messaging students")

        sent: list[str] = []
        failed: list[dict] = []
        batch_size = 40
        for start in range(0, len(ids), batch_size):
            batch = ids[start:start + batch_size]
            log(f"messaging {min(start + len(batch), len(ids))}/{len(ids)}",
                start + len(batch), len(ids))
            try:
                self.client.create_conversation(batch, subject, body, course_id)
                sent += batch
            except Exception as exc:  # noqa: BLE001
                failed.append({"names": [pool[u]["name"] for u in batch],
                               "error": str(exc)})
                log(f"  WARNING could not send to {len(batch)}: {exc}")

        log(f"messaged {len(sent)} student(s)"
            + (f", {sum(len(f['names']) for f in failed)} failed" if failed else ""))
        # Who was written to, and what was said. A message to a student about
        # missing work is the other half of the accommodations argument: "we
        # told them" needs the same evidence as "we gave them the time".
        if sent or failed:
            audit.record(
                self.course_dir(course_id), "grade", "messaged",
                'Sent a private Canvas message about "%s" to %d student(s)%s. '
                'Subject: "%s".'
                % (info["name"], len(sent),
                   ", %d could not be reached" % sum(len(f["names"]) for f in failed)
                   if failed else "", subject),
                students=[audit.person(u, pool[u]["name"]) for u in sent],
                count=len(sent), course_id=course_id,
                result="failed" if failed and not sent else "ok",
                detail={"assignment_id": str(assignment_id), "subject": subject,
                        "body": body[:2000],
                        "not_reached": [n for f in failed for n in f["names"]]})
        return {"assignment": info["name"], "subject": subject,
                "count": len(sent), "sent": [pool[u]["name"] for u in sent],
                "failed": failed}

    # ------------------------------------------------- carrying work between
    # machines. See handoff.py for what travels and what deliberately does not.

    def storage(self) -> dict:
        """How much Canvas file room the handoffs are using.

        The ceiling is rounded up to something a person would say out loud, and
        the difference is charged to what is already used. A bar reading
        "17 of 600 MB" is read at a glance; "17.4 of 599.3" makes you do
        arithmetic to learn the same thing, and the 0.7 MB was never going to
        change a decision.
        """
        raw = self.client.user_quota()
        quota = int(raw.get("quota") or 0)
        used = int(raw.get("quota_used") or 0)
        step = 10_000_000
        shown = ((quota + step - 1) // step) * step if quota else 0
        folder = getattr(self.cfg, "handoff_folder", handoff.FOLDER)
        mine = []
        try:
            mine = [{"name": f.get("display_name"), "bytes": f.get("size") or 0,
                     "at": f.get("updated_at")}
                    for f in self.client.user_folder_files(folder)]
        except Exception:  # noqa: BLE001
            pass
        return {
            "quota": shown or quota,
            "used": used + max(0, shown - quota),
            "real_quota": quota,
            "real_used": used,
            "folder": folder,
            "handoffs": sorted(mine, key=lambda f: -(f["bytes"] or 0)),
            "handoff_bytes": sum(f["bytes"] or 0 for f in mine),
        }

    def _handoff_key(self, course_id, assignment_id) -> str:
        return f"{course_id}/{assignment_id}"

    def handoff_status(self, course_id, assignment_id, remote: bool = True) -> dict:
        """What is here, what is in Canvas, and whether they have diverged."""
        adir = self.store.assignment_dir(course_id, assignment_id)
        draft = self.store.draft(course_id, assignment_id)
        state = handoff.local_state(adir)
        here = handoff.grading_fingerprint(
            draft, self.store.instructions(course_id, assignment_id))
        graded = sum(1 for e in (draft.get("students") or {}).values()
                     if e.get("total") is not None)

        out = {
            "enabled": handoff.enabled(adir),
            "machine": self.machine,
            "folder": getattr(self.cfg, "handoff_folder", handoff.FOLDER),
            "local": {
                "students": len(draft.get("students") or {}),
                "graded": graded,
                "rev": int(state.get("rev") or 0),
                "sent_at": state.get("at"),
                # Edited since the last successful send?
                "unsent": bool(state.get("sent_hash") and state["sent_hash"] != here),
                "blend_files": len(handoff.blend_sources(adir)),
                "blend_unsent": bool(handoff.blend_sources(adir))
                                and handoff.blend_fingerprint(adir) != state.get("blend_hash"),
            },
            "remote": None,
            "diverged": False,
            "note": (self._handoff_note.get(self._handoff_key(course_id, assignment_id))
                     or {}),
        }
        if not remote:
            return out

        try:
            want = handoff.draft_name(course_id, assignment_id)
            wantz = handoff.blend_name(course_id, assignment_id)
            files = {f.get("display_name"): f for f in
                     self.client.user_folder_files(out["folder"])}
            found = files.get(want)
            if found:
                env = handoff.read_envelope(self.client.read_file_bytes(found["url"]))
                out["remote"] = {
                    "rev": int(env.get("rev") or 0),
                    "machine": env.get("machine") or {},
                    "written_at": env.get("written_at"),
                    "students": env.get("students"),
                    "graded": env.get("graded"),
                    "mine": (env.get("machine") or {}).get("id") == self.machine["id"],
                    "bytes": found.get("size"),
                }
                # The one case worth stopping for: work here that has never been
                # sent, and a newer copy in Canvas that has never seen it.
                out["diverged"] = bool(out["remote"]["rev"] > int(state.get("rev") or 0)
                                       and out["local"]["unsent"])
            bundle = files.get(wantz)
            if bundle:
                out["remote_blend"] = {"bytes": bundle.get("size"),
                                       "at": bundle.get("updated_at")}
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    def handoff_send(self, course_id, assignment_id, blend: bool | None = None,
                     log=lambda *a, **k: None) -> dict:
        """Put this machine's grading state in Canvas.

        The draft goes every time. The Blender bundle only when its contents
        have actually changed, because it is megabytes and a pass that reused
        cached renders produced nothing new to carry.
        """
        # This uploads to the instructor's own Canvas files, not to the course,
        # so it needs no second click. It is still a Canvas write, and the hard
        # lock in config.json has to mean what it says everywhere.
        self._require_writes("carrying grading between machines")
        adir = self.store.assignment_dir(course_id, assignment_id)
        draft = self.store.draft(course_id, assignment_id)
        if not draft:
            raise ValueError("Nothing has been synced for this assignment yet.")
        folder = getattr(self.cfg, "handoff_folder", handoff.FOLDER)
        state = handoff.local_state(adir)

        # Never step on a higher revision: if the other machine sent while this
        # one was offline, carry on from its number rather than reusing one.
        rev = int(state.get("rev") or 0)
        try:
            files = {f.get("display_name"): f for f in
                     self.client.user_folder_files(folder)}
            found = files.get(handoff.draft_name(course_id, assignment_id))
            if found:
                env = handoff.read_envelope(self.client.read_file_bytes(found["url"]))
                rev = max(rev, int(env.get("rev") or 0))
        except Exception:  # noqa: BLE001
            pass
        rev += 1

        instructions = self.store.instructions(course_id, assignment_id)
        payload = handoff.build_envelope(draft, instructions, rev, self.machine,
                                         course_id, assignment_id)
        log(f"sending the draft ({len(payload) / 1024:.0f} KB)")
        self.client.upload_user_file(handoff.draft_name(course_id, assignment_id),
                                     payload, folder, "application/json")
        changes = {"rev": rev,
                   "sent_hash": handoff.grading_fingerprint(draft, instructions),
                   "machine": self.machine["id"]}

        here = handoff.blend_fingerprint(adir)
        want_blend = blend if blend is not None else (here != state.get("blend_hash"))
        if want_blend and handoff.blend_sources(adir):
            bundle = handoff.build_blend_zip(adir)
            if bundle:
                log(f"sending the Blender bundle ({len(bundle) / 1e6:.1f} MB)")
                self.client.upload_user_file(
                    handoff.blend_name(course_id, assignment_id), bundle,
                    folder, "application/zip")
                changes["blend_hash"] = here
                changes["blend_bytes"] = len(bundle)

        handoff.save_state(adir, **changes)
        log(f"handed off as revision {rev}")
        return {"ok": True, "rev": rev, "blend_sent": "blend_hash" in changes,
                "machine": self.machine}

    def handoff_fetch(self, course_id, assignment_id, log=lambda *a, **k: None,
                      confirm_token: str | None = None,
                      gated: bool = True) -> dict:
        """Take the copy in Canvas and make it the one here.

        A replace, not a merge: one instructor, one course, one machine at a
        time. The gate is on this side rather than on sending, because this is
        the direction that can lose work -- your own, not a student's.
        """
        adir = self.store.assignment_dir(course_id, assignment_id)
        folder = getattr(self.cfg, "handoff_folder", handoff.FOLDER)
        files = {f.get("display_name"): f for f in
                 self.client.user_folder_files(folder)}
        found = files.get(handoff.draft_name(course_id, assignment_id))
        if not found:
            raise ValueError(
                "There is no handoff in Canvas for this assignment yet. Hand off "
                "from the other machine first.")

        env = handoff.read_envelope(self.client.read_file_bytes(found["url"]))
        incoming = env.get("draft") or {}
        state = handoff.local_state(adir)
        draft = self.store.draft(course_id, assignment_id)
        unsent = bool(state.get("sent_hash")
                      and state["sent_hash"] != handoff.grading_fingerprint(
                          draft, self.store.instructions(course_id, assignment_id)))

        where = (env.get("machine") or {}).get("name") or "another machine"
        summary = (f"Replace the grading here with revision {env.get('rev')} "
                   f"from {where} ({env.get('graded')} of {env.get('students')} "
                   "students graded)")
        detail = ""
        if unsent:
            detail = ("This machine has edits that were never handed off. They "
                      "are not in the copy you are about to take, and picking it "
                      "up discards them.")
        # Asking is only worth a person's attention when there is something to
        # lose. Taking a newer copy onto a machine whose own work is already in
        # Canvas replaces nothing, and stopping to confirm that would train the
        # habit of clicking through the dialog that does matter.
        if gated or unsent:
            self.confirm.require("handoff_fetch",
                                 {"course_id": str(course_id),
                                  "assignment_id": str(assignment_id),
                                  "rev": int(env.get("rev") or 0)},
                                 summary, confirm_token, detail)

        bundle = files.get(handoff.blend_name(course_id, assignment_id))
        got_blend = 0
        if bundle:
            log(f"fetching the Blender bundle ({(bundle.get('size') or 0) / 1e6:.1f} MB)")
            got_blend = handoff.apply_blend_zip(
                adir, self.client.read_file_bytes(bundle["url"]))
            log(f"unpacked {got_blend} rendered artifact(s)")

        self.store.save_draft(course_id, assignment_id, incoming)
        if env.get("instructions"):
            self.store.save_instructions(course_id, assignment_id, env["instructions"])

        # Reattach the renders to the submissions, the same way a sync does. If
        # nothing has been synced here yet there is nothing to attach them to,
        # and the page says to sync first.
        attached = 0
        extracted = self.store.extracted(course_id, assignment_id)
        if extracted:
            attached = blender.attach_cached(self.store, course_id, assignment_id,
                                             extracted)
            if attached:
                self.store.write(adir / "extracted.json", extracted)
                log(f"reattached {attached} Blender result(s)")

        handoff.save_state(adir, rev=int(env.get("rev") or 0),
                           sent_hash=handoff.grading_fingerprint(
                               incoming, env.get("instructions") or ""),
                           blend_hash=handoff.blend_fingerprint(adir),
                           machine=self.machine["id"])
        log("picked up")
        return {"ok": True, "rev": env.get("rev"), "from": env.get("machine"),
                "students": len(incoming.get("students") or {}),
                "blend_files": got_blend, "attached": attached,
                "needs_sync": not extracted}

    def handoff_reconcile(self, course_id, assignment_id,
                          log=lambda *a, **k: None) -> dict:
        """Line this machine up with Canvas when an assignment is opened.

        The whole point of carrying work is that it happens by itself, so there
        is no button: opening the assignment is the moment both sides can be
        compared, and the three outcomes are decided here rather than being put
        to the instructor as a question they would answer the same way every
        time.

          * Canvas is ahead and nothing here is unsent  -> take it.
          * Both have moved                             -> leave everything
                                                           alone and say so.
          * Otherwise                                   -> make sure Canvas has
                                                           what this machine has.
        """
        draft = self.store.draft(course_id, assignment_id)
        if not draft or not (draft.get("students") or {}):
            return {"did": "nothing", "why": "not synced yet"}

        status = self.handoff_status(course_id, assignment_id)
        if status.get("error"):
            return {"did": "nothing", "why": status["error"]}
        local, remote = status["local"], status.get("remote")

        if status["diverged"]:
            return {"did": "diverged", "local": local, "remote": remote}

        if remote and int(remote["rev"]) > int(local["rev"]) and not local["unsent"]:
            out = self.handoff_fetch(course_id, assignment_id, log, gated=False)
            return {"did": "picked_up", **out}

        if not status["enabled"] or local["unsent"] or local["blend_unsent"]:
            out = self.handoff_send(course_id, assignment_id, log=log)
            return {"did": "handed_off", **out}

        return {"did": "in_step", "rev": local["rev"]}

    def handoff_disable(self, course_id, assignment_id, remove: bool = False,
                        confirm_token: str | None = None) -> dict:
        """Stop carrying this assignment, and optionally clear it from Canvas."""
        adir = self.store.assignment_dir(course_id, assignment_id)
        removed = 0
        if remove:
            folder = getattr(self.cfg, "handoff_folder", handoff.FOLDER)
            wanted = {handoff.draft_name(course_id, assignment_id),
                      handoff.blend_name(course_id, assignment_id)}
            found = [f for f in self.client.user_folder_files(folder)
                     if f.get("display_name") in wanted]
            if found:
                # Deleting a file from Canvas is a Canvas write like any other,
                # and the copy being deleted may be the only one of grading
                # done on another machine. Same second click as everything else.
                self._gate("handoff_off",
                           {"course_id": str(course_id),
                            "assignment_id": str(assignment_id),
                            "files": sorted(str(f.get("id")) for f in found)},
                           f"Delete {len(found)} handoff file(s) for this assignment "
                           f"from your Canvas files. Grading on this machine is kept.",
                           confirm_token, what="removing a handoff")
            for f in found:
                self.client.delete_file(f["id"])
                removed += 1
        try:
            handoff.state_path(adir).unlink()
        except OSError:
            pass
        return {"ok": True, "removed": removed}

    # ------------------------------------------------------ the quiet worker
    def start_handoff_worker(self) -> None:
        """Keep Canvas current for assignments already being carried.

        Hooking every route that can touch a draft would mean remembering to
        hook the next one too. This watches the fingerprint instead, so anything
        that changes grading state is covered whether it thought about it or not.
        """
        if not getattr(self.cfg, "handoff_auto", True):
            return
        if self._handoff_worker and self._handoff_worker.is_alive():
            return
        every = max(10, int(getattr(self.cfg, "handoff_debounce_s", 45)))

        def loop() -> None:
            while not self._handoff_stop.wait(every):
                try:
                    self._handoff_tick()
                except Exception:  # noqa: BLE001
                    pass

        self._handoff_worker = threading.Thread(target=loop, daemon=True,
                                                name="handoff")
        self._handoff_worker.start()

    def stop_handoff_worker(self) -> None:
        self._handoff_stop.set()

    def _handoff_tick(self) -> None:
        root = self.store.root
        if not root.is_dir():
            return
        for adir in sorted(root.glob("*/*")):
            if not adir.is_dir() or not handoff.enabled(adir):
                continue
            course_id, assignment_id = adir.parent.name, adir.name
            key = self._handoff_key(course_id, assignment_id)
            draft = self.store.draft(course_id, assignment_id)
            if not draft:
                continue
            state = handoff.local_state(adir)
            draft_changed = handoff.grading_fingerprint(
                draft, self.store.instructions(course_id, assignment_id)
            ) != state.get("sent_hash")
            blend_changed = (bool(handoff.blend_sources(adir))
                             and handoff.blend_fingerprint(adir) != state.get("blend_hash"))
            if not (draft_changed or blend_changed):
                continue
            with self._handoff_lock:
                try:
                    out = self.handoff_send(course_id, assignment_id)
                    self._handoff_note[key] = {
                        "ok": True, "at": datetime.now().isoformat(timespec="seconds"),
                        "rev": out["rev"], "blend_sent": out["blend_sent"]}
                except Exception as exc:  # noqa: BLE001
                    # Offline, or Canvas is down. Say so and try again next tick
                    # rather than interrupting whatever is being graded.
                    self._handoff_note[key] = {
                        "ok": False, "at": datetime.now().isoformat(timespec="seconds"),
                        "error": f"{type(exc).__name__}: {exc}"}

    def _plain_html(self, html: str) -> str:
        cleaned = htmlclean.clean(html or "", self.cfg.base_url)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cleaned)).strip()

    def _testing_policy(self, course_id) -> str:
        """The testing / SmarterProctoring section of this course's syllabus.

        Proctored assignments here often have an empty description (external
        tool). The real instructions live in the syllabus of each course, not
        in the assignment and not in a page titled SmarterProctoring. Cached
        per course for this run.
        """
        key = str(course_id)
        if key in self._policy_cache:
            return self._policy_cache[key]
        chunks: list[str] = []
        try:
            course = self.client.course_detail(course_id, include=["syllabus_body"])
            syl = self._plain_html(course.get("syllabus_body") or "")
            passage = instruct.extract_policy_passages(syl)
            if passage:
                chunks.append("Syllabus: %s" % passage)
        except Exception:  # noqa: BLE001
            pass
        try:
            pages = self.client.pages(course_id)
        except Exception:  # noqa: BLE001
            pages = []
        for page in pages:
            title = str(page.get("title") or "")
            want = instruct.policy_relevant(title) or instruct.SYLLABUS_TITLE.search(title)
            if not want:
                continue
            slug = page.get("url")
            try:
                full = self.client.page(course_id, slug) if slug else page
            except Exception:  # noqa: BLE001
                continue
            plain = self._plain_html(full.get("body") or "")
            passage = instruct.extract_policy_passages(plain) or (
                plain[:2500] if instruct.SYLLABUS_TITLE.search(title) else "")
            if passage:
                chunks.append("%s: %s" % (title, passage))
        text = "\n\n".join(chunks)[:6000]
        self._policy_cache[key] = text
        return text

    def apply_late_instructions(self, course_id, assignment_id, text: str) -> int:
        """Lift or restore the late dock from the instruction box, without regrading.

        The syllabus rule is applied in code after Claude scores, so a sentence
        in the instructions never reached it. Saving the box is what changes it.
        """
        policy = self.course_late_policy(course_id)
        extracted = self.store.extracted(course_id, assignment_id)
        with self.store.lock:
            draft = self.store.draft(course_id, assignment_id)
            rubric = draft.get("rubric") or []
            possible = draft.get("points_possible") or 0
            changed = 0
            for uid, entry in (draft.get("students") or {}).items():
                if not entry or entry.get("total") is None:
                    continue
                info = extracted.get(str(uid)) or {}
                late = bool(info.get("late")) or bool(entry.get("late_penalty"))
                if not late:
                    continue
                reason = latepolicy.waiver(text, info)
                current = dict(entry.get("late_penalty") or {})
                if reason:
                    if current.get("waived") and current.get("summary") == reason:
                        continue
                    entry["late_penalty"] = {
                        **current, "applied": False, "waived": True,
                        "points": 0, "summary": reason,
                    }
                elif current.get("waived"):
                    if policy and info.get("late"):
                        fresh = latepolicy.attach(
                            {"total": entry.get("total"), "scores": entry.get("scores"),
                             "flags": list(entry.get("flags") or [])},
                            info, policy, possible)
                        entry["late_penalty"] = fresh.get("late_penalty") or {}
                    else:
                        entry.pop("late_penalty", None)
                else:
                    continue
                if curve.is_scored(entry):
                    entry["final_total"] = curve.final_total(entry, rubric, possible)
                changed += 1
            if changed:
                self.store.save_draft(course_id, assignment_id, draft)
            return changed

    def course_late_policy(self, course_id) -> dict:
        """The late-work rule for this course, from the syllabus (or Canvas)."""
        key = str(course_id)
        if key in self._late_cache:
            return self._late_cache[key]
        try:
            policy = latepolicy.load(self.client, course_id, self._plain_html)
        except Exception as exc:  # noqa: BLE001
            policy = latepolicy.empty(f"Could not read the syllabus: {type(exc).__name__}")
        self._late_cache[key] = policy
        return policy

    def schedule_item(self, course_id, assignment_id) -> dict:
        """One assignment's own page: the description, as Canvas has it now.

        Fetched on demand rather than cached with the schedule. A term's
        descriptions run to hundreds of kilobytes of HTML, they are the thing
        most likely to have been edited since the last sync, and this is the
        moment the instructor is actually reading one. The result is kept for
        the life of the process so flicking between items is instant.
        """
        key = (str(course_id), str(assignment_id))
        if key in self._item_cache:
            return self._item_cache[key]

        raw = self.client.assignment(course_id, assignment_id)
        course = next((c for c in (self.store.courses() or [])
                       if str(c.get("id")) == str(course_id)), {"id": course_id})
        row = schedule.shape(course, dict(raw, due_at=raw.get("due_at") or "x"),
                             self.cfg.base_url) or {}
        description = htmlclean.clean(raw.get("description") or "",
                                      self.cfg.base_url)
        detail = {
            "course_id": str(course_id),
            "assignment_id": str(assignment_id),
            "course_code": schedule.course_code(course),
            # Prefer the label the schedule is already showing, so the badge in
            # here matches the row it was opened from -- including the catalogue
            # code where two courses share a name.
            "course_label": (self._cached_course_label(course_id)
                             or schedule.course_title(course)),
            "course_name": course.get("name") or "",
            "name": raw.get("name") or "(untitled)",
            "due_at": raw.get("due_at"),
            "unlock_at": raw.get("unlock_at"),
            "lock_at": raw.get("lock_at"),
            "points": raw.get("points_possible"),
            "kind_label": row.get("kind_label") or "ASSIGNMENT",
            "exam": bool(row.get("exam")),
            # Present only for a classic quiz, and the editor keys off that:
            # New Quizzes and publisher tests keep their settings elsewhere.
            "quiz_id": raw.get("quiz_id") or row.get("quiz_id"),
            "proctored": bool(row.get("proctored")),
            "published": bool(raw.get("published", True)),
            "needs_grading": int(raw.get("needs_grading_count") or 0),
            "submission_types": [str(t) for t in
                                 (raw.get("submission_types") or [])],
            "allowed_attempts": raw.get("allowed_attempts"),
            "url": raw.get("html_url") or row.get("url") or "",
            "description": description,
            "has_description": not htmlclean.looks_empty(description),
            "rubric": [
                {"label": r.get("description") or r.get("long_description") or "",
                 "points": r.get("points"),
                 "detail": (r.get("long_description") or "")[:600]}
                for r in (raw.get("rubric") or [])
            ],
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._item_cache[key] = detail
        return detail

    def teaching_signal(self, course_id, assignment_id) -> dict:
        """The measured part of the teaching read: no model, no cost, instant.

        This is what the teaching bar shows without being asked. The model pass
        (grader.teaching_read) is opt-in and builds on exactly this evidence.
        """
        draft = self.store.draft(course_id, assignment_id)
        extracted = self.store.extracted(course_id, assignment_id)
        rubric = draft.get("rubric") or []
        graded = {uid: e for uid, e in (draft.get("students") or {}).items()
                  if curve.is_scored(e)}
        unscored = self._unscored_counts(course_id, assignment_id)
        if not graded:
            return {"n_graded": 0, "health": [], "voice": {"n_students": 0,
                    "by_category": [], "students": []}, "patterns": [],
                    "unscored": unscored}
        health = teaching.rubric_health(rubric, list(graded.values()))
        voice = teaching.student_voice(
            {uid: extracted[uid] for uid in graded if uid in extracted},
            overlap.student_prose)
        return {
            "n_graded": len(graded),
            "health": health,
            "voice": voice,
            # Reported beside the analysis, never inside it.
            "unscored": unscored,
            "patterns": self.course_patterns(course_id, assignment_id),
            "has_read": bool(draft.get("teaching")),
        }

    def course_patterns(self, course_id, assignment_id) -> list[dict]:
        """Rubric rows that come out weak across this course's assignments.

        One weak row is an assignment; the same row weak three times is the
        course. Rows are matched on their wording, since ids are per-assignment.
        """
        course = self.store.course_dir(course_id)
        seen = []
        for path in sorted(course.iterdir()):
            if not path.is_dir():
                continue
            draft = self.store.read(path / "draft.json", {}) or {}
            rubric = draft.get("rubric") or []
            rows = [e for e in (draft.get("students") or {}).values()
                    if curve.is_scored(e)]
            if not rubric or len(rows) < 3:
                continue
            health = teaching.rubric_health(rubric, rows)
            seen.append({
                "name": draft.get("assignment_name") or path.name,
                "assignment_id": path.name,
                "criteria": [{"label": h["label"], "mean_pct": h["mean_pct"]}
                             for h in health],
            })
        return teaching.course_patterns(seen)

    def grade_summary(self, course_id, assignment_id) -> dict:
        """Letter distribution and averages over the graded pool."""
        draft = self.store.draft(course_id, assignment_id)
        rubric = draft.get("rubric") or []
        possible = draft.get("points_possible") or 0
        entries = (draft.get("students") or {}).values()
        totals = [curve.final_total(e, rubric, possible)
                  for e in entries if curve.is_scored(e)]
        percents = [(100.0 * t / possible) if possible else 0.0 for t in totals]
        return {"n": len(totals),
                "unscored": self._unscored_counts(course_id, assignment_id),
                "distribution": curve.distribution(percents, self.cfg.grade_scale)}

    def _unscored_counts(self, course_id, assignment_id) -> dict:
        """Students with no score, split by why. Reported, never averaged."""
        draft = self.store.draft(course_id, assignment_id)
        extracted = self.store.extracted(course_id, assignment_id)
        missing, unreadable, ungraded = [], [], []
        for uid, info in (extracted or {}).items():
            entry = (draft.get("students") or {}).get(str(uid))
            if curve.is_scored(entry):
                continue
            name = info.get("name") or str(uid)
            if info.get("status") == "unsubmitted":
                missing.append(name)
            elif entry:
                unreadable.append(name)
            else:
                ungraded.append(name)
        return {"no_submission": sorted(missing),
                "nothing_readable": sorted(unreadable),
                "not_graded_yet": sorted(ungraded)}

    def mark_reviewed(self, course_id, assignment_id, user_ids: list[str],
                      reviewed: bool = True) -> dict:
        """Approve (or un-approve) flagged entries so a push can include them.

        push() refuses anything with needs_human unless human_ok is set, and
        until now nothing could set it: a flagged student could never be posted.
        This is that switch, in bulk.
        """
        marked, no_score, not_graded = [], [], []
        extracted = self.store.extracted(course_id, assignment_id)
        # One read for the reporting; update_student re-reads under its own lock,
        # so the writes stay safe against a grading job running alongside this.
        entries = self.store.draft(course_id, assignment_id).get("students") or {}
        for uid in user_ids:
            uid = str(uid)
            name = (extracted.get(uid) or {}).get("name", uid)
            entry = entries.get(uid)
            if not entry:
                not_graded.append(name)     # nothing graded yet: nothing to approve
                continue
            self.store.update_student(
                course_id, assignment_id, uid,
                human_ok=bool(reviewed),
                reviewed_at=(datetime.now().isoformat(timespec="seconds")
                             if reviewed else None))
            # Marking reviewed clears the review block, not a missing score:
            # push still has nothing to post for an ungraded student.
            (marked if curve.is_scored(entry) else no_score).append(name)
        return {"ok": True, "reviewed": bool(reviewed), "marked": marked,
                "no_score": no_score, "not_graded": not_graded}

    # ---------------------------------------------------------- grade posting
    # A grade is visible when it is written. Holding grades for a later Post
    # was a second step nobody used, and it made a posted automatic score look
    # like a finished grade. The app does not set that hold, and a push turns
    # it off if an older version of this app left it on.

    def policy(self, course_id, assignment_id=None) -> dict:
        """Read the post policy from Canvas; for an assignment, cache it on the
        draft so the page can show it before the next round trip."""
        info = self.client.post_policy(course_id, assignment_id)
        if assignment_id:
            with self.store.lock:
                draft = self.store.draft(course_id, assignment_id)
                if draft:
                    draft["post_policy"] = info
                    self.store.save_draft(course_id, assignment_id, draft)
        return info

    def _require_writes(self, what: str) -> None:
        """The hard lock. Off by default; this is only reached when someone has
        deliberately bolted the doors shut in config.json."""
        if not self.cfg.allow_canvas_writes:
            raise PermissionError(
                f'Canvas writes are locked off in config.json, so {what} is '
                'read-only here. Remove "allow_canvas_writes": false to let '
                'this tool make changes -- each one still has to be confirmed.')

    def _gate(self, kind: str, payload, summary: str, token: str | None,
              detail: str = "", what: str = "this") -> None:
        """Both checks, in the order they should fail.

        The hard lock first, because if writes are bolted off there is no point
        asking for a confirmation that can never be spent. Then the
        confirmation itself, which is what actually protects a live course:
        nothing reaches Canvas until this exact change has been shown and
        agreed to.
        """
        self._require_writes(what)
        self.confirm.require(kind, payload, summary, token, detail)

    def _stop_holding_grades(self, course_id, assignment_id, log) -> None:
        """Turn off a manual post policy, if this assignment still has one.

        Older builds set that hold before every push. Students should see a
        grade when it is written, including one entered in SpeedGrader. A
        failure here does not stop the push: the grades are posted afterwards
        either way.
        """
        try:
            manual = (self.policy(course_id, assignment_id) or {}).get("effective_manual")
        except Exception as exc:  # noqa: BLE001
            log(f"could not read how Canvas posts this assignment ({exc})")
            return
        if not manual:
            return
        log("this assignment was holding new grades; turning that off")
        try:
            self.client.set_assignment_post_policy(assignment_id, False)
        except Exception as exc:  # noqa: BLE001
            log(f"could not turn off the hold ({exc}); the grades will still be posted")
            return
        audit.record(self.course_dir(course_id), "grade", "post-policy",
                     "Stopped holding new grades on this assignment. Students "
                     "can see a grade when it is written.",
                     course_id=course_id,
                     detail={"assignment_id": str(assignment_id), "manual": False})

    def pull(self, course_id, assignment_id) -> dict:
        """Refresh the Canvas side and merge. Cheap enough to run on a timer."""
        return gradesync.pull_grades(self.cfg, self.client, self.store,
                                     course_id, assignment_id, self.me_id)

    def release(self, course_id, assignment_id, only: list[str] | None,
                hide: bool, log=lambda _m: None,
                confirm_token: str | None = None) -> dict:
        """Canvas's own Post button, then a pull so posted_at is fresh.

        Hiding grades is not offered. A request that asks to hide them is
        refused before anything is sent.
        """
        if hide:
            raise ValueError("Grades are not hidden from here.")
        who = f"{len(only)} selected student(s)" if only else "every graded student"
        self._gate("release",
                   {"course_id": str(course_id),
                    "assignment_id": str(assignment_id),
                    "only": sorted(str(u) for u in (only or [])),
                    "hide": bool(hide)},
                   (f"Hide grades from {who}" if hide
                    else f"Show grades to {who} -- they see them at once"),
                   confirm_token, what="making grades live")
        log(("hiding grades for " if hide else "making grades live for ") + who)
        progress = self.client.release_grades(assignment_id, only, hide=hide)
        state = self.client.wait_progress(progress.get("_id"))
        log(f"Canvas reports: {state.get('workflow_state', 'unknown')}")
        if state.get("workflow_state") == "failed":
            raise RuntimeError(f"Canvas could not finish: {state.get('message') or state}")
        log("re-reading the gradebook")
        out = self.pull(course_id, assignment_id)
        out["hide"] = hide
        out["progress_state"] = state.get("workflow_state")
        name = (self.store.assignment(course_id, assignment_id) or {}).get("name") \
            or f"assignment {assignment_id}"
        audit.record(self.course_dir(course_id), "grade",
                     "hidden" if hide else "released",
                     ("Hid the grades on \"%s\" from %s." if hide
                      else "Made the grades on \"%s\" visible to %s.") % (name, who),
                     count=len(only or []) or None, course_id=course_id,
                     url=f"{self.cfg.base_url}/courses/{course_id}/gradebook",
                     detail={"assignment_id": str(assignment_id),
                             "user_ids": [str(u) for u in (only or [])]})
        return out

    def resolve(self, course_id, assignment_id, only: list[str], choice: str) -> dict:
        return gradesync.resolve_conflicts(self.store, course_id, assignment_id, only, choice)

    def push(self, course_id, assignment_id, dry_run: bool = True,
             only: list[str] | None = None, include_comments: bool = False,
             show: bool = False, log=lambda _m: None,
             confirm_token: str | None = None, comment_mode: str | None = None) -> dict:
        draft = self.store.draft(course_id, assignment_id)
        extracted = self.store.extracted(course_id, assignment_id)
        entries = draft.get("students", {})
        targets = [(uid, e) for uid, e in entries.items() if not only or uid in set(only)]

        rubric = draft.get("rubric") or []
        possible = draft.get("points_possible") or 0

        # Students see the grade when it is written. `show` is kept on the
        # signature so an older page cannot ask for a hidden push.
        show = True
        landing = "visible to students as soon as they land"
        mode = comment_mode if comment_mode in ("none", "selected", "all") else (
            "all" if include_comments else "none")

        planned, skipped = [], []
        for uid, entry in targets:
            name = extracted.get(uid, {}).get("name", uid)
            if not curve.is_scored(entry):
                skipped.append({"user_id": uid, "name": name,
                                "why": entry.get("unscored_reason")
                                or ("no submission"
                                    if (extracted.get(uid) or {}).get("status")
                                    == "unsubmitted" else "no score")})
            elif quizgrade.hold_for_review(entry, extracted.get(uid) or {}):
                skipped.append({"user_id": uid, "name": name, "why": "flagged for human review"})
            else:
                # The curved score is the one that counts, and the dry run has to
                # show it: posting a number the instructor never saw would be the
                # worst possible surprise.
                earned = curve.earned_total(entry, rubric)
                score = curve.final_total(entry, rubric, possible)
                item = {"user_id": uid, "name": name, "score": score,
                        "comment": self._push_comment(entry, mode)}
                if not entry.get("total_only"):
                    scores = entry.get("scores") or {}
                    rationales = entry.get("rationales") or {}
                    item["rubric"] = {
                        str(c.get("id")): {
                            "points": scores.get(str(c.get("id"))),
                            "comments": rationales.get(str(c.get("id"))) or "",
                        }
                        for c in rubric
                        if str(c.get("id") or "").isdigit()
                        and scores.get(str(c.get("id"))) is not None
                    }
                info = extracted.get(uid) or {}
                quiz_meta = info.get("quiz") if isinstance(info.get("quiz"), dict) else {}
                questions = quizgrade.question_scores(entry)
                submission_id = (entry.get("quiz_submission_id")
                                 or quiz_meta.get("submission_id"))
                if submission_id and questions:
                    item["quiz"] = {
                        "quiz_id": entry.get("quiz_id") or quiz_meta.get("quiz_id"),
                        "submission_id": submission_id,
                        "attempt": entry.get("quiz_attempt") or quiz_meta.get("attempt") or 1,
                        "questions": {
                            qid: {"score": pts,
                                  "comment": (entry.get("rationales") or {}).get(f"q{qid}") or ""}
                            for qid, pts in questions.items()
                        },
                    }
                    if quizgrade.written_answers_blank(info):
                        item["blank_written"] = True
                if round(score - earned, 2):
                    item["earned"] = earned
                    item["curved_by"] = round(score - earned, 2)
                planned.append(item)

        if dry_run:
            n_curved = sum(1 for p in planned if p.get("curved_by"))
            n_comments = sum(1 for p in planned if p.get("comment"))
            log(f"dry run: would push {len(planned)} score(s)"
                + (f", {n_comments} with a comment" if n_comments else ", scores only")
                + f", skip {len(skipped)}"
                + (f"; {n_curved} include a curve" if n_curved else "")
                + f"; grades land {landing}")
            return {"dry_run": True, "would_post": planned, "skipped": skipped,
                    "include_comments": mode != "none", "comment_mode": mode,
                    "comments_n": n_comments,
                    "show": bool(show), "landing": landing}

        # `show` is deliberately not part of what the token is bound to. Both
        # buttons on the confirmation screen write the same scores to the same
        # students; the only difference is whether the class can read them yet,
        # and that is chosen on that screen rather than in a dialog underneath.
        # Binding it would mean minting a token per button and asking twice.
        self._gate("push",
                   {"course_id": str(course_id),
                    "assignment_id": str(assignment_id),
                    "grades": sorted([str(i["user_id"]), i.get("score")]
                                     for i in planned),
                    # The words as well as the fact: editing a comment after
                    # the dialog was shown has to ask again, as promised.
                    "comments": (sorted([str(i["user_id"]), i.get("comment") or ""]
                                        for i in planned)
                                 if mode != "none" else False)},
                   f"Write {len(planned)} grade(s) to Canvas. Students will "
                   "be able to see them.",
                   confirm_token, what="posting grades")

        self._stop_holding_grades(course_id, assignment_id, log)

        posted, failed = [], []
        settled: dict[str, dict] = {}       # per-student draft changes
        canvas_side: dict[str, dict] = {}   # per-student extracted changes
        n_hidden = n_visible = 0
        for index, item in enumerate(planned, start=1):
            log(f"pushing {index}/{len(planned)}: {item['name']}")
            try:
                quiz = item.get("quiz") or {}
                if quiz.get("submission_id") and quiz.get("questions"):
                    self.client.grade_quiz_questions(
                        course_id, quiz.get("quiz_id"), quiz["submission_id"],
                        quiz.get("attempt") or 1, quiz["questions"])
                sub = self.client.post_grade(course_id, assignment_id, item["user_id"],
                                             score=item["score"], comment=item["comment"] or None,
                                             rubric=item.get("rubric") or None)
                posted.append(item)
                now = datetime.now().isoformat(timespec="seconds")
                # The baseline for the next pull: Canvas and this machine agree
                # on this score as of now. See gradesync.py.
                settled[item["user_id"]] = {"pushed_at": now, "synced_score": item["score"],
                                            "synced_at": now, "conflict": None}
                if item.get("blank_written"):
                    settled[item["user_id"]]["needs_human"] = False
                    settled[item["user_id"]]["human_ok"] = True
                if isinstance(sub, dict) and sub:
                    canvas_side[item["user_id"]] = {
                        "canvas_score": sub.get("score"),
                        "canvas_graded_at": sub.get("graded_at"),
                        "canvas_posted_at": sub.get("posted_at"),
                        "canvas_state": sub.get("workflow_state"),
                    }
                    if sub.get("posted_at"):
                        n_visible += 1
                    else:
                        n_hidden += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({**item, "error": str(exc)})

        # Write through the store rather than saving the snapshot taken before
        # the network calls: a timed pull may have run in the meantime.
        for uid, changes in settled.items():
            self.store.update_student(course_id, assignment_id, uid, **changes)
        with self.store.lock:
            fresh = self.store.extracted(course_id, assignment_id)
            for uid, fields in canvas_side.items():
                if uid in fresh:
                    fresh[uid].update(fields)
            self.store.save_extracted(course_id, assignment_id, fresh)

        log(f"pushed {len(posted)}, failed {len(failed)}")

        # Post anything just written, and any score already in the gradebook
        # that an older hold kept invisible. One step, the students in this push.
        shown = 0
        ids = [str(item["user_id"]) for item in posted]
        scope = {str(u) for u in only} if only else None
        for uid, info in extracted.items():
            uid = str(uid)
            if scope is not None and uid not in scope:
                continue
            if not isinstance(info, dict):
                continue
            if info.get("canvas_score") is None or info.get("canvas_posted_at"):
                continue
            if uid not in ids:
                ids.append(uid)
        if ids:
            log(f"showing {len(ids)} grade(s) to students")
            try:
                progress = self.client.release_grades(assignment_id, ids, hide=False)
                state = self.client.wait_progress(progress.get("_id"))
                if state.get("workflow_state") == "failed":
                    raise RuntimeError(state.get("message") or state)
                shown = len(ids)
                self.pull(course_id, assignment_id)
                n_visible, n_hidden = shown, 0
            except Exception as exc:  # noqa: BLE001
                log(f"WARNING the grades are in Canvas but students may not "
                    f"see them yet: {exc}")

        # Into the account of record: which students got which score, from this
        # account, at this moment. Grades are the other half of what a dispute
        # is usually about, and the ledger next door deliberately does not
        # carry names.
        if posted or failed:
            name = (self.store.assignment(course_id, assignment_id) or {}).get("name") or \
                f"assignment {assignment_id}"
            audit.record(
                self.course_dir(course_id), "grade", "posted",
                f"Wrote {len(posted)} grade(s) to \"{name}\""
                + (f", {len(failed)} refused by Canvas" if failed else "")
                + (f". Students can see {shown}." if shown else "."),
                students=[audit.person(i["user_id"], i.get("name", ""),
                                       score=i.get("score")) for i in posted],
                count=len(posted), course_id=course_id,
                result="failed" if failed and not posted else "ok",
                url=f"{self.cfg.base_url}/courses/{course_id}/gradebook",
                detail={"assignment_id": str(assignment_id),
                        "comments": mode,
                        "comments_n": sum(1 for i in posted if i.get("comment")),
                        "shown": shown, "hidden": n_hidden,
                        "failed": [{"user_id": f.get("user_id"),
                                    "error": str(f.get("error"))[:200]} for f in failed]})

        return {"dry_run": False, "posted": posted, "failed": failed, "skipped": skipped,
                "landing": landing, "show": bool(show), "shown": shown,
                "hidden": n_hidden, "visible": n_visible}


    # ================================================================ tests
    # Everything about administering a test rather than grading one: its
    # settings, and who gets longer on it.

    def quiz_admin(self, course_id, quiz_id, refresh: bool = False) -> dict:
        """One quiz as the editor needs it: settings, plus extensions already
        granted, plus which of the saved roster are actually in this course."""
        key = (str(course_id), str(quiz_id))
        if refresh:
            self._quiz_cache.pop(key, None)
        quiz = self._quiz(course_id, quiz_id, refresh)
        roster = self.roster.load()
        enrolled = self._enrolled(course_id)
        here = [s for s in roster if str(s.user_id) in enrolled]
        limit = quiz.get("time_limit")

        granted = self._granted(course_id, quiz_id)
        rows = []
        for student in here:
            want = student.extra_minutes_for(limit)
            have = granted.get(str(student.user_id)) or {}
            rows.append({
                "user_id": str(student.user_id),
                "name": student.name or enrolled.get(str(student.user_id), ""),
                "accommodation": student.label(),
                "would_be": want,
                "current_extra_time": have.get("extra_time") or None,
                "current_extra_attempts": have.get("extra_attempts") or None,
                "current_manually_unlocked": bool(have.get("manually_unlocked")),
                "in_sync": (want is not None
                            and int(have.get("extra_time") or 0) == int(want)),
            })

        # Anyone with an extension in Canvas who is not on the saved roster.
        # Worth surfacing: it is usually someone set up by hand last term whose
        # approval never made it onto the list.
        unlisted = [
            {"user_id": uid, "name": enrolled.get(uid, f"user {uid}"),
             "extra_time": info.get("extra_time") or None,
             "extra_attempts": info.get("extra_attempts") or None,
             "manually_unlocked": bool(info.get("manually_unlocked"))}
            for uid, info in sorted(granted.items())
            if uid not in {str(x.user_id) for x in here}
        ]

        return {
            "course_id": str(course_id),
            "quiz_id": str(quiz_id),
            "course_label": (self._cached_course_label(course_id) or ""),
            "title": quiz.get("title") or "",
            "assignment_id": quiz.get("assignment_id"),
            "url": quiz.get("html_url") or "",
            "question_count": quiz.get("question_count"),
            "points": quiz.get("points_possible"),
            "settings": {name: quiz.get(name) for name in quizedit.FIELDS},
            "has_access_code": bool(quiz.get("access_code")),
            "fields": {name: {"kind": kind, "label": label}
                       for name, (kind, label) in quizedit.FIELDS.items()},
            "accommodated": rows,
            "unlisted": unlisted,
            "roster_size": len(roster),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _quiz(self, course_id, quiz_id, refresh: bool = False) -> dict:
        key = (str(course_id), str(quiz_id))
        if not refresh and key in self._quiz_cache:
            return self._quiz_cache[key]
        quiz = self.client.quiz(course_id, quiz_id) or {}
        self._quiz_cache[key] = quiz
        return quiz

    def _enrolled(self, course_id) -> dict[str, str]:
        """user_id -> name for the students in one course, cached on disk."""
        students = self.store.students(course_id)
        if not students:
            students = self.client.students(course_id)
            self.store.save_students(course_id, students)
        return {str(s.get("id")): s.get("name") or "" for s in students}

    def _granted(self, course_id, quiz_id) -> dict[str, dict]:
        """The extensions Canvas already holds for a quiz, by user id.

        Canvas keeps these on the submission record, so a quiz nobody has
        touched and nobody has an extension on simply returns nothing.
        """
        out: dict[str, dict] = {}
        try:
            subs = self.client.quiz_submissions(course_id, quiz_id)
        except CanvasError:
            return out
        for sub in subs:
            uid = str(sub.get("user_id") or "")
            if not uid:
                continue
            out[uid] = {"extra_time": sub.get("extra_time"),
                        "extra_attempts": sub.get("extra_attempts"),
                        "manually_unlocked": sub.get("manually_unlocked")}
        return out

    def quiz_settings_save(self, course_id, quiz_id, changes: dict,
                           token: str | None = None) -> dict:
        """Check a settings edit, show it, then write it on the second pass."""
        quiz = self._quiz(course_id, quiz_id, refresh=True)
        checked = quizedit.validate(changes or {}, quiz)
        if not checked["changes"]:
            return {"ok": True, "changed": [], "rejected": checked["rejected"],
                    "message": "Nothing to change."}

        self._gate("quiz_settings",
                   {"course_id": str(course_id), "quiz_id": str(quiz_id),
                    "changes": checked["changes"]},
                   quizedit.summary(checked["described"], quiz), token,
                   detail=json.dumps(checked["described"]),
                   what="quiz settings")

        self.client.update_quiz_settings(course_id, quiz_id, checked["changes"])
        audit.record(self.course_dir(course_id), "tests", "quiz-settings",
                     'Changed the settings on the quiz "%s": %s.'
                     % (quiz.get("title") or quiz_id,
                        quizedit.summary(checked["described"], quiz)),
                     count=len(checked["changes"]), course_id=course_id,
                     url="%s/courses/%s/quizzes/%s" % (self.cfg.base_url, course_id, quiz_id),
                     detail={"quiz_id": str(quiz_id), "changed": checked["described"]})
        # A quiz assignment carries its own copy of the dates. Writing the quiz
        # is enough for Canvas to move both, but the local cache is now stale
        # either way, and so is the schedule's copy of this row.
        self._quiz_cache.pop((str(course_id), str(quiz_id)), None)
        self._item_cache.pop((str(course_id), str(quiz.get("assignment_id"))), None)
        return {"ok": True, "changed": checked["described"],
                "rejected": checked["rejected"],
                "message": quizedit.summary(checked["described"], quiz)}

    # ------------------------------------------------------- the saved roster
    def accommodation_roster(self) -> dict:
        students, dropped = self.roster.load_report()
        sync = {}
        if getattr(self, "state_sync", None):
            try:
                sync = self.state_sync.status(statesync.ROSTER_KEY)
            except Exception:  # noqa: BLE001
                sync = {"state": "error"}
        return {"students": [s.to_json() for s in students],
                "count": len(students),
                "dropped": dropped,
                "path": str(self.roster.path),
                "sync": sync}

    def accommodation_save(self, rows: list[dict]) -> dict:
        """Replace the standing list and push it to Canvas user files.

        Saving the list is not a Canvas course write (no quiz is changed), so
        it needs no confirm token. Applying extras to quizzes still does.
        """
        parsed, bad = [], []
        for row in (rows or []):
            try:
                parsed.append(accommodations.parse_student(row))
            except ValueError as exc:
                bad.append({"row": str(row.get("name") or row.get("user_id") or "?"),
                            "why": str(exc)})
        if bad and not parsed:
            raise ValueError("; ".join(b["why"] for b in bad))
        before = {str(s.user_id): s.label() for s in self.roster.load()}
        self.roster.save(parsed)
        pushed = None
        if getattr(self, "state_sync", None):
            self.state_sync.mark_dirty(statesync.ROSTER_KEY)
            try:
                pushed = self.state_sync.push(statesync.ROSTER_KEY)
            except statesync.Conflict as exc:
                pushed = {"state": "diverged", "did": "diverged", "error": str(exc),
                          "remote_rev": (exc.remote or {}).get("rev"),
                          "remote_machine": ((exc.remote or {}).get("machine") or {}).get("name")}
            except Exception as exc:  # noqa: BLE001
                pushed = {"state": "error", "did": "error", "error": str(exc)[:200]}
        # Who was added, dropped or changed, and to what. The list is the
        # college's approvals as this tool understands them, so the moment it
        # changes is worth a line: "it was never on the list" and "it was taken
        # off the list in March" are different answers to the same complaint.
        after = {str(s.user_id): s.label() for s in parsed}
        moved = [audit.person(uid, next((s.name for s in parsed
                                         if str(s.user_id) == uid), uid),
                              was=before.get(uid, "not on the list"),
                              now=after.get(uid, "removed"))
                 for uid in sorted(set(before) | set(after))
                 if before.get(uid) != after.get(uid)]
        if moved:
            audit.record(self.store.root, "accommodations", "roster",
                         f"Changed the standing accommodation list: "
                         f"{len(moved)} student(s) added, removed or altered. "
                         f"{len(parsed)} on the list now.",
                         students=moved, count=len(moved))
        out = {**self.accommodation_roster(), "rejected": bad}
        if pushed:
            out["sync"] = {**(out.get("sync") or {}), **pushed}
        return out

    def accommodation_hydrate(self) -> dict:
        """Pull the standing roster from Canvas user files onto this machine."""
        if not getattr(self, "state_sync", None):
            return {"did": "skipped", "detail": "state sync is not running"}
        return self.state_sync.hydrate_roster()

    def accommodation_resolve(self, take: str) -> dict:
        """Keep this computer's roster or the one waiting in Canvas."""
        if not getattr(self, "state_sync", None):
            raise ValueError("state sync is not running")
        return self.state_sync.resolve(statesync.ROSTER_KEY, take)

    def taught_students(self, refresh: bool = False, term: str | None = None) -> dict:
        """Every student across every course this token teaches, deduplicated.

        A Canvas user id is global to the instance, so the same person in three
        of your courses is one row here with three courses listed. That is what
        makes a college-granted accommodation a single entry rather than one
        per section.
        """
        courses = [c for c in self.courses(refresh) if not c.get("excluded")]
        if term:
            courses = [c for c in courses if (c.get("term_label") or "") == term]
        people: dict[str, dict] = {}
        failed = []
        for course in courses:
            cid = str(course.get("id"))
            try:
                students = (self.client.students(cid) if refresh
                            else self._students_cached(cid))
            except CanvasError as exc:
                failed.append({"course_id": cid,
                               "course": course.get("name") or cid,
                               "error": str(exc)[:160]})
                continue
            label = (self._cached_course_label(cid)
                     or schedule.course_title(course) or cid)
            for student in students:
                uid = str(student.get("id") or "")
                if not uid:
                    continue
                row = people.setdefault(uid, {
                    "user_id": uid,
                    "name": student.get("name") or "",
                    "sortable_name": student.get("sortable_name") or "",
                    "sis_user_id": str(student.get("sis_user_id") or ""),
                    "login_id": str(student.get("login_id") or ""),
                    "courses": [],
                })
                if label not in row["courses"]:
                    row["courses"].append(label)
                refs = row.setdefault("course_refs", [])
                if not any(str(r.get("id")) == cid for r in refs):
                    refs.append({
                        "id": cid,
                        "name": label,
                        "term": course.get("term_label") or "",
                    })

        listed = {str(s.user_id) for s in self.roster.load()}
        out = sorted(people.values(),
                     key=lambda r: (r["sortable_name"] or r["name"]).lower())
        for row in out:
            row["on_roster"] = row["user_id"] in listed
        return {"students": out, "count": len(out),
                "courses": len(courses), "failed": failed}

    def _students_cached(self, course_id) -> list[dict]:
        students = self.store.students(course_id)
        if not students:
            students = self.client.students(course_id)
            self.store.save_students(course_id, students)
        return students

    # ----------------------------------------------------------- the planner
    def accommodation_plan(self, scope: str, course_id=None, quiz_id=None,
                           user_ids: list | None = None,
                           timed_only: bool = True, term: str | None = None,
                           log=lambda *_a, **_k: None) -> dict:
        """What applying the roster would change, without changing anything.

        scope is "quiz" (one test), "course" (every quiz in one course), or
        "all" (every course this token teaches). The last one is the point of
        the feature: a college accommodation applies everywhere, so it should
        be set everywhere in one pass.
        """
        scope = str(scope or "quiz")
        if scope not in ("quiz", "course", "all"):
            raise ValueError(f"unknown scope {scope!r}")

        students = self.roster.load()
        if user_ids:
            want = {str(u) for u in user_ids}
            students = [s for s in students if str(s.user_id) in want]
        if not students:
            raise ValueError(
                "Nobody is selected. Add students to the accommodation list "
                "first -- Student roster will pull them from your courses.")

        targets = self._targets(scope, course_id, quiz_id, timed_only, term, log)
        if not targets:
            raise ValueError(
                "No classic quizzes in scope. Canvas only takes extra time "
                "through this API for classic quizzes -- New Quizzes and "
                "publisher tests (Pearson, ALEKS) hold their own timers.")

        log(f"reading what Canvas already holds on {len(targets)} quiz(zes)",
            0, len(targets))
        existing: dict[tuple[str, str], dict] = {}
        for index, target in enumerate(targets, start=1):
            for uid, info in self._granted(target["course_id"],
                                           target["quiz_id"]).items():
                existing[(str(target["quiz_id"]), uid)] = info
            log(f"checked {index}/{len(targets)}: {target['title'][:40]}",
                index, len(targets))

        result = accommodations.plan(students, targets, existing)
        result["scope"] = scope
        result["summary"] = accommodations.describe(result)
        # The plan and its review screen are one step here, so the confirmation
        # is handed over now rather than by refusing a write nobody attempted.
        # It only spends on these exact batches.
        if result["batches"]:
            result["confirm"] = self.confirm.offer(
                "accommodations", _shape(result["batches"]), result["summary"])
        result["students"] = [s.to_json() for s in students]
        result["quizzes_in_scope"] = len(targets)
        result["courses_in_scope"] = len({t["course_id"] for t in targets})
        result["term"] = term
        return result

    def _targets(self, scope: str, course_id, quiz_id, timed_only: bool,
                 term: str | None = None,
                 log=lambda *_a, **_k: None) -> list[dict]:
        """The quizzes in scope, each with the ids enrolled in its course."""
        if scope == "quiz":
            if not (course_id and quiz_id):
                raise ValueError("that scope needs a course and a quiz")
            course_ids = [str(course_id)]
        elif scope == "course":
            if not course_id:
                raise ValueError("that scope needs a course")
            course_ids = [str(course_id)]
        else:
            # "Every course I teach" means the term being taught, not the
            # sandboxes and last year's shells that also belong to this token.
            # The term comes from the page, which already has a term picker.
            wanted = term or terms.summarize(self.courses(False)).get("default")
            course_ids = [
                str(c.get("id")) for c in self.courses(False)
                if not c.get("excluded")
                and (not wanted or c.get("term_label") == wanted)
            ]

        # Only the single-quiz scope narrows to one quiz. The wider scopes carry
        # the quiz you opened them from -- it is what identified the course --
        # and honouring it there would silently turn "every quiz in this course"
        # back into the one test you were already looking at.
        only_quiz = str(quiz_id) if scope == "quiz" else None

        out: list[dict] = []
        for cid in course_ids:
            try:
                enrolled = set(self._enrolled(cid))
                quizzes = self.client.quizzes(cid)
            except CanvasError:
                continue
            label = self._cached_course_label(cid) or cid
            for quiz in quizzes:
                if only_quiz and str(quiz.get("id")) != only_quiz:
                    continue
                if timed_only and not quiz.get("time_limit"):
                    continue
                out.append({
                    "course_id": cid,
                    "course_label": label,
                    "quiz_id": str(quiz.get("id")),
                    "assignment_id": quiz.get("assignment_id"),
                    "title": quiz.get("title") or "",
                    "time_limit": quiz.get("time_limit"),
                    "published": bool(quiz.get("published", True)),
                    "enrolled": enrolled,
                })
            log(f"{label}: {len(out)} quiz(zes) so far")
        out.sort(key=lambda q: (q["course_label"], q["title"]))
        return out

    def accommodation_apply(self, scope: str, course_id=None, quiz_id=None,
                            user_ids: list | None = None,
                            timed_only: bool = True, term: str | None = None,
                            token: str | None = None,
                            log=lambda *_a, **_k: None) -> dict:
        """Send the plan. One Canvas call per quiz, however many students."""
        plan = self.accommodation_plan(scope, course_id, quiz_id, user_ids,
                                       timed_only, term, log)
        batches = plan["batches"]
        if not batches:
            return {**plan, "applied": [], "failed": [],
                    "message": "Everything was already set. Nothing sent."}

        # The fingerprint is the batches themselves, so agreeing to a plan
        # cannot apply a different one -- and a plan that has drifted since it
        # was shown (someone edited a quiz meanwhile) asks again.
        self._gate("accommodations", _shape(batches), plan["summary"], token,
                   detail=f"{len(batches)} quiz(zes)", what="accommodations")

        applied, failed = [], []
        total = len(batches)
        log(f"applying to {total} quiz(zes)", 0, total)
        for index, batch in enumerate(batches, start=1):
            line = (f"{batch['course_label']} - {batch['quiz_title']}: "
                    f"{len(batch['extensions'])} student(s)")
            log(f"{index}/{total} {line}", index - 1, total)
            # Who this batch is for, named, because an accommodation that a
            # student later says they never got is the case this record exists
            # to answer. Both outcomes are written down: a refusal from Canvas
            # is as much a fact about what happened as a success.
            people = [audit.person(r["user_id"], r.get("name", ""),
                                   extra_time=r.get("extra_time"),
                                   extra_attempts=r.get("extra_attempts"),
                                   approval=r.get("detail", ""))
                      for r in plan["rows"]
                      if r["course_id"] == batch["course_id"]
                      and r["quiz_id"] == batch["quiz_id"]]
            cdir = self.course_dir(batch["course_id"])
            try:
                self.client.quiz_extensions(batch["course_id"], batch["quiz_id"],
                                            batch["extensions"])
                applied.append({**{k: batch[k] for k in
                                   ("course_id", "quiz_id", "course_label",
                                    "quiz_title", "time_limit")},
                                "students": batch["names"],
                                "count": len(batch["extensions"])})
                audit.record(
                    cdir, "accommodations", "applied",
                    f"Set testing accommodations on the quiz \"{batch['quiz_title']}\" "
                    f"in {batch['course_label']} for {len(people)} student(s).",
                    students=people, count=len(people),
                    course_id=batch["course_id"],
                    url=f"{self.cfg.base_url}/courses/{batch['course_id']}"
                        f"/quizzes/{batch['quiz_id']}",
                    detail={"quiz_id": batch["quiz_id"],
                            "quiz_time_limit": batch["time_limit"],
                            "scope": scope})
            except (CanvasError, ValueError) as exc:
                failed.append({"course_label": batch["course_label"],
                               "quiz_title": batch["quiz_title"],
                               "error": f"{type(exc).__name__}: {exc}"[:220]})
                audit.record(
                    cdir, "accommodations", "failed",
                    f"Tried to set testing accommodations on the quiz "
                    f"\"{batch['quiz_title']}\" in {batch['course_label']} for "
                    f"{len(people)} student(s); Canvas refused.",
                    students=people, count=len(people), result="failed",
                    course_id=batch["course_id"],
                    detail={"quiz_id": batch["quiz_id"],
                            "error": f"{type(exc).__name__}: {exc}"[:220]})
            log(f"{index}/{total} done", index, total)

        people = {r["user_id"] for r in plan["rows"]}
        return {
            "applied": applied, "failed": failed,
            "rows": plan["rows"], "skipped": plan["skipped"],
            "scope": scope,
            "message": (f"{sum(a['count'] for a in applied)} accommodation(s) set "
                        f"across {len(applied)} quiz(zes) for {len(people)} "
                        f"student(s)"
                        + (f"; {len(failed)} quiz(zes) failed" if failed else "")),
        }


def _shape(batches: list[dict]) -> list:
    """A plan reduced to what will actually be sent, for fingerprinting."""
    return sorted(
        [batch["course_id"], batch["quiz_id"],
         sorted([[str(e.get("user_id")), e.get("extra_time"),
                  e.get("extra_attempts"), bool(e.get("manually_unlocked"))]
                 for e in batch["extensions"]])]
        for batch in batches
    )


def make_handler(app: App):
    studio_key = getattr(app, "studio_key", None) or secrets.token_hex(16)
    app.studio_key = studio_key

    def cfg_port() -> int:
        return int(app.cfg.port)

    class Handler(SimpleHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEB_DIR), **kw)

        def _listen_port(self) -> int:
            try:
                return int(self.server.server_address[1])
            except Exception:
                return cfg_port()

        def _studio_key_ok(self) -> bool:
            return keys_match(request_studio_key(self.headers), studio_key)

        def _hook_secret_ok(self, body: dict) -> bool:
            mgr = getattr(app, "assistant", None)
            expected = getattr(mgr, "secret", None) if mgr is not None else None
            got = body.get("secret") if isinstance(body, dict) else None
            if not isinstance(expected, str) or not isinstance(got, str):
                return False
            return keys_match(got, expected)

        def _allow_api(self, method: str, parts: list, body: dict) -> bool:
            """Host + key for /api. GET /api/health is open. The Assistant hook
            may skip the browser key when body.secret is this launch's hook secret."""
            is_health = method == "GET" and parts[1:] == ["health"]
            if is_health:
                return True
            if not api_host_allowed(self.headers.get("Host") or "", self._listen_port()):
                self._json({"error": "This request did not target the CourseForge "
                                     "Studio server on this machine, so it was "
                                     "refused."}, 403)
                return False
            is_permission = method == "POST" and parts[1:] == ["assistant", "permission"]
            if is_permission and self._hook_secret_ok(body):
                return True
            if not self._studio_key_ok():
                self._json({"error": "Studio was restarted, so this page is out "
                                     "of date. Reload this browser page, then "
                                     "try again."}, 403)
                return False
            return True

        def _serve_index(self):
            path = WEB_DIR / "index.html"
            try:
                html = path.read_text(encoding="utf-8")
            except OSError:
                return self._json({"error": "not found"}, 404)
            nonce = secrets.token_urlsafe(16)
            if 'name="cf-secret"' not in html:
                html = html.replace(
                    "<head>",
                    f'<head>\n<meta name="cf-secret" content="{studio_key}">',
                    1)
            html = html.replace(
                '<script type="importmap">',
                f'<script type="importmap" nonce="{nonce}">',
                1)
            body = html.encode("utf-8")
            self._csp_nonce = nonce
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Set-Cookie",
                f"{STUDIO_COOKIE}={studio_key}; Path=/; HttpOnly; SameSite=Strict")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        # ---------------------------------------------------------- helpers
        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-App-Build", build_id())
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        # A screen recording is hundreds of megabytes, and a <video> tag asks for
        # it in pieces: it sends Range for the first frames, then again for every
        # scrub of the timeline. Answering the whole file every time would read
        # it all into memory and leave the player unable to seek at all, so serve
        # byte ranges and stream from disk.
        def _send_file(self, path, ctype: str, download: bool = False):
            try:
                size = path.stat().st_size
            except OSError:
                return self._json({"error": "not found"}, 404)

            start, end = 0, size - 1
            partial = False
            header = (self.headers.get("Range") or "").strip()
            if header.startswith("bytes=") and size:
                first, _, last = header[6:].partition("-")
                try:
                    if first:
                        start = int(first)
                        end = int(last) if last else size - 1
                    elif last:                      # bytes=-500: the tail
                        start = max(0, size - int(last))
                    else:
                        raise ValueError
                except ValueError:
                    start, end = 0, size - 1
                else:
                    end = min(end, size - 1)
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    partial = True

            length = (end - start + 1) if size else 0
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if download:
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{path.name}"')
            self.end_headers()
            if self.command == "HEAD" or not length:
                return
            try:
                with open(path, "rb") as fh:
                    fh.seek(start)
                    left = length
                    while left > 0:
                        chunk = fh.read(min(256 * 1024, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass          # the player seeked away mid-send; nothing to do

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            # Nothing the page sends is more than a few hundred kilobytes; a
            # length past this is a mistake or a hostile client, and reading
            # it would pin a thread and the memory it asks for.
            if length <= 0 or length > MAX_BODY_BYTES:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}

        def _same_origin(self) -> bool:
            """Reject a POST that a different website sent to this port.

            The server listens on 127.0.0.1, which stops other machines but not
            the browser: any page the user visits can post to localhost, and
            these endpoints write the Canvas token and the Canvas host. Pointing
            the host at an attacker's server would hand them the real token on
            the next request, so the write endpoints need to know the request
            came from this app's own page.

            A browser POST always carries Origin; a mismatch or a missing Origin
            is refused. A plain HTML form cannot set a JSON content type, so
            requiring one blocks that path. The Assistant hook is the one POST
            allowed without Origin, and only when body.secret matches.
            """
            origin = (self.headers.get("Origin") or "").rstrip("/")
            if not origin:
                return False
            port = self._listen_port()
            allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            if origin not in allowed:
                return False
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            return ctype == "application/json"

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            nonce = getattr(self, "_csp_nonce", "")
            script = f"'self' 'nonce-{nonce}'" if nonce else "'self'"
            # wasm-unsafe-eval lets the local Word viewer compile its parser.
            # It does not allow eval of script. The viewer draws .docx pages in
            # the browser; the file is not sent anywhere.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' blob: data:; "
                "media-src 'self' blob:; font-src 'self' data:; "
                "script-src %s 'wasm-unsafe-eval'; "
                "worker-src 'self' blob: data:; style-src 'self' "
                "'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'" % script)
            super().end_headers()

        def log_message(self, fmt, *args):
            pass

        # --------------------------------------------------------------- GET
        def do_GET(self):
            url = urlparse(self.path)
            parts = [p for p in url.path.strip("/").split("/") if p]
            query = parse_qs(url.query)
            refresh = query.get("refresh", ["0"])[0] in ("1", "true")

            if not parts or parts[0] != "api":
                if not parts or parts == ["index.html"]:
                    return self._serve_index()
                return super().do_GET()

            if not self._allow_api("GET", parts, {}):
                return

            if routing.dispatch(app, self, "GET", url, query, {}):
                return

            try:
                # /api/health
                if parts[1:] == ["health"]:
                    return self._json(app.health())
                # /api/setup
                if parts[1:] == ["setup"]:
                    return self._json(app.setup_state())
                # /api/schedule/item/<course_id>/<assignment_id>
                if (len(parts) == 5 and parts[1] == "schedule"
                        and parts[2] == "item"):
                    return self._json(app.schedule_item(parts[3], parts[4]))
                # /api/schedule?term=...
                if parts[1:] == ["schedule"]:
                    return self._json(app.schedule(query.get("term", [None])[0]))
                # /api/claude-check
                if parts[1:] == ["claude-check"]:
                    return self._json(app.check_claude())
                # /api/courses
                if parts[1:] == ["courses"]:
                    return self._json(app.courses(refresh))
                # /api/terms
                if parts[1:] == ["terms"]:
                    return self._json(app.term_summary(refresh))
                # /api/storage   -- Canvas file room used by handoffs
                if parts[1:] == ["storage"]:
                    return self._json(app.storage())
                # /api/courses/<cid>/assignments
                if len(parts) == 4 and parts[1] == "courses" and parts[3] == "assignments":
                    return self._json(app.assignments(parts[2], refresh))
                # /api/jobs/<id>
                if len(parts) == 3 and parts[1] == "jobs":
                    job = app.jobs.get(parts[2])
                    return self._json(job or {"error": "no such job"}, 200 if job else 404)
                # /api/a/<cid>/<aid>/missing   -- who has turned nothing in
                if len(parts) == 5 and parts[1] == "a" and parts[4] == "missing":
                    return self._json(app.missing_for(parts[2], parts[3]))
                # /api/a/<cid>/<aid>/handoff   -- here vs Canvas
                if len(parts) == 5 and parts[1] == "a" and parts[4] == "handoff":
                    quick = query.get("local", ["0"])[0] in ("1", "true")
                    return self._json(app.handoff_status(parts[2], parts[3],
                                                         remote=not quick))
                # /api/a/<cid>/<aid>
                if len(parts) == 4 and parts[1] == "a":
                    return self._json(app.workspace(parts[2], parts[3]))
                # /api/quiz/<cid>/<qid>   -- settings + who has longer
                if len(parts) == 4 and parts[1] == "quiz":
                    return self._json(app.quiz_admin(parts[2], parts[3], refresh))

                # /api/accommodations   -- the saved list
                if parts[1:] == ["accommodations"]:
                    return self._json(app.accommodation_roster())

                # /api/accommodations/sync   -- Canvas replica + hydrate
                if parts[1:] == ["accommodations", "sync"]:
                    return self._json(app.accommodation_hydrate())

                # /api/accommodations/students   -- everyone you teach, deduped
                if parts[1:] == ["accommodations", "students"]:
                    return self._json(app.taught_students(refresh))

                # /api/a/<cid>/<aid>/file?name=...
                if len(parts) == 5 and parts[1] == "a" and parts[4] == "file":
                    raw_name = query.get("name", [""])[0]
                    target = submission_file(
                        app.store.assignment_dir(parts[2], parts[3]) / "files",
                        raw_name)
                    if target is None:
                        return self._json({"error": "not found"}, 404)
                    want_dl = query.get("dl", ["0"])[0] in ("1", "true")
                    ctype, as_attachment = student_file_response(target.name, want_dl)
                    return self._send_file(target, ctype, download=as_attachment)
                return self._json({"error": "unknown endpoint"}, 404)
            except confirm.ConfirmRequired as exc:
                return self._json(exc.payload(), 409)
            except confirm.ConfirmStale as exc:
                return self._json({"error": str(exc), "confirm_stale": True}, 409)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            except CanvasError as exc:
                return self._json({"error": str(exc), "status": exc.status}, 502)
            except Exception as exc:  # noqa: BLE001
                log_server_error(exc, getattr(app.cfg, "data_dir", None))
                return self._json({"error": _public_error(exc)}, 500)

        # -------------------------------------------------------------- POST
        def do_POST(self):
            url = urlparse(self.path)
            parts = [p for p in url.path.strip("/").split("/") if p]
            body = self._body()

            if len(parts) < 2 or parts[0] != "api":
                return self._json({"error": "unknown endpoint"}, 404)

            if not self._allow_api("POST", parts, body):
                return

            is_permission = parts[1:] == ["assistant", "permission"]
            if not (is_permission and self._hook_secret_ok(body)):
                if not self._same_origin():
                    return self._json(
                        {"error": "Studio was restarted, so this page is out of "
                                  "date. Reload this browser page, then try again."},
                        403)

            if routing.dispatch(app, self, "POST", url, {}, body):
                return

            try:
                if parts[1:] == ["settings"]:
                    return self._json(app.set_settings(body))

                # /api/setup/token  {token, base_url, force}
                # Write-only: the token never comes back out of this server.
                if parts[1:] == ["setup", "token"]:
                    return self._json(app.save_token(
                        body.get("token") or "",
                        body.get("base_url"),
                        bool(body.get("force"))))

                # /api/quiz/<cid>/<qid>/settings  {changes, confirm}
                if len(parts) == 5 and parts[1] == "quiz" and parts[4] == "settings":
                    return self._json(app.quiz_settings_save(
                        parts[2], parts[3], body.get("changes") or {},
                        body.get("confirm")))

                # /api/accommodations  {students: [...]}   -- list + Canvas replica
                if parts[1:] == ["accommodations"]:
                    return self._json(app.accommodation_save(
                        body.get("students") or []))

                # /api/accommodations/resolve  {take: local|remote}
                if parts[1:] == ["accommodations", "resolve"]:
                    return self._json(app.accommodation_resolve(
                        body.get("take") or ""))

                # /api/accommodations/plan  {scope, course_id, quiz_id, ...}
                if parts[1:] == ["accommodations", "plan"]:
                    job = app.jobs.start("accommodations", lambda log:
                        app.accommodation_plan(
                            body.get("scope") or "quiz", body.get("course_id"),
                            body.get("quiz_id"), body.get("user_ids"),
                            bool(body.get("timed_only", True)),
                            body.get("term"), log))
                    return self._json({"job": job})

                # /api/accommodations/apply  {..., confirm}
                if parts[1:] == ["accommodations", "apply"]:
                    job = app.jobs.start("accommodations", lambda log:
                        app.accommodation_apply(
                            body.get("scope") or "quiz", body.get("course_id"),
                            body.get("quiz_id"), body.get("user_ids"),
                            bool(body.get("timed_only", True)),
                            body.get("term"), body.get("confirm"), log))
                    return self._json({"job": job})

                # /api/schedule/announce  {course_id, assignment_id, extra}
                if parts[1:] == ["schedule", "announce"]:
                    job = app.jobs.start("announce", lambda log: app.announce_draft(
                        body.get("course_id"), body.get("assignment_id"),
                        body.get("extra") or "", log))
                    return self._json({"job": job})

                # /api/schedule/announce-batch  {targets: [...], extra}
                if parts[1:] == ["schedule", "announce-batch"]:
                    job = app.jobs.start("announce", lambda log: app.announce_batch(
                        body.get("targets") or [], body.get("extra") or "", log))
                    return self._json({"job": job})

                # /api/schedule/instruct  {instruction, now_local, zone, term}
                if parts[1:] == ["schedule", "instruct"]:
                    job = app.jobs.start("instruct", lambda log: app.instruct_plan(
                        body.get("instruction") or "", body.get("term"),
                        body.get("now_local") or "", body.get("zone") or "", log))
                    return self._json({"job": job})

                # /api/schedule/apply  {operations, dry_run, term}
                if parts[1:] == ["schedule", "apply"]:
                    job = app.jobs.start("apply", lambda log: app.instruct_apply(
                        body.get("operations") or [],
                        bool(body.get("dry_run", True)), body.get("term"), log,
                        body.get("confirm")))
                    return self._json({"job": job})

                if parts[1:] == ["schedule", "refresh"]:
                    term = body.get("term") or None
                    job = app.jobs.start(
                        "schedule", lambda log: app.schedule_refresh(term, log))
                    return self._json({"job": job})

                if len(parts) >= 5 and parts[1] == "a":
                    cid, aid, action = parts[2], parts[3], parts[4]

                    if action == "sync":
                        # One sync per assignment at a time: a second request
                        # while one is running joins it (see Jobs.start).
                        job = app.jobs.start("sync", lambda log: grader.sync_assignment(
                            app.cfg, app.client, app.store, cid, aid, log, me_id=app.me_id),
                            key=f"sync:{cid}:{aid}")
                        return self._json({"job": job})

                    if action == "pull":
                        # Synchronous on purpose: one paged read, no attachments,
                        # and the page calls it on a timer without a dialog.
                        return self._json(app.pull(cid, aid))

                    if action == "release":
                        only = [str(u) for u in (body.get("only") or [])] or None
                        hide = bool(body.get("hide"))
                        job = app.jobs.start("release", lambda log: app.release(
                            cid, aid, only, hide, log, body.get("confirm")))
                        return self._json({"job": job})

                    if action == "resolve":
                        ids = [str(u) for u in (body.get("only") or [])]
                        if not ids:
                            raise ValueError("no students given")
                        return self._json(app.resolve(cid, aid, ids, str(body.get("choice") or "")))

                    if action == "grade":
                        only = body.get("only") or None
                        policy = app.course_late_policy(cid)
                        job = app.jobs.start("grade", lambda log: grader.grade_assignment(
                            app.cfg, app.store, cid, aid, only=only, progress=log,
                            late_policy=policy))
                        return self._json({"job": job})

                    if action == "instructions":
                        text = body.get("text", "")
                        app.store.save_instructions(cid, aid, text)
                        lifted = app.apply_late_instructions(cid, aid, text)
                        return self._json({"ok": True, "late_waived": lifted})

                    if action == "student":
                        uid = parts[5] if len(parts) > 5 else body.get("user_id")
                        return self._json({"ok": True,
                                           "student": app.edit_student(cid, aid, uid, body)})

                    if action == "handoff":
                        how = str(body.get("do") or "send")
                        if how == "auto":
                            job = app.jobs.start("handoff", lambda log:
                                                 app.handoff_reconcile(cid, aid, log))
                            return self._json({"job": job})
                        if how == "fetch":
                            job = app.jobs.start("handoff", lambda log: app.handoff_fetch(
                                cid, aid, log, body.get("confirm")))
                            return self._json({"job": job})
                        if how == "off":
                            return self._json(app.handoff_disable(
                                cid, aid, bool(body.get("remove")),
                                body.get("confirm")))
                        job = app.jobs.start("handoff", lambda log: app.handoff_send(
                            cid, aid, body.get("blend"), log))
                        return self._json({"job": job})

                    if action == "remind":
                        job = app.jobs.start("remind", lambda log: app.remind_missing(
                            cid, aid, body.get("only") or None,
                            body.get("subject") or "", body.get("body") or "",
                            log, body.get("confirm")))
                        return self._json({"job": job})

                    if action == "export":
                        return self._json(app.export(cid, aid))

                    if action == "ask":
                        uid = parts[5] if len(parts) > 5 else body.get("user_id")
                        question = (body.get("question") or "").strip()
                        if not question:
                            raise ValueError("no question given")
                        job = app.jobs.start("ask", lambda log: grader.ask_about(
                            app.cfg, app.store, cid, aid, uid, question,
                            body.get("history") or [], progress=log))
                        return self._json({"job": job})

                    if action == "blend":
                        only = body.get("only") or None
                        # Finished renders are reused by default; force re-reads
                        # a file whose result is cached but no longer trusted.
                        force = bool(body.get("force", False))
                        job = app.jobs.start("blend", lambda log: blender.process_assignment(
                            app.cfg, app.store, cid, aid, only, log, force=force))
                        return self._json({"job": job})

                    if action == "summary":
                        only = body.get("only") or None
                        job = app.jobs.start("summary", lambda log: grader.class_summary(
                            app.cfg, app.store, cid, aid,
                            bool(body.get("include_missing", False)), progress=log,
                            only=only))
                        return self._json({"job": job})

                    if action == "curve":
                        return self._json(app.curve(cid, aid, body))

                    if action == "review":
                        ids = [str(u) for u in (body.get("only") or [])]
                        if not ids:
                            raise ValueError("no students given")
                        return self._json(app.mark_reviewed(
                            cid, aid, ids, bool(body.get("reviewed", True))))

                    if action == "teaching":
                        only = body.get("only") or None
                        job = app.jobs.start("teaching", lambda log: grader.teaching_read(
                            app.cfg, app.store, cid, aid, only=only, progress=log))
                        return self._json({"job": job})

                    if action == "overlap":
                        only = body.get("only") or None
                        job = app.jobs.start("overlap", lambda log: grader.overlap_check(
                            app.cfg, app.store, cid, aid, only=only, progress=log))
                        return self._json({"job": job})

                    if action == "push":
                        dry = bool(body.get("dry_run", True))
                        only = body.get("only") or None
                        mode = body.get("comments")
                        if mode not in ("none", "selected", "all"):
                            mode = "all" if body.get("include_comments") else "none"
                        show = bool(body.get("show", False))
                        job = app.jobs.start("push", lambda log: app.push(
                            cid, aid, dry, only, mode != "none", show, log,
                            body.get("confirm"), comment_mode=mode))
                        return self._json({"job": job})

                return self._json({"error": "unknown endpoint"}, 404)
            except confirm.ConfirmRequired as exc:
                # Nothing was sent. The page shows the summary and posts the
                # token back to go through with it.
                return self._json(exc.payload(), 409)
            except confirm.ConfirmStale as exc:
                return self._json({"error": str(exc), "confirm_stale": True}, 409)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            except PermissionError as exc:
                return self._json({"error": str(exc)}, 403)
            except Exception as exc:  # noqa: BLE001
                log_server_error(exc, getattr(app.cfg, "data_dir", None))
                return self._json({"error": _public_error(exc)}, 500)

    return Handler


def _within(seconds: float, fn):
    """Run fn on a daemon thread and stop waiting. A Canvas call whose address
    lookup never returns must not pin the course list."""
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError("Canvas did not answer within %d seconds." % int(seconds))
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _public_error(exc: BaseException) -> str:
    """The one line the page can show. The type name alone hid a Canvas failure."""
    line = str(exc).splitlines()[0].strip()
    name = type(exc).__name__
    if not line or line == name:
        return name
    return line[:300]


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def build_server(cfg: Config) -> tuple[ThreadedServer, App]:
    """Create the server without starting it, so a GUI can own its lifecycle."""
    app = App(cfg)
    return ThreadedServer(("127.0.0.1", cfg.port), make_handler(app)), app


def serve(cfg: Config) -> None:
    server, _app = build_server(cfg)

    # Keeps Canvas current for anything already being carried between machines.
    # Only touches assignments handed off at least once, so this uploads nothing
    # on its own initiative.
    _app.start_handoff_worker()

    # Ctrl+C or a kill would otherwise leave `claude` subprocesses grading in the
    # background, still spending. Reap them on the way out.
    def reap() -> None:
        _app.stop_handoff_worker()
        if getattr(_app, "state_sync", None):
            _app.state_sync.close()
        if getattr(_app, "audit_sync", None):
            _app.audit_sync.close()
        if getattr(_app, "humanize_refresh", None):
            _app.humanize_refresh.close()
        killed = llm.shutdown_all()
        if killed:
            print(f"  cancelled {killed} in-flight Claude call(s)", flush=True)
        stopped = blender.shutdown_all()
        if stopped:
            print(f"  stopped {stopped} Blender process(es)", flush=True)

    atexit.register(reap)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_a: (reap(), sys.exit(0)))
        except (ValueError, OSError, AttributeError):
            pass    # not the main thread, or the platform lacks this signal

    print(f"CourseForge Studio  ->  http://127.0.0.1:{cfg.port}")
    print(f"  Canvas : {cfg.base_url}")
    print(f"  Data   : {cfg.data}")
    print(f"  Model  : {cfg.model}   pseudonymize={cfg.pseudonymize}   "
          f"canvas_writes={'ON' if cfg.allow_canvas_writes else 'off'}")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
