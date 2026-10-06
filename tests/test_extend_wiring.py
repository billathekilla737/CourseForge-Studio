"""The extensions page is only useful if every seam it hangs off still exists.

Each of these is a connection that breaks silently: an area that is not loaded
registers no routes and every call 404s; a script tag that is missing leaves
`openExtensions` undefined and the hash route falls through to the picker; a
missing label leaves the record showing a raw area id. None of that raises.
"""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "courseforge"
WEB = SRC / "web"


def read(rel: Path) -> str:
    return rel.read_text(encoding="utf-8")


def braced(text: str) -> str:
    """The first `{...}` in `text`, nested braces included."""
    start = text.index("{")
    depth = 0
    for at in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[at], 0)
        if depth == 0:
            return text[start:at + 1]
    raise AssertionError("unbalanced braces")


class TheAreaIsLoaded(unittest.TestCase):
    def test_it_is_in_the_core_list(self):
        from courseforge import areas
        self.assertIn("extendarea", areas.CORE)

    def test_its_four_routes_register(self):
        import courseforge.extendarea.routes  # noqa: F401
        from courseforge.routing import ROUTER
        found = {(r.method, r.pattern) for r in ROUTER.routes
                 if r.pattern.startswith("/api/extend")}
        self.assertEqual(found, {
            ("GET", "/api/extend/students"), ("GET", "/api/extend/courses"),
            ("POST", "/api/extend/plan"), ("POST", "/api/extend/apply"),
        })

    def test_the_writing_route_goes_through_the_gate(self):
        s = read(SRC / "extendarea" / "routes.py")
        self.assertIn('app._gate("extend"', s)
        self.assertIn("what=\"moving due dates for named students\"", s)

    def test_the_planning_route_hands_out_no_token(self):
        """Rows can be unticked after planning, so a token offered then would
        be bound to a set nobody agreed to."""
        s = read(SRC / "extendarea" / "routes.py")
        plan_part = s[s.index("def plan_("):s.index("def apply_(")]
        self.assertNotIn("confirm.offer", plan_part)

    def test_every_write_is_recorded_by_name(self):
        s = read(SRC / "extendarea" / "routes.py")
        self.assertIn('"extend",\n        "extended" if ok else "failed"', s)
        self.assertIn("audit.person(", s)
        # A refusal is as much a fact as a success.
        self.assertIn('result="ok" if ok else "failed"', s)


class TheClientCanDoWhatTheAreaAsksOfIt(unittest.TestCase):
    def test_the_override_methods_exist(self):
        from courseforge.canvas import CanvasClient
        for name in ("assignment_overrides", "create_override", "update_override",
                     "delete_override", "students_with_sections", "submitted_pairs"):
            self.assertTrue(hasattr(CanvasClient, name), name)

    def test_an_override_cannot_be_emptied_of_students(self):
        """Canvas keeps an override with no students, and it governs nobody."""
        from courseforge.canvas import CanvasClient
        client = CanvasClient("https://x", "t")
        with self.assertRaises(ValueError):
            client.update_override(1, 2, 3, student_ids=[])

    def test_an_update_with_nothing_to_change_is_refused(self):
        from courseforge.canvas import CanvasClient
        client = CanvasClient("https://x", "t")
        with self.assertRaises(ValueError):
            client.update_override(1, 2, 3)

    def test_student_dates_are_refused_to_the_content_scope(self):
        """A prompt injected into a course page must not reach a due date that
        names a student."""
        from courseforge import canvas_policy
        for url in ("/api/v1/courses/1/assignments/2/overrides",
                    "/api/v1/courses/1/students/submissions",
                    "/api/v1/courses/1/users?include[]=enrollments"):
            with self.assertRaises(canvas_policy.PolicyDenied, msg=url):
                canvas_policy.check_scope("content", "POST", url)
        # And the grading scope, which is what this area holds, may.
        canvas_policy.check_scope("grading", "POST", "/api/v1/courses/1/assignments/2/overrides")


class TheShellKnowsAboutIt(unittest.TestCase):
    def test_the_script_is_loaded(self):
        # With or without the ?v= tag that makes a browser fetch it afresh.
        self.assertRegex(read(WEB / "index.html"), r'src="js/extend\.js(\?[^"]*)?"')

    def test_the_hash_route_opens_it(self):
        s = read(WEB / "js" / "core.js")
        self.assertIn("parts[0] === 'extensions'", s)
        self.assertIn("openExtensions", s)

    def test_the_tab_gets_its_own_title(self):
        self.assertIn("extensions: 'Deadline extensions", read(WEB / "js" / "core.js"))

    def test_the_page_is_reachable_from_the_picker(self):
        hub = read(WEB / "js" / "hub.js")
        self.assertIn('href="${href}"', hub)
        self.assertIn("'#/extensions'", hub)

    def test_the_record_names_the_area_in_words(self):
        self.assertIn("extend: 'Extensions'", read(WEB / "js" / "record.js"))

    def test_the_page_defines_what_the_router_calls(self):
        self.assertIn("window.openExtensions = open", read(WEB / "js" / "extend.js"))

    def test_the_apply_button_uses_the_servers_own_confirmation(self):
        """Composing a sentence in the browser would describe one thing while
        sending another; the gate's sentence is the one that is true."""
        s = read(WEB / "js" / "extend.js")
        self.assertIn("runJobConfirmed('Moving due dates in Canvas'", s)
        self.assertNotIn("askConfirm(", s)

    def test_the_confirmation_travels_inside_the_body(self):
        """`api()` sends `opts.body` and nothing else, and the gate reads the
        token from the body. A token handed to `api()` beside the body never
        leaves the page: every Yes arrives without one, is refused, and the
        same question comes back, so nothing is ever written."""
        s = read(WEB / "js" / "extend.js")
        call = s[s.index("api('/extend/apply'"):]
        body = braced(call[call.index("body:"):])
        self.assertRegex(body, r"\bconfirm:\s*token\b")

    def test_the_styles_it_uses_are_defined(self):
        css = read(WEB / "style.css")
        used = set(re.findall(r'class="(ex[A-Za-z]+)[" ]', read(WEB / "js" / "extend.js")))
        self.assertTrue(used, "no ex- classes found; the page was rewritten")
        for name in sorted(used):
            self.assertIn("." + name, css, f".{name} is used but never styled")


if __name__ == "__main__":
    unittest.main()
