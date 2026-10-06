"""Thin wrapper around the Claude Code CLI in print mode.

Uses the logged-in Claude account on this machine -- no API key required. The
prompt is written to a temp file and piped on stdin so that very large
submissions do not hit Windows command-line length limits.

Environment note: when this tool is launched from inside a Claude Code session,
the parent exports ANTHROPIC_BASE_URL and CLAUDE_CODE_* variables that redirect
a child `claude` at a proxy it cannot authenticate against ("Not logged in").
We strip those so the CLI falls back to normal OAuth either way.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# On Windows every subprocess.run would otherwise flash a console window, which
# is unusable behind a GUI launcher.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# In-flight `claude` processes, so the launcher can kill a running batch on quit
# instead of leaving orphans behind.
_ACTIVE: set[subprocess.Popen] = set()
_ACTIVE_LOCK = threading.Lock()

# Which job started each process, so Stop on one grading run ends that run's
# calls and nobody else's. The tag is per thread: a grading worker sets it
# around its student, and every `claude` it launches is filed under it.
_OWNER = threading.local()
_OWNED: dict[str, set[subprocess.Popen]] = {}
_STOPPED: set[str] = set()


@contextlib.contextmanager
def owned_by(tag: str | None):
    """File every `claude` this thread launches under `tag` until the block ends."""
    previous = getattr(_OWNER, "tag", None)
    _OWNER.tag = tag
    try:
        yield
    finally:
        _OWNER.tag = previous


def stop_owned(tag: str) -> int:
    """Kill the running calls filed under `tag`, and refuse any new ones."""
    with _ACTIVE_LOCK:
        _STOPPED.add(tag)
        procs = list(_OWNED.pop(tag, ()))
    for proc in procs:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    return len(procs)


def shutdown_all() -> int:
    """Terminate every running Claude subprocess. Returns how many were killed."""
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE)
    for proc in procs:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    for proc in procs:
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    return len(procs)

# Inherited variables that make a nested `claude` misbehave.
STRIP_ENV = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    # Secrets the child has no business seeing. The CLI grades on the login it
    # already holds; the Canvas token and any API key stay with the server.
    "ANTHROPIC_API_KEY",
    "CANVAS_TOKEN",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
    "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_OAUTH_SCOPES",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
)

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)


class ClaudeError(RuntimeError):
    pass


class NotLoggedIn(ClaudeError):
    def __init__(self, message: str | None = None):
        super().__init__(message or (
            "The Claude CLI is not logged in.\n"
            "Open a terminal and run:  claude  then  /login\n"
            "Then re-run this tool from that same terminal."
        ))


@dataclass
class ClaudeResult:
    text: str
    data: dict | None
    cost_usd: float = 0.0
    duration_ms: int = 0
    session_id: str = ""
    # Set when `data` came out of a repair rather than a clean parse: the caller
    # is holding a guess and should not treat it as authoritative.
    repaired: bool = False
    parse_error: str = ""


def child_env() -> dict:
    env = dict(os.environ)
    for key in STRIP_ENV:
        env.pop(key, None)
    return env


def cli_path() -> str:
    found = shutil.which("claude")
    if not found:
        raise ClaudeError(
            "`claude` was not found on PATH. Install Claude Code "
            "(https://claude.com/claude-code), then re-open your terminal."
        )
    return found


def doctor() -> dict:
    """Check that the CLI exists and is authenticated. Cheap, no model call."""
    info: dict = {"cli": None, "version": None, "logged_in": False, "detail": ""}
    try:
        info["cli"] = cli_path()
    except ClaudeError as exc:
        info["detail"] = str(exc)
        return info
    try:
        out = subprocess.run([info["cli"], "--version"], capture_output=True, text=True,
                             timeout=60, env=child_env(), creationflags=NO_WINDOW)
        info["version"] = (out.stdout or out.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        info["detail"] = f"could not run `claude --version`: {exc}"
        return info
    try:
        result = run("Reply with exactly: OK", model="sonnet", timeout_s=120, expect_json=False)
        info["logged_in"] = "OK" in result.text.upper()
        info["detail"] = result.text.strip()[:200] or "no output"
    except NotLoggedIn as exc:
        info["detail"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        info["detail"] = f"{type(exc).__name__}: {exc}"
    return info


IMAGE_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".jpe": "image/jpeg", ".jfif": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}
MAX_IMAGE_BYTES = 3_500_000        # base64 inflates 4/3 against the 5 MB API limit
MAX_IMAGES = 8


def _shrink_image(path: Path) -> tuple[bytes, str] | None:
    """A phone screenshot is often over the API size cap. Shrink it to JPEG."""
    try:
        import io
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((1600, 1600))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=72)
        raw = buf.getvalue()
        if raw and len(raw) <= MAX_IMAGE_BYTES:
            return raw, "image/jpeg"
    except Exception:  # noqa: BLE001
        return None
    return None


def _image_block(path: Path) -> dict | None:
    """Base64 image content block, or None if unusable."""
    if not path.is_file():
        return None
    media = IMAGE_MEDIA.get(path.suffix.lower())
    raw = path.read_bytes() if media else b""
    if not media or not raw or len(raw) > MAX_IMAGE_BYTES:
        shrunk = _shrink_image(path)
        if not shrunk:
            return None
        raw, media = shrunk
    return {"type": "image",
            "source": {"type": "base64", "media_type": media,
                       "data": base64.b64encode(raw).decode("ascii")}}


def _stream_payload(prompt: str, images: list[Path]) -> str:
    """One NDJSON user message carrying the images and the prompt.

    Images first, then the text: that ordering is what the API documents for
    image-plus-question and it measurably helps.
    """
    content: list[dict] = []
    for path in images[:MAX_IMAGES]:
        block = _image_block(path)
        if block:
            content.append(block)
    content.append({"type": "text", "text": prompt})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"


class _NoPartialSupport(ClaudeError):
    """Raised when this CLI build rejects --include-partial-messages."""


class _Activity:
    """Turns stream-json lines into coarse "what is it doing right now" updates.

    One grading call is a single long turn: without this, the only observable
    events are "process started" and "process exited", which is why a batch of
    one student used to look like a hang for a minute at a time.
    """

    MIN_INTERVAL = 0.4          # deltas arrive far faster than a UI can use

    def __init__(self, sink: Callable[[dict], None]):
        self.sink = sink
        self.phase = "starting"
        self.chars = 0
        self.thinking = 0
        self._last = 0.0

    def emit(self, phase: str | None = None, force: bool = True) -> None:
        if phase:
            force = force or phase != self.phase
            self.phase = phase
        now = time.monotonic()
        if not force and now - self._last < self.MIN_INTERVAL:
            return
        self._last = now
        try:
            self.sink({"phase": self.phase, "chars": self.chars,
                       "thinking_tokens": self.thinking})
        except Exception:  # noqa: BLE001  a progress sink must never break a grade
            pass

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return
        kind = obj.get("type")
        if kind == "system":
            subtype = obj.get("subtype")
            if subtype == "status" and obj.get("status") == "requesting":
                self.emit("requesting")
            elif subtype == "thinking_tokens":
                self.thinking = int(obj.get("estimated_tokens") or self.thinking)
                self.emit("thinking", force=False)
            return
        if kind != "stream_event":
            return
        event = obj.get("event") or {}
        etype = event.get("type")
        if etype == "message_start":
            self.emit("responding")
        elif etype == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                self.chars += len(delta.get("text") or "")
                self.emit("writing", force=False)
            elif delta.get("type") == "thinking_delta":
                self.emit("thinking", force=False)
        elif etype == "message_stop":
            self.emit("finishing")


def _consume(proc: subprocess.Popen, stdin_text: str, timeout_s: int,
             on_activity: Callable[[dict], None]) -> tuple[str, str, int]:
    """Read stream-json output line by line, reporting activity as it arrives.

    communicate() cannot be used here: it returns only once the process has
    exited, which is precisely the window we need to report on.
    """
    stderr_parts: list[str] = []

    def pump_stderr() -> None:
        try:
            stderr_parts.append(proc.stderr.read() or "")
        except Exception:  # noqa: BLE001
            pass

    def pump_stdin() -> None:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except Exception:  # noqa: BLE001  broken pipe if the CLI died early
            pass

    threading.Thread(target=pump_stderr, daemon=True).start()
    threading.Thread(target=pump_stdin, daemon=True).start()

    timed_out = threading.Event()

    def give_up() -> None:
        timed_out.set()
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass

    timer = threading.Timer(timeout_s, give_up)
    timer.start()

    tracker = _Activity(on_activity)
    tracker.emit("starting")
    lines: list[str] = []
    try:
        for line in proc.stdout:
            lines.append(line)
            tracker.feed(line)
    finally:
        timer.cancel()
    proc.wait()
    if timed_out.is_set():
        raise subprocess.TimeoutExpired(proc.args, timeout_s)
    tracker.emit("parsing")
    return "".join(lines), "".join(stderr_parts), proc.returncode


def _invoke(prompt: str, images: list[Path], model: str, timeout_s: int,
            system: str | None, on_activity: Callable[[dict], None] | None,
            partial: bool) -> tuple[str, str, int]:
    """Launch one `claude -p` and return (stdout, stderr, returncode)."""
    tag = getattr(_OWNER, "tag", None)
    with _ACTIVE_LOCK:
        if tag and tag in _STOPPED:
            raise ClaudeError("Stopped before this call started.")
    # A grading call is a question, not a session. With its built-in tools
    # left on, `claude -p` would honour "read data/<course>/map.json and quote
    # it" written inside a submission and hand the pseudonym map back in a
    # rationale. No tools, no project settings, no MCP servers: the model sees
    # the prompt and nothing else on this disk.
    cmd = [cli_path(), "-p", "--model", model, "--tools", "",
           "--strict-mcp-config", "--setting-sources", "user"]
    if images:
        cmd += ["--input-format", "stream-json"]
        stdin_text = _stream_payload(prompt, images)
    else:
        stdin_text = prompt
    # Images already require stream-json; a watching caller needs it too, so it
    # can see the turn progress instead of one silent block of output at the end.
    if images or on_activity:
        cmd += ["--output-format", "stream-json", "--verbose"]
        if on_activity and partial:
            cmd += ["--include-partial-messages"]
    else:
        cmd += ["--output-format", "json"]
    if system:
        cmd += ["--append-system-prompt", system]

    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=child_env(),
        creationflags=NO_WINDOW,
    )
    with _ACTIVE_LOCK:
        _ACTIVE.add(proc)
        if tag:
            if tag in _STOPPED:
                # Stop landed between the check above and the launch.
                proc.kill()
            else:
                _OWNED.setdefault(tag, set()).add(proc)
    try:
        if on_activity:
            out, err, code = _consume(proc, stdin_text, timeout_s, on_activity)
        else:
            out, err = proc.communicate(stdin_text, timeout=timeout_s)
            code = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate()
        except Exception:  # noqa: BLE001
            pass
        raise ClaudeError(f"Claude timed out after {timeout_s}s. "
                          "Try a smaller batch or raise claude_timeout_s.") from None
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(proc)
            if tag and tag in _OWNED:
                _OWNED[tag].discard(proc)
                if not _OWNED[tag]:
                    _OWNED.pop(tag, None)

    if partial and on_activity and _rejected_flag(out, err):
        raise _NoPartialSupport()
    return out or "", err or "", code


def _rejected_flag(out: str, err: str) -> bool:
    """True if the CLI refused --include-partial-messages (older build)."""
    text = f"{out} {err}".lower()
    return ("include-partial-messages" in text
            and ("unknown option" in text or "unknown argument" in text
                 or "unrecognized" in text or "unexpected argument" in text))


def run(prompt: str, model: str = "opus", timeout_s: int = 600,
        system: str | None = None, expect_json: bool = True,
        images: list[Path] | None = None,
        on_activity: Callable[[dict], None] | None = None) -> ClaudeResult:
    """Run one non-interactive Claude turn and return its output.

    With `images`, the call switches to stream-json input so the pictures ride
    along as base64 content blocks. That is both cheaper than granting the Read
    tool (measured: +$0.001 vs +$0.02 per call) and safer, since no tool access
    and no directory is handed to the model at all.

    With `on_activity`, output is read as it arrives and the callback is handed
    {"phase", "chars", "thinking_tokens"} several times a second, so a caller
    can show that a long turn is alive. Without it the call behaves exactly as
    before.
    """
    images = [Path(p) for p in (images or [])]
    images = [p for p in images if p.is_file()]

    try:
        out, err, code = _invoke(prompt, images, model, timeout_s, system,
                                 on_activity, partial=True)
    except _NoPartialSupport:
        # Older CLI: same call, coarser updates (no per-token deltas).
        out, err, code = _invoke(prompt, images, model, timeout_s, system,
                                 on_activity, partial=False)

    stdout = (out or "").strip()
    stderr = (err or "").strip()

    if code is not None and code < 0:
        raise ClaudeError("Claude was cancelled.")

    if "not logged in" in (stdout + stderr).lower():
        raise NotLoggedIn()
    if not stdout:
        raise ClaudeError(f"Claude produced no output (exit {code}). {stderr[:400]}")

    envelope = _envelope(stdout)

    if envelope.get("is_error"):
        message = str(envelope.get("result") or stderr or "unknown error")
        if "not logged in" in message.lower():
            raise NotLoggedIn()
        raise ClaudeError(f"Claude returned an error: {message[:500]}")

    text = str(envelope.get("result") or "")
    data, how, parse_error = (parse_json_ex(text) if expect_json
                              else (None, "clean", ""))
    return ClaudeResult(
        text=text,
        data=data,
        cost_usd=float(envelope.get("total_cost_usd") or 0.0),
        duration_ms=int(envelope.get("duration_ms") or 0),
        session_id=str(envelope.get("session_id") or ""),
        repaired=how == "repaired",
        parse_error=parse_error,
    )


def _envelope(stdout: str) -> dict:
    """Pull the result envelope out of either output format.

    `--output-format json` prints one object. `--output-format stream-json`
    prints one JSON object per line; the last one with type "result" is the
    envelope, and it carries the same keys we already read.
    """
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    envelope = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            envelope = obj
    # Nothing parseable: treat the raw text as the answer.
    return envelope or {"result": stdout, "is_error": False}


def parse_json(text: str) -> dict | None:
    """Pull a JSON object out of a model reply, fenced or bare."""
    return parse_json_ex(text)[0]


def parse_json_ex(text: str) -> tuple[dict | None, str, str]:
    """Parse a model reply into (object, how, error).

    `how` is "clean", "repaired", or "" when nothing could be read. Models
    occasionally emit a trailing comma or stop mid-sentence, and losing a whole
    student's grade to a stray character is worse than reading it back with a
    warning attached, so a repair pass runs before giving up.
    """
    if not text:
        return None, "", "empty reply"
    first = ""
    for candidate in _candidates(text):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            first = first or f"{exc.msg} at line {exc.lineno} column {exc.colno}"
            continue
        if isinstance(value, dict):
            return value, "clean", ""
    for candidate in _candidates(text):
        for repaired in _repairs(candidate):
            try:
                value = json.loads(repaired)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value, "repaired", first
    return None, "", first or "no JSON object in the reply"


def _repairs(text: str):
    """Candidate fixes for the ways a model reply is usually malformed."""
    # A comma before the closing brace or bracket.
    yield re.sub(r",(\s*[}\]])", r"\g<1>", text)
    # Stopped mid-reply: close whatever is still open and keep what arrived.
    closed = _close_truncated(text)
    if closed:
        yield closed
        yield re.sub(r",(\s*[}\]])", r"\g<1>", closed)


def _close_truncated(text: str) -> str | None:
    """Close a reply that was cut off, or None if it was not cut off.

    Walks the text tracking string state so that braces inside a rationale are
    not mistaken for structure.
    """
    stack: list[str] = []
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack:
            stack.pop()
    if not stack and not in_string:
        return None
    out = text + ('"' if in_string else "")
    # Drop a key whose value never arrived, then any dangling comma.
    out = re.sub(r'(?:,\s*)?"[^"]*"\s*:\s*$', "", out)
    out = re.sub(r"[,\s]+$", "", out)
    return out + "".join(reversed(stack))


def _candidates(text: str):
    stripped = text.strip()
    yield stripped
    for match in _FENCE.finditer(text):
        yield match.group(1)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        yield stripped[start:end + 1]
    # Truncated mid-reply: there is no closing brace to slice to.
    if start != -1 and end <= start:
        yield stripped[start:]
