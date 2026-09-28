"""The Canvas Inbox: what goes to a model, and what it takes to send.

Two properties carry this feature. A student's message must reach Claude with
every name taken out -- theirs and anyone else's they mention -- and a reply
must be impossible to send without the instructor pressing the button on that
specific reply.
"""
import json
import shutil
import tempfile
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
        self.gated.append({"kind": kind, "sentence": sentence, "token": token})
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
