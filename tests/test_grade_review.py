"""Regressions from the Grade area review. No network: Canvas is a fake.

Each class pins one thing that was wrong:

  * a slider moved under a curve left the curved total where it was, so the
    roster and a push disagreed about the same student;
  * removing a handoff deleted files from Canvas with no second click;
  * the handoff bundle's folder check was a string prefix, not a path test;
  * grade.js called the browser's confirm(), and declared one function twice
    so the schedule footer read "2954 weeks ago".
"""
import io
import json
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from courseforge import confirm, curve, grader, handoff
from courseforge.pseudonym import Pseudonymizer
from courseforge.server import App
from courseforge.store import Store

WEB = Path(__file__).resolve().parent.parent / "courseforge" / "web"


def bare_app(root: Path, client=None, writes: bool = True) -> App:
    """An App with only the parts these methods touch. The real constructor
    starts an audit thread and installs every area, none of which is wanted
    in a unit test."""
    app = App.__new__(App)
    app.store = Store(root)
    app.cfg = SimpleNamespace(allow_canvas_writes=writes, handoff_folder="canvas-grader")
    app.confirm = confirm.ConfirmGate()
    app._client = client
    return app


RUBRIC = [{"id": "c1", "label": "Form", "points": 10.0, "ratings": []},
          {"id": "c2", "label": "Finish", "points": 10.0, "ratings": []}]


class EditStudentKeepsTheCurvedTotalInStep(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = bare_app(self.tmp)
        self.app.store.save_draft("1", "2", {
            "rubric": RUBRIC, "points_possible": 20,
            "students": {
                "7": {"user_id": "7", "source": "claude", "scores": {"c1": 5, "c2": 5},
                      "total": 10, "final_total": 12,
                      "curve": {"flat": 2, "by_criterion": {}, "steps": [{"kind": "flat"}]}},
                "8": {"user_id": "8", "source": "claude", "scores": {"c1": 5, "c2": 5},
                      "total": 10},
            }})

    def test_a_slider_moves_the_curved_total_too(self):
        entry = self.app.edit_student("1", "2", "7", {"scores": {"c1": 8, "c2": 5},
                                                     "comment": "closer"})
        self.assertEqual(entry["total"], 13)
        self.assertEqual(entry["final_total"], 15, "earned 13 plus the flat +2")
        self.assertEqual(entry["source"], "human")
        self.assertFalse(entry["total_only"])
        saved = self.app.store.draft("1", "2")["students"]["7"]
        self.assertEqual(saved["final_total"], 15)

    def test_a_curved_total_is_capped_at_points_possible(self):
        entry = self.app.edit_student("1", "2", "7", {"scores": {"c1": 10, "c2": 10}})
        self.assertEqual(entry["total"], 20)
        self.assertEqual(entry["final_total"], 20, "a curve cannot invent 22 of 20")

    def test_no_curve_means_no_final_total_is_invented(self):
        entry = self.app.edit_student("1", "2", "8", {"scores": {"c1": 9, "c2": 5}})
        self.assertEqual(entry["total"], 14)
        self.assertNotIn("final_total", entry)

    def test_a_comment_alone_leaves_the_scores_alone(self):
        entry = self.app.edit_student("1", "2", "7", {"comment": "see me"})
        self.assertEqual(entry["scores"], {"c1": 5, "c2": 5})
        self.assertEqual(entry["total"], 10)
        self.assertEqual(entry["final_total"], 12)
        self.assertEqual(entry["comment"], "see me")

    def test_ticking_a_comment_does_not_count_as_a_regrade(self):
        entry = self.app.edit_student("1", "2", "7", {"post_comment": True})
        self.assertTrue(entry["post_comment"])
        self.assertEqual(entry["source"], "claude")
        self.assertEqual(entry["total"], 10)


class ATypedCanvasTotalKeepsItsCurve(unittest.TestCase):
    def test_a_worked_out_grade_is_a_whole_number(self):
        earned = {"total": 10, "scores": {"c1": 10}, "curve": {"flat": 3.6}}
        self.assertEqual(curve.final_total(earned, [{"id": "c1", "points": 20}], 20), 14)
        half = {"total": 10.5, "scores": {"c1": 10.5}}
        self.assertEqual(curve.final_total(half, [{"id": "c1", "points": 20}], 20), 11)
        typed = {"total": 85.5, "total_only": True, "scores": {}}
        self.assertEqual(curve.final_total(typed, [{"id": "c1", "points": 100}], 100), 85.5)

    def test_final_total_does_not_sum_empty_rubric_cells(self):
        entry = {"total": 85, "total_only": True, "scores": {},
                 "curve": {"flat": 5, "by_criterion": {}}}
        rubric = [{"id": "c1", "label": "Form", "points": 100}]
        self.assertEqual(curve.earned_total(entry, rubric), 85)
        self.assertEqual(curve.final_total(entry, rubric, 100), 90)


class PushSendsOnlyTickedComments(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = bare_app(self.tmp)
        self.app.store.save_draft("1", "2", {
            "rubric": RUBRIC, "points_possible": 20,
            "students": {
                "7": {"user_id": "7", "source": "claude",
                      "scores": {"c1": 8, "c2": 8}, "total": 16,
                      "comment": "Tighten the silhouette.", "post_comment": True},
                "8": {"user_id": "8", "source": "claude",
                      "scores": {"c1": 5, "c2": 5}, "total": 10,
                      "comment": "Add a backrest."},
            }})

    def plan(self, mode):
        return self.app.push("1", "2", dry_run=True, comment_mode=mode)

    def test_selected_writes_only_the_ticked_comment(self):
        out = self.plan("selected")
        by = {p["user_id"]: p for p in out["would_post"]}
        self.assertEqual(by["7"]["comment"], "Tighten the silhouette.")
        self.assertEqual(by["8"]["comment"], "")
        self.assertEqual(out["comment_mode"], "selected")
        self.assertEqual(out["comments_n"], 1)

    def test_none_writes_no_comments(self):
        out = self.plan("none")
        self.assertTrue(all(not p["comment"] for p in out["would_post"]))
        self.assertEqual(out["comments_n"], 0)

    def test_all_writes_every_comment(self):
        out = self.plan("all")
        by = {p["user_id"]: p for p in out["would_post"]}
        self.assertEqual(by["7"]["comment"], "Tighten the silhouette.")
        self.assertEqual(by["8"]["comment"], "Add a backrest.")
        self.assertEqual(out["comments_n"], 2)

    def test_a_push_is_visible_to_students(self):
        out = self.plan("none")
        self.assertTrue(out["show"])
        self.assertNotIn("hidden", out["landing"].lower())
        self.assertIn("visible", out["landing"].lower())


class NamesDoNotLeaveOnAGrade(unittest.TestCase):
    def test_build_prompt_does_not_contain_the_roster_name(self):
        students = [{"id": 7, "name": "Jordan Alvarez"},
                    {"id": 8, "name": "Dana Wu"}]
        pseud = Pseudonymizer(students, enabled=True)
        entry = {
            "user_id": "7", "name": "Jordan Alvarez", "pseudonym": "S-001",
            "status": "submitted",
            "body_text": "Signed, Jordan Alvarez.",
            "text": "Signed, Jordan Alvarez.",
            "filenames": ["Jordan Alvarez essay.docx"],
            "discussion": {
                "post": {"text": "hello from Jordan Alvarez", "words": 4},
                "replies": [{"to": "Dana Wu", "to_id": "8",
                             "to_excerpt": "Dana Wu said wait",
                             "text": "thanks Dana Wu", "words": 3}],
            },
        }
        prompt = grader.build_prompt(
            {"name": "Essay", "points_possible": 10, "description": "<p>x</p>"},
            [{"id": "c1", "label": "Form", "points": 10, "detail": "", "ratings": []}],
            entry, "", "S-001", pseud=pseud)
        for secret in ("Jordan", "Alvarez", "Dana Wu"):
            self.assertNotIn(secret, prompt, secret)
        self.assertIn("S-001", prompt)
        self.assertIn("S-002", prompt)


class FakeFiles:
    """The two Canvas calls handoff_disable makes, and a record of the deletes."""

    def __init__(self, files):
        self.files = files
        self.deleted = []

    def user_folder_files(self, folder):
        return list(self.files)

    def delete_file(self, file_id):
        self.deleted.append(str(file_id))
        return {}


class RemovingAHandoffAsksFirst(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.client = FakeFiles([
            {"id": 11, "display_name": handoff.draft_name("1", "2")},
            {"id": 12, "display_name": handoff.blend_name("1", "2")},
            {"id": 13, "display_name": handoff.draft_name("1", "3")},   # another assignment
        ])
        self.app = bare_app(self.tmp, self.client)
        self.adir = self.app.store.assignment_dir("1", "2")
        handoff.save_state(self.adir, rev=3)

    def test_the_first_call_is_refused_and_deletes_nothing(self):
        with self.assertRaises(confirm.ConfirmRequired) as caught:
            self.app.handoff_disable("1", "2", remove=True)
        self.assertEqual(self.client.deleted, [])
        self.assertIn("2 handoff file(s)", caught.exception.summary)
        self.assertTrue(handoff.enabled(self.adir), "still carried until it goes through")

    def test_the_second_call_with_the_token_deletes_only_this_assignment(self):
        try:
            self.app.handoff_disable("1", "2", remove=True)
        except confirm.ConfirmRequired as asked:
            token = asked.token
        out = self.app.handoff_disable("1", "2", remove=True, confirm_token=token)
        self.assertEqual(out, {"ok": True, "removed": 2})
        self.assertEqual(sorted(self.client.deleted), ["11", "12"])
        self.assertFalse(handoff.enabled(self.adir))

    def test_stopping_without_removing_never_touches_canvas(self):
        out = self.app.handoff_disable("1", "2", remove=False)
        self.assertEqual(out, {"ok": True, "removed": 0})
        self.assertEqual(self.client.deleted, [])
        self.assertFalse(handoff.enabled(self.adir))

    def test_nothing_in_canvas_means_nothing_to_confirm(self):
        self.client.files = []
        out = self.app.handoff_disable("1", "2", remove=True)
        self.assertEqual(out["removed"], 0)

    def test_the_hard_lock_wins_over_the_confirmation(self):
        app = bare_app(self.tmp, self.client, writes=False)
        with self.assertRaises(PermissionError):
            app.handoff_disable("1", "2", remove=True)
        self.assertEqual(self.client.deleted, [])


class TheBlendBundleStaysInsideItsFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.adir = self.tmp / "data" / "1" / "2"
        self.adir.mkdir(parents=True)

    def bundle(self, names):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name in names:
                zf.writestr(name, "{}")
        return buf.getvalue()

    def test_a_sibling_folder_sharing_the_prefix_is_refused(self):
        # "data/1/2" is a string prefix of "data/1/23", which is what the old
        # startswith() check let through.
        raw = self.bundle(["blend/9/model/stats.json",
                           "../23/blend/9/model/stats.json",
                           "../../../elsewhere/blend/9/model/contact.png"])
        written = handoff.apply_blend_zip(self.adir, raw)
        self.assertEqual(written, 1)
        self.assertTrue((self.adir / "blend" / "9" / "model" / "stats.json").is_file())
        self.assertFalse((self.tmp / "data" / "1" / "23").exists())
        self.assertFalse((self.tmp / "elsewhere").exists())

    def test_only_the_named_parts_are_unpacked(self):
        raw = self.bundle(["blend/9/model/stats.json", "blend/9/model/evil.py"])
        self.assertEqual(handoff.apply_blend_zip(self.adir, raw), 1)
        self.assertFalse((self.adir / "blend" / "9" / "model" / "evil.py").exists())


class GradeJsKeepsToTheContract(unittest.TestCase):
    """There is no JavaScript runner here; these read the source."""

    @classmethod
    def setUpClass(cls):
        cls.src = (WEB / "js" / "grade.js").read_text(encoding="utf-8")

    def test_no_browser_confirm_or_alert(self):
        """The front-end contract: askConfirm and banners, never confirm()."""
        calls = re.findall(r"(?<![\w.$])(confirm|alert)\(", self.src)
        self.assertEqual(calls, [])

    def test_closing_the_roster_does_not_leave_it_in_the_address(self):
        """Close used to empty the dialog and leave #/roster, so a refresh
        opened it again and the dim layer kept eating clicks."""
        self.assertIn("if (top === 'roster') location.hash = '#/';", self.src)
        self.assertIn("let gone = false;", self.src)
        self.assertIn('type="button" id="roClose"', self.src)

    def test_no_location_reload(self):
        self.assertNotIn("location.reload(", self.src)

    def test_each_top_level_function_is_declared_once(self):
        """Two declarations of one name in a plain script: the later one wins
        everywhere, and the first caller gets the wrong function without any
        error. That is how ago(seconds) came to be handed to ago(iso)."""
        names = re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
                           self.src, flags=re.MULTILINE)
        dupes = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(dupes, [])

    def test_the_schedule_footer_is_fed_seconds_by_name(self):
        self.assertIn("agoSeconds(sc.age_s)", self.src)

    def test_a_flagged_roster_row_has_a_review_menu(self):
        self.assertIn("function openRosterMenu(", self.src)
        self.assertIn("function onRosterContext(", self.src)
        self.assertIn("function runSelectionAction(", self.src)
        self.assertIn("function selectionItems(", self.src)
        self.assertIn("Mark as reviewed", self.src)
        self.assertIn("Push grade to Canvas", self.src)
        self.assertIn("doMarkReviewed(reviewed, only)", self.src)
        self.assertIn("openPush(st.ids)", self.src)
        # Bulk bar and context menu share one action list, so a new verb cannot
        # land on shift-click and be missing from right-click (or the reverse).
        self.assertIn("selectionItems(st)", self.src)
        self.assertIn("js/grade.js?v=pdf-1",
                      (WEB / "index.html").read_text(encoding="utf-8"))
        self.assertIn('step="1"', self.src)
        self.assertNotIn("data-tiers", self.src)
        self.assertIn('id="rosterSort"', (WEB / "index.html").read_text(encoding="utf-8"))
        self.assertIn("function rosterOrder(", self.src)
        self.assertIn("grade-desc", self.src)

    def test_a_schedule_assignment_name_is_a_real_link(self):
        """Middle-click uses the href, not the click handler. href="#" is the
        home page in this router, which is how a new tab used to open empty."""
        row = self.src[self.src.index("function schedRow("):]
        row = row[:row.index("\n}\n")]
        self.assertIn('class="itemName"', row)
        self.assertIn('href="#/c/${esc(it.course_id)}/a/${esc(it.assignment_id)}"', row)
        self.assertNotIn('href="#"', row)
        body = self.src[self.src.index("$('#schedBody').querySelectorAll('.itemName')"):]
        body = body[:body.index("});\n")]
        self.assertIn("ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button",
                      body)

    def test_remind_missing_is_wired_in_the_header(self):
        """The Send group paints Remind missing when the deadline has passed.
        Without an onclick it sits there dead; the per-student button is a
        different id and is not this check."""
        self.assertIn('id="btnRemind"', self.src)
        self.assertIn("$('#btnRemind')", self.src)
        self.assertIn(
            "rm.onclick = () => openRemind(S.ids.courseId, S.ids.assignmentId)",
            self.src)

    def test_the_student_panel_has_a_comment_tick(self):
        self.assertIn('id="postCmt"', self.src)
        self.assertIn("Only comments I ticked on the student panel", self.src)
        self.assertIn("comments: $('#pushComments').value", self.src)

    def test_the_schedule_tiles_follow_the_course_chips(self):
        """The five numbers used to come from the server for the whole term,
        so turning courses off left the tiles lying."""
        self.assertIn("function scheduleStats(", self.src)
        self.assertIn("function schedPicked(", self.src)
        self.assertIn("Still due this term", self.src)
        self.assertIn("Waiting to grade", self.src)
        self.assertNotIn("Master Schedule", self.src)

    def test_the_announcement_draft_has_a_canvas_page_preview(self):
        self.assertIn("function wrapAnnouncement(", self.src)
        self.assertIn("function paintAnnouncePreview(", self.src)
        self.assertIn("How it will read", self.src)
        self.assertIn("paperFrame canvasPage", self.src)
        self.assertIn("id=\"anPreview\"", self.src)

    def test_a_word_file_is_drawn_instead_of_flattened(self):
        """The work pane used to dump docx_to_text into one prose block, which
        threw away headings, tables, pictures and charts."""
        body = self.src[self.src.index("function renderWork("):
                        self.src.index("/* ----------------------------------------------------------------- events */")]
        self.assertIn("function docxCard(", self.src)
        self.assertIn("docxCard(p, reused)", body)
        self.assertIn("DocxView.take", body)
        self.assertLess(body.index("DocxView.take"), body.index("host.innerHTML"))
        self.assertIn('class="docxHost"', self.src)
        self.assertIn("Text only", self.src)
        self.assertIn("wireDocx(host)", body)
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("js/docview.js?v=docx-1", page)
        self.assertIn("js/pdfview.js?v=pdf-1", page)
        self.assertIn("style.css?v=pdf-1", page)
        self.assertIn("function pdfCard(", self.src)
        self.assertIn("pdfCard(p, url, reused)", body)
        self.assertIn("PdfView.take", body)
        self.assertLess(body.index("PdfView.take"), body.index("host.innerHTML"))
        self.assertIn('class="pdfHost"', self.src)
        pdf = (WEB / "js" / "pdfview.js").read_text(encoding="utf-8")
        self.assertIn("enableScripting: false", pdf)
        self.assertNotIn("cdn", pdf.lower())
        self.assertNotIn("mozilla.github.io", pdf)
        viewer = (WEB / "js" / "docview.js").read_text(encoding="utf-8")
        self.assertIn("useGoogleFonts: false", viewer)
        self.assertNotIn("useGoogleFonts: true", viewer)
        self.assertIn("DocxScrollViewer", viewer)

    def test_the_work_pane_close_is_not_an_inline_handler(self):
        """Inline onclick is blocked by the page policy, so Close did nothing
        and the only working control was Hide work, back at the top."""
        self.assertNotIn('onclick="toggleWork()"', self.src)
        self.assertIn('id="btnWorkClose"', self.src)
        self.assertIn("closeWork.onclick = toggleWork", self.src)

    def test_regrade_covers_the_selection(self):
        """The student-panel button used to grade only the open student."""
        self.assertIn("doGrade(gradeIds)", self.src)
        self.assertIn("Re-grade ${gradeIds.length} students", self.src)
        self.assertNotIn("doGrade([String(s.user_id)])", self.src)

    def test_keep_the_proposal_covers_every_selected_conflict(self):
        """The buttons on one student used to settle only that student,
        even when a range was selected."""
        self.assertIn("function conflictChoiceIds(", self.src)
        detail = self.src[self.src.index("const cfC = $('#cfCanvas')"):
                          self.src.index("const cfM = $('#cfMine')") + 200]
        self.assertIn("resolveConflicts(choiceIds, 'canvas')", detail)
        self.assertIn("resolveConflicts(choiceIds, 'mine')", detail)
        self.assertNotIn("resolveConflicts([String(s.user_id)]", detail)
        self.assertIn("selected students who disagree with Canvas", self.src)

    def test_a_posted_total_is_not_still_to_grade(self):
        """The quiz stays 'pending review' after the gradebook post. The
        page was still saying the written answers were a proposal."""
        body = self.src[self.src.index("function writtenOpen("):
                        self.src.index("function writtenOpenCount(")]
        self.assertIn("gradeIsPosted(s) || (writtenScoresRecorded(s) && !reviewHold(s))", body)
        self.assertIn("This grade is in Canvas and students can see it.", self.src)

    def test_a_score_that_stays_an_f_still_shows_it_moved_up(self):
        """Leaving F, or gaining points and still being an F, is a move up.
        The old change column painted a smaller F count in red."""
        body = self.src[self.src.index("function curvePreviewHTML("):
                        self.src.index("function openPush(")]
        self.assertIn("failing ? moved < 0 : moved > 0", body)
        self.assertIn("up", body)
        self.assertIn("ltrHold", body)
        self.assertIn("no writing", body)
        self.assertIn("those points are 0", body)
        self.assertNotIn("The score went up", body)
        self.assertIn('class="ltrIco"', body)
        self.assertNotIn("↑", body)

    def test_the_resume_course_stays_on_the_list(self):
        """Carry on is a shortcut. It does not take the course off the list."""
        body = self.src[self.src.index("function paintHome("):
                        self.src.index("function courseCount(")]
        self.assertNotIn("list.filter", body)
        self.assertNotIn("Your other courses", self.src)
        self.assertNotIn("That is every course in this term.", self.src)
        self.assertIn("$('#pickerTitle').textContent = 'Your courses'", body)

    def test_an_ungraded_written_answer_is_not_called_in_step(self):
        """A posted automatic score is not a finished grade. A zero on a
        short answer that Canvas has not scored must not read as a zero,
        and the nickname box must not use a real student's nickname as
        its example."""
        self.assertIn("function writtenOpen(", self.src)
        self.assertIn("written answers are not in the posted score", self.src)
        self.assertIn("grade${n === 1 ? '' : 's'} live", self.src)
        self.assertIn("stamp.className = 'gradedStamp'", self.src)
        self.assertIn('class="pill good gradedMark"', self.src)
        self.assertNotIn("Push hidden", self.src)
        self.assertNotIn("Make grades live", self.src)
        self.assertIn("Not graded · worth ", self.src)
        self.assertIn("Proposed on this computer", self.src)
        self.assertNotIn('placeholder="Jack"', self.src)
        self.assertNotIn('id="gdNick"', self.src)
        detail = self.src[self.src.index("function renderDetail("):
                          self.src.index("function renderWork(")]
        self.assertNotIn("Nickname", detail)
        student = (WEB / "js" / "student.js").read_text(encoding="utf-8")
        self.assertNotIn('placeholder="Jack"', student)
        self.assertIn('placeholder="nickname"', student)
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("js/student.js?v=students-11", page)

    def test_the_announcement_dialog_tolerates_no_schedule(self):
        body = self.src[self.src.index("function openAnnounce("):]
        body = body[:body.index("\n}\n")]
        unguarded = re.findall(r"(?<!&& )S\.sched\.items", body)
        self.assertEqual(unguarded, [])
        self.assertIn("(S.sched && S.sched.items)", body)


class AnnouncePromptUsesTheCoursePolicy(unittest.TestCase):
    def test_a_proctored_test_without_policy_forbids_invented_steps(self):
        from courseforge import instruct
        prompt = instruct.announce_prompt({
            "name": "Test 1 - Weeks 1-3 (Proctored exam)",
            "kind_label": "TEST", "proctored": True, "exam": True,
        })
        self.assertIn("The assignment page has no description", prompt)
        self.assertIn("Do not name a vendor", prompt)
        self.assertNotIn("webcam is required", prompt.lower())

    def test_policy_text_is_quoted_in_the_prompt(self):
        from courseforge import instruct
        prompt = instruct.announce_prompt(
            {"name": "Test 1", "kind_label": "TEST", "proctored": True},
            policy_text="Bring a photo ID. SmarterProctoring needs a webcam.")
        self.assertIn("Bring a photo ID", prompt)
        self.assertIn("SmarterProctoring needs a webcam", prompt)
        self.assertIn("from the syllabus", prompt)

    def test_syllabus_passages_are_the_testing_section_not_the_opening(self):
        from courseforge import instruct
        padding = "Course description. " * 200
        body = padding + "SmarterProctoring requires a webcam and a photo ID. " + padding
        passage = instruct.extract_policy_passages(body)
        self.assertIn("SmarterProctoring requires a webcam", passage)
        self.assertNotIn("Course description. Course description.", passage[:80])

    def test_policy_relevant_matches_smarterproctoring_titles(self):
        from courseforge import instruct
        self.assertTrue(instruct.policy_relevant("SmarterProctoring"))
        self.assertTrue(instruct.policy_relevant("How to take a test"))
        self.assertTrue(instruct.needs_testing_policy(
            {"name": "Test 1 - Proctored exam", "kind_label": "TEST"}))
        self.assertFalse(instruct.policy_relevant("Week 3 Homework"))
        self.assertFalse(instruct.needs_testing_policy(
            {"name": "Homework 4", "kind_label": "ASSIGNMENT"}))


class AnnouncementWrapIsACanvasPage(unittest.TestCase):
    def test_plain_sentences_become_school_html(self):
        from courseforge import instruct
        html = instruct.wrap_announcement(
            "The quiz opens Wednesday at 8am.\n\nIt is due Friday at 11:59 PM.")
        self.assertIn("Announcement", html)
        self.assertIn("#E9A821", html)
        self.assertIn("The quiz opens Wednesday at 8am.", html)
        self.assertIn("<p", html)
        self.assertNotIn("<h2", html)

    def test_html_is_not_wrapped_twice(self):
        from courseforge import instruct
        once = instruct.wrap_announcement("Due Friday.")
        twice = instruct.wrap_announcement(once)
        self.assertEqual(once.count("Announcement"), 1)
        self.assertEqual(twice.count("Announcement"), 1)


if __name__ == "__main__":
    unittest.main()
