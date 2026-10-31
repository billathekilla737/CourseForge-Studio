"""Textual overlap between submissions. Deterministic, local, no model call.

Finds word sequences that two submissions share, after subtracting the text
everybody shares (the assignment prompt, a supplied template, a quoted rubric),
and reports the overlapping passages themselves so the instructor can read them
and judge.

This measures overlap. It is not a cheating detector, and nothing in here should
ever be presented as a finding of misconduct: students who used the same source,
worked together where that was allowed, or answered a narrow prompt about a
single reading will overlap legitimately. The output is a reading list, ordered
by how much two submissions have in common.
"""
from __future__ import annotations

import math
import os
import re

WORD = re.compile(r"[a-z0-9']+")
# The same words found in the original text, so a match can be drawn where it
# sits, with the student's own capitals, punctuation and indentation intact.
WORD_AT = re.compile(r"[A-Za-z0-9']+")

# Six-word windows: long enough that ordinary phrasing does not collide, short
# enough to survive a student reordering a clause.
DEFAULT_K = 6

# A pair is worth a closer look at roughly two sentences of verbatim overlap, or
# a sixth of the shorter submission in common. Below that, shared phrasing is
# normal: a quoted definition, a term of art, a sentence from the reading.
NOTABLE_WORDS = 20
NOTABLE_CONTAINMENT = 0.15
# Containment is a ratio, so two short submissions whose only common text is one
# quoted sentence can score high on it. Require a real amount of shared text
# before the ratio alone is allowed to flag a pair.
NOTABLE_WINDOWS = 10

# The grader measures the whole class on its own and marks these for the
# instructor without being asked, so the bar sits far above "notable": most of
# one submission's own wording found in another, or a long passage shared word
# for word. Ordinary shared phrasing must never put a student on that list.
FLAG_CONTAINMENT = 0.6
FLAG_WORDS = 60


# The colour follows the percentage the instructor reads beside it. Red stays
# the push hold, which also needs a real amount of text (see flag_worthy), so a
# short answer that is half shared lands on yellow, not on red or on green.
YELLOW_CONTAINMENT = 0.25


def level(pair: dict, watched: bool = False) -> str:
    """The colour of a pair on the class list: red when it holds a push (the
    flag bar, or a named pair past "notable"), yellow when it is notable or a
    quarter or more is shared, green for anything less. Under one percent is
    no colour."""
    share = pair.get("containment") or 0
    if round(100 * share) < 1:
        return ""
    if flag_worthy(pair) or (watched and pair.get("notable")):
        return "red"
    if pair.get("notable") or share >= YELLOW_CONTAINMENT:
        return "yellow"
    return "green"


def flag_worthy(pair: dict) -> bool:
    if pair.get("method") == "code":
        return bool(pair.get("flag"))
    return ((pair.get("containment") or 0) >= FLAG_CONTAINMENT
            and (pair.get("shared_windows") or 0) >= NOTABLE_WINDOWS
            or (pair.get("longest_words") or 0) >= FLAG_WORDS)


# Part kinds that the student actually wrote. Everything else in an extracted
# entry is produced by this tool (a Blender scene report, an extraction error)
# or by a machine, and is identical across submissions by construction.
PROSE_KINDS = ("text",)


def _chunks(entry: dict) -> list[tuple[str, str]]:
    """(file label, text) for each piece the student wrote, in compare order.

    A .blend submission's extracted text is a scene report this tool generated:
    every student gets near-identical wording, so comparing it would flag a
    whole class for "overlap" that none of them typed. When an entry has parts,
    trust the part kinds; when it has none (a plain online text entry), the
    whole text is the student's.
    """
    parts = entry.get("parts")
    if isinstance(parts, list) and parts:
        out = []
        for part in parts:
            if isinstance(part, dict) and part.get("kind") in PROSE_KINDS:
                text = str(part.get("text") or "")
                if text.strip():
                    out.append((str(part.get("path") or part.get("label") or ""), text))
        return out
    text = str(entry.get("text") or "")
    return [("", text)] if text else []


def student_prose(entry: dict) -> str:
    """Only the text the student wrote, for comparison purposes."""
    return "\n\n".join(text for _, text in _chunks(entry))


def tokens(text: str) -> list[str]:
    return WORD.findall((text or "").lower())


def shingles(toks: list, k: int) -> list[tuple]:
    if len(toks) < k:
        return []
    return [tuple(toks[i:i + k]) for i in range(len(toks) - k + 1)]


def _mask(wins: list, drop: set, k: int) -> list:
    """Windows with every position covered by a `drop` window blanked out.

    Subtracting shared windows from a set is not enough on its own: a window
    straddling the last words of the assignment prompt and the first words of a
    student's own sentence belongs to neither, and matches between two students
    purely because both were answering the same prompt. Masking the covered
    token positions removes those junction artifacts.
    """
    if not drop:
        return list(wins)
    covered = bytearray(len(wins) + k - 1 if wins else 0)
    for i, sh in enumerate(wins):
        if sh in drop:
            covered[i:i + k] = b"\x01" * k
    return [None if any(covered[i:i + k]) else sh for i, sh in enumerate(wins)]


def _masked_shingles(toks: list[str], drop: set, k: int) -> list[tuple | None]:
    return _mask(shingles(toks, k), drop, k)


def _runs(shared: set, b_shingles: list, b_toks: list[str], k: int,
          limit: int = 4) -> list[dict]:
    """Merge consecutive shared windows back into readable passages."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for i, sh in enumerate(b_shingles):
        if sh is not None and sh in shared:
            if start is None:
                start = i
        elif start is not None:
            spans.append((start, i - 1 + k))
            start = None
    if start is not None:
        spans.append((start, len(b_shingles) - 1 + k))
    spans.sort(key=lambda s: s[1] - s[0], reverse=True)
    return [{"words": end - begin, "text": " ".join(b_toks[begin:end])}
            for begin, end in spans[:limit]]


def _group(wins: dict, boiler: list, k: int) -> tuple[set, dict]:
    """What the group shares, and each submission's windows with it masked out.

    Pass one is the assignment prompt, the instructions, a supplied template.
    Pass two is whatever more than a third of the group still has in common.
    """
    common: set = set(boiler)
    masked = {key: _mask(w, common, k) for key, w in wins.items()}

    # Pass two: whatever the group still shares is the assignment, not the
    # student -- a pasted rubric, a citation everyone used, a class template.
    if len(wins) > 2:
        cap = max(2, math.ceil(0.3 * len(wins)))
        frequency: dict[tuple, int] = {}
        for key in wins:
            for sh in set(masked[key]):
                if sh is not None:
                    frequency[sh] = frequency.get(sh, 0) + 1
        group = {sh for sh, n in frequency.items() if n > cap}
        if group:
            common |= group
            masked = {key: _mask(w, common, k) for key, w in wins.items()}
    return common, masked


def compare(texts: dict[str, str], boilerplate: str = "",
            k: int = DEFAULT_K) -> dict:
    """Compare every pair in `texts` (keyed by whatever id the caller uses).

    Returns {"k", "pairs", "skipped", "common_dropped"}. `pairs` is sorted with
    the most overlapping first; each carries the passages themselves.
    """
    toks = {key: tokens(text) for key, text in texts.items()}
    skipped = [key for key, t in toks.items() if len(t) < k]
    keys = [key for key in texts if len(toks[key]) >= k]
    common, shing = _group({key: shingles(toks[key], k) for key in keys},
                           shingles(tokens(boilerplate), k), k)
    sets = {key: {sh for sh in shing[key] if sh is not None} for key in keys}

    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            set_a, set_b = sets[a], sets[b]
            if not set_a or not set_b:
                continue
            shared = set_a & set_b
            if not shared:
                continue
            union = len(set_a | set_b)
            passages = _runs(shared, shing[b], toks[b], k)
            longest = passages[0]["words"] if passages else 0
            containment = len(shared) / min(len(set_a), len(set_b))
            pairs.append({
                "a": a, "b": b,
                "shared_windows": len(shared),
                "jaccard": round(len(shared) / union, 4) if union else 0.0,
                "containment": round(containment, 4),
                "longest_words": longest,
                "passages": passages,
                "notable": (longest >= NOTABLE_WORDS
                            or (containment >= NOTABLE_CONTAINMENT
                                and len(shared) >= NOTABLE_WINDOWS)),
            })

    pairs.sort(key=lambda p: (p["longest_words"], p["containment"]), reverse=True)
    # `pairs` holds only the pairs with something in common; `compared` is how
    # many were actually measured, which is what a report should quote.
    return {"k": k, "pairs": pairs, "skipped": skipped,
            "compared": len(keys) * (len(keys) - 1) // 2,
            "common_dropped": len(common)}


def common_windows(texts: dict[str, str], boilerplate: str = "",
                   k: int = DEFAULT_K) -> set:
    """The windows compare() treats as everyone's for this group."""
    toks = {key: tokens(text) for key, text in texts.items()}
    keys = [key for key in texts if len(toks[key]) >= k]
    return _group({key: shingles(toks[key], k) for key in keys},
                  shingles(tokens(boilerplate), k), k)[0]


# ------------------------------------------------------------------- code
# Code is compared a second time with the names taken out. Renaming `total`
# to `sum` changes every line it is on, so a word match breaks around it,
# but the statements, operators and keywords stay where they were. Inside
# each window, names are numbered by first appearance (`a = b + a` becomes
# `#0 = #1 + #0`), so a consistent rename still matches while two different
# programs that merely use variables do not. Comments and the contents of
# strings are left out of this reading: the word match already compares them.
#
# Windows are tokens, not words, and a line of code is many tokens, so the
# window is longer and the bars are set in tokens: a dozen tokens is about a
# line and a half of a beginner's program.
CODE_K = 16
CODE_NOTABLE_TOKENS = 50
CODE_FLAG_TOKENS = 120
CODE_FLAG_WINDOWS = 25

_HASH_COMMENT = {".py", ".rb", ".r", ".pl", ".sh"}
_STRING = (r'"""[\s\S]*?"""|' r"'''[\s\S]*?'''|"
           r'"(?:\\.|[^"\\\n])*"|' r"'(?:\\.|[^'\\\n])*'|" r"`(?:\\.|[^`\\])*`")
_OPS = (r"==|!=|<=|>=|\+=|-=|\*=|/=|%=|\*\*|//|->|=>|&&|\|\||\+\+|--|<<|>>|::"
        r"|[^\sA-Za-z0-9_]")
_REST = (r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<num>\d+(?:\.\d+)?)"
         r"|(?P<op>" + _OPS + r")")
# `#` starts a comment in Python and its kin; `//` is floor division there.
_CODE_HASH = re.compile(r"(?P<str>" + _STRING + r")|(?P<com>#[^\n]*)" + _REST)
_CODE_SLASH = re.compile(r"(?P<str>" + _STRING + r")|(?P<com>//[^\n]*|/\*[\s\S]*?\*/)"
                         + _REST)

# Kept as themselves: the language, and the library calls every program
# shares. Renaming them is not something a student does to hide a copy.
_KEEP = set("""
and as assert async await break case catch class const continue def default del
do elif else enum except extends false False final finally for foreach from
function global if implements import in instanceof interface is lambda let new
None nonlocal not null or pass private protected public raise return self static
struct super switch this throw throws true True try typeof using var void while
with yield int float double char bool boolean long short byte string String str
list dict set tuple print len range input open main Main console log System out
println printf scanf cout cin endl std Math Console WriteLine ReadLine Write
""".split())


def code_tokens(text: str, ext: str = "") -> list[tuple]:
    """(token, start, end) for one code file. A name becomes ("id", name)."""
    lexer = _CODE_HASH if ext in _HASH_COMMENT else _CODE_SLASH
    out = []
    for m in lexer.finditer(text or ""):
        kind = m.lastgroup
        if kind == "com":
            continue
        if kind == "str":
            tok = "STR"
        elif kind == "name":
            word = m.group(0)
            tok = word if word in _KEEP else ("id", word)
        else:
            tok = m.group(0)
        out.append((tok, m.start(), m.end()))
    return out


def code_tokens_of(entry: dict, code_ext) -> list[tuple]:
    """The code files in a submission as tokens, placed in the text that
    student_prose() returns, so a match can be drawn on it."""
    out, offset = [], 0
    for label, text in _chunks(entry):
        ext = os.path.splitext(label)[1].lower()
        if ext in code_ext:
            out.extend((tok, start + offset, end + offset)
                       for tok, start, end in code_tokens(text, ext))
        offset += len(text) + 2
    return out


def code_windows(ctoks: list[tuple], k: int = CODE_K) -> list[tuple]:
    toks = [t for t, _, _ in ctoks]
    out = []
    for i in range(len(toks) - k + 1):
        names: dict[str, int] = {}
        out.append(tuple(f"#{names.setdefault(t[1], len(names))}" if isinstance(t, tuple)
                         else t for t in toks[i:i + k]))
    return out


def _ranges(spans: list, marked: bytearray) -> list[tuple[int, int, int, int]]:
    """Runs of marked tokens as (first token, last token, char start, char end)."""
    out, first = [], None
    for i, on in enumerate(marked):
        if on and first is None:
            first = i
        elif not on and first is not None:
            out.append((first, i - 1, spans[first][1], spans[i - 1][2]))
            first = None
    if first is not None:
        out.append((first, len(marked) - 1, spans[first][1], spans[-1][2]))
    return out


def _hit_runs(shared: set, masked: list, spans: list, k: int) -> list[tuple]:
    hit = bytearray(len(spans))
    for i, sh in enumerate(masked):
        if sh is not None and sh in shared:
            hit[i:i + k] = b"\x01" * k
    return _ranges(spans, hit)


def compare_code(ctoks: dict[str, list], texts: dict[str, str], boilerplate: str = "",
                 k: int = CODE_K) -> dict:
    """compare() for code files, with names allowed to differ.

    `ctoks` is code_tokens_of() per student and `texts` their student_prose(),
    which the token positions point into. Pairs come back in compare()'s shape
    with "method": "code", their own "notable", and "flag" in place of the
    word thresholds, which do not fit a stream of tokens.
    """
    keys = [key for key, toks in ctoks.items() if len(toks) >= k]
    common, masked = _group({key: code_windows(ctoks[key], k) for key in keys},
                            code_windows(code_tokens(boilerplate), k), k)
    sets = {key: {sh for sh in masked[key] if sh is not None} for key in keys}
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            set_a, set_b = sets[a], sets[b]
            shared = set_a & set_b
            if not shared:
                continue
            text_b = texts.get(b, "")
            runs = sorted(_hit_runs(shared, masked[b], ctoks[b], k),
                          key=lambda r: r[1] - r[0], reverse=True)
            passages = []
            for first, last, start, end in runs[:4]:
                piece = text_b[start:end]
                passages.append({"words": len(WORD_AT.findall(piece)),
                                 "lines": piece.count("\n") + 1,
                                 "tokens": last - first + 1, "text": piece})
            longest = passages[0]["tokens"] if passages else 0
            containment = len(shared) / min(len(set_a), len(set_b))
            pairs.append({
                "a": a, "b": b, "method": "code",
                "shared_windows": len(shared),
                "jaccard": round(len(shared) / len(set_a | set_b), 4),
                "containment": round(containment, 4),
                "longest_words": passages[0]["words"] if passages else 0,
                "longest_lines": passages[0]["lines"] if passages else 0,
                "longest_tokens": longest,
                "passages": passages,
                "notable": (longest >= CODE_NOTABLE_TOKENS
                            or (containment >= NOTABLE_CONTAINMENT
                                and len(shared) >= NOTABLE_WINDOWS)),
                "flag": (longest >= CODE_FLAG_TOKENS
                         or (containment >= FLAG_CONTAINMENT
                             and len(shared) >= CODE_FLAG_WINDOWS)),
            })
    return {"k": k, "pairs": pairs, "common_dropped": len(common)}


def common_code_windows(ctoks: dict[str, list], boilerplate: str = "",
                        k: int = CODE_K) -> set:
    keys = [key for key, toks in ctoks.items() if len(toks) >= k]
    return _group({key: code_windows(ctoks[key], k) for key in keys},
                  code_windows(code_tokens(boilerplate), k), k)[0]


_RANK = {"red": 3, "yellow": 2, "green": 1, "": 0}


def _strength(pair: dict) -> tuple:
    return (_RANK[level(pair)], pair.get("containment") or 0)


def merge_pairs(word_pairs: list[dict], code_pairs: list[dict]) -> list[dict]:
    """One reading per pair: the words or the code, whichever finds more."""
    best: dict[frozenset, dict] = {}
    for pair in word_pairs:
        best[frozenset((pair["a"], pair["b"]))] = {**pair, "method": "words"}
    for pair in code_pairs:
        key = frozenset((pair["a"], pair["b"]))
        if key not in best or _strength(pair) > _strength(best[key]):
            best[key] = pair
    return sorted(best.values(), key=lambda p: (_strength(p), p["longest_words"]),
                  reverse=True)


# ------------------------------------------------------------ side by side
def _word_spans(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0).lower(), m.start(), m.end()) for m in WORD_AT.finditer(text or "")]


def _pair_runs(spans_a: list, wins_a: list, spans_b: list, wins_b: list,
               common: set, k: int) -> dict:
    """Shared and masked runs on each side of a pair, each shared run pointing
    at the run on the other side it starts in."""
    sides = [{"spans": spans_a, "wins": wins_a}, {"spans": spans_b, "wins": wins_b}]
    for side in sides:
        side["masked"] = _mask(side["wins"], common, k)
    sets = [{sh for sh in side["masked"] if sh is not None} for side in sides]
    shared = sets[0] & sets[1]

    for side in sides:
        dim = bytearray(len(side["spans"]))
        for i, sh in enumerate(side["wins"]):
            if sh in common:
                dim[i:i + k] = b"\x01" * k
        side["hit"] = _hit_runs(shared, side["masked"], side["spans"], k)
        side["dim"] = _ranges(side["spans"], dim)
        # Where each shared window starts on this side, for pointing at a peer.
        side["at"] = {}
        for i, sh in enumerate(side["masked"]):
            if sh is not None and sh in shared:
                side["at"].setdefault(sh, i)

    def peers(me, other):
        out = []
        for first, last, start, end in me["hit"]:
            peer = None
            for i in range(first, max(first, last - k + 1) + 1):
                sh = me["masked"][i] if i < len(me["masked"]) else None
                if sh in other["at"]:
                    at = other["at"][sh]
                    peer = next((n for n, run in enumerate(other["hit"])
                                 if run[0] <= at <= run[1]), None)
                    break
            out.append({"start": start, "end": end, "tokens": last - first + 1,
                        "peer": peer})
        return out

    smaller = min(len(sets[0]), len(sets[1]))
    return {
        "containment": round(len(shared) / smaller, 4) if smaller else 0.0,
        "longest": max((last - first + 1 for first, last, _, _ in sides[1]["hit"]),
                       default=0),
        "a": {"runs": peers(sides[0], sides[1]),
              "common": [{"start": s, "end": e} for _, _, s, e in sides[0]["dim"]]},
        "b": {"runs": peers(sides[1], sides[0]),
              "common": [{"start": s, "end": e} for _, _, s, e in sides[1]["dim"]]},
    }


def pair_spans(text_a: str, text_b: str, common: set | None = None,
               k: int = DEFAULT_K) -> dict:
    """Where two submissions share wording, as character ranges into each.

    `shared` is what compare() counts for this pair: windows both have and the
    group does not. Each run carries `peer`, the run on the other side it
    starts in, so the page can jump between them. `common` is wording masked
    as everyone's (the prompt, a template); it is drawn dimmed, not hidden,
    so the instructor can see what was left out of the count.
    """
    spans_a, spans_b = _word_spans(text_a), _word_spans(text_b)
    found = _pair_runs(spans_a, shingles([w for w, _, _ in spans_a], k),
                       spans_b, shingles([w for w, _, _ in spans_b], k),
                       common or set(), k)

    def side(runs):
        return [{"start": r["start"], "end": r["end"], "words": r["tokens"],
                 "peer": r["peer"]} for r in runs]

    return {
        "k": k,
        "containment": found["containment"],
        "longest_words": found["longest"],
        "a": {"shared": side(found["a"]["runs"]), "common": found["a"]["common"]},
        "b": {"shared": side(found["b"]["runs"]), "common": found["b"]["common"]},
    }


def code_pair_spans(text_a: str, ctoks_a: list, text_b: str, ctoks_b: list,
                    common: set | None = None, k: int = CODE_K) -> dict:
    """pair_spans() for code with names allowed to differ: the "similar" runs,
    the same statements in the same order under other names."""
    found = _pair_runs(ctoks_a, code_windows(ctoks_a, k),
                       ctoks_b, code_windows(ctoks_b, k), common or set(), k)

    def side(runs, text):
        return [{"start": r["start"], "end": r["end"],
                 "lines": text[r["start"]:r["end"]].count("\n") + 1,
                 "peer": r["peer"]} for r in runs]

    similar_b = side(found["b"]["runs"], text_b)
    return {
        "k": k,
        "containment": found["containment"],
        "longest_lines": max((r["lines"] for r in similar_b), default=0),
        "a": {"similar": side(found["a"]["runs"], text_a)},
        "b": {"similar": similar_b},
    }
