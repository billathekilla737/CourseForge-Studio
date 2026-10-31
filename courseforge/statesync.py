"""Carry Studio state between machines through Canvas user files.

The standing roster, instructor notes, Assistant chats and unpublished Build
drafts live on this computer under data/. They also live in the instructor's
own Canvas user files under courseforge-studio/state/, which no course copy
or term rollover touches. A second computer signed into the same Canvas
account hydrates from that folder on startup (and when you open Assistant or
Build) and pushes when the local copy changes.

This is not the audit chain (courseforge-studio/record) and not the grading
handoff (canvas-grader/). Those stay as they are.

Envelope (one Canvas file per key)::

    {
      "schema": 1,
      "key": "accommodations.json",
      "rev": 4,
      "written_at": "2026-09-21T18:00:00+00:00",
      "machine": {"id": "...", "name": "..."},
      "sha256": "...",
      "payload": { ... }
    }

Conflict rule: if Canvas holds a higher revision whose payload hash is not
what this machine last sent, do not overwrite either side. The UI asks
take-remote or keep-local.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA = 1
FOLDER = "courseforge-studio/state"
SIDECAR_DIR = ".statesync"
KEY_ROSTER = "accommodations.json"
ROSTER_KEY = KEY_ROSTER


def assistant_key(course_id) -> str:
    return f"assistant-{course_id}.json"


def build_key(course_id) -> str:
    return f"build-{course_id}.json"


class Conflict(Exception):
    """Local and Canvas both moved. The UI must pick a side."""

    def __init__(self, key: str, local=None, remote=None, reason: str = ""):
        self.key = key
        self.local = local or {}
        self.remote = remote or {}
        self.reason = reason or "this computer and Canvas both moved"
        super().__init__(self.reason)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _safe_key(key: str) -> str:
    """A Canvas display_name. No slashes: nested keys become dashes."""
    text = str(key or "").strip().replace("\\", "/")
    text = text.lstrip("/")
    if not text or text in (".", "..") or ".." in text.split("/"):
        raise ValueError(f"{key!r} is not a state key")
    return text.replace("/", "-")


def sidecar_dir(root: Path) -> Path:
    path = Path(root) / SIDECAR_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def sidecar_path(root: Path, key: str) -> Path:
    return sidecar_dir(root) / (_safe_key(key) + ".sync.json")


def load_sidecar(root: Path, key: str) -> dict:
    path = sidecar_path(root, key)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_sidecar(root: Path, key: str, state: dict) -> None:
    path = sidecar_path(root, key)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def wrap(key: str, payload: Any, rev: int, machine: dict) -> dict:
    body = {
        "schema": SCHEMA,
        "key": _safe_key(key),
        "rev": int(rev),
        "written_at": _now(),
        "machine": {
            "id": (machine or {}).get("id") or "",
            "name": (machine or {}).get("name") or "",
        },
        "payload": payload,
    }
    body["sha256"] = _digest(payload)
    return body


def unwrap(raw: bytes | str | dict) -> dict:
    if isinstance(raw, dict):
        env = raw
    else:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        env = json.loads(text)
    if not isinstance(env, dict) or "payload" not in env:
        raise ValueError("that Canvas file is not a Studio state envelope")
    if int(env.get("schema") or 0) > SCHEMA:
        raise ValueError("that state file was written by a newer Studio; update this copy")
    return env


def _read_named(client, name: str, folder: str) -> bytes | None:
    reader = getattr(client, "read_user_named", None)
    if callable(reader):
        return reader(name, folder)
    files = []
    try:
        files = list(client.user_folder_files(folder) or [])
    except Exception:  # noqa: BLE001
        return None
    want = name.lower()
    found = None
    for row in files:
        label = (row.get("display_name") or row.get("filename") or "").lower()
        if label == want:
            found = row
            break
    if not found:
        return None
    url = found.get("url") or found.get("download_url") or ""
    if not url:
        return None
    return client.read_file_bytes(url)


def pull(client, key: str, folder: str = FOLDER) -> dict | None:
    """Download one envelope from Canvas, or None if it is not there yet."""
    name = _safe_key(key)
    raw = _read_named(client, name, folder)
    if raw is None:
        return None
    return unwrap(raw)


def push(client, key: str, payload: Any, machine: dict, *,
         rev: int, folder: str = FOLDER) -> dict:
    """Upload one envelope. Caller has already chosen the revision."""
    name = _safe_key(key)
    env = wrap(key, payload, rev, machine)
    blob = json.dumps(env, indent=2, ensure_ascii=False).encode("utf-8")
    probe = getattr(client, "quota_headroom", None)
    if callable(probe):
        head = probe(len(blob))
    else:
        head = {"ok": True}
    if not head.get("ok"):
        raise RuntimeError(
            f"Canvas user files are full ({head.get('quota_used')} of "
            f"{head.get('quota')} bytes); cannot store {name}"
        )
    client.upload_user_file(name, blob, folder, "application/json")
    return env


def reconcile(local_payload: Any, remote: dict | None, side: dict,
              machine: dict) -> dict:
    """Decide what to do with one key. Never writes.

    `side` is the sidecar {rev, sha256, sent_at}.
    action is one of: push, pull, keep, diverged, empty.
    """
    def _is_empty(value):
        if value in (None, "", {}, []):
            return True
        if not isinstance(value, dict):
            return False
        if set(value.keys()) <= {"students", "updated"}:
            return not (value.get("students") or [])
        files = value.get("files")
        if isinstance(files, dict):
            for name, body in files.items():
                if name == "drafts":
                    if isinstance(body, dict) and body:
                        return False
                    if isinstance(body, list) and body:
                        return False
                    continue
                if body not in (None, "", {}, []):
                    return False
            return True
        return False

    local_empty = _is_empty(local_payload)
    local_hash = "" if local_empty else _digest(local_payload)
    sent_hash = (side or {}).get("sha256") or ""
    sent_rev = int((side or {}).get("rev") or 0)
    dirty = bool(local_hash and sent_hash and local_hash != sent_hash)
    if local_empty:
        dirty = False

    if remote is None:
        if local_empty:
            return {"action": "empty", "reason": "nothing here and nothing in Canvas"}
        return {"action": "push", "reason": "Canvas has no copy yet",
                "local_rev": sent_rev, "remote_rev": 0, "dirty": dirty}

    remote_rev = int(remote.get("rev") or 0)
    remote_hash = remote.get("sha256") or _digest(remote.get("payload"))
    if local_empty:
        return {"action": "pull", "reason": "this computer has no copy",
                "local_rev": sent_rev, "remote_rev": remote_rev, "remote": remote}
    if local_hash == remote_hash:
        return {"action": "keep", "reason": "both copies match",
                "local_rev": max(sent_rev, remote_rev), "remote_rev": remote_rev,
                "remote": remote}
    if dirty and remote_rev > sent_rev and remote_hash != sent_hash:
        return {"action": "diverged",
                "reason": "this computer and Canvas both moved",
                "local_rev": sent_rev, "remote_rev": remote_rev, "remote": remote,
                "remote_machine": (remote.get("machine") or {}).get("name") or ""}
    if remote_rev > sent_rev and not dirty:
        return {"action": "pull", "reason": "Canvas is newer and local work is already there",
                "local_rev": sent_rev, "remote_rev": remote_rev, "remote": remote}
    return {"action": "push", "reason": "local work is ahead",
            "local_rev": sent_rev, "remote_rev": remote_rev, "dirty": True}


class StateSyncer:
    """Hydrate on start, push dirty keys in the background."""

    def __init__(self, app, every_s: int = 45):
        self.app = app
        self.every_s = max(15, int(every_s or 45))
        self.folder = FOLDER
        self.last: dict[str, dict] = {}
        self._stop = threading.Event()
        self._changed = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        cfg = getattr(self.app, "cfg", None)
        return bool(getattr(cfg, "state_to_canvas", True))

    @property
    def writes_ok(self) -> bool:
        cfg = getattr(self.app, "cfg", None)
        return bool(getattr(cfg, "allow_canvas_writes", True))

    @property
    def root(self) -> Path:
        store = getattr(self.app, "store", None)
        if store is not None and getattr(store, "root", None):
            return Path(store.root)
        return Path(getattr(self.app.cfg, "data"))

    @property
    def machine(self) -> dict:
        return getattr(self.app, "machine", None) or {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="state-sync")
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._changed.set()

    def mark(self, key: str = KEY_ROSTER) -> None:
        """Call after a local save so the worker pushes soon."""
        self._changed.set()
        self.last[key] = {**(self.last.get(key) or {}), "dirty": True, "at": _now()}

    def mark_dirty(self, key: str = KEY_ROSTER) -> None:
        self.mark(key)

    def status(self, key: str = KEY_ROSTER) -> dict:
        side = load_sidecar(self.root, key)
        info = self.last.get(key) or {}
        state = info.get("did") or info.get("action") or info.get("state") or (
            "in_sync" if side.get("rev") else "empty")
        return {
            "key": _safe_key(key),
            "folder": self.folder,
            "enabled": self.enabled,
            "rev": int(side.get("rev") or info.get("rev") or 0),
            "sent_at": side.get("sent_at") or "",
            "sha256": side.get("sha256") or "",
            "last": info,
            "state": state,
            "did": info.get("did") or state,
            "detail": info.get("reason") or info.get("detail") or "",
            "machine": self.machine,
            "remote_rev": info.get("remote_rev"),
            "remote_machine": info.get("remote_machine") or "",
            "conflict": info if state == "diverged" else None,
        }

    def _local_blob(self, key: str) -> Path:
        return sidecar_dir(self.root) / (_safe_key(key) + ".json")

    def get(self, key: str) -> dict | None:
        """Envelope for this key: memory, local cache, then Canvas."""
        info = self.last.get(key)
        if isinstance(info, dict) and "payload" in info:
            return info
        path = self._local_blob(key)
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                side = load_sidecar(self.root, key)
                env = wrap(key, payload, int(side.get("rev") or 0), self.machine)
                self.last[key] = env
                return env
            except (OSError, ValueError):
                pass
        if not self.enabled:
            return info if isinstance(info, dict) else None
        try:
            env = pull(self.app.client, key, self.folder)
        except Exception:  # noqa: BLE001
            return info if isinstance(info, dict) else None
        if env:
            self.last[key] = env
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(env.get("payload"), indent=2,
                                           ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
            save_sidecar(self.root, key, {
                "rev": int(env.get("rev") or 0),
                "sha256": env.get("sha256") or "",
                "sent_at": env.get("written_at") or _now(),
                "machine": (env.get("machine") or {}).get("id") or "",
            })
        return env

    def put(self, key: str, payload: Any, force: bool = False) -> dict:
        """Write payload through Canvas. Used by student notes."""
        with self._lock:
            try:
                path = self._local_blob(key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                encoding="utf-8")
            except OSError:
                pass
            out = self._put(key, payload, force=force)
            if isinstance(out, dict) and out.get("action") == "diverged" and not force:
                raise Conflict(key, local={"rev": out.get("local_rev")},
                               remote={"rev": out.get("remote_rev"),
                                       "machine": {"name": out.get("remote_machine") or ""}},
                               reason=out.get("reason") or "diverged")
            if isinstance(out, dict) and "payload" not in out:
                out = {**out, "payload": payload, "key": _safe_key(key),
                       "state": out.get("did") or out.get("action") or "sent"}
                self.last[key] = {**self.last.get(key, {}), **out, "payload": payload}
            return out

    def push(self, key: str = KEY_ROSTER, force: bool = False) -> dict:
        """Upload one key now. Roster payload is read from disk."""
        if key in (KEY_ROSTER, "accommodations"):
            key = KEY_ROSTER
            roster = getattr(self.app, "roster", None)
            payload = {
                "students": [s.to_json() for s in roster.load()] if roster else [],
                "updated": _now(),
            }
        else:
            env = self.get(key)
            payload = (env or {}).get("payload")
            if payload is None:
                raise ValueError(f"nothing on this computer for {key} to send")
        out = self._put(key, payload, force=force)
        if isinstance(out, dict) and out.get("action") == "diverged" and not force:
            raise Conflict(key, remote=out.get("remote"),
                           reason=out.get("reason") or "")
        return out

    def hydrate_roster(self) -> dict:
        roster = getattr(self.app, "roster", None)
        if roster is None:
            return {"action": "empty", "did": "empty", "reason": "no roster on this app"}
        students = [s.to_json() for s in roster.load()]
        payload = {"students": students, "updated": _now()}
        return self.hydrate(KEY_ROSTER, payload, apply=self._apply_roster)

    def hydrate(self, key: str = KEY_ROSTER, local_payload: Any = None,
                apply: Callable[[Any], None] | None = None,
                skip_pull: bool = False) -> dict:
        if key in ("accommodations",) and key != KEY_ROSTER:
            key = KEY_ROSTER
        if key == KEY_ROSTER and local_payload is None and apply is None:
            return self.hydrate_roster()
        if local_payload is None:
            got = self.get(key)
            local_payload = (got or {}).get("payload") or {}
        if not self.enabled:
            return {"action": "skipped", "did": "skipped",
                    "reason": "state_to_canvas is off"}
        client = getattr(self.app, "client", None)
        if client is None:
            return {"action": "skipped", "did": "skipped", "reason": "no Canvas client yet"}
        with self._lock:
            try:
                remote = pull(client, key, self.folder)
            except Exception as exc:  # noqa: BLE001
                out = {"action": "error", "did": "error",
                       "reason": f"{type(exc).__name__}: {exc}"}
                self.last[key] = out
                return out
            side = load_sidecar(self.root, key)
            plan = reconcile(local_payload, remote, side, self.machine)
            plan["key"] = _safe_key(key)
            if skip_pull and plan["action"] in ("pull", "diverged"):
                plan["did"] = "skipped_pull" if plan["action"] == "pull" else "diverged"
                if plan["action"] == "pull":
                    plan["reason"] = "Claude is still running on this computer"
                plan["payload"] = local_payload
                self.last[key] = plan
                return plan
            if plan["action"] == "pull" and remote is not None:
                payload = remote.get("payload")
                if apply:
                    apply(payload)
                self._remember(key, payload)
                save_sidecar(self.root, key, {
                    "rev": int(remote.get("rev") or 0),
                    "sha256": remote.get("sha256") or _digest(payload),
                    "sent_at": remote.get("written_at") or _now(),
                    "machine": (remote.get("machine") or {}).get("id") or "",
                })
                plan["did"] = "picked_up"
                plan["rev"] = remote.get("rev")
                plan["payload"] = payload
            elif plan["action"] == "push":
                if not self.writes_ok:
                    plan["did"] = "error"
                    plan["reason"] = "Canvas writes are locked; the replica was not uploaded"
                    self.mark(key)
                else:
                    try:
                        env = self._upload(key, local_payload,
                                           rev=max(int(plan.get("local_rev") or 0),
                                                   int(plan.get("remote_rev") or 0)) + 1)
                        plan["did"] = "seeded" if int(plan.get("remote_rev") or 0) == 0 else "sent"
                        plan["rev"] = env["rev"]
                        plan["payload"] = local_payload
                        plan["dirty"] = False
                    except Exception as exc:  # noqa: BLE001
                        plan["did"] = "error"
                        plan["reason"] = f"{type(exc).__name__}: {exc}"
                        self.mark(key)
            elif plan["action"] == "keep" and remote is not None:
                save_sidecar(self.root, key, {
                    "rev": int(plan.get("local_rev") or remote.get("rev") or 0),
                    "sha256": remote.get("sha256") or "",
                    "sent_at": remote.get("written_at") or _now(),
                    "machine": (remote.get("machine") or {}).get("id") or "",
                })
                plan["did"] = "in_sync"
                plan["rev"] = remote.get("rev")
                plan["payload"] = local_payload
                plan["dirty"] = False
            elif plan["action"] == "diverged":
                plan["did"] = "diverged"
                plan["payload"] = local_payload
            else:
                plan["did"] = plan["action"]
                plan["payload"] = local_payload
            self.last[key] = plan
            return plan

    def resolve(self, key: str, take: str, local_payload: Any = None,
                apply: Callable[[Any], None] | None = None) -> dict:
        """Settle a diverged key. take is 'local' or 'remote'."""
        take = (take or "").strip().lower()
        if take not in ("local", "remote"):
            raise ValueError("take must be local or remote")
        roster = getattr(self.app, "roster", None)
        if key in (KEY_ROSTER, "accommodations"):
            key = KEY_ROSTER
        if local_payload is None:
            if key == KEY_ROSTER and roster is not None:
                local_payload = {"students": [s.to_json() for s in roster.load()],
                                 "updated": _now()}
            else:
                local_payload = (self.last.get(key) or {}).get("payload")
                if local_payload is None:
                    path = self._local_blob(key)
                    if path.is_file():
                        try:
                            local_payload = json.loads(path.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            local_payload = {}
                    else:
                        local_payload = {}
        client = self.app.client
        remote = pull(client, key, self.folder)
        if take == "remote":
            if remote is None:
                raise ValueError("Canvas has no copy to take")
            payload = remote.get("payload")
            if key == KEY_ROSTER:
                self._apply_roster(payload)
            elif apply:
                apply(payload)
            self._remember(key, payload)
            save_sidecar(self.root, key, {
                "rev": int(remote.get("rev") or 0),
                "sha256": remote.get("sha256") or "",
                "sent_at": remote.get("written_at") or _now(),
                "machine": (remote.get("machine") or {}).get("id") or "",
            })
            out = {"did": "picked_up", "take": "remote", "rev": remote.get("rev"),
                   "payload": payload, "dirty": False}
            self.last[key] = out
            return out
        env = self._upload(key, local_payload,
                           rev=max(int((load_sidecar(self.root, key) or {}).get("rev") or 0),
                                   int((remote or {}).get("rev") or 0)) + 1)
        out = {"did": "sent", "take": "local", "rev": env["rev"],
               "payload": local_payload, "dirty": False}
        self.last[key] = out
        return out

    def _put(self, key: str, payload: Any, force: bool = False) -> dict:
        side = load_sidecar(self.root, key)
        remote = None
        try:
            remote = pull(self.app.client, key, self.folder)
        except Exception:  # noqa: BLE001
            if not force:
                raise
        plan = reconcile(payload, remote, side, self.machine)
        if plan["action"] == "diverged" and not force:
            self.last[key] = {**plan, "did": "diverged"}
            return plan
        if not self.writes_ok and not force:
            return {"did": "error", "reason": "Canvas writes are locked"}
        rev = max(int(plan.get("local_rev") or 0), int(plan.get("remote_rev") or 0)) + 1
        env = self._upload(key, payload, rev=rev)
        out = {"did": "sent", "rev": env["rev"], "key": _safe_key(key),
               "payload": payload, "dirty": False}
        self.last[key] = {**(self.last.get(key) or {}), **out}
        return out

    def _upload(self, key: str, payload: Any, rev: int) -> dict:
        env = push(self.app.client, key, payload, self.machine, rev=rev, folder=self.folder)
        save_sidecar(self.root, key, {
            "rev": env["rev"],
            "sha256": env["sha256"],
            "sent_at": env["written_at"],
            "machine": self.machine.get("id") or "",
        })
        try:
            self._local_blob(key).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return env

    def _remember(self, key: str, payload: Any) -> None:
        try:
            path = self._local_blob(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        except OSError:
            pass

    def _flush_dirty(self) -> None:
        for key, info in list(self.last.items()):
            if key in (KEY_ROSTER, "accommodations"):
                continue
            if not (info or {}).get("dirty"):
                continue
            if not self._local_blob(key).is_file():
                continue
            try:
                self.push(key)
            except Conflict:
                pass
            except Exception:  # noqa: BLE001
                pass

    def _apply_roster(self, payload: Any) -> None:
        roster = self.app.roster
        rows = (payload or {}).get("students") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("Canvas roster payload has no students list")
        parsed = []
        from . import accommodations
        for row in rows:
            try:
                parsed.append(accommodations.parse_student(row))
            except (ValueError, TypeError):
                continue
        roster.save(parsed)

    def _course_list_ready(self) -> bool:
        """The home page reads courses.json. Until it exists, leave Canvas
        alone so that first read is not competing with a sync."""
        try:
            return (Path(self.root) / "courses.json").is_file()
        except Exception:  # noqa: BLE001
            return True

    def _run(self) -> None:
        import time
        started = time.monotonic()
        if self._stop.wait(8):
            return
        while not self._stop.is_set():
            if not self._course_list_ready() and time.monotonic() - started < 90:
                if self._stop.wait(2):
                    return
                continue
            try:
                if self.enabled:
                    self.hydrate_roster()
                    try:
                        from . import nicknames
                        nicknames.reconcile(self.app)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        from . import watchnotes
                        watchnotes.reconcile(self.app)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        from .attendance import book as attendance_book
                        attendance_book.push_outstanding(self.app)
                    except Exception:  # noqa: BLE001
                        pass
                    self._flush_dirty()
            except Exception:  # noqa: BLE001
                pass
            self._changed.clear()
            self._changed.wait(self.every_s)
