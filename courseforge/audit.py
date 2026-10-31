"""An account of what was done, kept where it cannot quietly be edited.

The ledger next door answers "what did I change in this course lately". This
answers a different and much less comfortable question: a year from now,
somebody disputes whether a student's approved accommodation was actually
given, and the college has to show that it was. The ledger cannot carry that.
It names pages and files rather than people, it lives in a folder on one
laptop, and nothing about it says whether a line was added after the fact.

So this is a second record, with three properties the ledger does not have.

**It names people where naming them is the point.** An entry about an
accommodation says which student, by Canvas id and by name, what was set, on
which quiz, and whether Canvas accepted it. That is student data, and it goes
exactly one place: Canvas, where the same student's grades already are. It is
never sent to a model -- the Assistant's tags are in `identity.py` and the
grader's are in `pseudonym.py`, and neither of those reads this file.

**It is tamper-evident.** Each entry carries the hash of the one before it, so
the file is a chain. Editing a sentence, deleting a row or slipping one in
later breaks every hash after it, and `verify` says which entry it broke at.
This is not the same as tamper-proof. Anyone who can write the file can
rewrite the whole chain from that point. What it does mean is that a quiet
edit is not possible: the whole tail has to be rebuilt, and Canvas has its own
timestamp on the copy it holds.

**It leaves the machine.** Each month's file is uploaded to the instructor's
own Canvas user files, which no course copy, term rollover or sandbox cleanup
touches. Canvas stamps it with a modified date nobody here controls. So the
record of what was done in Canvas lives in Canvas, and a laptop being replaced
does not take the evidence with it.

What it is not: a substitute for Canvas's own logs, and not a legal document.
It is a contemporaneous record made by the tool that did the work.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from . import folders

SCHEMA = 1
DIR = "audit"
CHAIN = "chain.json"
UPLOADS = "uploaded.json"
FOLDER = "courseforge-studio/record"
GENESIS = "0" * 64

_LOCK = threading.RLock()
_ACTOR: dict | None = None
_ACTOR_SOURCE: Callable[[], dict] | None = None
_MACHINE: dict = {}


# --------------------------------------------------------------------- who
def set_actor_source(fn: Callable[[], dict], machine: dict | None = None) -> None:
    """How to find out whose account did this. Called once by the App.

    A callable rather than a value because it is a Canvas round trip, and the
    server must start without one.
    """
    global _ACTOR_SOURCE, _MACHINE
    _ACTOR_SOURCE = fn
    _MACHINE = dict(machine or {})


def actor() -> dict:
    """The Canvas account behind these actions, looked up once."""
    global _ACTOR
    if _ACTOR is None and _ACTOR_SOURCE is not None:
        try:
            found = _ACTOR_SOURCE() or {}
            _ACTOR = {"id": found.get("id"), "name": found.get("name") or ""}
        except Exception:  # noqa: BLE001
            return {}
    return dict(_ACTOR or {})


def forget_actor() -> None:
    global _ACTOR
    _ACTOR = None


# ------------------------------------------------------------------ paths
def folder(root: Path) -> Path:
    return Path(root) / DIR


def month_file(root: Path, when: datetime | None = None) -> Path:
    when = when or datetime.now(timezone.utc)
    return folder(root) / f"{when:%Y-%m}.jsonl"


def months(root: Path) -> list[Path]:
    try:
        return sorted(p for p in folder(root).glob("*.jsonl"))
    except OSError:
        return []


# ----------------------------------------------------------------- writing
def _digest(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _chain_state(root: Path) -> dict:
    path = folder(root) / CHAIN
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("hash"):
            return raw
    except (OSError, ValueError):
        pass
    # No state file, or a broken one. Rebuild from the files themselves rather
    # than starting a second chain beside the first, which would look exactly
    # like the tampering this is here to reveal.
    last, seq = GENESIS, 0
    for path_ in months(root):
        for row in _rows(path_):
            last = row.get("hash") or last
            seq = max(seq, int(row.get("seq") or 0))
    return {"hash": last, "seq": seq}


def _save_chain(root: Path, state: dict) -> None:
    path = folder(root) / CHAIN
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


# Set whenever an entry is written, watched by the Syncer. A record that is
# only in Canvas three minutes later is a record you can lose by closing the
# laptop, and the moments worth having are exactly the busy ones.
CHANGED = threading.Event()


def person(user_id=None, name: str = "", **extra) -> dict:
    """One student, as an entry names them."""
    out = {"id": str(user_id) if user_id is not None else "", "name": name or ""}
    out.update({k: v for k, v in extra.items() if v not in (None, "")})
    return out


def record(root: Path, area: str, action: str, sentence: str, *,
           students: Iterable[dict] | None = None, url: str | None = None,
           count: int | None = None, result: str = "ok",
           detail: dict | None = None, course_id=None) -> dict:
    """Append one entry to the chain. Never raises: a record that cannot be
    written must not be the reason an accommodation does not get applied."""
    try:
        return _record(root, area, action, sentence, students=students, url=url,
                       count=count, result=result, detail=detail, course_id=course_id)
    except Exception:  # noqa: BLE001
        return {}


def _record(root, area, action, sentence, *, students, url, count, result,
            detail, course_id) -> dict:
    root = Path(root)
    with _LOCK:
        state = _chain_state(root)
        entry = {
            "schema": SCHEMA,
            "seq": int(state["seq"]) + 1,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "actor": actor(),
            "machine": _MACHINE.get("name") or "",
            "course_id": str(course_id) if course_id is not None else "",
            "area": area,
            "action": action,
            "sentence": sentence,
            "students": [dict(s) for s in (students or [])],
            "url": url or "",
            "count": count,
            "result": result,
            "detail": detail or {},
            "prev": state["hash"],
        }
        entry["hash"] = _digest(entry)
        path = month_file(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _save_chain(root, {"hash": entry["hash"], "seq": entry["seq"],
                           "at": entry["at"]})
    CHANGED.set()      # tell the syncer there is something new
    return entry


# ----------------------------------------------------------------- reading

def _rows(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        pass
    return out


# Canvas changes that are written here and not in the ledger. Grade pushes
# are the main one: the ledger does not carry names, so a push never appeared
# in "Canvas writes today".
CANVAS_ACTIONS = frozenset({
    "posted", "released", "hidden", "messaged",
    "applied", "extended", "schedule", "quiz-settings",
})


def _local_day(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(str(iso)).astimezone().date().isoformat()
    except ValueError:
        return ""


def canvas_writes_today(root: Path) -> int:
    """Successful Canvas writes recorded only in this chain, for the local day.

    One push of a whole class is one write, not one per student. Editing a
    score on this computer is not a write, and neither is a Canvas refusal.
    """
    today = datetime.now().astimezone().date().isoformat()
    n = 0
    for path in months(root)[-2:]:
        for row in _rows(path):
            if row.get("action") not in CANVAS_ACTIONS:
                continue
            if str(row.get("result") or "ok") != "ok":
                continue
            at = str(row.get("at") or "")
            if at.startswith(today) or _local_day(at) == today:
                n += 1
    return n


def read(root: Path, limit: int = 200, month: str = "", student: str = "",
         area: str = "") -> list[dict]:
    """Entries newest first. `student` matches a Canvas user id or a name."""
    rows: list[dict] = []
    for path in months(root):
        if month and path.stem != month:
            continue
        rows.extend(_rows(path))
    rows.sort(key=lambda r: int(r.get("seq") or 0), reverse=True)
    needle = str(student or "").strip().lower()
    out = []
    for row in rows:
        if area and row.get("area") != area:
            continue
        if needle:
            people = row.get("students") or []
            if not any(needle == str(p.get("id", "")).lower()
                       or needle in str(p.get("name", "")).lower() for p in people):
                continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def students_seen(root: Path) -> list[dict]:
    """Everyone named anywhere in the record, for the filter box."""
    seen: dict[str, dict] = {}
    for path in months(root):
        for row in _rows(path):
            for p in row.get("students") or []:
                key = str(p.get("id") or p.get("name") or "")
                if key and key not in seen:
                    seen[key] = {"id": p.get("id"), "name": p.get("name") or key}
    return sorted(seen.values(), key=lambda p: str(p.get("name") or "").lower())


def verify(root: Path) -> dict:
    """Walk the whole chain. Says where it first stops adding up, and why.

    Three ways it can break, and they are told apart because they mean
    different things: a hash that does not match its own contents (the row was
    edited), a `prev` that does not match the row before (a row was removed or
    inserted), and a sequence number out of order (two writers, or a file put
    back from a copy).
    """
    entries = 0
    last_hash, last_seq = GENESIS, 0
    for path in months(root):
        for row in _rows(path):
            entries += 1
            seq = int(row.get("seq") or 0)
            where = {"file": path.name, "seq": seq, "at": row.get("at"),
                     "sentence": row.get("sentence", "")}
            if _digest(row) != row.get("hash"):
                return {"ok": False, "entries": entries, "broke_at": where,
                        "why": "This entry does not match its own fingerprint, "
                               "which means its text was changed after it was written."}
            if row.get("prev") != last_hash:
                return {"ok": False, "entries": entries, "broke_at": where,
                        "why": "This entry does not follow the one before it, which "
                               "means an entry was removed or inserted."}
            if seq != last_seq + 1:
                return {"ok": False, "entries": entries, "broke_at": where,
                        "why": f"This entry is numbered {seq} where {last_seq + 1} "
                               "was expected, so the file is not the one that was written."}
            last_hash, last_seq = row.get("hash"), seq
    # Cutting the last line off leaves nothing behind it to disagree, so the
    # walk above cannot see it. The bookmark written with every entry can: if
    # it remembers more entries than the files hold, the tail was removed.
    mark = _bookmark(root)
    if mark and int(mark.get("seq") or 0) > last_seq:
        gone = int(mark["seq"]) - last_seq
        where = {"file": "", "seq": last_seq, "at": mark.get("at"),
                 "sentence": ""}
        return {"ok": False, "entries": entries, "broke_at": where,
                "why": (f"The record ends at entry {last_seq} but {mark['seq']} were "
                        f"written, so the last {gone} "
                        f"{'entry was' if gone == 1 else 'entries were'} removed.")}
    # Same count, different fingerprint: the tail was rebuilt. A bookmark that
    # is BEHIND the file is not that; _save_chain is best effort and may have
    # missed a write, and the next entry brings it back into step.
    if mark and last_seq and int(mark.get("seq") or 0) == last_seq \
            and mark.get("hash") != last_hash:
        return {"ok": False, "entries": entries,
                "broke_at": {"file": "", "seq": last_seq, "at": mark.get("at"), "sentence": ""},
                "why": "The last entry does not match the fingerprint written when it "
                       "was recorded, so the end of the file was changed."}
    return {"ok": True, "entries": entries, "broke_at": None,
            "why": ("Every entry follows the one before it." if entries
                    else "Nothing has been recorded here yet.")}


def _bookmark(root: Path) -> dict | None:
    """chain.json as written, or None. Unlike `_chain_state` this never rebuilds
    from the files: a bookmark rebuilt from a shortened file would agree with
    it, which is the one thing verify must not let happen."""
    try:
        raw = json.loads((folder(root) / CHAIN).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) and raw.get("hash") else None


def summary(root: Path) -> dict:
    rows = read(root, limit=1)
    state = _chain_state(root)
    return {"entries": int(state.get("seq") or 0),
            "last": rows[0] if rows else None,
            "months": [p.stem for p in months(root)],
            "uploaded": _uploads(root)}


# --------------------------------------------------------------- to Canvas
README = """CourseForge Studio: record of actions

Each file in this folder is one month of what CourseForge Studio did in a
Canvas course, written as it happened. One line per action, in JSON.

Every line carries a `hash` of itself and the `prev` hash of the line before
it, so the file is a chain: changing, removing or inserting a line breaks
every hash after it. The Studio checks this with

    python -m courseforge record --course <id> --verify

Lines about accommodations name the student, because that is what they are
for. These files sit in the instructor's own Canvas user files, which a course
copy, a term rollover and a sandbox cleanup all leave alone.

This is a contemporaneous record made by the tool that did the work. It is not
a substitute for Canvas's own logs.
"""


def _uploads(root: Path) -> dict:
    try:
        raw = json.loads((folder(root) / UPLOADS).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_uploads(root: Path, state: dict) -> None:
    path = folder(root) / UPLOADS
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def upload_name(course_id, stem: str) -> str:
    return (f"course-{course_id}-{stem}.jsonl" if course_id
            else f"account-{stem}.jsonl")


def sync(client, root: Path, course_id=None, force: bool = False,
         say=lambda _t: None) -> dict:
    """Put every month file that has changed into Canvas user files.

    Only what changed: the finished months of a long term would otherwise be
    re-uploaded every couple of minutes for no reason. `force` sends them all,
    which is what the button on the Record screen does.
    """
    state = _uploads(root)
    sent, failed = [], []
    files = months(root)
    if not files:
        return {"sent": [], "failed": [], "detail": "nothing recorded yet"}
    if not state.get("readme"):
        try:
            client.upload_user_file("README.txt", README.encode("utf-8"),
                                    folder=FOLDER, content_type="text/plain")
            state["readme"] = True
        except Exception:  # noqa: BLE001
            pass
    for path in files:
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(payload).hexdigest()
        name = upload_name(course_id, path.stem)
        if not force and state.get(name, {}).get("sha256") == digest:
            continue
        try:
            out = client.upload_user_file(name, payload, folder=FOLDER,
                                          content_type="application/json")
            state[name] = {"sha256": digest, "bytes": len(payload),
                           "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "file_id": (out or {}).get("id"),
                           "url": (out or {}).get("url", "").split("?")[0]}
            sent.append(name)
            say(f"{name} saved to Canvas ({len(payload)} bytes)")
        except Exception as exc:  # noqa: BLE001
            failed.append({"name": name, "error": f"{type(exc).__name__}: {exc}"[:200]})
            say(f"{name} did not save: {exc}")
    _save_uploads(root, state)
    return {"sent": sent, "failed": failed,
            "detail": (f"{len(sent)} file(s) saved to Canvas" if sent
                       else "already current in Canvas")}


class Syncer(threading.Thread):
    """Keeps Canvas current in the background, quietly and not too often.

    A record that only exists when someone remembers to press a button is not
    a record. This is the same reasoning as the grading handoff worker next
    door, and the same shape: a daemon thread, a debounce, and a failure that
    is written down rather than raised at whoever happened to be typing.
    """

    def __init__(self, app, every_s: int = 180, settle_s: int = 15):
        super().__init__(name="audit-sync", daemon=True)
        self.app = app
        self.every_s = max(30, int(every_s))
        # After something is recorded, wait this long before uploading: a push
        # of thirty grades writes one entry, but a batch of area work writes
        # several in a row, and they belong in one upload.
        self.settle_s = max(2, int(settle_s))
        self.stop_event = threading.Event()
        self.last: dict = {}

    def roots(self) -> list[tuple[Path, str]]:
        """(folder, course id) for everything with a record in it."""
        base = Path(self.app.cfg.data)
        out: list[tuple[Path, str]] = []
        if folder(base).is_dir():
            out.append((base, ""))
        try:
            for child in base.iterdir():
                if child.is_dir() and folder(child).is_dir():
                    out.append((child, folders.id_of(child)))
        except OSError:
            pass
        return out

    def once(self) -> dict:
        if not bool(getattr(self.app.cfg, "audit_to_canvas", True)):
            return {"skipped": "turned off in config.json"}
        done = {}
        for root, cid in self.roots():
            try:
                done[cid or "account"] = sync(self.app.client, root, cid or None)
            except Exception as exc:  # noqa: BLE001
                done[cid or "account"] = {"failed": [{"error": str(exc)[:200]}]}
        self.last = {"at": time.time(), "courses": done}
        return self.last

    def run(self) -> None:
        # Not straight away: the first minute of a server's life is the picker
        # loading, and a 33 MB upload racing that helps nobody.
        if self.stop_event.wait(45):
            return
        while not self.stop_event.is_set():
            self.once()
            # Whichever comes first: something was written, or the period is up.
            if CHANGED.wait(self.every_s):
                CHANGED.clear()
                self.stop_event.wait(self.settle_s)

    def close(self) -> None:
        self.stop_event.set()
