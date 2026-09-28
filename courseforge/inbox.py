"""The Canvas Inbox: what students asked, what it means, and a drafted reply.

Three things happen here and they are worth keeping apart.

**Reading** is a Canvas call on the grading-scoped client, because
`/conversations` is student data and `canvas_policy` refuses it to everything
else. Opening one thread marks that thread read. The list itself does not.

**Thinking** happens with the names taken out. A student's message goes to
Claude as `Student-14 wrote`, and the body is scrubbed of names, emails, ids
and phone numbers first -- including the names of other students they mention,
which is the case a per-message swap would miss. Claude answers in tags. The
tags become names again on the way to the screen and on the way into the reply,
so what the student eventually reads says their name and what Anthropic saw
never did.

**Sending** is the instructor's, always. A draft sits in a box until somebody
presses Send on that specific reply, and that press goes through the same
confirm gate as every other Canvas write: refused once, shown as a sentence,
sent on the second pass. There is no rule engine and no unattended send. A
message to a student under somebody's own name is the least reversible thing
this tool can do, and the one place where "it seemed right at the time" is not
a defence anybody wants to make to a dean.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from . import audit, humanize, identity, llm, pseudonym

MAX_BODY = 4000          # of one message, before it is handed to a model
MAX_THREADS = 60


def _text(raw: str) -> str:
    """Canvas message bodies are plain text with the odd entity. Flatten.

    Tags come out, which is what used to drop every picture. Files are lifted
    off the message first (see message_assets) and shown beside this text.
    """
    out = html.unescape(str(raw or ""))
    out = re.sub(r"<[^>]+>", " ", out)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", out)).strip()


_IMG = re.compile(r"<img\b([^>]*)>", re.I)
_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
_ATTR = re.compile(r"""\b(src|href|alt)\s*=\s*("|\')(.*?)\2""", re.I)
_FILE_ID = re.compile(r"/files/(\d+)")
_KEY = re.compile(r"^[a-z]-\d{1,20}$|^u-[0-9a-f]{16}$")


def _attr(tag: str, name: str) -> str:
    for found, _q, value in _ATTR.findall(tag or ""):
        if found.lower() == name:
            return html.unescape(value).strip()
    return ""


def _abs_url(base: str, src: str) -> str:
    src = html.unescape(str(src or "")).strip()
    if not src or src.lower().startswith(("javascript:", "data:", "blob:")):
        return ""
    if src.startswith("//"):
        src = "https:" + src
    if src.startswith("/") and base:
        return base.rstrip("/") + src
    if src.startswith(("http://", "https://")):
        return src
    if base:
        return urljoin(base.rstrip("/") + "/", src)
    return ""


def _file_id(url: str):
    match = _FILE_ID.search(url or "")
    return match.group(1) if match else ""


def _ext_of(name: str) -> str:
    base = (name or "").rsplit("/", 1)[-1].split("?", 1)[0]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def _kind(mime: str, name: str, url: str = "") -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    ext = _ext_of(name) or _ext_of(url)
    if "svg" in mime or ext == "svg" or mime in ("text/html", "application/xhtml+xml"):
        return "file"
    if mime.startswith("image/") or ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
        return "image"
    if mime.startswith("video/") or ext in ("mp4", "webm", "mov", "m4v"):
        return "video"
    if mime.startswith("audio/") or ext in ("mp3", "m4a", "wav", "ogg"):
        return "audio"
    if mime == "application/pdf" or ext == "pdf":
        return "pdf"
    return "file"


def _safe_name(name: str, fallback: str) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(name or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return (text or fallback)[:180]


def _asset(key: str, name: str, mime: str, url: str, size=None, hint: str = "") -> dict:
    name = _safe_name(name, "file")
    mime = (mime or "").split(";")[0].strip().lower()
    kind = _kind(mime, name, url)
    if (hint == "image" and kind == "file" and "svg" not in mime
            and "svg" not in (url or "").lower()):
        kind = "image"
    if kind == "file" and ("svg" in mime or mime in ("text/html", "application/xhtml+xml")):
        mime = "application/octet-stream"
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None
    return {"key": key, "name": name, "mime": mime, "kind": kind, "size": size, "url": url}


def message_assets(msg: dict, base: str = "") -> list[dict]:
    """Files on one Canvas message: attachments, a media comment, inline images.

    The url stays here for the download. The screen gets public_asset, which
    leaves it out, and the browser loads the file through this app.
    """
    if not isinstance(msg, dict):
        return []
    out = []
    seen = set()

    def add(asset: dict) -> None:
        if not asset or not asset.get("url") or not asset.get("key"):
            return
        if asset["key"] in seen:
            return
        fid = _file_id(asset["url"])
        if fid and fid in seen:
            return
        seen.add(asset["key"])
        if fid:
            seen.add(fid)
        out.append(asset)

    def walk(one: dict) -> None:
        for att in one.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            fid = str(att.get("id") or "").strip()
            url = _abs_url(base, att.get("url") or "")
            if not fid.isdigit() or not url:
                continue
            mime = att.get("content-type") or att.get("content_type") or ""
            add(_asset("a-" + fid,
                       att.get("display_name") or att.get("filename") or "file",
                       mime, url, att.get("size")))
        media = one.get("media_comment") or {}
        if isinstance(media, dict) and (media.get("url") or media.get("media_id")):
            url = _abs_url(base, media.get("url") or "")
            mid = re.sub(r"[^A-Za-z0-9_-]", "", str(media.get("media_id") or ""))[:40]
            fid = _file_id(url)
            key = ("a-" + fid) if fid else ("u-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16])
            if mid and not fid:
                key = "u-" + hashlib.sha256(("media:" + mid).encode("utf-8")).hexdigest()[:16]
            mime = media.get("content-type") or media.get("content_type") or ""
            if not mime and media.get("media_type") == "video":
                mime = "video/mp4"
            elif not mime and media.get("media_type") == "audio":
                mime = "audio/mp4"
            add(_asset(key, media.get("display_name") or "recording", mime, url))
        body = str(one.get("body") or "")
        for tag in _IMG.findall(body):
            url = _abs_url(base, _attr(tag, "src"))
            if not url:
                continue
            fid = _file_id(url)
            key = ("a-" + fid) if fid else ("u-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16])
            add(_asset(key, _attr(tag, "alt") or "image", "", url, hint="image"))
        for tag, inner in _ANCHOR.findall(body):
            url = _abs_url(base, _attr(tag, "href"))
            fid = _file_id(url)
            if not url or not fid:
                continue
            label = _text(inner) or "file"
            add(_asset("a-" + fid, label, "", url))
        for child in one.get("forwarded_messages") or []:
            if isinstance(child, dict):
                walk(child)

    walk(msg)
    return out


def public_asset(asset: dict) -> dict:
    """What the browser is allowed to see. The Canvas URL stays on the server."""
    return {key: asset.get(key) for key in ("key", "name", "mime", "kind", "size")}


def find_asset(messages, key: str, base: str = "") -> dict | None:
    if not _KEY.match(str(key or "")):
        return None
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        for asset in message_assets(msg, base):
            if asset["key"] == key:
                return asset
    return None


def _echoed(reply: str, instruction: str) -> bool:
    """True when the draft is the direction pasted back, not a message."""
    def norm(text):
        return re.sub(r"\s+", " ", (text or "").strip()).casefold()

    got, told = norm(reply), norm(instruction)
    if not got or not told:
        return False
    if got == told:
        return True
    if told in got and len(got) - len(told) < 40:
        return True
    return False


def _when(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    hours = (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds() / 3600
    if hours < 1:
        return "just now"
    if hours < 36:
        return "%d hour%s ago" % (round(hours), "" if round(hours) == 1 else "s")
    return "%d days ago" % round(hours / 24)


def course_of(row: dict):
    """The course a thread belongs to, or None for account-level mail.

    A message started from the inbox often has no context_code. Canvas still
    names the course under audience_contexts.
    """
    code = str(row.get("context_code") or "")
    if code.startswith("course_"):
        return code.split("course_", 1)[1]
    contexts = row.get("audience_contexts") or {}
    courses = contexts.get("courses") if isinstance(contexts, dict) else None
    if isinstance(courses, dict) and courses:
        return str(next(iter(courses)))
    return None


def course_label(app, row: dict, course_id) -> str:
    """The short course name shown beside the sender. Never an id."""
    known = {}
    for c in (app.store.courses() or []):
        if isinstance(c, dict) and c.get("id") is not None:
            known[str(c.get("id"))] = c.get("title") or c.get("name") or ""
    ids = []
    if course_id:
        ids.append(str(course_id))
    contexts = row.get("audience_contexts") or {}
    courses = contexts.get("courses") if isinstance(contexts, dict) else None
    if isinstance(courses, dict):
        for cid in courses:
            if str(cid) not in ids:
                ids.append(str(cid))
    names = [known.get(cid) or "" for cid in ids]
    names = [n for n in names if n]
    if names:
        return ", ".join(names)
    return str(row.get("context_name") or "").strip()


class Thread:
    """One conversation, and the tags for the people in it.

    Tags come from the course's own map where the thread has a course, so
    Student-14 means the same person here as in the Assistant. A thread with no
    course gets a map of its own, because a message still has to be readable
    without a name in it.
    """

    def __init__(self, app, row: dict, me_id=None):
        self.app = app
        self.row = row
        self.id = row.get("id")
        self.course_id = course_of(row)
        self.me_id = str(me_id or "")
        self.names = (identity.for_course(app, self.course_id)
                      if self.course_id else identity.NameMap("inbox", []))
        people = [p for p in (row.get("participants") or [])
                  if str(p.get("id")) != self.me_id]
        # Canvas Inbox threads are usually account-level rather than
        # course-level, so the first question is whether this person is already
        # tagged in some course on this machine. If they are, they keep that
        # number: Student-2 has to mean the same person here as in the
        # Assistant, or the tag is just a different name for them.
        for p in people:
            if self.names.tag_for(p.get("id")):
                continue
            hit = identity.known(app, p.get("id"))
            if hit:
                self.names.adopt(hit[0], hit[1])
        missing = [p for p in people if not self.names.tag_for(p.get("id"))]
        if missing:
            self.names.absorb(missing)
        # Account-level mail has no course map. Seed every student already
        # tagged on this machine so a classmate named in the body is swapped
        # too, not only the people Canvas listed as participants.
        if not self.course_id:
            for uid, hit in identity.known_all(app).items():
                if not self.names.tag_for(uid):
                    self.names.adopt(hit["tag"], hit["row"])
        self.people = people

    def tag(self, user_id) -> str:
        return self.names.tag_for(user_id) or "Someone"

    def mask(self, text: str) -> str:
        """Names out of a message body, whoever they belong to.

        Every roster name, not only the sender's: a student writing "Jordan
        said the deadline moved" hands over somebody else's name, and a swap
        that only knew the sender would let it through.
        """
        masked = self.names.mask(_text(text)[:MAX_BODY])
        return pseudonym.Pseudonymizer([], enabled=False).scrub(masked.text)

    def unmask(self, text: str) -> str:
        return self.names.unmask(text or "")

    def view(self) -> dict:
        """The thread as the screen draws it: real names, this machine only."""
        last = self.row.get("last_message") or ""
        return {
            "id": self.id,
            "course_id": self.course_id,
            "course_name": course_label(self.app, self.row, self.course_id),
            "subject": self.row.get("subject") or "(no subject)",
            "unread": self.row.get("workflow_state") == "unread",
            "messages": self.row.get("message_count") or 1,
            "at": self.row.get("last_message_at"),
            "ago": _when(self.row.get("last_message_at")),
            "preview": _text(last)[:180],
            "with": [{"tag": self.tag(p.get("id")), "name": p.get("name") or "",
                      "user_id": str(p.get("id"))} for p in self.people],
        }

    def transcript(self, full: dict, mask: bool = True, base: str = "") -> list[dict]:
        """Every message in the thread, oldest first.

        `mask` is for the model, not for the screen. The instructor is reading
        their own inbox: if a student wrote their phone number, showing them
        "[PHONE]" is the tool withholding the very thing the student sent. So
        the screen gets the message as written, and only the copy that leaves
        the machine has anything taken out of it.

        Files are named on both copies. The Canvas address stays off both:
        the screen loads the bytes through this app, and the model only hears
        that a file was attached.
        """
        out = []
        for msg in reversed(full.get("messages") or []):
            author = str(msg.get("author_id") or "")
            body = msg.get("body") or ""
            files = []
            for asset in message_assets(msg, base):
                shown = public_asset(asset)
                if mask:
                    shown["name"] = self.mask(shown.get("name") or "")
                files.append(shown)
            out.append({
                "id": msg.get("id"),
                "from": "you" if author == self.me_id else self.tag(author),
                "at": msg.get("created_at"),
                "body": self.mask(body) if mask else _text(body),
                "files": files,
            })
        return out


# ------------------------------------------------------------------ reading
def unread_count(app) -> dict:
    """What the header chip shows. One Canvas call, and it may fail quietly:
    a number in the corner is never worth a banner across the page."""
    try:
        rows = app.client.conversations(scope="unread", limit=MAX_THREADS)
        return {"unread": len(rows), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    except Exception as exc:  # noqa: BLE001
        return {"unread": None, "detail": "%s: %s" % (type(exc).__name__, exc)}


def listing(app, scope: str = "", course_id=None, limit: int = 40) -> dict:
    rows = app.client.conversations(scope=scope, course_id=course_id, limit=limit)
    me = app.me_id
    threads = [Thread(app, row, me).view() for row in rows]
    return {
        "scope": scope or "inbox",
        "course_id": str(course_id) if course_id else None,
        "threads": threads,
        "unread": sum(1 for t in threads if t["unread"]),
        "note": "Opening a thread marks it read. Nothing is sent until you "
                "press Send on a reply.",
    }


def canvas_base(app) -> str:
    cfg = getattr(app, "cfg", None)
    return str(getattr(cfg, "base_url", "") or "").rstrip("/")


def thread(app, conversation_id) -> dict:
    row = app.client.conversation(conversation_id, mark_read=True)
    t = Thread(app, row, app.me_id)
    out = t.view()
    out["transcript"] = t.transcript(row, mask=False, base=canvas_base(app))
    return out


# ----------------------------------------------------------------- thinking
SYSTEM = """You are helping a college instructor answer their Canvas Inbox.

Students reach you as tags: Student-14, Student-3. You never see a real name
and must never invent one; write the tag if you need to refer to someone.

Answer with JSON and nothing else:
{"asking": "...", "kind": "...", "urgency": "routine|soon|today",
 "needs_you": true|false, "why": "...", "reply": "..."}

- "asking" is one sentence: what this student actually wants.
- "kind" is a short label: deadline, grade question, rubric, technical, absence,
  accommodation, thanks, other.
- "needs_you" is true when answering needs a judgement only the instructor can
  make -- anything about a grade, an accommodation, a late penalty, a personal
  circumstance, or anything you would be guessing at.
- "reply" is a draft the instructor can send as written: their voice, two or
  three sentences, no greeting beyond the student's tag, no sign-off, no
  promises about grades or extensions, and no invented facts about the course.
  If you do not know something, say what you would need rather than filling it
  in. When needs_you is true, draft the part you can and leave the decision to
  them in plain words.

When the instructor has given a direction, that direction decides what the
reply should do. It is not the reply. Never copy the direction into "reply",
and do not answer with their note lightly reworded. Write the short message
the student will read, in the instructor's voice, that carries out the
direction. If they said there is no extension, the reply tells the student
that, in a sentence a student can understand. If the direction cannot be
squared with what the student asked, follow the direction and say what the
mismatch is in "why" rather than quietly splitting the difference. When a
direction is present and it already decides the question, "needs_you" is false."""


def read_thread(app, conversation_id, model: str | None = None,
                instructions: str = "") -> dict:
    """What the student is asking, and a reply for the instructor to consider.

    `instructions` is the instructor saying how to answer -- "no extensions
    this time", "point them at the rubric", "warm, we have been through this".
    Empty is the ordinary case and means: work it out from the message.

    It is masked like everything else. Somebody typing "tell Jordan he can have
    until Friday" has put a real name in a prompt, and the box being theirs
    rather than the student's makes no difference to where it would end up.

    The model never sees a name either way. It sees the thread with tags in it,
    and what comes back is turned into names only on the way to the screen.
    """
    row = app.client.conversation(conversation_id, mark_read=False)
    t = Thread(app, row, app.me_id)
    base = canvas_base(app)
    msgs = t.transcript(row, mask=True, base=base)
    if not msgs:
        raise ValueError("that conversation has no messages in it")

    course = ""
    if t.course_id:
        for c in (app.store.courses() or []):
            if str(c.get("id")) == str(t.course_id):
                course = c.get("title") or c.get("name") or ""
                break
    lines = ["Course: %s" % (course or "not a course message"),
             "Subject: %s" % t.mask(t.row.get("subject") or "(no subject)"), ""]
    for m in msgs:
        lines.append("%s wrote:\n%s\n" % (m["from"], m["body"]))
        names = [f.get("name") or "file" for f in (m.get("files") or [])]
        if names:
            lines.append("Attached (the instructor can already see these): %s\n" % ", ".join(names))
    told = t.mask(instructions or "")
    if told:
        lines.append(
            "The instructor's direction (this tells you how to answer; "
            "it is not text to paste into the reply):\n%s\n" % told)

    chosen = model or getattr(app.cfg, "describe_model", "sonnet")
    result = llm.run("\n".join(lines), model=chosen, system=SYSTEM,
                     expect_json=True, timeout_s=180)
    data = llm.parse_json(result.text) or {}
    if not isinstance(data, dict):
        raise ValueError("the model did not answer in the shape this screen reads")

    draft = str(data.get("reply") or "").strip()
    # A direction such as "no extensions, point them at the rubric" was coming
    # back as the reply itself. Ask once more, and keep the second answer when
    # it is actually a message.
    if told and _echoed(draft, told):
        again = list(lines) + [
            "",
            "Your previous reply copied the instructor's direction. "
            "That direction is not the message. Write the reply the student should read.",
        ]
        second = llm.run("\n".join(again), model=chosen, system=SYSTEM,
                         expect_json=True, timeout_s=180)
        data2 = llm.parse_json(second.text) or {}
        if isinstance(data2, dict):
            draft2 = str(data2.get("reply") or "").strip()
            if draft2 and not _echoed(draft2, told):
                data = data2
                draft = draft2
    # The second pass, while the tags are still in place: the reply a student
    # will read and the note to the instructor both go through the humanizer
    # skill. The tag is checked after the rewrite; a draft that lost it, or
    # changed a date or a number, keeps its original words.
    why = str(data.get("why") or "").strip()
    edited: dict = {}
    if humanize.enabled(app.cfg):
        fields, edited = humanize.humanize_fields(
            {"reply": draft, "why": why}, app.cfg, humanize.KIND_INBOX)
        draft, why = fields.get("reply", draft), fields.get("why", why)
    return {
        "id": t.id,
        "humanized": edited,
        "asking": t.unmask(str(data.get("asking") or "").strip()),
        "kind": str(data.get("kind") or "other").strip(),
        "urgency": str(data.get("urgency") or "routine").strip(),
        "needs_you": bool(data.get("needs_you")),
        "why": t.unmask(why),
        # Both forms: the tagged one is what was actually generated, and the
        # readable one is what goes in the box. Sending re-resolves from the
        # box, so an edit is never lost to the tag round trip.
        "draft": t.unmask(draft),
        "draft_tagged": draft,
        "instructions": instructions or "",
        "with": out_people(t),
        "note": "Nothing has been sent. This draft exists only on this computer.",
    }


def fetch_file(app, conversation_id, key: str):
    """One attachment from one thread, saved under the data folder.

    The key has to be a file this conversation actually carries. A URL from
    the query string is never fetched, so this cannot be aimed at some other
    host. Returns (path, content_type, download_name, as_download).
    """
    if not _KEY.match(str(key or "")):
        raise ValueError("That file is not on this thread.")
    row = app.client.conversation(conversation_id, mark_read=False)
    asset = find_asset(row.get("messages") or [], key, canvas_base(app))
    if not asset:
        raise ValueError("That file is not on this thread.")
    root = Path(app.store.root) / ".inbox-cache"
    root.mkdir(parents=True, exist_ok=True)
    safe_cid = re.sub(r"[^A-Za-z0-9_-]", "", str(conversation_id))[:40] or "thread"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", asset.get("name") or "file").strip(".-")[:80] or "file"
    dest = root / ("%s-%s-%s" % (safe_cid, key, stem))
    app.client.download(asset["url"], dest)
    mime = asset.get("mime") or ""
    if not mime or mime == "application/octet-stream":
        mime = _kind_mime(asset.get("name") or "", asset.get("kind") or "file")
    download = asset.get("kind") == "file"
    return dest, mime or "application/octet-stream", asset.get("name") or "file", download


def _kind_mime(name: str, kind: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    known = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
        "pdf": "application/pdf", "mp4": "video/mp4", "webm": "video/webm",
        "mov": "video/quicktime", "mp3": "audio/mpeg", "m4a": "audio/mp4",
        "wav": "audio/wav", "ogg": "audio/ogg",
    }
    if ext in known:
        return known[ext]
    return {
        "image": "image/jpeg", "video": "video/mp4", "audio": "audio/mpeg",
        "pdf": "application/pdf",
    }.get(kind, "application/octet-stream")


def out_people(t: "Thread") -> list[dict]:
    return [{"tag": t.tag(p.get("id")), "name": p.get("name") or "",
             "user_id": str(p.get("id"))} for p in t.people]


# ------------------------------------------------------------------ sending
def send_reply(app, conversation_id, body: str, confirm_token: str | None = None,
               log=lambda *_a, **_k: None) -> dict:
    """Send one reply, to one thread, after the gate has been through twice.

    No batch form and no rule that sends on its own. Whatever a queue of
    unattended replies would save, it is not worth the morning somebody finds
    out their tool told a student something it had no business saying.
    """
    body = (body or "").strip()
    if not body:
        raise ValueError("There is nothing in the reply box to send.")
    row = app.client.conversation(conversation_id, mark_read=False)
    t = Thread(app, row, app.me_id)
    who = ", ".join(p["name"] or p["tag"] for p in out_people(t)) or "this thread"
    subject = t.row.get("subject") or "(no subject)"

    # A tag left in the text would reach the student as "Student-14". Resolve
    # from the box, so an edit the instructor made is what goes out.
    final = t.unmask(body)

    app._gate("inbox-reply",
              {"conversation_id": str(conversation_id), "body": final},
              'Send this reply to %s in Canvas about "%s"' % (who, subject),
              confirm_token,
              detail=final[:1200],
              what="replying to a student")

    log("sending the reply")
    out = app.client.reply_to_conversation(conversation_id, final)
    audit.record(
        app.course_dir(t.course_id) if t.course_id else app.store.root,
        "inbox", "replied",
        'Replied in Canvas to %s about "%s".' % (who, subject),
        students=[audit.person(p["user_id"], p["name"]) for p in out_people(t)],
        count=1, course_id=t.course_id or "",
        detail={"conversation_id": str(conversation_id), "body": final[:2000]})
    log("sent")
    try:
        app.client.set_conversation_state(conversation_id, "read")
    except Exception as exc:  # noqa: BLE001
        log("sent, but the unread mark could not be cleared (%s)" % exc)
    return {"ok": True, "conversation_id": str(conversation_id),
            "sent_to": [p["name"] or p["tag"] for p in out_people(t)],
            "body": final, "canvas": out}


# ------------------------------------------------------------- several at once
# Marking, archiving and deleting are not sending. They change your own copy of
# a thread and the student is never told, which is why a bulk form is offered
# here and deliberately is not offered for a reply. Deleting is still the one
# that cannot be taken back, so it says so and it is its own red button.
MAX_BULK = 200

BULK_ACTIONS = {
    "read": {
        "state": "read",
        "doing": "Marking read",
        "sentence": "Mark %s read in your Canvas Inbox.",
        "note": "This changes the unread mark on your own copy. Nothing is sent, "
                "the student is not told, and you can mark them unread again.",
        "done": "marked read",
    },
    "unread": {
        "state": "unread",
        "doing": "Marking unread",
        "sentence": "Mark %s unread in your Canvas Inbox.",
        "note": "This changes the unread mark on your own copy. Nothing is sent, "
                "the student is not told, and you can mark them read again.",
        "done": "marked unread",
    },
    "archive": {
        "state": "archived",
        "doing": "Archiving",
        "sentence": "Archive %s in your Canvas Inbox.",
        "note": "Archived threads leave the inbox and stay in Canvas under "
                "Archived. Nothing is sent and the student is not told. You can "
                "move them back.",
        "done": "archived",
    },
    "unarchive": {
        "state": "read",
        "doing": "Moving to the inbox",
        "sentence": "Move %s back into your Canvas Inbox.",
        "note": "They return to the inbox as read. Nothing is sent and the "
                "student is not told.",
        "done": "moved back to the inbox",
    },
    "delete": {
        "state": None,
        "doing": "Deleting",
        "sentence": "Delete %s from your Canvas Inbox.",
        "note": "This deletes your copy only, and it cannot be undone. The "
                "student keeps their copy of the conversation and is not told. "
                "Nothing is sent.",
        "done": "deleted",
    },
}


def _bulk_ids(conversation_ids) -> list[str]:
    seen, out = set(), []
    for raw in (conversation_ids or []):
        one = str(raw).strip()
        if one and one not in seen:
            seen.add(one)
            out.append(one)
    if not out:
        raise ValueError("Pick at least one thread first.")
    if len(out) > MAX_BULK:
        raise ValueError("That is %d threads at once. %d is the most this will do "
                         "in one go, so a slip of the hand cannot empty an inbox."
                         % (len(out), MAX_BULK))
    return out


def _bulk_names(app, ids: list[str], scope: str = "") -> dict:
    """Subject and who, for the threads being acted on.

    One listing read rather than one read per thread: the confirmation has to
    name what it is about to change, and the record has to name whose messages
    they were, but neither is worth N round trips to Canvas. A thread the
    listing does not carry is named by its id and still acted on.
    """
    found: dict[str, dict] = {}
    try:
        rows = app.client.conversations(scope=scope or "", limit=MAX_THREADS)
    except Exception:  # noqa: BLE001
        return found
    wanted = set(ids)
    for row in rows:
        key = str(row.get("id"))
        if key not in wanted:
            continue
        t = Thread(app, row, app.me_id)
        found[key] = {
            "subject": row.get("subject") or "(no subject)",
            "people": out_people(t),
            "course_id": t.course_id or "",
            "was": "unread" if row.get("workflow_state") == "unread" else "read",
        }
    return found


def _bulk_line(one: str, known: dict | None, spec: dict) -> dict:
    """One row of the confirmation: which thread, and what happens to it."""
    known = known or {}
    who = ", ".join(p["name"] or p["tag"] for p in known.get("people") or [])
    subject = known.get("subject") or ("thread " + one)
    return {"label": subject + (" \u00b7 " + who if who else ""),
            "from": known.get("was") or "in your inbox",
            "to": spec["done"]}


def bulk(app, conversation_ids, action: str, scope: str = "",
         confirm_token: str | None = None, log=lambda *_a, **_k: None) -> dict:
    """Mark, archive or delete several threads at once, after the gate.

    One Canvas call per thread. The batch endpoint would be one call, but it
    answers with a progress object and no per-thread result, and a screen that
    says "12 deleted" has to be able to say which twelve and which one failed.
    """
    spec = BULK_ACTIONS.get(action)
    if spec is None:
        raise ValueError("Unknown inbox action %r. It does one of: %s."
                         % (action, ", ".join(sorted(BULK_ACTIONS))))
    ids = _bulk_ids(conversation_ids)
    known = _bulk_names(app, ids, scope)

    count = "%d thread%s" % (len(ids), "" if len(ids) == 1 else "s")
    app._gate("inbox-bulk",
              {"action": action, "conversation_ids": sorted(ids)},
              spec["sentence"] % count + " " + spec["note"],
              confirm_token,
              # The sender names the row; the state is what changes. The
              # dialog strikes the "from" value through, so a name there read
              # as though the student were the thing being deleted.
              detail=json.dumps([_bulk_line(i, known.get(i), spec) for i in ids]),
              what=spec["doing"].lower() + " conversations")

    done, failed = [], []
    for index, one in enumerate(ids, start=1):
        log("%s %d/%d" % (spec["doing"], index, len(ids)), index - 1, len(ids))
        try:
            if spec["state"] is None:
                app.client.delete_conversation(one)
            else:
                app.client.set_conversation_state(one, spec["state"])
            done.append(one)
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": one, "error": "%s: %s" % (type(exc).__name__, exc)})

    # One line in the record per run, naming everyone whose messages moved.
    # Deleting somebody's message is exactly the kind of thing that has to be
    # answerable a year later.
    people, seen = [], set()
    for one in done:
        for p in (known.get(one) or {}).get("people") or []:
            if p["user_id"] not in seen:
                seen.add(p["user_id"])
                people.append(audit.person(p["user_id"], p["name"]))
    courses = {(known.get(one) or {}).get("course_id") for one in done}
    courses.discard("")
    if done:
        audit.record(
            app.course_dir(next(iter(courses))) if len(courses) == 1 else app.store.root,
            "inbox", action,
            "%s %d conversation%s in your own Canvas Inbox. Nothing was sent."
            % (spec["done"].capitalize(), len(done), "" if len(done) == 1 else "s"),
            students=people, count=len(done),
            course_id=next(iter(courses)) if len(courses) == 1 else "",
            result="failed" if failed and not done else "ok",
            detail={"conversation_ids": done,
                    "subjects": [(known.get(one) or {}).get("subject") or one
                                 for one in done],
                    "failed": failed})
    log("done", len(ids), len(ids))
    sentence = "%d thread%s %s." % (len(done), "" if len(done) == 1 else "s",
                                    spec["done"])
    if failed:
        sentence += " %d could not be changed." % len(failed)
    return {"action": action, "done": done, "failed": failed,
            "sentence_done": sentence + " Nothing was sent to anybody."}
