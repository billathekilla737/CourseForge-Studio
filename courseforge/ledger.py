"""What the Studio changed in a course, in plain sentences.

Every area appends one line here after a Canvas write succeeds. The course hub
shows the last few; the Assistant's rail shows them all. It is an append-only
JSONL file under the course's data folder, so it survives restarts and never
carries student data (sentences name pages, files and settings, not people).

It is the recent-activity list, not the account of record: it lives on one
machine and nothing about it says whether a line was added afterwards. Every
entry is copied into `audit.py`, which is chained, names people where that is
the point, and is kept in Canvas.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import folders

_LOCK = threading.Lock()
FILE = "ledger.jsonl"


def record(course_dir: Path, area: str, sentence: str, url: str | None = None,
           undo: dict | None = None, kind: str = "", count: int | None = None) -> dict:
    """Append one entry. `undo` is an optional {"route": ..., "body": ...} the UI
    can offer as Roll back; `count` is how many Canvas objects changed."""
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "area": area,
        "kind": kind,
        "sentence": sentence,
        "url": url,
        "count": count,
        "undo": undo,
    }
    path = Path(course_dir) / FILE
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # And into the tamper-evident record, which is the copy that goes to Canvas
    # and outlives this folder. Every area already calls this function after a
    # successful write, so putting it here means no area has to remember to.
    from . import audit
    audit.record(course_dir, area, kind or "write", sentence, url=url,
                 count=count, course_id=folders.id_of(course_dir))
    return entry


def read(course_dir: Path, limit: int = 50, area: str | None = None) -> list[dict]:
    path = Path(course_dir) / FILE
    if not path.is_file():
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if area and row.get("area") != area:
                continue
            rows.append(row)
    rows.reverse()
    return rows[:limit]


def today_counts(course_dir: Path) -> dict:
    """{"writes": n} for the current local day, for the hub's stat strip."""
    today = datetime.now().date().isoformat()
    n = sum(1 for r in read(course_dir, limit=10_000)
            if str(r.get("at", "")).startswith(today) or
            _local_day(r.get("at")) == today)
    return {"writes": n}


def _local_day(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone().date().isoformat()
    except ValueError:
        return ""
