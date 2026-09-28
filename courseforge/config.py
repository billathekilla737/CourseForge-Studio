"""Configuration loading for CourseForge Studio.

Resolution order for every value: explicit config.json -> environment -> default.
The Canvas token is never stored in config.json; it is read from a token file or
the CANVAS_TOKEN environment variable so that config.json stays diffable.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import secrets as tokenstore

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = APP_DIR / "config.json"


def user_dir() -> Path:
    """Per-user settings, outside any copy of the app.

    %APPDATA% on Windows, ~/.config elsewhere. This is where the token belongs:
    it is a property of the person, not of the folder the code happens to be
    sitting in, and a folder gets replaced every time the app is re-downloaded.
    """
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "CourseForge-Studio"
    return Path(os.environ.get("XDG_CONFIG_HOME")
                or (Path.home() / ".config")) / "courseforge-studio"


@dataclass
class Config:
    base_url: str = "https://mgccc.instructure.com"
    token_path: str = ""
    data_dir: str = ""
    port: int = 8900

    # Which Canvas enrollment types to list courses for.
    enrollment_types: list[str] = field(default_factory=lambda: ["teacher", "ta"])

    # Courses to hide in the picker (e.g. ones whose artifacts cannot be graded
    # from text, like Unity projects). Values may be ids or substrings of the name.
    excluded_courses: list[str] = field(default_factory=list)

    # Claude CLI
    model: str = "opus"
    # Offered in the UI dropdown. Any alias the Claude CLI accepts works here.
    models: list[str] = field(default_factory=lambda: ["opus", "sonnet", "haiku"])
    # Parallel `claude` CLI calls while grading. 8 is the ceiling enforced in
    # grader.py; concurrency buys wall-clock only -- the token spend for a
    # section is the same at 1 lane or 8 -- so the thing it trades against is
    # rate limits, which show up as failures against individual students.
    grading_concurrency: int = 8
    claude_timeout_s: int = 600

    # Send pseudonyms (S-001...) instead of names to Claude. Identities stay local.
    pseudonymize: bool = True

    # Blender. blender_path empty means auto-detect (PATH, install dirs, registry).
    blender_path: str = ""
    blend_concurrency: int = 2          # Blender is CPU and RAM heavy; 2 is safe
    blend_timeout_s: int = 180
    blend_max_mb: int = 300             # refuse absurd files without launching
    blend_vision: bool = True           # send the contact sheet to the model
    vision_model: str = "sonnet"        # opus vision is not worth the cost here
    # Carrying grading between machines through your own Canvas files.
    handoff_folder: str = "canvas-grader"
    handoff_auto: bool = True           # keep Canvas current once handed off once
    handoff_debounce_s: int = 45        # how long to let edits settle first
    max_images_per_student: int = 8

    # How stale the term schedule may be before opening it refetches from
    # Canvas in the background. The cached copy shows immediately either way.
    schedule_max_age_min: int = 30

    # Opening an assignment syncs it from Canvas by itself. One that has never
    # been synced always does; one synced more recently than this is taken as
    # current and opens straight away. Files already downloaded are not fetched
    # twice, so a re-sync is mostly API calls. 0 syncs on every open.
    assignment_max_age_min: int = 15

    # Letter-grade cutoffs, as the minimum percent for each letter. Anything
    # below the lowest cutoff is an F. Change these to match your institution.
    grade_scale: dict = field(default_factory=lambda: {
        "A": 90.0, "B": 80.0, "C": 70.0, "D": 60.0})

    # A hard read-only lock, off by default because the point of the tool is
    # to change live courses. Safety comes from confirm.py instead: every write
    # is refused once, shown to you, and only sent when you agree to that exact
    # change. Set this to false to bolt the doors shut regardless -- useful on a
    # shared machine, or while someone is learning the tool.
    allow_canvas_writes: bool = True

    # While an assignment is open, re-read its Canvas grades this often so work
    # pushed from another machine shows up here. One cheap call per pull, and
    # it never overwrites unpushed local work (see gradesync.py). 0 turns the
    # timer off; the pull on opening an assignment still happens.
    pull_interval_s: int = 120

    # ------------------------------------------------ CourseForge Studio areas
    # Which backend answers llm.run(): "cli" (Claude Code, signed in on this
    # machine) or "api" (ANTHROPIC_API_KEY in the environment; the hosted build).
    llm_backend: str = "cli"
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    api_models: dict = field(default_factory=lambda: {
        "opus": "claude-opus-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5"})
    # Hosts the Canvas token may be sent to besides base_url (self-hosted Canvas).
    canvas_hosts: list[str] = field(default_factory=lambda: ["*.instructure.com"])
    # Brand palette for generated markup; empty = courseforge/brand.json.
    brand_path: str = ""
    # Accessibility restyle look: clean scores 0 advisory flags; hybrid/rich add colour.
    a11y_look: str = "clean"
    # PDF engine worker processes; 0 = cpu_count() - 2.
    pdf_jobs: int = 0
    tesseract_path: str = ""
    verapdf_path: str = ""
    java_path: str = ""
    # Alt text and other image descriptions: sonnet sees well and costs less.
    describe_model: str = "sonnet"
    # The second pass. Every piece of prose the Studio writes (the comment a
    # student reads, the per-criterion rationales, the class summary, the
    # teaching read, the overlap triage, an answered question, an announcement
    # draft, an inbox reply) goes back through the humanizer skill before it is
    # shown, so it reads as a person wrote it. See humanize.py. A pass that
    # fails keeps the original words, so turning this off only saves the call.
    humanize: bool = True
    humanize_model: str = "sonnet"      # an editing pass; sonnet is enough
    humanize_timeout_s: int = 180
    # Empty = the newest of: the copy this app refreshes from GitHub on its own
    # (see humanize_auto_update), ~/.claude/skills/humanizer/SKILL.md when
    # installed, and the copy bundled in courseforge/knowledge/humanizer.md.
    # A path pins one file and stops the automatic choice.
    humanize_skill_path: str = ""
    # Once a day, fetch the current humanizer SKILL.md from its GitHub repo
    # into the per-user folder, keep it only if it is a valid, newer skill
    # file, and use it from the next grade on. No click, no restart. Off means
    # the skill only changes when a person installs a new copy.
    humanize_auto_update: bool = True
    humanize_update_s: int = 86400
    # College-specific syllabus statements. Empty = courseforge/knowledge/syllabus-policies.json.
    syllabus_policies_path: str = ""
    # The account of what the Studio did (audit.py), kept in your own Canvas
    # user files as evidence. That copy is not a replica you resume from on
    # another PC. The chained local copy is written either way; this only
    # decides whether Canvas gets one.
    audit_to_canvas: bool = True
    audit_sync_s: int = 180
    # Standing roster, student notes and other small JSON blobs, kept in your
    # own Canvas user files (Files → courseforge-studio → state) so a second
    # computer signed into the same account can pick them up. Hydrate still
    # runs when allow_canvas_writes is false; upload does not.
    state_to_canvas: bool = True
    state_sync_s: int = 45
    # Where the launcher looks for a newer version, and whether it looks at
    # all. The updater refuses any update_repo other than the public app
    # source. A GitHub token is never stored here; the public source needs none.
    check_updates: bool = True
    update_repo: str = "billathekilla737/CourseForge-Studio-app"
    update_branch: str = "main"
    update_token: str = ""
    # The Assistant: a Claude Code session per course with an Allow/Deny gate.
    assistant_enabled: bool = True
    assistant_model: str = ""           # empty = Claude Code's default
    assistant_ask_timeout_s: int = 1200
    # Backend choices a hosted build swaps. Unused locally; documented in
    # docs/DEPLOYMENT-SCHOOL.md so the school build is configuration, not a fork.
    backends: dict = field(default_factory=lambda: {
        "llm": "cli", "store": "files", "auth": "none", "secrets": "dpapi", "jobs": "threads"})

    # Path this config was loaded from, so settings changed in the UI can persist.
    _path: Path | None = field(default=None, repr=False, compare=False)

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        path = Path(path) if path else DEFAULT_CONFIG
        raw: dict = {}
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))

        cfg = cls()
        cfg._path = path
        for key, value in raw.items():
            # Only settings. A key such as "token" names a method here, and
            # letting it through would replace the method with a string.
            # A GitHub PAT does not belong in config.json; ignore one if present.
            if key == "update_token":
                continue
            if (hasattr(cfg, key) and not key.startswith("_")
                    and not callable(getattr(cfg, key))):
                setattr(cfg, key, value)

        cfg.base_url = (os.environ.get("CANVAS_BASE_URL") or cfg.base_url).rstrip("/")
        if os.environ.get("CANVAS_GRADER_PORT"):
            cfg.port = int(os.environ["CANVAS_GRADER_PORT"])

        if not cfg.data_dir:
            cfg.data_dir = str(APP_DIR / "data")
        Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)

        return cfg

    # ------------------------------------------------------------------ token
    def token_candidates(self) -> list[Path]:
        """Every file the token is looked for in, in order."""
        found = []
        if self.token_path:
            found.append(Path(self.token_path).expanduser())
        return found + [
            # Per-user first: any copy of the app on this machine finds it, so
            # a re-download or a second folder does not have to be set up again.
            # The encrypted copy wins; the plaintext name is read for migration.
            user_dir() / tokenstore.ENC_NAME,
            user_dir() / "canvas.token",
            APP_DIR / "canvas.token",
            # Windows hides known extensions, so "Save As -> canvas.token" in
            # Notepad lands as canvas.token.txt and the file looks correct in
            # Explorer. Accepted here, and gitignored alongside canvas.token.
            APP_DIR / "canvas.token.txt",
            Path.home() / "Documents" / "canvas-work" / "canvas.token",
            Path.home() / ".canvas.token",
        ] + tokenstore.legacy_token_files(self.host)

    @staticmethod
    def _clean_token(raw: str) -> str:
        """A token as the user meant it, whatever their editor or shell added.

        Two things bite people here and both look like a rejected token rather
        than a mangled file. `>` in PowerShell writes a UTF-8 byte-order mark, and
        str.strip() does not remove one, so the token goes out as "Bearer \ufeff7~..."
        and Canvas answers 401. And a token pasted straight from documentation
        often keeps its quotes.
        """
        value = raw.lstrip("\ufeff").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
        return value

    def has_token(self) -> bool:
        """True when a token is saved. Does not raise, and does not call Canvas."""
        try:
            return bool(self.token())
        except RuntimeError:
            return False

    def token(self) -> str:
        """Canvas API token, from env or a token file. Never from config.json."""
        env = os.environ.get("CANVAS_TOKEN")
        if env and self._clean_token(env):
            return self._clean_token(env)

        for candidate in self.token_candidates():
            try:
                if candidate.is_file():
                    # Reads every format this project ever wrote: DPAPI .enc,
                    # legacy .bin blobs, and plaintext (BOM tolerated).
                    value = self._clean_token(tokenstore.read_token_file(candidate))
                    if value:
                        return value
            except (OSError, ValueError, tokenstore.TokenError):
                continue

        looked = "\n".join(f"    {path}" for path in self.token_candidates())
        raise RuntimeError(
            "No Canvas API token found.\n\n"
            "Fix it in one step: open CourseForge Studio in your browser and paste\n"
            "the token on the setup screen. It saves to\n"
            f"    {user_dir() / 'canvas.token'}\n"
            "which every copy of this app on this machine reads, so you only\n"
            "do it once.\n\n"
            "Get a token from Canvas: your avatar (Account) -> Settings ->\n"
            "+ New Access Token -> Generate Token, and copy it right away.\n\n"
            "Files checked, in order:\n" + looked + "\n\n"
            "(Setting CANVAS_TOKEN in the environment also works and wins over "
            "every file above. You only need one of these, not all of them.)"
        )

    @property
    def host(self) -> str:
        from urllib.parse import urlparse
        return (urlparse(self.base_url).hostname or "").lower()

    def save_token(self, token: str) -> Path:
        """Store the token under the per-user folder, encrypted where possible."""
        return tokenstore.write_token(user_dir(), token)

    def persist(self, **changes) -> None:
        """Write changed settings back to config.json, preserving everything else.

        Unknown keys (including the "_comment" documentation keys in
        config.example.json) are left untouched, so editing a setting in the UI
        does not silently strip the file.
        """
        if not self._path:
            return
        for key, value in changes.items():
            setattr(self, key, value)
        raw: dict = {}
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        raw.update(changes)
        raw.pop("update_token", None)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def is_excluded(self, course: dict) -> bool:
        name = f"{course.get('name','')} {course.get('course_code','')}".lower()
        cid = str(course.get("id"))
        for rule in self.excluded_courses:
            rule = str(rule).strip().lower()
            if not rule:
                continue
            if rule == cid or rule in name:
                return True
        return False

    @property
    def data(self) -> Path:
        return Path(self.data_dir)
