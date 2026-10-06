"""One seam for every model call in CourseForge Studio.

Everything that talks to Claude goes through `run()`. Two backends sit behind it:

- `cli`  -- the Claude Code CLI on this machine, signed in with the instructor's
            own Claude account. The local build. See claude_cli.py.
- `api`  -- the Anthropic Messages API with a server-side key. The hosted
            (school) build. See claude_api.py.

Callers never import a backend directly. They call `llm.run(...)` and catch
`llm.NotLoggedIn` / `llm.ClaudeError`, which is exactly what the grader did with
`claude_cli` before the merge. `configure(cfg)` is called once at startup.
"""
from __future__ import annotations

from typing import Callable

from . import claude_cli
from .claude_cli import ClaudeError, ClaudeResult, NotLoggedIn  # noqa: F401  (re-exported)

_cfg = None


def configure(cfg) -> None:
    """Remember the loaded Config so the backend choice follows config.json."""
    global _cfg
    _cfg = cfg


def backend_name() -> str:
    name = (getattr(_cfg, "llm_backend", "") or "cli").strip().lower()
    return "api" if name == "api" else "cli"


def run(prompt: str, model: str = "opus", timeout_s: int = 600,
        system: str | None = None, expect_json: bool = True,
        images: list | None = None,
        on_activity: Callable[[dict], None] | None = None) -> ClaudeResult:
    if backend_name() == "api":
        from . import claude_api
        return claude_api.run(prompt, model=model, timeout_s=timeout_s, system=system,
                              expect_json=expect_json, images=images,
                              on_activity=on_activity, cfg=_cfg)
    return claude_cli.run(prompt, model=model, timeout_s=timeout_s, system=system,
                          expect_json=expect_json, images=images, on_activity=on_activity)


def doctor() -> dict:
    """{backend, cli, version, logged_in, detail} -- what the UI shows in the header."""
    if backend_name() == "api":
        from . import claude_api
        info = claude_api.doctor(_cfg)
    else:
        info = claude_cli.doctor()
    info["backend"] = backend_name()
    return info


def owned_by(tag: str | None):
    """File the model calls this thread makes under one job, so Stop can end them."""
    return claude_cli.owned_by(tag)


def stop_owned(tag: str) -> int:
    """End one job's in-flight calls. The API backend stops between students instead."""
    return claude_cli.stop_owned(tag)


def shutdown_all() -> int:
    """Stop in-flight model calls on exit. Only the CLI backend owns subprocesses."""
    return claude_cli.shutdown_all()


def parse_json(text: str):
    return claude_cli.parse_json(text)


def parse_json_ex(text: str):
    return claude_cli.parse_json_ex(text)
