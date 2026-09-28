"""The second pass: every piece of prose the Studio writes goes through the humanizer.

The grader, the class summary, the teaching read, the overlap triage, an
instructor's question answered, the announcement drafter and the inbox drafter
all ask Claude for prose, and each of those prompts already carries the house
style block from style.py. That block is guidance at prompt time, and a model
that has just spent a long turn scoring a rubric still lets tells through:
a praise opener, an "-ing" tail, a dash. This is the editing pass that catches
them. The finished text goes back to Claude with the humanizer skill as the
editor's brief, and comes back rewritten with the meaning, the numbers, the
tags and the quoted student words intact.

Which brief is used: `humanize_skill_path` in config.json when it is set (a pin;
nothing else is considered), otherwise the NEWEST version among the copies on
this PC, which are

  - the copy this app refreshes from the skill's GitHub repository on its own
    (%APPDATA%\\CourseForge-Studio\\humanizer\\SKILL.md; see `refresh` and
    `Refresher` below),
  - the person's own installed skill, ~/.claude/skills/humanizer/SKILL.md,
  - the copy bundled in courseforge/knowledge/humanizer.md.

Keeping it current takes nobody's attention. Once a day a background thread
fetches upstream SKILL.md, keeps it only if it is a valid humanizer skill file
whose version is not older than the one already kept, and writes it beside the
per-user settings; the next grade reads it. A fetch that fails for any reason
(offline, a proxy that refuses the host, a page that is not the skill) is
recorded in state.json and changes nothing. The trust here is the same as
`update.py`'s: TLS to one fixed host, one fixed repository, no redirects off
that host, a size band, and a structural check of what came back.

The brief is about 30 KB, so it rides in the prompt body (stdin), never on the
command line: Windows caps a command line at 32 KB, and the CLI's own system
prompt flag would push past it.

Rules this module keeps, in order of importance:

  - A grade is never lost to an editing pass. Any failure at all (no CLI, a
    timeout, an unparseable reply, a field that came back empty or lost a
    number) keeps the ORIGINAL text for that field, and the report says so.
  - One model call per result, not per field. All the prose of one grade
    (the comment plus every rationale) travels as one JSON object.
  - The rewrite is checked before it is trusted: same numbers, same tags
    (S-001, Student-3), same quoted phrases, no bloat, no dash that was not
    there before. A field that fails any check keeps its original.
  - Names never enter. Callers pass the text as the model produced it, with
    pseudonym tags still in place, and unmask afterwards.
  - `enabled(cfg)` is False unless the config object says otherwise. The real
    Config defaults it on; a bare test object leaves it off, so a test that
    fakes one model call does not find a second one in its way.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import llm
from .config import user_dir
from .style import HUMANIZE_RULES

BUNDLED = Path(__file__).resolve().parent / "knowledge" / "humanizer.md"
INSTALLED = Path(os.path.expanduser("~")) / ".claude" / "skills" / "humanizer" / "SKILL.md"
# The auto-refreshed copy. Per-user and outside the app folder, like the token:
# an app update replaces the folder the code sits in, and this must survive it.
AUTO = user_dir() / "humanizer" / "SKILL.md"

# Where the refresh comes from. One fixed file in one fixed repository, over
# TLS; a config.json cannot point this anywhere else (pin a local file with
# humanize_skill_path instead). The README of the skill names this repo as
# its home.
UPSTREAM_URL = "https://raw.githubusercontent.com/blader/humanizer/main/SKILL.md"
UA = "CourseForge-Studio-Humanizer"
TIMEOUT = 20
# The skill is about 30 KB. Outside this band it is not the skill: an error
# page, an empty file, or something that grew a hundredfold.
MIN_BRIEF_BYTES = 8_000
MAX_BRIEF_BYTES = 400_000
# What a humanizer SKILL.md always carries, and what it never has a reason to.
# Checked case-insensitively. The skill has been reorganised between versions
# (2.7.0's "Em Dashes" section is 3.1.0's "Dashes as the universal
# connector"), so these are the things every version talks about, not the
# headings of one of them: its own name, dashes, its Wikipedia source, and a
# handful of its topics.
MUST_CONTAIN = ("# humanizer", "dash", "wikipedia")
TOPICS = ("triad", "rule of three", "filler", "hedg", "passive", "-ing", "chatbot",
          "sales", "bold", "sycophan", "cliche", "cliché")
MIN_TOPICS = 3
MUST_NOT_CONTAIN = ("ignore previous", "ignore all previous", "disregard the above",
                    "system prompt", "authorization:", "bearer ", "canvas.token",
                    "/api/v1/", "<script", "http://")

# ------------------------------------------------------------------- kinds
# What each caller is editing. The audience and the per-field rules go into
# the prompt so the editor keeps the register: a comment stays two sentences
# to a student, a rationale stays evidence for the instructor.
KIND_GRADE = "grade"
KIND_ANSWER = "answer"
KIND_SUMMARY = "summary"
KIND_TEACHING = "teaching"
KIND_OVERLAP = "overlap"
KIND_ANNOUNCE = "announce"
KIND_INBOX = "inbox"

KINDS: dict[str, dict] = {
    KIND_GRADE: {
        "what": "a graded submission: the comment the student will read and the "
                "per-criterion rationales the instructor keeps as the record",
        "audience": "comment: the student, in second person. rationale:* : the instructor.",
        "rules": {
            "comment": "At most two sentences and under 45 words. Say what cost the "
                       "points and the one thing to do differently. No opening praise, "
                       "no recap of what they submitted, no sign-off. An empty comment "
                       "stays empty.",
            "rationale": "Two or three sentences for the instructor citing the specific "
                         "evidence the original cites. Keep every score and every "
                         "quoted phrase.",
        },
    },
    KIND_ANSWER: {
        "what": "an answer to the instructor's question about one student's submission",
        "audience": "the instructor",
        "rules": {"text": "Keep it as short as the original. Keep every quoted phrase."},
    },
    KIND_SUMMARY: {
        "what": "a written read of how a class did on one assignment",
        "audience": "the instructor",
        "rules": {"text": "Keep the same number of paragraphs in the same order, and "
                          "every number, mean, median, percentage and criterion name."},
    },
    KIND_TEACHING: {
        "what": "a teaching read: what to reteach, what to fix in the assignment, "
                "what worked, what to watch next time",
        "audience": "the instructor",
        "rules": {
            "headline": "One sentence.",
            "reteach": "Keep the numbers and quotes in evidence; keep each action a "
                       "concrete ten-minute step.",
            "fix": "Keep it a concrete change to the prompt, rubric row or materials.",
            "worked": "One or two sentences, or empty if it was empty.",
            "watch_next_time": "One sentence.",
        },
    },
    KIND_OVERLAP: {
        "what": "a triage of shared wording between two submissions",
        "audience": "the instructor",
        "rules": {
            "pair": "Never say or imply that anyone cheated, and never suggest a "
                    "penalty. Keep every quoted passage exactly as written.",
            "note": "One or two sentences about the set as a whole.",
        },
    },
    KIND_ANNOUNCE: {
        "what": "a course announcement and its subject line",
        "audience": "students, half of them on a phone",
        "rules": {
            "title": "Under 60 characters. No course code.",
            "message": "One paragraph of plain sentences. Keep every date, time, "
                       "points value and instruction exactly. No greeting, no "
                       "exclamation marks, no motivational sign-off.",
        },
    },
    KIND_INBOX: {
        "what": "a draft reply to a student's Canvas message, and a note to the "
                "instructor about it",
        "audience": "reply: the student, addressed by tag. why: the instructor.",
        "rules": {
            "reply": "Two or three sentences in the instructor's voice. Keep the "
                     "student tag as written. No promises about grades or extensions "
                     "that the original does not make. No greeting beyond the tag, "
                     "no sign-off.",
            "why": "One or two sentences.",
        },
    },
}

# --------------------------------------------------------------- the brief
_cache: dict[str, tuple[float, str, str]] = {}
_FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)


def _split_frontmatter(raw: str) -> tuple[str, str]:
    """(version, body). The skill file opens with YAML; the prompt is the rest."""
    m = _FRONT.match(raw)
    if not m:
        return "", raw
    version = ""
    # 2.x wrote `version: 2.7.0` at the top level; 3.x nests it as
    # `metadata:` / `  version: "3.1.0"`. Leading whitespace is allowed so
    # both parse, and quotes are optional.
    vm = re.search(r"^\s*version:\s*['\"]?([\w.\-]+)", m.group(1), re.M)
    if vm:
        version = vm.group(1)
    return version, raw[m.end():]


def version_tuple(version: str) -> tuple:
    """"2.7.0" -> (2, 7, 0). Anything that is not a number counts as 0, so a
    "2.8.0-beta" sorts with 2.8.0 and an empty version sorts last."""
    out = []
    for part in re.split(r"[.\-+]", str(version or "").strip()):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group(0)))
    return tuple(out) if out else (0,)


def _describe(kind: str, path: Path) -> dict | None:
    try:
        version, _body = _split_frontmatter(path.read_text(encoding="utf-8-sig"))
        mtime = path.stat().st_mtime
    except OSError:
        return None
    tag = f" v{version}" if version else ""
    label = {"configured": f"the pinned file {path}{tag}",
             "auto": f"the copy the Studio keeps current from GitHub{tag}",
             "installed": f"your installed /humanizer skill{tag}",
             "bundled": f"the copy bundled with the Studio{tag}"}[kind]
    return {"path": str(path), "kind": kind, "version": version,
            "label": label, "ok": True, "mtime": mtime}


def candidates(cfg=None) -> list[dict]:
    """Every brief this PC could use, best first.

    A pinned path is the only candidate when it is set. Otherwise the newest
    version wins; on a tie the auto-refreshed copy comes first (it is what
    upstream has now), then the person's own installed skill, then the
    bundled copy. The sort is stable, so that order holds among equals.
    """
    configured = str(getattr(cfg, "humanize_skill_path", "") or "").strip()
    if configured:
        path = Path(os.path.expanduser(configured))
        found = _describe("configured", path) if path.is_file() else None
        return [found] if found else []
    found = []
    for kind, path in (("auto", AUTO), ("installed", INSTALLED), ("bundled", BUNDLED)):
        if path.is_file():
            row = _describe(kind, path)
            if row:
                found.append(row)
    found.sort(key=lambda row: version_tuple(row["version"]), reverse=True)
    return found


def skill_source(cfg=None) -> dict:
    """Which brief this machine would use: {path, kind, version, label, ok}."""
    found = candidates(cfg)
    if found:
        return found[0]
    return {"path": "", "kind": "missing", "version": "",
            "label": "no humanizer skill found (bundled copy missing)", "ok": False}


def skill_text(cfg=None) -> str:
    """The editor's brief, frontmatter stripped. Cached by path and mtime."""
    src = skill_source(cfg)
    if not src["ok"]:
        raise FileNotFoundError("humanizer skill not found")
    path = Path(src["path"])
    mtime = path.stat().st_mtime
    hit = _cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    _version, body = _split_frontmatter(path.read_text(encoding="utf-8-sig"))
    body = body.strip()
    _cache[str(path)] = (mtime, body, _version)
    return body


# -------------------------------------------------------------- settings

def enabled(cfg) -> bool:
    """Off unless the config says on. Config() says on."""
    return bool(getattr(cfg, "humanize", False))


def model_for(cfg) -> str:
    """An editing pass does not need the grading model. sonnet by default."""
    return (str(getattr(cfg, "humanize_model", "") or "")
            or str(getattr(cfg, "describe_model", "") or "")
            or str(getattr(cfg, "model", "") or "sonnet"))


def timeout_for(cfg) -> int:
    try:
        return int(getattr(cfg, "humanize_timeout_s", 0) or 180)
    except (TypeError, ValueError):
        return 180


# ---------------------------------------------------------------- guards
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_TAG = re.compile(r"\b(?:S-\d{3,}|Student-\d+)\b")
_QUOTED = re.compile(r'"([^"\n]{3,}?)"')
_DASH = re.compile("[–—]")


def _numbers(text: str) -> list[str]:
    # Tags carry digits (S-001) and have their own check; a lost tag should be
    # reported as a lost tag, not as a changed number.
    return sorted(_NUMBER.findall(_TAG.sub(" ", text or "")))


def _quotes(text: str) -> list[str]:
    out = []
    for inner in _QUOTED.findall(text or ""):
        words = inner.split()
        if len(words) >= 3:
            out.append(" ".join(words))
    return out


def _squash(text: str) -> str:
    return " ".join((text or "").split())


def why_kept(original: str, rewritten, key: str = "") -> str:
    """Empty when the rewrite may replace the original; else the reason it may not.

    Each check is a way an editing pass has been seen to do damage: a score
    restated wrongly, a student's quoted words paraphrased, a tag turned into
    a name, a two-sentence comment grown into a paragraph, a dash introduced
    by the very pass meant to remove them.
    """
    if not isinstance(rewritten, str):
        return "not text"
    new = rewritten.strip()
    old = (original or "").strip()
    if not old:
        return "original was empty"
    if not new:
        return "came back empty"
    if _numbers(old) != _numbers(new):
        return "changed a number"
    if sorted(set(_TAG.findall(old))) != sorted(set(_TAG.findall(new))):
        return "changed a student tag"
    squashed = _squash(new)
    for phrase in _quotes(old):
        if phrase not in squashed:
            return "lost a quoted phrase"
    if len(new) > max(int(len(old) * 1.5), len(old) + 80):
        return "grew too much"
    if _DASH.search(new) and not _DASH.search(old):
        return "added a dash"
    if key == "comment" and len(new.split()) > 60:
        return "comment too long"
    return ""


# ---------------------------------------------------------------- prompt

def _rule_for(kind: dict, key: str) -> str:
    rules = kind.get("rules") or {}
    if key in rules:
        return rules[key]
    head = key.split(":", 1)[0]
    return rules.get(head, "")


def build_prompt(fields: dict[str, str], kind_name: str, brief: str) -> str:
    kind = KINDS.get(kind_name) or KINDS[KIND_ANSWER]
    lines = [
        "# The editor's brief: the humanizer skill",
        "",
        brief,
        "",
        "# House style for everything this tool writes",
        "",
        HUMANIZE_RULES,
        "",
        "# What you are editing",
        "",
        f"This is {kind['what']}.",
        f"Audience: {kind['audience']}",
        "",
        "Hard rules for this pass, on top of the brief:",
        "- You are an editor, not a grader. Do not change what any field says, "
        "only how it says it. Do not add facts, advice, praise or hedges.",
        "- Keep every number, score, percentage, criterion name, id, URL, date "
        "and time exactly as written.",
        "- Keep every student tag (S-001, Student-3) exactly as written. Never "
        "replace a tag with a name.",
        "- Keep every quoted phrase exactly as written. It is the student's own "
        "wording, and it is the evidence.",
        "- An empty field stays empty. Do not fill one in.",
        "- Plain text only: no markdown, no bullets, no headings, no emoji, no "
        "em or en dashes, straight quotes.",
        "- Keep each field at or under its stated length. Shorter is fine.",
        "- Whatever the brief says about adding personality, voice, opinions or "
        "asides does not apply here: this is an instructor's plain voice, not a "
        "blog post. No opinion or aside the original does not have.",
        "",
        "Rules per field:",
    ]
    for key in fields:
        rule = _rule_for(kind, key)
        if rule:
            lines.append(f"- {key}: {rule}")
    lines += [
        "",
        "Work the brief's own method in your head (a draft, a check for what "
        "still reads as machine-written, then the final rewrite) and return ONLY "
        "the final rewrite of each field. If a field already reads as a person "
        "wrote it, return it unchanged.",
        "",
        "# Fields",
        "",
        json.dumps({"fields": fields}, ensure_ascii=False, indent=1),
        "",
        "Return ONLY this JSON object, with the same keys and nothing else:",
        '{"fields": {' + ", ".join(f'"{k}": "<final>"' for k in fields) + "}}",
    ]
    return "\n".join(lines)


SYSTEM = ("You are the writing editor described in the brief that follows. You "
          "rewrite prose so it reads as a person wrote it. You never grade, never "
          "add facts, and never change a number, an id, a student tag or a quoted "
          "phrase. Reply with a single JSON object and nothing else. No preamble, "
          "no code fence.")


# ------------------------------------------------------------------ run

def humanize_fields(fields: dict[str, str], cfg, kind: str,
                    model: str | None = None, timeout_s: int | None = None,
                    on_activity: Callable[[dict], None] | None = None,
                    ) -> tuple[dict[str, str], dict]:
    """Rewrite the prose fields of one result in one model call.

    Returns (fields, report). `fields` has every key that was passed, with the
    rewrite where it passed the checks and the original where it did not.
    `report` says what happened: which keys changed, which were kept and why,
    what it cost, and `skipped` (a reason) when the pass did not run at all.
    Never raises.
    """
    report: dict = {"model": "", "cost_usd": 0.0, "changed": [], "kept": {},
                    "skipped": "", "skill": ""}
    out = {k: (v if isinstance(v, str) else "") for k, v in fields.items()}
    todo = {k: v for k, v in out.items() if v.strip()}
    if not todo:
        report["skipped"] = "nothing to edit"
        return out, report
    try:
        src = skill_source(cfg)
        report["skill"] = src["label"]
        brief = skill_text(cfg)
        chosen = model or model_for(cfg)
        report["model"] = chosen
        result = llm.run(build_prompt(todo, kind, brief), model=chosen,
                         timeout_s=timeout_s or timeout_for(cfg), system=SYSTEM,
                         expect_json=True, on_activity=on_activity)
        report["cost_usd"] = round(float(getattr(result, "cost_usd", 0.0) or 0.0), 4)
        data = getattr(result, "data", None)
        if not isinstance(data, dict):
            data = llm.parse_json(getattr(result, "text", "") or "")
        got = (data or {}).get("fields") if isinstance(data, dict) else None
        if not isinstance(got, dict):
            report["skipped"] = "the editor did not answer in the expected shape"
            return out, report
        for key, original in todo.items():
            rewritten = got.get(key)
            reason = why_kept(original, rewritten, key)
            if reason:
                report["kept"][key] = reason
                continue
            new = rewritten.strip()
            if _squash(new) == _squash(original):
                report["kept"][key] = "unchanged"
                continue
            out[key] = new
            report["changed"].append(key)
    except Exception as exc:  # noqa: BLE001  an editing pass must never cost a grade
        report["skipped"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return out, report


def humanize_text(text: str, cfg, kind: str, key: str = "text",
                  **kw) -> tuple[str, dict]:
    """One field. Same guarantees as humanize_fields."""
    fields, report = humanize_fields({key: text or ""}, cfg, kind, **kw)
    return fields.get(key, text or ""), report


def doctor(cfg) -> dict:
    """What the doctor command prints: on/off, which brief, and the refresh state."""
    src = skill_source(cfg)
    return {"enabled": enabled(cfg), "model": model_for(cfg),
            "auto_update": auto_update_enabled(cfg), "state": read_state(), **src}


# ------------------------------------------------------------ refreshing
# Keeping the brief current takes nobody's attention: once a day the server's
# Refresher thread calls refresh(), which fetches upstream SKILL.md, keeps it
# only if it is a valid skill file no older than the one already kept, and
# writes it beside the per-user settings. candidates() then prefers it by
# version. Everything here fails quietly into state.json, never into a grade.

def auto_update_enabled(cfg) -> bool:
    """Off unless the config says on. Config() says on; a bare test object does
    not, so no test reaches the network by accident."""
    return bool(getattr(cfg, "humanize_auto_update", False))


def state_path() -> Path:
    return AUTO.with_name("state.json")


def read_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _front(text: str) -> str:
    m = _FRONT.match(text)
    return m.group(1) if m else ""


def validate_brief(raw: bytes) -> tuple[str, str]:
    """(version, "") when these bytes are a humanizer SKILL.md this tool will
    use; ("", why) otherwise.

    Structural, not semantic: it tells the skill apart from an error page, a
    truncated download, a different skill under the same address, and a file
    carrying text a writing guide has no reason to carry. It cannot judge the
    prose; the version gate and the fixed source do the rest.
    """
    if len(raw) < MIN_BRIEF_BYTES:
        return "", f"only {len(raw)} bytes; the skill is about 30 KB"
    if len(raw) > MAX_BRIEF_BYTES:
        return "", f"{len(raw)} bytes is far larger than the skill"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "", "not UTF-8 text"
    head = text[:2000].lower()
    if "<html" in head or "<!doctype" in head:
        return "", "an HTML page, not the skill"
    front = _front(text)
    if not front:
        return "", "no YAML frontmatter"
    if not re.search(r"^name:\s*['\"]?humanizer\b", front, re.M):
        return "", "the frontmatter does not name the humanizer skill"
    version, body = _split_frontmatter(text)
    if not version_tuple(version) > (0,):
        return "", "no version in the frontmatter"
    low = body.lower()
    for needle in MUST_CONTAIN:
        if needle not in low:
            return "", f"does not read like the humanizer skill: no {needle!r}"
    if sum(1 for topic in TOPICS if topic in low) < MIN_TOPICS:
        return "", "does not read like the humanizer skill: too few of its topics"
    for bad in MUST_NOT_CONTAIN:
        if bad in low:
            return "", f"carries {bad!r}, which a writing guide has no reason to"
    return version, ""


class _StayOnHost(urllib.request.HTTPRedirectHandler):
    """A redirect off the upstream host, or down to plain http, is refused."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        want = urllib.parse.urlparse(UPSTREAM_URL).netloc.lower()
        got = urllib.parse.urlparse(newurl)
        if got.scheme != "https" or got.netloc.lower() != want:
            raise urllib.error.URLError(f"refusing a redirect to {got.netloc or newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url: str, etag: str = "") -> tuple[int, bytes, str]:
    """One conditional GET: (status, body, etag). 304 means unchanged. Module
    level so a test can replace it, the way test_update.py replaces update._get."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/plain, text/markdown, */*"})
    if etag:
        req.add_header("If-None-Match", etag)
    opener = urllib.request.build_opener(_StayOnHost())
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            body = resp.read(MAX_BRIEF_BYTES + 1)
            return (int(getattr(resp, "status", 200) or 200), body,
                    str(resp.headers.get("ETag") or ""))
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, b"", etag
        raise


def refresh(cfg=None, force: bool = False) -> dict:
    """Fetch upstream once; keep it when it is a valid brief no older than the
    one already kept. Returns the state it wrote. Never raises."""
    state = read_state()
    state["checked"] = datetime.now().isoformat(timespec="seconds")
    state["checked_ts"] = time.time()
    state["url"] = UPSTREAM_URL
    state["error"] = ""
    try:
        status, body, etag = _get(UPSTREAM_URL, "" if force else str(state.get("etag") or ""))
        if status == 304:
            state["result"] = "unchanged"
            return state
        version, why = validate_brief(body)
        if why:
            state["result"] = "refused"
            state["error"] = why
            return state
        kept = str(state.get("version") or "") if AUTO.is_file() else ""
        if kept and version_tuple(version) < version_tuple(kept):
            state["result"] = "refused"
            state["error"] = f"upstream is v{version}, older than the v{kept} already kept"
            return state
        digest = hashlib.sha256(body).hexdigest()
        if AUTO.is_file() and digest == state.get("sha256"):
            state["result"] = "unchanged"
        else:
            _write_atomic(AUTO, body)
            state["result"] = "updated"
            state["updated"] = state["checked"]
        state["version"] = version
        state["sha256"] = digest
        state["etag"] = etag
        return state
    except Exception as exc:  # noqa: BLE001  a refresh must never reach a grade
        state["result"] = "error"
        state["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return state
    finally:
        try:
            _write_atomic(state_path(), json.dumps(state, indent=1).encode("utf-8"))
        except OSError:
            pass


def seconds_since_check() -> float | None:
    ts = read_state().get("checked_ts")
    try:
        return max(0.0, time.time() - float(ts)) if ts else None
    except (TypeError, ValueError):
        return None


class Refresher(threading.Thread):
    """Once a day, on a thread: fetch, validate, keep. The server starts it
    when humanize_auto_update is on; close() ends it at shutdown."""

    def __init__(self, cfg, first_wait_s: int = 60):
        super().__init__(daemon=True, name="humanizer-refresh")
        self.cfg = cfg
        try:
            self.every_s = max(3600, int(getattr(cfg, "humanize_update_s", 0) or 86400))
        except (TypeError, ValueError):
            self.every_s = 86400
        self.first_wait_s = first_wait_s
        self.stop_event = threading.Event()
        self.last: dict = {}

    def once(self) -> dict:
        self.last = refresh(self.cfg)
        return self.last

    def run(self) -> None:
        if not auto_update_enabled(self.cfg):
            return
        # Not in the first minute of the server's life, and not again on every
        # restart in the same day: a check four hours old still counts, so the
        # wait picks up where that one left off.
        wait = float(self.first_wait_s)
        since = seconds_since_check()
        if since is not None and since < self.every_s:
            wait = max(wait, self.every_s - since)
        if self.stop_event.wait(wait):
            return
        while not self.stop_event.is_set():
            self.once()
            if self.stop_event.wait(self.every_s):
                return

    def close(self) -> None:
        self.stop_event.set()
