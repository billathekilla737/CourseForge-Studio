"""The Canvas Inbox: what goes to a model, and what it takes to send.

Two properties carry this feature. A student's message must reach Claude with
every name taken out -- theirs and anyone else's they mention -- and a reply
must be impossible to send without the instructor pressing the button on that
specific reply.
"""
import base64
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from courseforge import identity, inbox

ROSTER = [
    {"id": 101, "name": "Jordan Alvarez", "sortable_name": "Alvarez, Jordan"},
    {"id": 205, "name": "Dana Wu", "sortable_name": "Wu, Dana"},
]

THREAD = {
    "id": 77, "subject": "Project 2 deadline", "workflow_state": "unread",
    "message_count": 2, "context_code": "course_734975",
    "last_message_at": "2026-09-12T10:00:00Z",
    "last_message": "I missed the deadline because my laptop died",
    "participants": [{"id": 101, "name": "Jordan Alvarez"},
                     {"id": 9, "name": "Zachary Garris"}],
    "messages": [
        {"id": 2, "author_id": 9, "created_at": "2026-09-12T11:00:00Z",
         "body": "Send me what you have."},
        {"id": 1, "author_id": 101, "created_at": "2026-09-12T10:00:00Z",
         "body": "Hi, this is Jordan Alvarez. Dana Wu said the deadline moved. "
                 "My email is jalvarez3@example.edu and my number is 601-555-0143."},
    ],
}


class FakeClient:
    def __init__(self):
        self.sent = []
        self.marked = []
        self.states = []

    def conversations(self, scope="", course_id=None, limit=50):
        return [THREAD]

    def conversation(self, cid, mark_read=False):
        self.marked.append(mark_read)
        return THREAD

    def set_conversation_state(self, cid, state):
        self.states.append((str(cid), state))
        return {"id": cid, "workflow_state": state}

    def reply_to_conversation(self, cid, body, recipients=None):
        self.sent.append((str(cid), body))
        return {"id": cid}

    def students(self, cid):
        return ROSTER


class FakeStore:
    def __init__(self, root):
        self.root = Path(root)

    def courses(self):
        return [{"id": "734975", "name": "202630 IST 2824 301 Intro", "title": "Intro"}]

    def assignments(self, cid):
        return []


class FakeApp:
    def __init__(self, root):
        self.root = Path(root)
        self.client = FakeClient()
        self.store = FakeStore(root)
        self.me_id = 9
        self.cfg = type("C", (), {"data": str(root), "pseudonymize": True,
                                  "describe_model": "sonnet"})()
        self.gated = []

    def course_dir(self, cid):
        p = self.root / str(cid)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _gate(self, kind, payload, sentence, token, detail=None, what=""):
        self.gated.append({"kind": kind, "sentence": sentence, "token": token,
                           "payload": payload, "detail": detail})
        if not token:
            raise PermissionError(sentence)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        identity.forget()
        self.addCleanup(identity.forget)
        self.app = FakeApp(self.tmp)


class NothingLeavesWithAName(Base):
    def test_the_thread_name_opens_the_student_page(self):
        src = (Path(__file__).resolve().parents[1] / "courseforge" / "web" / "js"
               / "inbox.js").read_text(encoding="utf-8")
        self.assertIn("#/c/${esc(t.course_id)}/student/${esc(p.user_id)}", src)
        self.assertIn('class="ibWhoLink"', src)

    def test_the_list_names_the_course_beside_the_sender(self):
        view = inbox.Thread(self.app, THREAD, me_id=9).view()
        self.assertEqual(view["course_name"], "Intro")

    def test_a_thread_without_a_context_code_still_names_the_course(self):
        row = dict(THREAD, context_code="",
                   audience_contexts={"courses": {"734975": ["101"]}})
        view = inbox.Thread(self.app, row, me_id=9).view()
        self.assertEqual(view["course_id"], "734975")
        self.assertEqual(view["course_name"], "Intro")

    def test_the_sender_is_a_tag_in_what_goes_out(self):
        t = inbox.Thread(self.app, THREAD, me_id=9)
        body = t.mask(THREAD["messages"][1]["body"])
        self.assertNotIn("Jordan", body)
        self.assertNotIn("Alvarez", body)

    def test_so_is_a_third_student_they_mention(self):
        """The case a per-sender swap would miss: one student naming another."""
        t = inbox.Thread(self.app, THREAD, me_id=9)
        body = t.mask(THREAD["messages"][1]["body"])
        self.assertNotIn("Dana", body)
        self.assertNotIn("Wu,", body)

    def test_an_email_and_a_phone_number_go_too(self):
        t = inbox.Thread(self.app, THREAD, me_id=9)
        body = t.mask(THREAD["messages"][1]["body"])
        self.assertNotIn("jalvarez3", body)
        self.assertNotIn("601-555-0143", body)

    def test_the_whole_prompt_is_checked_not_just_one_message(self):
        seen = {}

        def fake_run(prompt, **kw):
            seen["prompt"] = prompt
            return type("R", (), {"text": json.dumps(
                {"asking": "an extension", "kind": "deadline", "urgency": "soon",
                 "needs_you": True, "why": "a late penalty is yours to decide",
                 "reply": "Student-1, send me what you have by Friday."})})()

        with mock.patch.object(inbox.llm, "run", fake_run):
            out = inbox.read_thread(self.app, 77)
        for secret in ("Jordan", "Alvarez", "Dana", "jalvarez3", "601-555-0143"):
            self.assertNotIn(secret, seen["prompt"], secret)
        # and the answer comes back readable
        self.assertIn("Jordan Alvarez", out["draft"])
        self.assertEqual(out["draft_tagged"], "Student-1, send me what you have by Friday.")

    def test_the_me_participant_is_not_given_a_student_tag(self):
        t = inbox.Thread(self.app, THREAD, me_id=9)
        self.assertEqual([p["user_id"] for p in t.view()["with"]], ["101"])


class TheScreenShowsWhatWasWritten(Base):
    """The instructor is reading their own inbox. Withholding a phone number
    the student typed would be the tool hiding the message from the person it
    was sent to; only the copy that leaves the machine is scrubbed."""

    def test_the_screen_gets_the_message_as_written(self):
        out = inbox.thread(self.app, 77)
        body = [m["body"] for m in out["transcript"] if m["from"] != "you"][0]
        self.assertIn("Jordan Alvarez", body)
        self.assertIn("jalvarez3@example.edu", body)
        self.assertIn("601-555-0143", body)

    def test_and_the_model_still_gets_none_of_it(self):
        seen = {}

        def fake_run(prompt, **kw):
            seen["prompt"] = prompt
            return type("R", (), {"text": '{"asking":"x","reply":"y"}'})()

        with mock.patch.object(inbox.llm, "run", fake_run):
            inbox.read_thread(self.app, 77)
        for secret in ("Jordan", "Alvarez", "jalvarez3", "601-555-0143"):
            self.assertNotIn(secret, seen["prompt"], secret)


class TellingItHowToAnswer(Base):
    """The instruction box. Empty is the ordinary case; when it is not empty,
    what the instructor said outranks what the model made of the message."""

    def _run(self, instructions=""):
        seen = {}

        def fake_run(prompt, **kw):
            seen["prompt"] = prompt
            seen["system"] = kw.get("system") or ""
            return type("R", (), {"text": '{"asking":"x","reply":"ok"}'})()

        with mock.patch.object(inbox.llm, "run", fake_run):
            out = inbox.read_thread(self.app, 77, instructions=instructions)
        return seen, out

    def test_nothing_typed_means_work_it_out_from_the_message(self):
        seen, out = self._run("")
        self.assertNotIn("The instructor says", seen["prompt"])
        self.assertEqual(out["instructions"], "")

    def test_what_was_typed_is_carried_into_the_prompt(self):
        seen, _out = self._run("No extensions this week. Point them at the rubric.")
        self.assertIn("it is not text to paste", seen["prompt"])
        self.assertIn("No extensions this week", seen["prompt"])

    def test_a_name_typed_into_the_box_is_swapped_like_any_other(self):
        """The box being the instructor's rather than the student's makes no
        difference to where the name would end up."""
        seen, _out = self._run("Tell Jordan Alvarez he has until Friday. "
                               "Copy dana@example.edu.")
        self.assertNotIn("Jordan", seen["prompt"])
        self.assertNotIn("Alvarez", seen["prompt"])
        self.assertNotIn("dana@example.edu", seen["prompt"])
        self.assertIn("Student-1", seen["prompt"])

    def test_the_system_prompt_says_the_instruction_is_not_the_reply(self):
        """The box is a direction. Pasting it back is the bug: the student
        would be sent the instructor's note instead of an answer."""
        seen, _out = self._run("say no")
        self.assertIn("It is not the reply", seen["system"])
        self.assertIn("Never copy the direction", seen["system"])

    def test_a_pasted_direction_is_sent_back_for_a_real_reply(self):
        seen = {"n": 0}

        def fake_run(prompt, **kw):
            seen["n"] += 1
            seen["prompt"] = prompt
            if seen["n"] == 1:
                reply = "No extensions this week. Point them at the rubric."
            else:
                reply = "Student-1, I can't move the deadline. The rubric is on the module page."
            return type("R", (), {"text": json.dumps({"asking": "an extension", "reply": reply})})()

        with mock.patch.object(inbox.llm, "run", fake_run):
            out = inbox.read_thread(
                self.app, 77,
                instructions="No extensions this week. Point them at the rubric.")
        self.assertEqual(seen["n"], 2)
        self.assertIn("Your previous reply copied", seen["prompt"])
        self.assertIn("I can't move the deadline", out["draft"])
        self.assertNotIn("Point them at the rubric.", out["draft"])


class OneTagPerPerson(Base):
    """Canvas Inbox threads are usually account-level rather than course-level,
    so the tag cannot come from the thread's course -- there is not one. A
    student already numbered in a course on this machine keeps that number, or
    "Student-2 means the same person everywhere" is not true."""

    def _cached_map(self, course, tag, uid, name):
        d = self.tmp / course
        d.mkdir(parents=True, exist_ok=True)
        (d / identity.FILE).write_text(json.dumps({
            "students": {tag: {"user_id": str(uid), "name": name,
                               "sortable_name": name}}}), encoding="utf-8")

    def test_a_student_keeps_the_tag_they_have_in_a_course(self):
        self._cached_map("734975", "Student-24", 101, "Jordan Alvarez")
        identity.forget()
        row = dict(THREAD, context_code="account_11")
        t = inbox.Thread(self.app, row, me_id=9)
        self.assertEqual(t.tag(101), "Student-24")

    def test_somebody_new_to_this_machine_still_gets_one(self):
        identity.forget()
        row = dict(THREAD, context_code="account_11")
        t = inbox.Thread(self.app, row, me_id=9)
        self.assertTrue(t.tag(101).startswith("Student-"))

    def test_and_the_name_still_does_not_reach_the_model(self):
        self._cached_map("734975", "Student-24", 101, "Jordan Alvarez")
        identity.forget()
        row = dict(THREAD, context_code="account_11")
        t = inbox.Thread(self.app, row, me_id=9)
        body = t.mask(THREAD["messages"][1]["body"])
        self.assertNotIn("Jordan", body)
        self.assertIn("Student-24", body)

    def test_a_classmate_named_in_account_level_mail_is_swapped(self):
        """Account-level threads only listed participants; a third student
        mentioned in the body used to go out as written."""
        self._cached_map("734975", "Student-24", 101, "Jordan Alvarez")
        self._cached_map("734736", "Student-2", 205, "Dana Wu")
        identity.forget()
        row = dict(THREAD, context_code="account_11")
        t = inbox.Thread(self.app, row, me_id=9)
        body = t.mask(THREAD["messages"][1]["body"])
        self.assertNotIn("Dana", body)
        self.assertNotIn("Wu", body)
        self.assertIn("Student-2", body)


class ReadingChangesNothing(Base):
    def test_opening_a_thread_marks_it_read(self):
        inbox.thread(self.app, 77)
        self.assertEqual(self.app.client.marked, [True])

    def test_the_listing_does_not_mark_the_inbox_read(self):
        out = inbox.listing(self.app)
        self.assertIn("Opening a thread marks it read", out["note"])
        self.assertEqual(out["unread"], 1)
        self.assertEqual(self.app.client.marked, [])


class SendingTakesTwo(Base):
    def test_a_reply_is_refused_without_the_token(self):
        with self.assertRaises(PermissionError):
            inbox.send_reply(self.app, 77, "Send me what you have.")
        self.assertEqual(self.app.client.sent, [], "it sent on the first pass")
        self.assertEqual(self.app.client.states, [])

    def test_the_sentence_names_the_student_and_the_thread(self):
        try:
            inbox.send_reply(self.app, 77, "anything")
        except PermissionError:
            pass
        said = self.app.gated[0]["sentence"]
        self.assertIn("Jordan Alvarez", said)
        self.assertIn("Project 2 deadline", said)

    def test_with_the_token_it_goes_once(self):
        out = inbox.send_reply(self.app, 77, "Send me what you have.", confirm_token="t")
        self.assertEqual(len(self.app.client.sent), 1)
        self.assertEqual(out["sent_to"], ["Jordan Alvarez"])
        self.assertEqual(self.app.client.states, [("77", "read")])

    def test_a_tag_left_in_the_box_becomes_a_name_before_it_goes(self):
        """The student must never receive "Student-1"."""
        inbox.send_reply(self.app, 77, "Student-1, send me what you have.",
                         confirm_token="t")
        _cid, body = self.app.client.sent[0]
        self.assertEqual(body, "Jordan Alvarez, send me what you have.")

    def test_an_empty_box_sends_nothing(self):
        with self.assertRaises(ValueError):
            inbox.send_reply(self.app, 77, "   ", confirm_token="t")
        self.assertEqual(self.app.client.sent, [])

    def test_it_is_written_into_the_record_with_the_student_named(self):
        from courseforge import audit
        audit.forget_actor()
        audit.set_actor_source(lambda: {}, {})
        inbox.send_reply(self.app, 77, "ok", confirm_token="t")
        rows = audit.read(self.app.course_dir("734975"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["students"][0]["name"], "Jordan Alvarez")
        self.assertIn("Replied in Canvas", rows[0]["sentence"])

    def test_there_is_no_route_that_sends_more_than_one(self):
        """An unattended or batched send is the one thing this must not grow
        by accident, so its absence is asserted rather than assumed."""
        from courseforge.inboxarea import routes as r
        from courseforge.routing import ROUTER
        paths = [rt.pattern for rt in ROUTER.routes if "/api/inbox" in rt.pattern]
        self.assertIn("/api/inbox/{cid}/reply", paths)
        for bad in ("send-all", "auto", "batch", "rules"):
            self.assertFalse([p for p in paths if bad in p], bad)
        self.assertFalse(hasattr(inbox, "send_all"))
        self.assertTrue(hasattr(r, "reply"))


class FilesStayOnTheThread(Base):
    """A picture in the message used to vanish when the HTML was flattened.
    The screen gets the file through this app. The model hears the name only."""

    SHOT = {
        "id": 55, "display_name": "shot.png", "content-type": "image/png",
        "url": "https://canvas.example.edu/files/55/download", "size": 12,
    }

    def _row(self):
        row = json.loads(json.dumps(THREAD))
        row["messages"][1]["body"] = (
            'See <img src="https://canvas.example.edu/files/55/preview" alt="shot"> '
            'and <img src="javascript:alert(1)" alt="no">')
        row["messages"][1]["attachments"] = [self.SHOT]
        return row

    def test_the_inline_copy_of_an_attachment_is_not_a_second_file(self):
        assets = inbox.message_assets(self._row()["messages"][1], "https://canvas.example.edu")
        self.assertEqual([a["key"] for a in assets], ["a-55"])
        self.assertEqual(assets[0]["kind"], "image")
        self.assertNotIn("url", inbox.public_asset(assets[0]))

    def test_a_script_address_is_dropped(self):
        msg = {"body": '<img src="javascript:alert(1)" alt="x">', "attachments": []}
        self.assertEqual(inbox.message_assets(msg, "https://canvas.example.edu"), [])

    def test_a_relative_picture_is_joined_to_the_canvas_host(self):
        msg = {"body": '<img src="/files/9/preview" alt="diagram">'}
        assets = inbox.message_assets(msg, "https://canvas.example.edu")
        self.assertEqual(assets[0]["key"], "a-9")
        self.assertEqual(assets[0]["kind"], "image")
        self.assertTrue(assets[0]["url"].startswith("https://canvas.example.edu/files/9/"))

    def test_the_screen_lists_the_file_and_the_model_does_not_get_the_address(self):
        row = self._row()
        self.app.cfg.base_url = "https://canvas.example.edu"
        self.app.client.conversation = lambda cid, mark_read=False: row
        seen = {}

        def fake_run(prompt, **kw):
            seen["prompt"] = prompt
            return type("R", (), {"text": '{"asking":"x","reply":"ok"}'})()

        screen = inbox.thread(self.app, 77)
        files = [m for m in screen["transcript"] if m["from"] != "you"][0]["files"]
        self.assertEqual(files[0]["name"], "shot.png")
        self.assertNotIn("url", files[0])
        with mock.patch.object(inbox.llm, "run", fake_run):
            inbox.read_thread(self.app, 77)
        self.assertIn("shot.png", seen["prompt"])
        self.assertNotIn("canvas.example.edu", seen["prompt"])
        self.assertNotIn("/files/55", seen["prompt"])

    def test_only_a_file_on_this_thread_can_be_fetched(self):
        row = self._row()
        self.app.cfg.base_url = "https://canvas.example.edu"
        self.app.client.conversation = lambda cid, mark_read=False: row
        fetched = {}

        def download(url, dest):
            fetched["url"] = url
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"PNG")
            return dest

        self.app.client.download = download
        path, mime, _name, as_download = inbox.fetch_file(self.app, 77, "a-55")
        self.assertEqual(fetched["url"], self.SHOT["url"])
        self.assertEqual(mime, "image/png")
        self.assertFalse(as_download)
        self.assertEqual(path.read_bytes(), b"PNG")
        with self.assertRaises(ValueError):
            inbox.fetch_file(self.app, 77, "a-999")
        with self.assertRaises(ValueError):
            inbox.fetch_file(self.app, 77, "https://evil.example/x")

    def test_the_page_draws_an_image_from_this_app(self):
        src = (Path(__file__).resolve().parent.parent / "courseforge" / "web" / "js" / "inbox.js"
               ).read_text(encoding="utf-8")
        self.assertIn("function fileHtml", src)
        self.assertIn("/file/", src)
        self.assertIn('class="ibImg"', src)
        self.assertIn("not the words that get sent", src)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class AttachClient(FakeClient):
    """Canvas as an attachment sees it: the file goes into your own files
    first, and add_message answers with the new message and what is on it."""

    def __init__(self, keep=None):
        super().__init__()
        self.uploads = []
        self.keep = keep            # how many attached ids Canvas keeps; None is all

    def upload_user_file(self, name, payload, folder="canvas-grader",
                         content_type="application/octet-stream", on_duplicate="overwrite"):
        self.uploads.append({"name": name, "bytes": payload, "folder": folder,
                             "type": content_type, "on_duplicate": on_duplicate})
        return {"id": 900 + len(self.uploads), "display_name": name}

    def reply_to_conversation(self, cid, body, recipients=None, attachment_ids=None):
        ids = list(attachment_ids or [])
        self.sent.append((str(cid), body, ids))
        kept = ids if self.keep is None else ids[:self.keep]
        return {"id": cid, "messages": [{"id": 3, "author_id": 9, "body": body,
                                         "attachments": [{"id": int(a)} for a in kept]}]}


class AttachingFiles(Base):
    """A picture or a file on a reply. It waits on this computer until the
    reply is sent; the confirmation names it and is bound to its bytes; and
    nothing about it reaches Canvas on the first pass."""

    def setUp(self):
        super().setUp()
        self.app.client = AttachClient()

    def stage(self, raw=PNG, name="image.png", mime="image/png", cid=77):
        return inbox.stage_file(self.app, cid, name, mime, b64(raw))

    def kept(self, chip, suffix=""):
        return self.tmp / inbox.OUTBOX / (chip["id"] + suffix)

    def test_attaching_keeps_the_file_here_and_sends_nothing(self):
        chip = self.stage()
        self.assertEqual((chip["name"], chip["kind"], chip["size"]),
                         ("image.png", "image", len(PNG)))
        self.assertEqual(set(chip), {"id", "name", "mime", "size", "kind"})
        self.assertEqual(self.kept(chip).read_bytes(), PNG)
        self.assertEqual(self.app.client.uploads, [])
        self.assertEqual(self.app.client.sent, [])

    def test_nothing_reaches_canvas_on_the_first_pass(self):
        chip = self.stage()
        with self.assertRaises(PermissionError):
            inbox.send_reply(self.app, 77, "See the picture.", attachments=[chip["id"]])
        self.assertEqual(self.app.client.uploads, [], "it uploaded before the second click")
        self.assertEqual(self.app.client.sent, [])

    def test_the_confirmation_names_the_file_and_is_bound_to_its_bytes(self):
        chip = self.stage()
        with self.assertRaises(PermissionError):
            inbox.send_reply(self.app, 77, "See the picture.", attachments=[chip["id"]])
        gated = self.app.gated[0]
        self.assertIn("with 1 file attached: image.png", gated["sentence"])
        self.assertEqual(gated["payload"]["files"],
                         [{"name": "image.png", "size": len(PNG),
                           "sha256": hashlib.sha256(PNG).hexdigest()}])

    def test_a_plain_reply_is_confirmed_exactly_as_before(self):
        """No files, no new key: a plain reply keeps the fingerprint it had."""
        with self.assertRaises(PermissionError):
            inbox.send_reply(self.app, 77, "ok")
        self.assertEqual(set(self.app.gated[0]["payload"]), {"conversation_id", "body"})

    def test_with_the_token_the_files_go_up_first_and_then_the_message(self):
        a = self.stage()
        b = self.stage(raw=b"%PDF-1.4 notes", name="notes.pdf", mime="application/pdf")
        out = inbox.send_reply(self.app, 77, "Both attached.", confirm_token="t",
                               attachments=[a["id"], b["id"]])
        ups = self.app.client.uploads
        self.assertEqual([u["name"] for u in ups], ["image.png", "notes.pdf"])
        self.assertEqual({u["folder"] for u in ups}, {inbox.ATTACH_FOLDER})
        self.assertEqual({u["on_duplicate"] for u in ups}, {"rename"})
        self.assertEqual(ups[0]["bytes"], PNG)
        self.assertEqual(ups[1]["type"], "application/pdf")
        self.assertEqual(self.app.client.sent, [("77", "Both attached.", ["901", "902"])])
        self.assertEqual((out["attached"], out["files"]), (2, ["image.png", "notes.pdf"]))
        self.assertNotIn("warning", out)
        self.assertFalse(self.kept(a).exists(), "a sent file stayed on this computer")
        self.assertFalse(self.kept(a, ".json").exists())

    def test_a_failed_upload_sends_nothing_and_says_why(self):
        from courseforge.canvas import CanvasError

        def refuse(*_a, **_k):
            raise CanvasError(400, "https://canvas.example.edu/api/v1/users/self/files",
                              '{"message": "file size exceeds quota"}')

        self.app.client.upload_user_file = refuse
        chip = self.stage()
        with self.assertRaises(inbox.Refused) as caught:
            inbox.send_reply(self.app, 77, "See the picture.", confirm_token="t",
                             attachments=[chip["id"]])
        said = str(caught.exception)
        self.assertIn("file size exceeds quota", said)
        self.assertIn("Nothing was sent", said)
        self.assertNotIn("/api/v1", said)
        self.assertEqual(self.app.client.sent, [])
        self.assertTrue(self.kept(chip).is_file(), "kept so Send can try again")

    def test_a_file_canvas_dropped_is_reported_not_called_a_success(self):
        self.app.client.keep = 0
        chip = self.stage()
        out = inbox.send_reply(self.app, 77, "See the picture.", confirm_token="t",
                               attachments=[chip["id"]])
        self.assertEqual(out["attached"], 0)
        self.assertIn("attached only 0 of the 1", out["warning"])

    def test_when_canvas_does_not_say_the_thread_is_read_back(self):
        client = self.app.client
        plain = AttachClient.reply_to_conversation
        client.reply_to_conversation = lambda *a, **k: {
            **plain(client, *a, **k), "messages": []}
        chip = self.stage()
        out = inbox.send_reply(self.app, 77, "x", confirm_token="t", attachments=[chip["id"]])
        # The fake thread's newest message by me carries no files.
        self.assertEqual(out["attached"], 0)
        self.assertIn("warning", out)

    def test_a_file_from_another_thread_cannot_ride_on_this_reply(self):
        chip = self.stage(cid=88)
        with self.assertRaises(inbox.Refused):
            inbox.send_reply(self.app, 77, "x", confirm_token="t", attachments=[chip["id"]])
        self.assertEqual(self.app.client.uploads, [])

    def test_an_id_that_is_not_one_this_computer_kept_is_refused(self):
        for bad in ("../../config.json", "a" * 31, "z" * 32, "0" * 32):
            with self.assertRaises(inbox.Refused, msg=bad):
                inbox.staged(self.app, 77, [bad])

    def test_bytes_changed_after_the_yes_are_not_sent(self):
        chip = self.stage()
        self.kept(chip).write_bytes(b"something else entirely")
        with self.assertRaises(inbox.Refused) as caught:
            inbox.send_reply(self.app, 77, "x", confirm_token="t", attachments=[chip["id"]])
        self.assertIn("changed after it was attached", str(caught.exception))
        self.assertEqual(self.app.client.uploads, [])

    def test_empty_too_big_and_garbled_are_each_a_sentence(self):
        with self.assertRaises(inbox.Refused):
            self.stage(raw=b"")
        with mock.patch.object(inbox, "MAX_ATTACH_BYTES", 16):
            with self.assertRaises(inbox.Refused) as caught:
                self.stage(raw=b"x" * 17)
        self.assertIn("can be up to", str(caught.exception))
        with self.assertRaises(inbox.Refused):
            inbox.stage_file(self.app, 77, "a.png", "image/png", "not base64!!")
        with self.assertRaises(inbox.Refused):
            inbox.stage_file(self.app, "../77", "a.png", "image/png", b64(PNG))

    def test_too_many_on_one_reply_is_refused(self):
        ids = [self.stage()["id"] for _ in range(inbox.MAX_ATTACH + 1)]
        with self.assertRaises(inbox.Refused):
            inbox.staged(self.app, 77, ids)

    def test_a_name_is_made_safe_for_canvas_and_for_windows(self):
        name = self.stage(name='..\\..\\evil"name<1>.png')["name"]
        self.assertEqual(name, "evil name 1.png")
        self.assertEqual(self.stage(name="", mime="image/png")["name"], "attachment.png")

    def test_a_type_the_browser_left_out_is_worked_out_from_the_name(self):
        chip = self.stage(raw=b"%PDF-1.4", name="notes.pdf", mime="")
        self.assertEqual(chip["mime"], "application/pdf")
        self.assertEqual(chip["kind"], "pdf")

    def test_a_file_staged_and_never_sent_is_swept(self):
        old = self.stage()
        stale = time.time() - inbox.OUTBOX_KEEP_S - 60
        for path in (self.tmp / inbox.OUTBOX).iterdir():
            os.utime(path, (stale, stale))
        fresh = self.stage()
        self.assertFalse(self.kept(old).exists())
        self.assertFalse(self.kept(old, ".json").exists())
        self.assertTrue(self.kept(fresh).exists())

    def test_it_is_written_into_the_record_with_the_files(self):
        from courseforge import audit
        audit.forget_actor()
        audit.set_actor_source(lambda: {}, {})
        chip = self.stage()
        inbox.send_reply(self.app, 77, "ok", confirm_token="t", attachments=[chip["id"]])
        row = audit.read(self.app.course_dir("734975"))[0]
        self.assertIn("with 1 file attached", row["sentence"])
        self.assertEqual(row["detail"]["files"][0]["name"], "image.png")
        self.assertEqual(row["detail"]["files"][0]["canvas_file_id"], "901")
        self.assertEqual(row["detail"]["files"][0]["sha256"], hashlib.sha256(PNG).hexdigest())


class TheRoutesCarryAttachments(Base):
    def setUp(self):
        super().setUp()
        self.app.client = AttachClient()

    def req(self, path, body, cid="77"):
        from courseforge.routing import Request
        return Request(app=self.app, handler=None, method="POST", path=path,
                       params={"cid": cid}, body=body)

    def test_the_attach_route_is_registered(self):
        from courseforge.inboxarea import routes as _r  # noqa: F401
        from courseforge.routing import ROUTER
        self.assertIn(("POST", "/api/inbox/{cid}/attach"),
                      {(rt.method, rt.pattern) for rt in ROUTER.routes})

    def test_attaching_answers_with_the_chip_and_touches_no_canvas(self):
        from courseforge.inboxarea import routes as r
        out = r.attach(self.req("/api/inbox/77/attach",
                                {"name": "shot.png", "type": "image/png", "data": b64(PNG)}))
        self.assertEqual((out["name"], out["kind"]), ("shot.png", "image"))
        self.assertEqual(self.app.client.uploads, [])

    def test_a_missing_or_garbled_file_is_a_400_with_a_sentence(self):
        from courseforge.inboxarea import routes as r
        from courseforge.routing import HTTPError
        for body in ({}, {"name": "a.png", "data": "!!!"}):
            with self.assertRaises(HTTPError) as caught:
                r.attach(self.req("/api/inbox/77/attach", body))
            self.assertEqual(caught.exception.status, 400)

    def test_a_file_that_has_gone_is_said_before_the_job_starts(self):
        from courseforge.inboxarea import routes as r
        from courseforge.routing import HTTPError
        with self.assertRaises(HTTPError) as caught:
            r.reply(self.req("/api/inbox/77/reply", {"body": "x", "attachments": ["0" * 32]}))
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("no longer on this computer", caught.exception.message)

    def test_the_reply_job_hands_the_files_to_the_send(self):
        from courseforge.inboxarea import routes as r
        chip = inbox.stage_file(self.app, 77, "shot.png", "image/png", b64(PNG))
        job = r.reply(self.req("/api/inbox/77/reply",
                               {"body": "See it.", "attachments": [chip["id"]], "confirm": "t"}))
        out = job.fn(lambda *a, **k: None)
        self.assertEqual(out["attached"], 1)
        self.assertEqual(self.app.client.sent[0][2], ["901"])


class TheClientCarriesThem(unittest.TestCase):
    def client(self):
        from courseforge.canvas import CanvasClient
        made = CanvasClient("https://canvas.example.edu", "t", scope="grading")
        made.seen = []
        made._form = lambda method, path, fields: made.seen.append((path, list(fields))) or {}
        return made

    def test_add_message_names_each_attachment(self):
        c = self.client()
        c.reply_to_conversation(77, "hi", attachment_ids=["901", "902"])
        path, fields = c.seen[0]
        self.assertEqual(path, "/conversations/77/add_message")
        self.assertEqual([v for k, v in fields if k == "attachment_ids[]"], ["901", "902"])

    def test_a_plain_reply_sends_no_attachment_field(self):
        c = self.client()
        c.reply_to_conversation(77, "hi")
        self.assertEqual(c.seen[0][1], [("body", "hi")])

    def test_an_upload_can_keep_both_files_rather_than_replace(self):
        c = self.client()
        # No upload slot comes back, so it stops before any network.
        with self.assertRaises(RuntimeError):
            c.upload_user_file("a.png", b"x", folder=inbox.ATTACH_FOLDER,
                               content_type="image/png", on_duplicate="rename")
        fields = dict(c.seen[0][1])
        self.assertEqual(fields["on_duplicate"], "rename")
        self.assertEqual(fields["parent_folder_path"], "conversation attachments")
        with self.assertRaises(ValueError):
            c.upload_user_file("a.png", b"x", on_duplicate="delete")

    def test_every_other_upload_still_overwrites(self):
        c = self.client()
        with self.assertRaises(RuntimeError):
            c.upload_user_file("state.json", b"{}")
        self.assertEqual(dict(c.seen[0][1])["on_duplicate"], "overwrite")


class ThePageAttaches(unittest.TestCase):
    WEB = Path(__file__).resolve().parents[1] / "courseforge" / "web"

    def src(self):
        return (self.WEB / "js" / "inbox.js").read_text(encoding="utf-8")

    def test_the_files_travel_inside_the_body_with_the_token(self):
        self.assertIn("{ body: { body, attachments, confirm: token } }", self.src())

    def test_text_on_the_clipboard_pastes_as_text(self):
        """Excel and Word put a picture of the copy beside its text."""
        self.assertIn("getData('text/plain')", self.src())

    def test_the_picture_beside_copied_text_is_offered_not_dropped(self):
        s = self.src()
        self.assertIn("offerPictures(t, got.filter(", s)
        self.assertIn('id="ibOffer"', s)

    def test_a_screenshot_pasted_while_reading_opens_the_reply(self):
        """The reply box may not be open yet when a screenshot is pasted."""
        s = self.src()
        self.assertIn("document.addEventListener('paste', onPagePaste)", s)
        self.assertIn("onLeave(() => document.removeEventListener('paste', onPagePaste))", s)

    def test_its_limits_are_the_servers(self):
        s = self.src()
        self.assertIn("const MAX_FILE = %d * 1024 * 1024;"
                      % (inbox.MAX_ATTACH_BYTES // (1024 * 1024)), s)
        self.assertIn("const MAX_FILES = %d;" % inbox.MAX_ATTACH, s)

    def test_an_svg_is_never_drawn_as_a_thumbnail(self):
        line = next(l for l in self.src().splitlines() if "const RASTER" in l)
        self.assertNotIn("svg", line.lower())

    def test_the_styles_it_uses_are_defined(self):
        css = (self.WEB / "css" / "inbox.css").read_text(encoding="utf-8")
        for name in ("ibAttach", "ibChip", "ibThumb", "ibThumbFile", "ibChipText",
                     "ibChipName", "ibChipSize", "ibChipX", "ibAttachHint",
                     "ibDraftBox.dropping", "ibOffer"):
            self.assertIn("." + name, css, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
