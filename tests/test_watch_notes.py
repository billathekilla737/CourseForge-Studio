"""Overlap the grader finds on its own, and the notes kept on a student.

  * two students who turned in the same essay are marked without anyone
    shift-clicking the class and asking;
  * push leaves them out until the instructor marks the overlap checked, and
    the check survives a re-scan unless the matches change;
  * a note written from the grader lands on the student's page and in the
    grader's view of every assignment, and a delete stays deleted.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from courseforge import confirm, grader, overlap, watchnotes
from courseforge.server import App
from courseforge.store import Store

ESSAY = ("The industrial revolution changed how families lived because factory "
         "wages pulled young workers away from farms and into crowded towns where "
         "rent was high and the air was thick with coal smoke from the mills that "
         "ran day and night so children often worked beside their parents for "
         "twelve hours and school became something only richer households could "
         "afford which is why reformers later pushed for laws limiting child labor")
OTHER = ("Steam engines let ships cross oceans faster, which opened new markets for "
         "cotton and tea while railways tied inland cities to the ports and made "
         "fresh food cheaper for people who had never seen the coast before then")
THIRD = ("Printing presses spread pamphlets about workers rights across Europe and "
         "unions grew from small guilds of craftsmen into national movements that "
         "bargained with owners over hours pay and safety on the shop floor")


def bare_app(root: Path) -> App:
    app = App.__new__(App)
    app.store = Store(root)
    app.cfg = SimpleNamespace(allow_canvas_writes=True, pseudonymize=False)
    app.confirm = confirm.ConfirmGate()
    app.state_sync = None
    return app


class OverlapIsFoundWithoutAsking(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = Store(self.tmp)
        self.store.write(self.store.assignment_dir("1", "2") / "assignment.json",
                         {"name": "9-1", "description": "Write about history."})
        # Wording more than a third of the class shares is treated as the
        # assignment, so the class needs to be big enough for three copies.
        rest = {str(uid): {"user_id": str(uid), "name": f"Student {uid}",
                           "text": " ".join(f"w{uid}x{j}" for j in range(40))}
                for uid in range(20, 24)}
        self.store.save_extracted("1", "2", {
            "7": {"user_id": "7", "name": "John Johnson", "text": ESSAY},
            "8": {"user_id": "8", "name": "Jane Doe", "text": ESSAY},
            "9": {"user_id": "9", "name": "Sam Lee", "text": OTHER},
            "10": {"user_id": "10", "name": "Ada Ray", "text": THIRD},
            **rest,
        })

    def test_identical_pair_is_marked_and_nobody_else(self):
        flags = grader.overlap_flags(self.store, "1", "2")
        self.assertEqual(set(flags["students"]), {"7", "8"})
        match = flags["students"]["7"][0]
        self.assertEqual(match["with"], "8")
        self.assertEqual(match["containment"], 1.0)
        draft = self.store.draft("1", "2")
        self.assertTrue(grader.overlap_held(draft, "7"))
        self.assertFalse(grader.overlap_held(draft, "9"))

    def test_checked_survives_a_rescan_until_the_matches_change(self):
        grader.overlap_flags(self.store, "1", "2")
        grader.mark_overlap_checked(self.store, "1", "2", ["7"])
        grader.overlap_flags(self.store, "1", "2")
        draft = self.store.draft("1", "2")
        self.assertFalse(grader.overlap_held(draft, "7"))
        self.assertTrue(grader.overlap_held(draft, "8"))

        extracted = self.store.extracted("1", "2")
        extracted["9"]["text"] = ESSAY
        self.store.save_extracted("1", "2", extracted)
        grader.overlap_flags(self.store, "1", "2")
        self.assertTrue(grader.overlap_held(self.store.draft("1", "2"), "7"),
                        "a new match puts the flag back")

    def test_push_waits_for_the_check(self):
        grader.overlap_flags(self.store, "1", "2")
        draft = self.store.draft("1", "2")
        draft.update({"rubric": [], "points_possible": 10, "students": {
            uid: {"user_id": uid, "source": "claude", "scores": {}, "total": 8}
            for uid in ("7", "8", "9")}})
        self.store.save_draft("1", "2", draft)
        app = bare_app(self.tmp)
        out = app.push("1", "2", dry_run=True)
        held = {row["user_id"] for row in out["skipped"]}
        self.assertEqual(held, {"7", "8"})
        self.assertIn("Jane Doe", next(r["why"] for r in out["skipped"] if r["user_id"] == "7"))

        grader.mark_overlap_checked(self.store, "1", "2", ["7", "8"])
        out = app.push("1", "2", dry_run=True)
        self.assertFalse(out["skipped"])


SHARED = ("the treaty of versailles punished germany so harshly that resentment "
          "festered for twenty years and gave extremists a story about betrayal they "
          "could sell to angry veterans")


def own_words(tag: str, n: int = 100) -> str:
    return " ".join(f"{tag}w{j}" for j in range(n))


class ANamedPairIsWatchedClosely(unittest.TestCase):
    """Some shared wording, well under the bar for the whole class. Once a
    note says one may have copied from the other, the pair is flagged."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = Store(self.tmp)
        self.app = bare_app(self.tmp)
        self.store.write(self.store.assignment_dir("1", "2") / "assignment.json",
                         {"name": "9-2", "description": "Write about the treaty."})
        people = {"7": own_words("a") + " " + SHARED, "8": SHARED + " " + own_words("b")}
        people.update({str(u): own_words(f"s{u}") for u in range(20, 25)})
        self.store.save_extracted("1", "2", {
            uid: {"user_id": uid, "name": f"Student {uid}", "text": text}
            for uid, text in people.items()})

    def test_below_the_class_bar_nobody_is_flagged(self):
        flags = grader.overlap_flags(self.store, "1", "2")
        self.assertEqual(flags["students"], {})
        self.assertEqual(flags["watching"], {})

    def test_a_note_naming_the_pair_flags_them(self):
        stamp = grader.overlap_stamp(self.store, "1", "2")
        note = watchnotes.add(self.app, "7", "I think 7 copied from 8.", kind="integrity",
                              about_user_id="8", about_name="Student 8")["note"]
        self.assertNotEqual(grader.overlap_stamp(self.store, "1", "2"), stamp,
                            "a new pair makes the last scan stale")
        flags = grader.overlap_flags(self.store, "1", "2")
        self.assertEqual(set(flags["students"]), {"7", "8"})
        self.assertTrue(flags["students"]["7"][0]["watched"])
        self.assertTrue(flags["watching"]["8"][0]["flagged"])
        # The note shows under both students.
        self.assertEqual([n["id"] for n in watchnotes.for_student(self.tmp, "8")], [note["id"]])
        self.assertEqual(set(watchnotes.for_students(self.tmp, ["7", "8"])), {"7", "8"})

        watchnotes.change(self.app, "8", note["id"], resolved=True)
        flags = grader.overlap_flags(self.store, "1", "2")
        self.assertEqual(flags["students"], {}, "resolving the note ends the watch")

    def test_a_watched_pair_with_nothing_shared_is_still_reported(self):
        watchnotes.add(self.app, "20", "watch these two", about_user_id="21")
        flags = grader.overlap_flags(self.store, "1", "2")
        self.assertEqual(flags["students"], {})
        self.assertEqual(flags["watching"]["20"][0]["with"], "21")
        self.assertEqual(flags["watching"]["20"][0]["containment"], 0.0)
        self.assertFalse(flags["watching"]["20"][0]["flagged"])

    def test_a_student_cannot_be_their_own_source(self):
        with self.assertRaises(ValueError):
            watchnotes.add(self.app, "7", "x", about_user_id="7")


class EveryStudentGetsAColour(unittest.TestCase):
    """Red for a copy, yellow for a passage worth reading, green for a few
    shared words, and nothing at all for a student who shares nothing."""

    def test_the_colours_follow_how_much_is_shared(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        store = Store(tmp)
        store.write(store.assignment_dir("1", "2") / "assignment.json", {"name": "9-3"})
        few = "the factory whistle blew at six every single morning"
        people = {
            "1": ESSAY, "2": ESSAY,                                   # copied
            "3": own_words("c") + " " + SHARED,                       # a passage
            "4": SHARED + " " + own_words("d"),
            "5": own_words("e") + " " + few,                          # a phrase
            "6": few + " " + own_words("f"),
        }
        people.update({str(u): own_words(f"s{u}") for u in range(20, 24)})
        store.save_extracted("1", "2", {
            uid: {"user_id": uid, "name": f"Student {uid}", "text": text}
            for uid, text in people.items()})
        levels = grader.overlap_flags(store, "1", "2")["levels"]
        self.assertEqual({uid: lv["level"] for uid, lv in levels.items()}, {
            "1": "red", "2": "red", "3": "yellow", "4": "yellow",
            "5": "green", "6": "green"})
        self.assertEqual(levels["1"]["with"], "2")
        self.assertGreaterEqual(round(100 * levels["5"]["containment"]), 1)


class TheColourFollowsThePercent(unittest.TestCase):
    def test_half_of_a_short_answer_is_yellow_not_green(self):
        # Too little text to hold a push, but half of it is not "a little".
        pair = {"containment": 0.5, "shared_windows": 4, "longest_words": 9, "notable": False}
        self.assertEqual(overlap.level(pair), "yellow")

    def test_bands(self):
        self.assertEqual(overlap.level({"containment": 0.004}), "")
        self.assertEqual(overlap.level({"containment": 0.1, "shared_windows": 3}), "green")
        self.assertEqual(overlap.level({"containment": 0.7, "shared_windows": 30}), "red")


class SideBySide(unittest.TestCase):
    CODE_A = ("def area(width, height):\n    result = width * height\n"
              "    print('area is', result)\n    return result\n")
    CODE_B = ("# Jaylen\ndef area(width, height):\n    result = width * height\n"
              "    print('area is', result)\n    return result\n")

    def test_the_match_is_marked_on_the_original_text(self):
        spans = overlap.pair_spans(self.CODE_A, self.CODE_B)
        self.assertEqual(spans["containment"], 1.0)
        run = spans["b"]["shared"][0]
        self.assertTrue(self.CODE_B[run["start"]:run["end"]].startswith("def area(width"))
        self.assertIn("\n    result", self.CODE_B[run["start"]:run["end"]],
                      "indentation inside a match is kept")
        self.assertEqual(run["peer"], 0)
        self.assertEqual(spans["a"]["shared"][0]["peer"], 0)

    def test_the_prompt_is_dimmed_not_counted(self):
        prompt = "explain in your own words why the sky looks blue during the day"
        a = prompt + " " + own_words("x", 30)
        b = prompt + " " + own_words("y", 30)
        common = overlap.common_windows({"a": a, "b": b}, boilerplate=prompt)
        spans = overlap.pair_spans(a, b, common)
        self.assertEqual(spans["a"]["shared"], [])
        self.assertEqual(spans["containment"], 0.0)
        dim = spans["a"]["common"][0]
        self.assertEqual(a[dim["start"]:dim["end"]], prompt)

    def test_the_grader_returns_both_students(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        store = Store(tmp)
        store.write(store.assignment_dir("1", "2") / "assignment.json", {"name": "Lab 3"})
        store.save_extracted("1", "2", {
            "7": {"user_id": "7", "name": "Drew Linden",
                  "parts": [{"kind": "text", "label": "area.py", "text": self.CODE_A}]},
            "8": {"user_id": "8", "name": "Casey Marsh",
                  "parts": [{"kind": "text", "label": "area.py", "text": self.CODE_B}]},
        })
        out = grader.overlap_pair(store, "1", "2", "7", "8")
        self.assertEqual(out["a"]["name"], "Drew Linden")
        self.assertTrue(out["a"]["code"])
        self.assertEqual(out["containment"], 1.0)
        with self.assertRaises(ValueError):
            grader.overlap_pair(store, "1", "2", "7", "7")


GRADES = """def grade_report(scores):
    total = 0
    for s in scores:
        total += s
    average = total / len(scores)
    if average >= 90:
        return 'A'
    elif average >= 80:
        return 'B'
    return 'C'
"""
RENAMED = """# my lab, all my own work
def calc_letter(nums):
    sum_ = 0
    for n in nums:   # add them up
        sum_ += n
    avg = sum_ / len(nums)
    if avg >= 90:
        return "A grade"
    elif avg >= 80:
        return "B grade"
    return "C grade"
"""
VOWELS = """def count_vowels(word):
    count = 0
    for ch in word.lower():
        if ch in 'aeiou':
            count = count + 1
    print('Vowels:', count)
    return count

name = input('Name? ')
count_vowels(name)
"""


def code_entry(uid, code, label="lab.py"):
    return {"user_id": uid, "name": f"Student {uid}",
            "parts": [{"kind": "text", "label": label, "path": label, "text": code}]}


class RenamedCodeStillMatches(unittest.TestCase):
    def test_renamed_variables_are_caught_where_words_are_not(self):
        texts = {"a": GRADES, "b": RENAMED}
        self.assertEqual(overlap.compare(texts)["pairs"], [])
        toks = {key: overlap.code_tokens(text, ".py") for key, text in texts.items()}
        pair = overlap.compare_code(toks, texts)["pairs"][0]
        self.assertEqual(pair["containment"], 1.0)
        self.assertEqual(overlap.level(pair), "red")
        self.assertIn("def calc_letter", pair["passages"][0]["text"])

    def test_different_programs_do_not_match(self):
        texts = {"a": GRADES, "c": VOWELS}
        toks = {key: overlap.code_tokens(text, ".py") for key, text in texts.items()}
        self.assertEqual(overlap.compare_code(toks, texts)["pairs"], [])

    def test_python_floor_division_is_not_a_comment(self):
        toks = [t for t, _, _ in overlap.code_tokens("half = n // 2\n", ".py")]
        self.assertIn("//", toks)
        self.assertEqual(len(toks), 5)
        toks = [t for t, _, _ in overlap.code_tokens("int half = n; // why\n", ".java")]
        self.assertNotIn("why", [t[1] if isinstance(t, tuple) else t for t in toks])

    def test_the_scan_and_the_side_by_side_use_it(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        store = Store(tmp)
        store.write(store.assignment_dir("1", "2") / "assignment.json", {"name": "Lab 4"})
        entries = {"7": code_entry("7", GRADES), "8": code_entry("8", RENAMED),
                   "9": code_entry("9", VOWELS)}
        store.save_extracted("1", "2", entries)
        flags = grader.overlap_flags(store, "1", "2")
        self.assertEqual(set(flags["students"]), {"7", "8"})
        self.assertEqual(flags["levels"]["7"]["method"], "code")
        self.assertNotIn("9", flags["levels"])
        side = grader.overlap_pair(store, "1", "2", "7", "8")
        self.assertEqual(side["code"]["containment"], 1.0)
        run = side["b"]["similar"][0]
        self.assertIn("calc_letter", side["b"]["text"][run["start"]:run["end"]])

    def test_starter_code_the_class_shares_is_not_counted(self):
        starter = ("import math\n\ndef read_numbers(path):\n    with open(path) as f:\n"
                   "        return [float(line) for line in f if line.strip()]\n\n")
        mine = {str(u): starter + f"def task_{u}():\n    return {u} * {u} + {u}\n"
                for u in range(10, 16)}
        toks = {k: overlap.code_tokens(v, ".py") for k, v in mine.items()}
        self.assertEqual(overlap.compare_code(toks, mine)["pairs"], [])


class NotesFollowTheStudent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = bare_app(self.tmp)

    def test_note_from_the_grader_shows_on_the_student_and_the_class(self):
        saved = watchnotes.add(self.app, "7", "I think this was copied from Jane.",
                               kind="integrity", course_id="1", assignment_id="2",
                               assignment_name="9-1")["note"]
        mine = watchnotes.for_student(self.tmp, "7")
        self.assertEqual([n["id"] for n in mine], [saved["id"]])
        self.assertEqual(mine[0]["assignment_name"], "9-1")
        self.assertEqual(mine[0]["kind"], "integrity")
        cls = watchnotes.for_students(self.tmp, ["7", "8"])
        self.assertEqual(list(cls), ["7"])

    def test_resolve_and_delete(self):
        nid = watchnotes.add(self.app, "7", "watch them")["note"]["id"]
        watchnotes.change(self.app, "7", nid, resolved=True)
        self.assertTrue(watchnotes.for_student(self.tmp, "7")[0]["resolved"])
        watchnotes.change(self.app, "7", nid, delete=True)
        self.assertEqual(watchnotes.for_student(self.tmp, "7"), [])
        with self.assertRaises(KeyError):
            watchnotes.change(self.app, "7", nid, resolved=False)

    def test_a_note_belongs_to_one_student(self):
        nid = watchnotes.add(self.app, "7", "watch them")["note"]["id"]
        with self.assertRaises(KeyError):
            watchnotes.change(self.app, "8", nid, delete=True)
        with self.assertRaises(ValueError):
            watchnotes.add(self.app, "7", "   ")

    def test_a_delete_wins_over_an_older_copy(self):
        nid = watchnotes.add(self.app, "7", "watch them")["note"]["id"]
        older = watchnotes.Book(self.tmp).read()
        watchnotes.change(self.app, "7", nid, delete=True)
        merged = watchnotes.merge_maps(watchnotes.Book(self.tmp).read(), older)
        self.assertTrue(merged[nid]["deleted"])


if __name__ == "__main__":
    unittest.main()
