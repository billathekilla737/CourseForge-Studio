"""Type a student's name; Anthropic sees a tag.

The grader already pseudonymises the work it sends for marking. The Assistant
did not, because the Assistant is free text: you type "why is Jordan Alvarez
failing" and every word of that goes to a model running on somebody else's
computer. Which meant the honest answer to "can I ask it about a student?" was
no.

This is the bridge. A course gets one stable map -- `Student-1`, `Student-2`,
in Canvas user-id order -- kept in `data/<course>/names.json` and never
uploaded anywhere. Outbound, every way of writing a student's name is swapped
for their tag. Inbound, the tags in Claude's reply are turned back into names
before the page draws them, so the conversation reads normally at both ends
and the roster stays on this machine.

What it does not do, said plainly because the UI says it too:

  * It only knows this course's roster. A name that is not on it is not a name
    as far as this module is concerned, and goes out as typed.
  * It covers what you type and what comes back. It cannot cover a file the
    model reads: if you let the Assistant open the gradebook, real names go
    with it. That is what the Allow card is for.
  * A surname two students share is refused rather than guessed. Guessing
    would either send a real name or attribute the question to the wrong
    person, and both are worse than a question.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

FILE = "names.json"
PREFIX = "Student"
TAG = re.compile(r"\b" + PREFIX + r"-(\d{1,4})\b")

# Family and given names that are also ordinary words. For these the full name
# has to be written out: swapping a bare one would rewrite "how long is the
# essay" into "how Student-4 is the essay", which is worse than not swapping.
ALSO_WORDS = {
    "april", "art", "august", "bill", "chance", "chase", "cliff", "dean",
    "drew", "earl", "faith", "forest", "frank", "grace", "grant", "green",
    "hope", "house", "hunter", "joy", "june", "king", "lane", "long", "love",
    "mark", "may", "miles", "moore", "noble", "page", "parker", "price",
    "rich", "rose", "royal", "rusty", "sky", "small", "stone", "summer",
    "will", "wood", "young",
}
# Too short to be safely matched on their own.
MIN_TOKEN = 3

# A misspelt name is not the student's name, so it is not a roster leak in the
# strict sense. It is still close enough to identify somebody, and worse, the
# person typing it believes it was swapped. So a near miss stops the message
# and asks, exactly as an ambiguous surname does.
#
# The threshold is edit distance rather than a similarity ratio because the
# question being asked is "is this the same name typed badly", and a typo is
# one or two keystrokes. Short words are excluded outright: at four letters a
# single edit reaches too many ordinary words, and "Chem" would start asking
# about a student called Chen.
NEAR_MIN = 5


def _near_budget(n: int) -> int:
    return 1 if n < 8 else 2


# Capitalised words that turn up in course prose and are nobody's name. Without
# these the check spends its time asking whether "Monday" was meant to be a
# student. Roster names are matched before this list is consulted, so a student
# really called May is still swapped by the exact match.
NOT_NAMES = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "canvas", "studio", "claude", "ally", "word", "excel", "powerpoint",
    "outlook", "teams", "zoom", "blender", "unity", "maya", "photoshop",
    "google", "microsoft", "adobe", "youtube", "python", "windows",
    "unit", "module", "week", "quiz", "exam", "test", "midterm", "final",
    "assignment", "syllabus", "discussion", "page", "course", "section",
    "rubric", "chapter", "lesson", "lecture", "project", "essay", "paper",
    "spring", "summer", "fall", "autumn", "winter", "term", "semester",
    # The tag prefix itself, so "Student" out of a swapped tag is never
    # offered back as somebody's misspelt name.
    "student", "students",
}


class Swap:
    """One substitution that happened, for the "3 names swapped" chip."""

    def __init__(self, wrote: str, tag: str, name: str):
        self.wrote, self.tag, self.name = wrote, tag, name

    def view(self) -> dict:
        return {"wrote": self.wrote, "tag": self.tag, "name": self.name}


class Masked:
    def __init__(self, text: str, swaps: list[Swap], ambiguous: list[dict],
                 near: list[dict] | None = None):
        self.text = text
        self.swaps = swaps
        self.ambiguous = ambiguous
        self.near = near or []

    @property
    def clean(self) -> bool:
        return not self.ambiguous and not self.near

    def sentence(self) -> str:
        """What stopped it, and what to do about it."""
        if self.ambiguous:
            parts = []
            for row in self.ambiguous:
                who = " and ".join(row["candidates"])
                parts.append(f'"{row["wrote"]}" is {who}')
            return ("Two students match what you wrote, so nothing was sent: "
                    + "; ".join(parts) + ". Write the full name and send it again.")
        if self.near:
            parts = [f'"{r["wrote"]}" looks like {r["suggestion"]}' for r in self.near]
            return ("Nothing was sent: " + "; ".join(parts)
                    + ". A name spelled even slightly wrong is not swapped, so it "
                      "would have gone out as you typed it.")
        return ""

    def view(self) -> dict:
        return {"swapped": [s.view() for s in self.swaps],
                "ambiguous": self.ambiguous, "near": self.near}


class NameMap:
    """One course's roster, its tags, and the substitutions both ways."""

    def __init__(self, course_id, students: list[dict] | None = None,
                 path: Path | None = None, enabled: bool = True):
        self.course_id = str(course_id)
        self.path = Path(path) if path else None
        self.enabled = bool(enabled)
        self.by_tag: dict[str, dict] = {}
        self.by_user: dict[str, str] = {}
        self._needles: list[tuple[re.Pattern, str]] = []
        self._shared: dict[str, list[str]] = {}
        self._spellings: list[tuple[str, str]] = []
        if students is not None:
            self.absorb(students)

    # -------------------------------------------------------------- the map
    def absorb(self, students: list[dict]) -> bool:
        """Add anyone not already tagged. Existing tags never move: a tag that
        changed meaning between two sessions would make the saved conversation
        say something that is not true. Returns whether anything was added."""
        known = {row.get("user_id") for row in self.by_tag.values()}
        highest = max((int(t.split("-")[1]) for t in self.by_tag), default=0)
        added = False
        for user in sorted(students or [], key=lambda u: _num(u.get("id"))):
            uid = str(user.get("id") or "").strip()
            if not uid or uid in known:
                continue
            highest += 1
            tag = f"{PREFIX}-{highest}"
            self.by_tag[tag] = {
                "user_id": uid,
                "name": (user.get("name") or "").strip(),
                "sortable_name": (user.get("sortable_name") or "").strip(),
                "short_name": (user.get("short_name") or "").strip(),
                "login_id": (user.get("login_id") or "").strip(),
                "sis_user_id": str(user.get("sis_user_id") or "").strip(),
                "email": (user.get("email") or "").strip(),
            }
            known.add(uid)
            added = True
        self.by_user = {row["user_id"]: tag for tag, row in self.by_tag.items()}
        self._build()
        return added

    def adopt(self, tag: str, row: dict) -> bool:
        """Take a tag a student already has somewhere else on this machine.

        Used where a thread has no course of its own: the person keeps the
        number they wear in the Assistant rather than being renumbered from one.
        """
        tag, uid = str(tag), str((row or {}).get("user_id") or "")
        if not tag or not uid or tag in self.by_tag or uid in self.by_user:
            return False
        self.by_tag[tag] = dict(row)
        self.by_user[uid] = tag
        self._build()
        return True

    def tag_for(self, user_id) -> str:
        return self.by_user.get(str(user_id), "")

    def name_for(self, tag: str) -> str:
        """The spelling the instructor's screen uses. Tags stay in the file."""
        row = self.by_tag.get(tag) or {}
        legal = (row.get("name") or "").strip()
        if not legal:
            return ""
        try:
            from . import nicknames
            return nicknames.shown(legal, row.get("user_id"))
        except Exception:  # noqa: BLE001
            return legal

    def __len__(self) -> int:
        return len(self.by_tag)

    # ------------------------------------------------------------- matching
    def _build(self) -> None:
        """Every spelling of every student, longest first.

        Longest first matters: "Jordan Alvarez" has to be taken before
        "Jordan", or the surname is left behind in the text.
        """
        whole: list[tuple[str, str]] = []      # (needle, tag), case-insensitive
        single: dict[str, set[str]] = {}       # token -> tags that claim it
        try:
            from . import nicknames
        except Exception:  # noqa: BLE001
            nicknames = None
        for tag, row in self.by_tag.items():
            # sortable_name ("Alvarez, Jordan") is a needle in its own right and
            # not only as the flipped form, or the two halves match separately
            # and the message goes out reading "Student-1, Student-1".
            for field in ("name", "sortable_name", "short_name", "login_id",
                          "sis_user_id", "email"):
                value = (row.get(field) or "").strip()
                if len(value) >= MIN_TOKEN:
                    whole.append((value, tag))
            flipped = _flip(row.get("sortable_name", ""))
            if flipped:
                whole.append((flipped, tag))
            tokens = _tokens(row.get("name", "")) | _tokens(_flip(row.get("sortable_name", "")))
            nick = ""
            if nicknames is not None:
                nick = nicknames.lookup(row.get("user_id") or "")
            if nick:
                # The quoted form is one needle, so John "Jack" Doe does not
                # leave the nickname sitting in the text after the legal name
                # is taken. A bare nickname that is also an ordinary word is
                # left alone, the same as a legal name that is.
                shown = nicknames.format_name(row.get("name") or "", nick)
                if shown and shown.lower() != (row.get("name") or "").strip().lower():
                    whole.append((shown, tag))
                if len(nick) >= MIN_TOKEN and nick.lower() not in ALSO_WORDS:
                    whole.append((nick, tag))
                tokens |= _tokens(nick)
            for token in tokens:
                single.setdefault(token, set()).add(tag)

        self._shared = {t: sorted(tags) for t, tags in single.items() if len(tags) > 1}
        needles: list[tuple[re.Pattern, str]] = []
        seen: set[str] = set()
        for value, tag in sorted(whole, key=lambda p: -len(p[0])):
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            needles.append((re.compile(_bound(value), re.IGNORECASE), tag))
        # Bare given or family names, only where exactly one student owns them
        # and the word is not also an ordinary English word. Case-sensitive,
        # because a capital is the only evidence that "Chase" is a person.
        for token, tags in sorted(single.items(), key=lambda p: -len(p[0])):
            if len(tags) != 1 or token.lower() in ALSO_WORDS or len(token) < MIN_TOKEN:
                continue
            if token.lower() in seen:
                continue
            needles.append((re.compile(_bound(token)), next(iter(tags))))
        self._needles = needles
        # Everything a near miss is measured against, longest first so a full
        # name wins over one of its halves.
        self._spellings = sorted(
            {v.lower(): (v, t) for v, t in whole}.values(),
            key=lambda p: -len(p[0]))
        self._spellings += [(tok, next(iter(tags)))
                            for tok, tags in single.items() if len(tags) == 1]

    def mask(self, text: str, allow_near: bool = False) -> Masked:
        """Real names out, tags in. Nothing is guessed."""
        if not text or not self.enabled or not self.by_tag:
            return Masked(text or "", [], [])
        out = text
        swaps: list[Swap] = []
        for pattern, tag in self._needles:
            def swap(m, tag=tag, swaps=swaps):
                swaps.append(Swap(m.group(0), tag, self.name_for(tag)))
                return tag
            out = pattern.sub(swap, out)

        # Anything left that two students could answer to. Checked against the
        # masked text, so a full name already swapped does not raise it.
        ambiguous = []
        for token, tags in self._shared.items():
            if token.lower() in ALSO_WORDS or len(token) < MIN_TOKEN:
                continue
            if re.search(_bound(token), out):
                ambiguous.append({"wrote": token,
                                  "candidates": [self.name_for(t) for t in tags]})
        near = [] if (ambiguous or allow_near) else self.near_misses(out)
        return Masked(out, swaps, ambiguous, near)

    def near_misses(self, text: str) -> list[dict]:
        """Capitalised words left in the text that look like a name typed badly.

        Run on the masked text, so anything that matched exactly is already
        gone and cannot be reported against itself. Tags are blanked first, or
        the word "Student" out of `Student-1` becomes a candidate.

        Both a pair of words and each word alone are tried, longest first. A
        sentence beginning "Is Jordam caught up" offers "Is Jordam" before
        "Jordam", and taking only the greedy match meant the typo went
        unnoticed.
        """
        if not self.by_tag:
            return []
        scan = TAG.sub(lambda m: " " * len(m.group(0)), text)
        words = [(m.group(0), m.start(), m.end())
                 for m in re.finditer(r"\b[A-Z][a-z]+\b", scan)]
        cands: list[tuple[str, int, int]] = []
        for i, (word, start, end) in enumerate(words):
            if i + 1 < len(words) and 0 < words[i + 1][1] - end <= 2:
                cands.append((word + " " + words[i + 1][0], start, words[i + 1][2]))
        cands += words
        cands.sort(key=lambda c: (-len(c[0]), c[1]))

        found: dict[str, dict] = {}
        answered: list[tuple[int, int]] = []
        for word, start, end in cands:
            if any(start < b and end > a for a, b in answered):
                continue                      # already covered by a longer hit
            if len(word) < NEAR_MIN:
                continue
            if all(part.lower() in NOT_NAMES for part in word.split()):
                continue
            hit = self._closest(word)
            if hit:
                answered.append((start, end))
                found.setdefault(word.lower(), {"wrote": word,
                                                "suggestion": hit[0], "tag": hit[1]})
        return list(found.values())

    def closest(self, written: str, limit: int = 3) -> list[dict]:
        """Roster names nearest to something typed, best first. For the UI."""
        written = (written or "").strip().lower()
        if not written:
            return []
        scored = []
        for tag, row in self.by_tag.items():
            name = row.get("name") or ""
            if not name:
                continue
            best = min((_distance(written, s.lower(), 6)
                        for s, t in self._spellings if t == tag), default=99)
            scored.append((best, name, tag))
        scored.sort()
        return [{"name": n, "tag": t} for d, n, t in scored[:limit] if d <= 6]

    def _closest(self, cand: str) -> tuple[str, str] | None:
        low = cand.lower()
        for spelling, tag in self._spellings:
            n = max(len(low), len(spelling))
            if n < NEAR_MIN:
                continue
            budget = _near_budget(n)
            d = _distance(low, spelling.lower(), budget + 1)
            if 0 < d <= budget:
                return self.name_for(tag) or spelling, tag
        return None

    def roster(self) -> list[dict]:
        """Tag, name and the parts worth matching on, for the @ picker."""
        return [{"tag": tag,
                 "user_id": row.get("user_id") or "",
                 "name": row.get("name") or tag,
                 "sortable": row.get("sortable_name") or ""}
                for tag, row in sorted(self.by_tag.items(),
                                       key=lambda kv: (kv[1].get("sortable_name")
                                                       or kv[1].get("name") or "").lower())]

    def unmask(self, text: str) -> str:
        """Tags back into names, for the page only. Never for anything stored,
        and never for anything on its way out."""
        if not text or not self.by_tag:
            return text or ""
        return TAG.sub(lambda m: self.name_for(m.group(0)) or m.group(0), str(text))

    def hold_len(self, text: str) -> int:
        """How much of the end of a streamed chunk might still become a tag.

        Claude's prose arrives a few characters at a time, and a tag lands
        split across events: "Stud", "ent-1 is behind". Turned back one event
        at a time, neither half matches and the page shows the tag instead of
        the name. So a chunk is cut short of anything that could still be the
        beginning of one, and the rest waits for the next chunk.
        """
        if not text or not self.by_tag:
            return 0
        stem = PREFIX + "-"
        for n in range(min(len(text), len(stem) + 4), 0, -1):
            tail = text[-n:]
            if stem.startswith(tail):
                return n
            if tail.startswith(stem) and tail[len(stem):].isdigit():
                return n
        return 0

    def unmask_event(self, ev: dict) -> dict:
        """A transcript event with every human-readable field turned back."""
        if not self.by_tag or not isinstance(ev, dict):
            return ev
        fields = [k for k in ("text", "summary", "what", "detail", "description",
                              "reason", "command") if isinstance(ev.get(k), str)]
        if not fields:
            return ev
        return dict(ev, **{k: self.unmask(ev[k]) for k in fields})

    def roster_note(self) -> str:
        """The one line the Assistant's rail shows about all this."""
        if not self.enabled:
            return ("Names are sent as you type them: pseudonymize is off in "
                    "config.json.")
        if not self.by_tag:
            return ("No roster read for this course yet, so names go out as you "
                    "type them. Open Grade once to read it.")
        return (f"{len(self.by_tag)} students are swapped for {PREFIX}-1, "
                f"{PREFIX}-2 ... before anything is sent, and turned back for you "
                "here. Ask about a student by name and the Assistant answers from "
                "what the Studio has graded on this PC; it cannot read Canvas's "
                "roster, submissions or gradebook, and the list of who is who "
                "never leaves this machine.")

    # ------------------------------------------------------------- on disk
    def to_json(self) -> dict:
        return {
            "note": "Tag -> student. Local only: this file is never uploaded, "
                    "not to Canvas and not to Anthropic.",
            "course_id": self.course_id,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "students": self.by_tag,
        }

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def load(self) -> bool:
        if not self.path or not self.path.is_file():
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        students = raw.get("students")
        if not isinstance(students, dict):
            return False
        self.by_tag = {str(k): dict(v) for k, v in students.items()
                       if isinstance(v, dict) and v.get("user_id")}
        self.by_user = {row["user_id"]: tag for tag, row in self.by_tag.items()}
        self._build()
        return True


# --------------------------------------------------------------------- cache
_CACHE: dict[str, NameMap] = {}
_LOCK = threading.RLock()


def rebuild_all() -> None:
    """Nicknames changed. Rebuild matchers without rewriting names.json."""
    with _LOCK:
        for nm in _CACHE.values():
            nm._build()


def for_course(app, course_id, refresh: bool = False) -> NameMap:
    """The course's map: from memory, then disk, then Canvas.

    Canvas is only asked when there is nothing on disk or a refresh was asked
    for. A roster read is a read, so it never needs a confirmation -- but it is
    still a network call on the path of someone pressing Enter, and doing that
    every message would make the composer feel broken.
    """
    cid = str(course_id)
    with _LOCK:
        nm = _CACHE.get(cid)
        enabled = bool(getattr(app.cfg, "pseudonymize", True))
        if nm is not None and not refresh:
            nm.enabled = enabled
            return nm
        path = Path(app.course_dir(cid)) / FILE
        nm = nm or NameMap(cid, path=path, enabled=enabled)
        nm.enabled = enabled
        if not nm.by_tag:
            nm.load()
        if refresh or not nm.by_tag:
            try:
                added = nm.absorb(app.client.students(cid))
                if added:
                    nm.save()
            except Exception:  # noqa: BLE001
                # Offline, or a token without roster rights. An empty map masks
                # nothing, and the rail says so rather than pretending.
                pass
        _CACHE[cid] = nm
        return nm


def forget(course_id=None) -> None:
    with _LOCK:
        if course_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(str(course_id), None)
        _KNOWN.clear()


# A student who is already tagged somewhere on this machine keeps that tag
# wherever they turn up next. Canvas Inbox threads are usually account-level
# rather than course-level -- students write to you, not to a course -- so
# without this the same person would be Student-2 in the Assistant and
# Student-1 in the inbox, and "Student-2 means the same person everywhere"
# would be a claim the tool did not honour.
_KNOWN: dict = {}


def known(app, user_id) -> tuple[str, dict] | None:
    """(tag, row) for a student already tagged in any course on this machine."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    hit = known_all(app).get(uid)
    return (hit["tag"], hit["row"]) if hit else None


def known_all(app) -> dict:
    """Every student already tagged on this machine, keyed by Canvas user id.

    Account-level Inbox threads have no course, so a classmate named in the
    body is not a participant and would otherwise go out as written.
    """
    if not _KNOWN:
        _build_known(app)
    return dict(_KNOWN)


def _build_known(app) -> None:
    """One pass over the maps already on disk. Rebuilt when a map changes."""
    try:
        root = Path(app.cfg.data)
        for path in sorted(root.glob("*/" + FILE)):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for tag, row in (raw.get("students") or {}).items():
                uid = str((row or {}).get("user_id") or "")
                if uid and uid not in _KNOWN:
                    _KNOWN[uid] = {"tag": str(tag), "row": dict(row)}
    except Exception:  # noqa: BLE001
        pass


# -------------------------------------------------------------------- bits
def _num(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bound(value: str) -> str:
    """A whole-word match for something that may contain spaces or dots.

    The edges stop at word characters and at `@`, and deliberately not at a
    full stop: a name at the end of a sentence is the ordinary case, and
    refusing to match "ask Jordan Alvarez." would have left the commonest way
    of writing a name untouched.
    """
    return r"(?<![\w@])" + re.escape(value).replace(r"\ ", r"\s+") + r"(?![\w@])"


def _flip(sortable: str) -> str:
    """"Alvarez, Jordan" -> "Jordan Alvarez"."""
    if "," not in (sortable or ""):
        return ""
    family, _, given = sortable.partition(",")
    given, family = given.strip(), family.strip()
    return f"{given} {family}" if given and family else ""


def _distance(a: str, b: str, cap: int) -> int:
    """Edit distance counting a swap of two neighbours as one mistake.

    Plain Levenshtein calls "Alvarze" two edits away from "Alvarez", which
    puts the single commonest typo there is outside a one-edit budget. Counting
    the transposition once is the difference between catching that and not.

    Abandoned as soon as it passes `cap`: the only question being asked is "is
    this within a keystroke or two", and giving up early keeps a long message
    from walking the whole roster at full cost.
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    before = None                          # row i-2, for the transposition
    previous = list(range(len(b) + 1))     # row i-1
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            best = min(previous[j] + 1, current[j - 1] + 1,
                       previous[j - 1] + (ca != cb))
            if (i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb):
                best = min(best, before[j - 2] + 1)
            current.append(best)
        if min(current) > cap:
            return cap + 1
        before, previous = previous, current
    return previous[-1]


def _tokens(name: str) -> set[str]:
    """The parts of a name worth matching on their own: no initials, no
    particles, nothing with a dot in it."""
    out = set()
    for part in re.split(r"[\s,]+", name or ""):
        part = part.strip(".,'\"")
        if len(part) >= MIN_TOKEN and part[:1].isalpha() and "." not in part:
            out.add(part)
    return out
