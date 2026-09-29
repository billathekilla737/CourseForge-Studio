"""The home list and the open course show the same four figures."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from courseforge import audit
from courseforge.hub.routes import build_hub, local_state
from courseforge.store import Store


class _App:
    def __init__(self, root: Path):
        self.store = Store(root)
        self.area_status = {}

    def course_dir(self, cid):
        path = self.store.root / str(cid)
        path.mkdir(parents=True, exist_ok=True)
        return path


class TheHomeListDrawsTheCards(unittest.TestCase):
    def test_each_known_course_gets_the_four_labels(self):
        src = (Path(__file__).resolve().parents[1] / "courseforge" / "web" / "js"
               / "grade.js").read_text(encoding="utf-8")
        body = src[src.index("function glanceCards"):src.index("function courseRow")]
        for label in ("Assignments to Grade", "Waiting to grade", "Graded here",
                      "Canvas writes today"):
            self.assertIn(label, body)
        row = src[src.index("function courseRow"):src.index("function renderResume")]
        self.assertIn("glanceCards(c)", row)
        self.assertIn("not opened yet", row)
        self.assertIn('href="#/c/${esc(c.id)}"', row)
        self.assertIn("refreshButton(c)", row)
        self.assertIn("e && e.late_penalty && e.late_penalty.waived", src)
        self.assertIn("aria-expanded", src[src.index("function glanceFold"):])
        self.assertIn("/hub/refresh", src)
        css = (Path(__file__).resolve().parents[1] / "courseforge" / "web" / "css"
               / "hub.css").read_text(encoding="utf-8")
        self.assertIn("text-align:center", css)
        self.assertIn("border-radius:18px", css)
        self.assertNotIn(">Figures", src)
        self.assertIn(".pickStats{display:none", css.replace(" ", "").replace("\n", ""))
        self.assertIn("function setAllGlance(", src)
        opened = src[src.index("function glanceIsOpen"):src.index("function glanceCards")]
        self.assertIn("return true", opened)
        self.assertIn("all[id] = 0", src)
        resume = src[src.index("function renderResume"):src.index("function ago(iso)")]
        self.assertIn("Where you left off", resume)
        self.assertIn("Carry on grading", resume)
        self.assertNotIn("Open the course", resume)
        self.assertNotIn("glanceCards", resume)
        self.assertIn("btnExpand", src)
        self.assertIn("btnCollapse", src)
        page = (Path(__file__).resolve().parents[1] / "courseforge" / "web"
                / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="btnExpand"', page)
        self.assertIn('id="btnCollapse"', page)
        hub = (Path(__file__).resolve().parents[1] / "courseforge" / "web" / "js"
               / "hub.js").read_text(encoding="utf-8")
        self.assertIn('id="behindH">To Do', hub)
        self.assertNotIn("Five assignments, by how many days", hub)
        self.assertIn("days behind", hub)
        self.assertIn('class="nm">${esc(assignment)}', hub)
        self.assertIn('class="m">${esc(meta)}', hub)
        self.assertNotIn('class="pickCode"', hub)
        self.assertNotIn("Most behind on grading", hub)
        self.assertIn("id=\"behindMount\"", hub)
        css_home = (Path(__file__).resolve().parents[1] / "courseforge" / "web"
                    / "style.css").read_text(encoding="utf-8")
        self.assertIn(".behindGrade", css_home)
        self.assertIn(".acrossCol", css_home)
        self.assertIn("Every dated assignment in the term", hub)
        self.assertIn('title="${esc(note)}"', hub)
        flat_home = css_home.replace(" ", "").replace("\n", "")
        self.assertIn(".acrossItem.m{position:absolute", flat_home)


class GlanceMatchesTheCoursePage(unittest.TestCase):
    def test_the_four_figures_agree_and_a_blank_draft_is_not_graded(self):
        root = Path(tempfile.mkdtemp())
        app = _App(root)
        cid = "9"
        cdir = root / cid
        cdir.mkdir()
        app.store.write(cdir / "assignments.json", [
            {"id": 1, "name": "Pitch", "needs_grading": 90},
            {"id": 2, "name": "Quiz", "needs_grading": 5},
            {"id": 3, "name": "Empty", "needs_grading": 0},
        ])
        (cdir / "1").mkdir()
        (cdir / "1" / "draft.json").write_text(json.dumps({
            "students": {"7": {"total": 8}},
            "last_graded_at": "2026-09-01T12:00:00+00:00",
        }), encoding="utf-8")
        (cdir / "2").mkdir()
        (cdir / "2" / "draft.json").write_text("{}", encoding="utf-8")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        (cdir / "ledger.jsonl").write_text(
            json.dumps({"at": now, "area": "grade", "sentence": "Pushed a score."}) + "\n",
            encoding="utf-8")

        state = local_state(app, cid)
        hub = build_hub(app, cid)
        stats = {row["label"]: row["value"] for row in hub["stats"]}
        self.assertEqual(state["assignments"], 3)
        self.assertEqual(state["to_grade"], 2)
        self.assertEqual(state["waiting"], 95)
        self.assertEqual(state["graded"], 1)
        self.assertEqual(state["writes_today"], 1)
        self.assertEqual(stats["Assignments to Grade"], state["to_grade"])
        self.assertEqual(stats["Waiting to grade"], state["waiting"])
        self.assertEqual(stats["Graded here"], state["graded"])
        self.assertEqual(stats["Canvas writes today"], state["writes_today"])
        self.assertEqual(stats["Waiting to grade"] and hub["stats"][1]["kind"], "warn")

    def test_a_grade_push_counts_and_a_local_edit_does_not(self):
        root = Path(tempfile.mkdtemp())
        app = _App(root)
        cdir = root / "9"
        cdir.mkdir()
        audit.record(cdir, "grade", "drafted", "Graded on this computer.",
                     course_id="9")
        audit.record(cdir, "grade", "posted", "Wrote 22 grades to Canvas.",
                     count=22, course_id="9")
        state = local_state(app, "9")
        self.assertEqual(state["writes_today"], 1)

    def test_a_course_never_opened_is_not_given_a_folder(self):
        root = Path(tempfile.mkdtemp())
        app = _App(root)
        state = local_state(app, "404")
        self.assertFalse(state["known"])
        self.assertFalse((root / "404").exists())
        self.assertEqual(state["writes_today"], 0)
