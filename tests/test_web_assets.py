"""Cheap guards on the files the browser loads.

There is no JavaScript test runner here and adding one for six files would cost
more than it saves, so this pins the two things that have actually gone wrong
while editing them with tools rather than by hand: a stray control byte written
into the source, and a backslash lost out of a string that becomes a regular
expression.

Neither is caught by reading the diff. Both break the page silently.
"""
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "courseforge" / "web"


def web_files(*suffixes):
    return [p for p in WEB.rglob("*")
            if p.is_file() and p.suffix in suffixes and "vendor" not in p.parts]


class NoStrayBytes(unittest.TestCase):
    def test_every_file_is_utf8(self):
        for path in web_files(".js", ".css", ".html"):
            with self.subTest(file=path.name):
                path.read_bytes().decode("utf-8")

    def test_no_control_characters(self):
        """A NUL written into a source file makes the browser refuse the whole
        script, and the page then loads with one area silently missing."""
        allowed = {0x09, 0x0A, 0x0D}
        for path in web_files(".js", ".css", ".html"):
            raw = path.read_bytes()
            bad = sorted({b for b in raw if b < 0x20 and b not in allowed})
            with self.subTest(file=path.name):
                self.assertEqual(bad, [], f"control bytes {bad} in {path.name}")


class RegexStrings(unittest.TestCase):
    """A regex built from a string needs its backslashes doubled.

    `join('\\s+')` is the separator that lets a name repair replace "Jordan
    Vancc" with "Jordan Vance" rather than "Jordan Jordan Vance". Written with
    one backslash it is the literal "s+", the pattern never matches, and the
    only symptom is a duplicated first name in the composer.
    """

    def test_the_name_repair_joins_on_a_real_whitespace_class(self):
        src = (WEB / "js" / "assistant.js").read_text(encoding="utf-8")
        self.assertIn("function repairName", src, "the helper was renamed")
        self.assertIn(r"join('\\s+')", src,
                      r"the separator must be '\\s+' in the source, so the "
                      r"built pattern is \s+ and not the letter s")
        self.assertNotIn(r"join('\s+')", src.replace(r"join('\\s+')", ""))

    def test_no_single_backslash_escapes_survive_in_built_patterns(self):
        """Anything handed to `new RegExp` from a literal in these files has to
        survive JavaScript's own string escaping first."""
        for path in web_files(".js"):
            src = path.read_text(encoding="utf-8")
            for m in re.finditer(r"new RegExp\(([^)]{0,200})", src):
                arg = m.group(1)
                # A lone \s \d \b \w \p inside a single-quoted piece is the
                # mistake; the doubled form is what is wanted.
                stray = re.findall(r"(?<!\\)\\[sdbwp](?![a-z])", arg.replace(r"\\", ""))
                with self.subTest(file=path.name, arg=arg[:60]):
                    self.assertEqual(stray, [], f"single-escaped {stray} in {path.name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheShellNamesWhereYouAre(unittest.TestCase):
    """Three small things the review found missing from the shell."""

    def test_the_page_has_an_icon(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="icon"', html)
        self.assertTrue((WEB / "favicon.svg").is_file())

    def test_the_home_list_is_real_links(self):
        """Middle-click / open-in-new-tab only works if the row is an <a href>,
        not a button that assigns location.hash."""
        grade = (WEB / "js" / "grade.js").read_text(encoding="utf-8")
        self.assertIn('href="#/c/${esc(c.id)}"', grade)
        self.assertNotIn(".pickRow:not(.off)", grade)
        self.assertIn('class="asgLink"', grade)
        self.assertIn('href="#/c/${esc(courseId)}/a/${esc(a.id)}"', grade)
        self.assertIn('href="#/schedule"', grade)
        hub = (WEB / "js" / "hub.js").read_text(encoding="utf-8")
        self.assertIn('href="${href}"', hub)
        self.assertIn("'#/roster'", hub)
        core = (WEB / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn("parts[0] === 'roster'", core)

    def test_the_wordmark_goes_home(self):
        """The product name in the header is how you leave a course for the list."""
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="#/"', html)
        self.assertIn('class="brandHome"', html)
        css = (WEB / "style.css").read_text(encoding="utf-8")
        self.assertIn("a.brandHome", css)

    def test_every_route_resets_the_tab_title(self):
        """Leaving a course for the list kept the course name in the tab."""
        src = (WEB / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn("document.title = TITLES[parts[0]] || 'CourseForge Studio';", src)

    def test_the_inbox_view_has_a_heading_to_land_on(self):
        """Skip to content and the after-route focus knew every view but the
        one Inbox and Reports share, so neither could be reached by keyboard."""
        src = (WEB / "js" / "core.js").read_text(encoding="utf-8")
        self.assertIn("inbox: '#viewInbox h2'", src)
        self.assertIn("'#viewInbox', '#viewWork'", src)


class GatewayColumns(unittest.TestCase):
    """A table column names the field it reads, and the component reads it.

    `renderGateway` takes columns as `{label, key, render(item)}`. a11y.js
    once wrote them as `{label, id, format(value)}`; every lookup came back
    undefined, and the Pages list drew fifty-nine rows of tick boxes with
    nothing beside them. No error, in the console or anywhere else.
    """

    def columns_blocks(self, src):
        """Each `listColumns: [ ... ]` array in one file, by brace depth."""
        out = []
        for start in (i for i in range(len(src)) if src.startswith("listColumns:", i)):
            open_at = src.find("[", start)
            if open_at < 0:
                continue
            depth, i = 0, open_at
            while i < len(src):
                if src[i] in "[{":
                    depth += 1
                elif src[i] in "]}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            out.append(src[open_at:i + 1])
        return out

    def entries(self, block):
        """Each `{...}` column inside one array."""
        out, depth, start = [], 0, None
        for i, ch in enumerate(block[1:-1], start=1):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    out.append(block[start:i + 1])
                    start = None
        return out

    def test_every_column_names_the_field_it_reads(self):
        for path in web_files(".js"):
            src = path.read_text(encoding="utf-8")
            for block in self.columns_blocks(src):
                for entry in self.entries(block):
                    if "..." in entry:            # a spread, not a literal column
                        continue
                    with self.subTest(file=path.name, column=entry[:60]):
                        self.assertTrue(
                            "key:" in entry or "id:" in entry,
                            "a column with no key reads it[undefined] and "
                            "renders an empty cell")

    def test_the_component_reads_both_spellings(self):
        """Tolerance on purpose: a whole table rendering blank with no error is
        too expensive a failure for a mismatch this easy to make."""
        src = (WEB / "js" / "components.js").read_text(encoding="utf-8")
        self.assertIn("it[c.key || c.id]", src)
        self.assertIn("c.format ? esc(c.format(raw))", src,
                      "format(value) returns text and has to be escaped here")

    def test_the_pages_list_reads_fields_that_exist(self):
        """The five columns on the Pages tab, against what a11y/routes.py
        actually puts in each item."""
        src = (WEB / "js" / "a11y.js").read_text(encoding="utf-8")
        block = self.columns_blocks(src)[0]
        for field in ("title", "kind", "size", "modified", "state"):
            with self.subTest(field=field):
                self.assertIn("key: '%s'" % field, block)
        routes = (WEB.parent / "a11y" / "routes.py").read_text(encoding="utf-8")
        for field in ("title", "kind", "size", "modified", "state"):
            with self.subTest(server=field):
                self.assertIn('"%s":' % field, routes,
                              "the server stopped sending a field a column reads")


class ButtonsThatStartWorkExplainThemselves(unittest.TestCase):
    """The two Pages sweeps carried their explanation in a title attribute.

    A title needs a hover and does not exist on a touchscreen, so on the page
    they were two bare verbs beside a red destructive one, and pressing either
    started a read of every body in the course with nothing on screen saying
    what had begun. Both now say what they look for and that they only report.
    """

    def test_each_sweep_says_what_it_looks_for_on_the_page(self):
        src = (WEB / "js" / "a11y.js").read_text(encoding="utf-8")
        self.assertIn("const SWEEPS = [", src)
        for button in ("a11yBold", "a11yBoxes"):
            with self.subTest(button=button):
                start = src.index("const SWEEPS = [")
                block = src[start:src.index("];", start)]
                self.assertIn("id: '%s'" % button, block)
        block = src[src.index("const SWEEPS = ["):src.index("];", src.index("const SWEEPS = ["))]
        self.assertEqual(block.count("what:"), 2, "both sweeps need a what")
        self.assertEqual(block.count("why:"), 2, "both sweeps need a why")

    def test_the_progress_dialog_names_the_reading_it_does(self):
        """Neither sweep reads the local copy; both pull every body from Canvas,
        which takes the better part of a minute on a real course."""
        src = (WEB / "js" / "a11y.js").read_text(encoding="utf-8")
        self.assertIn("Reading every body in the course to find bordered boxes", src)
        self.assertIn("Reading every body in the course to find bold used as structure", src)
