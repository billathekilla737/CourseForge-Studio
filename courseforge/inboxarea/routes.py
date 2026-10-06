"""HTTP routes for the Canvas Inbox.

    GET  /api/inbox/unread                  the header chip's count
    GET  /api/inbox?scope=&course=          the thread list
    GET  /api/inbox/{cid}                   one thread, with its messages
    POST /api/inbox/{cid}/read {instructions}   what it asks, and a draft (a job)
    POST /api/inbox/{cid}/attach {name, type, data}   keep a file for a reply, here
    POST /api/inbox/{cid}/reply {body, attachments}   send one reply (gated twice)
    POST /api/inbox/bulk {ids, action}      mark, archive or delete several (a job)

Reading the inbox is student data, so every call here goes through the
grading-scoped client. Nothing is marked read by reading it. Nothing is sent
without the confirm token, and there is no route that sends more than one
reply: the bulk route only ever changes your own copy of a thread, so it can
never put a word in front of a student. Attaching a file keeps it on this
computer; it reaches Canvas only inside the reply it was attached to.
"""
from __future__ import annotations

from .. import inbox
from ..routing import FileResponse, HTTPError, route


def install(app) -> None:  # noqa: ARG001  (routes register on import)
    return None


@route("GET", "/api/inbox/unread", area="inbox")
def unread(req):
    return inbox.unread_count(req.app)


@route("GET", "/api/inbox", area="inbox")
def listing(req):
    try:
        limit = max(1, min(int(req.q("limit", "40")), inbox.MAX_THREADS))
    except ValueError:
        limit = 40
    scope = req.q("scope")
    if scope not in ("", "unread", "archived", "sent"):
        raise HTTPError(400, "Canvas knows inbox, unread, archived and sent.")
    return inbox.listing(req.app, scope=scope, course_id=req.q("course") or None,
                         limit=limit)


@route("POST", "/api/inbox/bulk", area="inbox")
def bulk(req):
    """Several threads, one action, one trip through the gate.

    Registered before `/api/inbox/{cid}` so "bulk" is read as the verb it is
    rather than as a conversation id.
    """
    body = req.body if isinstance(req.body, dict) else {}
    ids = body.get("ids") or body.get("conversation_ids")
    action = str(body.get("action") or "").strip()
    if action not in inbox.BULK_ACTIONS:
        raise HTTPError(400, "The inbox can mark threads read or unread, archive "
                             "them, move them back, or delete them.")

    def job(log):
        return inbox.bulk(req.app, ids, action, scope=str(body.get("scope") or ""),
                          confirm_token=body.get("confirm"), log=log)

    return req.job("inbox.bulk", job)


@route("GET", "/api/inbox/{cid}/file/{key}", area="inbox")
def attachment(req):
    """One picture or file from this thread. The Canvas address never reaches
    the browser; the bytes are read here and sent back."""
    try:
        path, mime, _name, download = inbox.fetch_file(
            req.app, req.params["cid"], req.params["key"])
    except ValueError as exc:
        raise HTTPError(404, str(exc)) from None
    return FileResponse(path, mime, download=download)


@route("GET", "/api/inbox/{cid}", area="inbox")
def one(req):
    return inbox.thread(req.app, req.params["cid"])


@route("POST", "/api/inbox/{cid}/read", area="inbox")
def read(req):
    """A job: it calls a model, which takes long enough to need progress."""
    cid = req.params["cid"]
    body = req.body if isinstance(req.body, dict) else {}
    model = body.get("model") or None
    told = body.get("instructions")
    told = told.strip() if isinstance(told, str) else ""

    def job(log):
        log("Reading the thread. No name is in what goes out"
            + (", including the one in your instructions." if told else "."), 0, 2)
        out = inbox.read_thread(req.app, cid, model=model, instructions=told)
        log("Drafted a reply. Nothing has been sent.", 2, 2)
        out["sentence_done"] = "Drafted a reply. Nothing has been sent."
        return out

    return req.job("inbox.read", job)


@route("POST", "/api/inbox/{cid}/attach", area="inbox")
def attach(req):
    """One file for a reply on this thread, kept on this computer. Nothing
    goes to Canvas until that reply is sent, after the gate."""
    body = req.body if isinstance(req.body, dict) else {}
    data = body.get("data")
    if not isinstance(data, str) or not data:
        # The server reads nothing past its request cap, so a file far over
        # the limit arrives as no file at all.
        raise HTTPError(400, "No file arrived. One attachment can be up to %d MB."
                             % (inbox.MAX_ATTACH_BYTES // (1024 * 1024)))
    try:
        return inbox.stage_file(req.app, req.params["cid"], str(body.get("name") or ""),
                                str(body.get("type") or ""), data)
    except inbox.Refused as exc:
        raise HTTPError(400, str(exc)) from None


@route("POST", "/api/inbox/{cid}/reply", area="inbox")
def reply(req):
    body = req.body if isinstance(req.body, dict) else {}
    text = body.get("body")
    if not isinstance(text, str) or not text.strip():
        raise HTTPError(400, "There is nothing in the reply box to send.")
    cid = req.params["cid"]
    files = body.get("attachments") or []
    # Checked here as well as in the job, so a file that has gone is said
    # before anything starts rather than after the course has been read.
    try:
        inbox.staged(req.app, cid, files)
    except inbox.Refused as exc:
        raise HTTPError(400, str(exc)) from None

    def job(log):
        return inbox.send_reply(req.app, cid, text, confirm_token=body.get("confirm"),
                                log=log, attachments=files)

    return req.job("inbox.reply", job)
