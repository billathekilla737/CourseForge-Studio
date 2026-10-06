# Grading in CourseForge Studio

This is the grading area's manual, carried over from canvas-grader, the tool CourseForge Studio grew out of. Paths and commands below are updated for the Studio; the behaviour is unchanged.

A local grading assistant for Canvas LMS. It pulls an assignment's rubric and
submissions onto your machine, drafts a rubric-aligned grade for each student
using the **Claude Code CLI** and your existing Claude account, and gives you a
review UI where you adjust anything you disagree with before a single point
reaches Canvas.

It is a drafting tool. It never posts a grade you have not looked at.

---

## What it does

- **Pick a course, pick an assignment.** Reads your real Canvas enrollments,
  filtered to the term you are actually in, with a dropdown for older ones.
- **Sync.** Downloads submissions, attachments and graded-discussion entries, and
  extracts readable text from `.docx`, `.pdf`, source files and Canvas rich-text
  entries.
- **Auto-grade.** Sends each student's work to Claude with the assignment's Canvas
  rubric — including its exact rating tiers — and gets back a per-criterion score,
  a rationale for each, and a comment written to the student.
- **Custom instructions.** A free-text box per assignment for anything not in
  Canvas: a requirement you changed in class, an extension you granted, a
  misreading to be lenient about. These override the rubric where they conflict.
- **Review side by side.** The student's actual submission sits next to the
  rubric sliders. Move a slider and your score replaces Claude's.
- **Watch video submissions in place.** A `.mov`, `.mp4` or `.webm` attachment
  plays in the review pane, with speed controls for getting through a stack of
  screen recordings. Video is never sent to Claude -- it is the most expensive
  thing a student can turn in and the cheapest thing to watch -- so those
  students come back as "video to watch" for you to score by hand.
- **Long jobs run in the background.** Closing the progress dialog on an
  auto-grade or a Blender pass does not stop it: the job condenses to a chip in
  the header with its own progress bar, and clicking the chip puts the dialog
  back.
- **Insights.** A radar of class mean achievement per rubric criterion, bars
  sorted weakest first, a per-student heatmap, and a distribution. Plus a written
  class summary: Claude reads the aggregate results and the notes it wrote while
  grading, then says where the class struggled, whether the cause looks like a
  skill gap or an ambiguous prompt, and what to reteach. Export as SVG or print.
- **Export or push.** Write `grades.csv` / `grades.json`, or push scores to Canvas
  behind a read-only plan and an explicit master switch. Comments are opt-in.

## Terms

The course picker defaults to the term you are in, worked out from today's date:
January to May is Spring, June and July are Summer, August to December is Fall.
A dropdown lists every term you have courses in, newest first, plus "All terms".

Canvas's own term objects are not used for this. In practice `start_at` comes back
null, `end_at` can be a year off, and the same term id arrives with different dates
depending on the enrollment it was read through. The `YYYYTT` prefix in the course
code (`202630 IMT 1213 001`) is reliable and already sorts correctly, so that is
what gets parsed, with the Canvas term name as a fallback for sandbox shells.

Claude flags anything it cannot grade fairly — an empty submission, handwriting
it cannot read, work that does not answer the prompt — and those never get pushed
until you clear them.

---

## Requirements

- **Python 3.10+**
- **Claude Code CLI**, installed and logged in — <https://claude.com/claude-code>
- A **Canvas API token** — [step 2](#2-put-your-canvas-token-in-a-file) below
- `pip install -e .[pdf]` (the only third-party dependency; everything else is stdlib)

---

## Setup

Three steps. The token is the only fiddly one, so it gets its own section.

### 1. Get the code and the one dependency

```bash
git clone https://github.com/billathekilla737/CourseForge-Studio-app
cd CourseForge-Studio-app
pip install -e .[pdf]
```

Downloaded the ZIP instead of cloning? That works too. Unzip it and `cd` into
the folder — everything below is the same.

Then copy the example config:

```bash
cp config.example.json config.json
```

On Windows PowerShell: `Copy-Item config.example.json config.json`

Open `config.json` and change `base_url` if you are not at MGCCC. **Your token
does not go in this file** — see the next step.

### 2. Connect to Canvas

**The easy way: let the app do it.** Start the tool (next section) and it will
show a **Connect to Canvas…** button, because it has no token yet. Paste the
token there and it checks the token against Canvas, writes the file for you in
the right place with the right encoding, and tells you who you signed in as.
Nothing to name, nothing to save in the wrong folder. You can change it later
from the **Canvas token…** button on the courses screen.

To get the token: in Canvas click your avatar (**Account**) → **Settings** →
scroll to *Approved Integrations* → **+ New Access Token** → purpose
`canvas-grader`, expiry blank → **Generate Token**, and copy it straight away,
because Canvas will not show it again.

That is the whole step. The rest of this section is only for setting it up by
hand, or on a machine with no browser.

<details>
<summary>Doing it by hand instead</summary>

**Create a file named exactly `canvas.token` containing nothing but the token,
in your per-user settings folder:**

```
Windows   %APPDATA%\CourseForge-Studio\canvas.token.enc  (encrypted; the old canvas-grader location is still read)
           (C:\Users\<you>\AppData\Roaming\canvas-grader\canvas.token)
macOS      ~/.config/courseforge-studio/canvas.token
Linux      ~/.config/courseforge-studio/canvas.token
```

That is where the setup screen saves it, and it is the right place for one
reason: **every copy of this app on the machine reads it.** A token kept inside
the app folder is lost the next time you download a new copy, and a second copy
somewhere else cannot see it — which shows up as "Canvas is not connected" in a
folder you already set up once.

The app folder still works if you prefer it there:

```
canvas-grader/
├── canvas.token        <-- create this file. Just the token, nothing else.
├── config.json
├── config.example.json
├── README.md
└── courseforge/
```

**First, get the token from Canvas:**

1. Click your avatar (**Account**) in the left sidebar
2. **Settings**
3. Scroll to *Approved Integrations* and click **+ New Access Token**
4. Purpose: anything, e.g. `canvas-grader`. Leave the expiry blank.
5. **Generate Token**, then copy it immediately — Canvas will not show it again

**Then put it in the file.** Any of these work; use whichever suits you.

*Notepad, or any editor* — the least error-prone way:

```bash
notepad canvas.token
```

Paste the token, save, close. (Notepad will offer to create the file.)

*macOS or Linux shell:*

```bash
printf '%s' 'PASTE_YOUR_TOKEN_HERE' > canvas.token
```

*Windows PowerShell:*

```powershell
Set-Content -Path canvas.token -Value 'PASTE_YOUR_TOKEN_HERE' -NoNewline -Encoding utf8
```

The file should contain **only** the token, which looks something like
`7~aBcD3fGhIjK...`. Not `CANVAS_TOKEN=7~...`, not quotes around it, not your
username — just the token. A stray newline, surrounding quotes or a byte-order
mark are tolerated, but anything else is treated as part of the token and Canvas
will reject it.

`canvas.token` is gitignored, along with `config.json` and everything under
`data/`, so none of it can be committed by accident.

<details>
<summary>Other places the token can live (you only need one)</summary>

The token is looked for in this order, and the first one found wins:

1. the `CANVAS_TOKEN` environment variable
2. whatever path you set as `"token_path"` in `config.json`
3. `canvas.token` in the per-user folder ← **where the setup screen writes it,
   and the only one every copy of the app can see**
4. `canvas.token` in the project folder
5. `canvas.token.txt` in the project folder (Notepad's *Save As* adds the `.txt`)
6. `~/Documents/canvas-work/canvas.token`
7. `~/.canvas.token`

The environment variable is handy on a shared machine, since it leaves nothing
on disk, but it has to be set again in every new terminal. If you are unsure,
use the file.

Run `python -m courseforge doctor` and, if it cannot find a token, it prints
every path it checked so you can see exactly where it was looking.

</details>

</details>

### 3. Check it works

```bash
python -m courseforge doctor
```

This confirms your token reaches Canvas and that the Claude CLI is logged in.
Run it once now rather than finding out halfway through a batch of grading. A
good run ends with `All good.` and names you:

```
Canvas host    : https://mgccc.instructure.com
Canvas token   : found (69 chars)
Canvas login   : OK -- Your Name (id 1234567)
Claude login   : OK
...
All good.
```

The two ways it can go wrong are told apart on purpose:

- `Canvas token   : NOT FOUND` — the file is not where the tool looked. It
  prints every path it checked; compare those with where you actually saved it.
  A file named `canvas.token.txt` is the usual culprit, since Windows hides
  known extensions.
- `Canvas login   : FAILED` — the token was read but Canvas rejected it. Either
  the file has something extra in it, or the token was revoked. Generate a new
  one and paste it again.

## Running it

**Windows, no terminal:** double-click **`CourseForge Studio.vbs`**.

A small window appears saying *Running*, with a button that opens the web UI in
your browser. It also shows whether Canvas and Claude are reachable, so a broken
login is visible before you start grading rather than after. Closing that window
(or pressing **Quit**) shuts down the HTTP server, its worker threads, and any
Claude subprocesses still mid-batch — if grading is in flight it asks first.

No console window ever appears: the launcher runs under `pythonw.exe`, and every
Claude subprocess is spawned with `CREATE_NO_WINDOW`.

Pin it to your taskbar, or right-click → *Send to* → *Desktop (create shortcut)*.

**From a terminal**, if you prefer logs:

```bash
python -m courseforge serve      # http://127.0.0.1:8900, Ctrl+C to stop
python -m courseforge gui        # same GUI launcher, console attached
```

`run.cmd` is the old terminal-plus-browser launcher and still works.

**A true standalone .exe** is possible but not shipped, because it would add a
build step and a large binary to a repo whose users already have Python:

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name "CourseForge Studio" ^
  --add-data "courseforge/web;courseforge/web" ^
  -c "from canvasgrader.launcher import main; main()"
```

---

## Important: run it from a normal terminal

If you launch this from **inside** a Claude Code session, the parent process
exports `ANTHROPIC_BASE_URL` and a set of `CLAUDE_CODE_*` variables. A child
`claude` inherits them, points at a proxy it cannot authenticate against, and
fails with `Not logged in`.

The tool strips those variables from the subprocess environment, which fixes the
common case. If you still see `Not logged in`, open a plain terminal, run
`claude`, then `/login`, and start the tool from that same terminal.

---

## Opening an assignment

Picking an assignment — from the course list, or from the term schedule — syncs
it from Canvas by itself. Choosing it is the request to work on it, so there is
no Sync button to find first: the page draws, the sync runs on top of it, and
the dialog closes itself when it is done. A sync it started for you never leaves
a dialog waiting to be dismissed; a sync that *fails* stays on screen so you can
read why.

One that has never been synced always syncs. One synced more recently than
`assignment_max_age_min` (default 15) is taken as current and opens straight
away — files already downloaded are never fetched twice, but re-reading two
dozen PDFs is not free, and the grade pull on a timer already keeps scores in
step. Set it to `0` to sync on every open. **Re-sync** in the header is still
there for the moment you know something changed.

### Roster filters

Above the roster: **All**, **Turned in**, **Needs review**, **Edited**,
**Ungraded**, **Missing**, **Conflicts**.

**Turned in** is the working set most of the time — whoever actually submitted
something. Everyone else is a roster entry with nothing to read. Whichever chip
you pick is remembered, across assignments and across restarts, because it is a
habit rather than a property of the assignment.

**Ungraded** is everyone who did not have a finished grade live on Canvas when
the assignment was scanned. Moving a slider or pushing a grade does not take a
student off the list, so it holds the whole batch until you are done; the next
sync rebuilds it from what Canvas shows then.

---

## How grading works

For each student the prompt contains:

1. The assignment name, points, and description **as students saw it**
2. Every rubric criterion: label, long description, max points, and the exact
   Canvas rating tiers, so scores land on real tier values
3. Your custom instructions, marked as overriding the rubric
4. Submission metadata — status, timestamp, late flag, word count, filenames,
   and an explicit note listing any parts that could not be converted to text
5. The student's work. On a quiz, Canvas has already scored the multiple
   choice; the written answers are read from the quiz and scored on their own,
   then added to that. Word, PowerPoint, Excel and text PDFs are read as
   text. Screenshots (PNG, JPEG and the other common picture types), pictures
   pasted inside those Office files, and scanned PDFs are sent as images and
   graded from what is on the page. Video is not sent; you watch that.

A late submission is still graded as if it were on time. After the scores come
back, the Studio reads this course's syllabus (the late-work section, not the
opening) and applies that rule: 10% per day, a flat percent, or no late work
at all. The rubric cells stay as the work earned them. The number that posts
is the earned score minus that dock.

Studio always grades lateness itself. If the course has Canvas's automatic
late deduction turned on (Grades → Late Policies), Studio applies that rule
when the syllabus has none, and at the push sets each late submission's Canvas
status to **None** just before its grade is written, so Canvas takes nothing
off. The course's Late Policies setting itself is never changed; only the
individual late submissions Studio posts are. The push confirmation says how
many will be set to None. A course without a Canvas late policy keeps its
"Late" labels untouched.

A grade sync compares Canvas's *entered* score, not the score left after
Canvas's deduction, so a deduction Canvas took is never copied back over your
grade. Where Canvas already took one, the student's page shows "Canvas took
−x late" until the next push undoes it.

A late student's page has a **Late penalty applied** slider under the score,
from 0 to 100 percent of that syllabus dock in steps of 10. 100 is the whole
penalty and 0 is none. It does not need the auto-grader: on a student you
graded by hand, moving it applies the syllabus rule at the share you pick, and
on one waived by your instructions it puts part of the penalty back. A re-grade
keeps the share you set. It says so when neither the syllabus nor Canvas
has a rule to scale.

A grading run can be stopped: **Stop** in its progress dialog, or right-click
the progress chip in the header and pick **Stop grading**. The Claude calls in
flight are ended right away. Students who finished before the stop keep their
new drafts, and nobody else is touched, so a run started before the custom
instructions were written costs only the students it already finished. Other
jobs (sync, push) cannot be stopped partway, and the menu says so.

Claude returns strict JSON: per-criterion points and rationale, a comment, flags,
a confidence level, and a `needs_human` boolean. Anything unparseable is recorded
as an error against that student rather than silently scored.

A document that could not be read is never graded from what was left. If a
Word, PowerPoint, Excel or PDF attachment fails to open, and there is little
else to read, the student gets no score and a reason ("could not read
MDA_Teardown.docx ... re-sync the assignment, then re-grade"); if there is
real text beside the broken file, a score is drafted but held for review, and
the push will not send it. Behind that, the sync writes each download beside
its destination and renames it into place, fetches again any copy that is
empty or shorter than Canvas says, reads a document twice a second apart
before calling it unreadable, and runs one sync per assignment at a time.
Each of those exists because a Word file read while another sync was still
writing it was once scored from the single picture inside it.

The rationale is written for you, one per criterion. The comment is written to the
student and is capped at two sentences and 45 words: what cost the points, and the
one thing to do differently. No opening praise, no recap of what they submitted, no
sign-off. A student reads two sentences and skims anything longer, so a long comment
is a wasted one. Comments stay on this machine unless you tick **Include this
comment when pushing** on that student, then choose **Only comments I ticked**
(or **Every student's comment**) on the push dialog. The default is scores only.

### House style

`style.py` holds one block of writing rules, and every prompt that produces prose
includes it: grading comments and rationales, the class summary, the teaching
feedback, the overlap triage, and the class announcements. The rules come from
Wikipedia's [Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing)
and they are blunt about it. No em dashes. No "Great work, however". No *delve*,
*crucial*, *robust*, *showcase*, *testament*. No praise sandwich, no upbeat
sign-off, no groups of three, no chatbot manners. Say "is" rather than "serves as".
Name who did what. Quote the student's own words or drop the claim. Then reread and
cut whatever is not carrying information.

This is not decoration. Feedback that reads like a chatbot gets ignored by students
and embarrasses the instructor whose name is on it. `style.py` is meant to be edited
directly: it describes how you write, not how the tool works.

### The second pass: the humanizer

The style block is guidance at prompt time, and a model that has just spent a
long turn scoring a rubric still lets tells through. So everything the grader
writes goes back through the `/humanizer` skill before you see it: the comment
to the student and every per-criterion rationale, whether one student was
re-graded or the whole class was run; the written class summary; the teaching
read; the overlap triage; an answer to a question you asked about a student;
an announcement draft; and a drafted inbox reply. `humanize.py` sends the
finished text to Claude with the skill as the editor's brief and takes the
rewrite back.

The brief keeps itself current with no one's attention. Once a day the Studio
fetches the skill's `SKILL.md` from its GitHub repository into your per-user
folder (`%APPDATA%\CourseForge-Studio\humanizer\`), keeps it only if it is a
valid skill file whose version is not older than the one already kept, and the
next grade reads it. No click, no restart. What it fetches is checked before it
is trusted: one fixed address over TLS, no redirects off that host, a size
band, the skill's own frontmatter and sections present, and none of the text a
writing guide has no reason to carry. A fetch that fails for any reason
(offline, a proxy that refuses the host, a page that is not the skill) is
written to `state.json` beside the copy and changes nothing.

Of the copies on the PC, the newest version is used: that refreshed copy, your
own installed skill at `~/.claude/skills/humanizer/SKILL.md`, or the one bundled
in `courseforge/knowledge/humanizer.md`. `python -m courseforge doctor` checks
GitHub on the spot and says which copy is in use. `"humanize_auto_update":
false` freezes the skill at whatever is installed, and `humanize_skill_path`
pins one file and stops the choice altogether.

Nothing a grade depends on can be lost to the editing. The pass runs while the
pseudonym tags are still in place, so the editor never sees a name. Every
rewrite is checked before it replaces the original: the same numbers and
scores, the same tags, every quoted student phrase still present verbatim, no
bloat, no dash that was not there before, and a comment still under its cap.
A field that fails any check keeps the grading model's words, and a pass that
fails outright (no CLI, a timeout, an unreadable reply) keeps all of them. The
outcome is recorded on the entry as `humanized` (which fields changed, which
were kept and why, what it cost), and the cost is folded into the entry's
`cost_usd`.

It is one extra model call per graded student and per summary, on
`humanize_model` (`sonnet` by default; an editing pass does not need the
grading model). `"humanize": false` in `config.json` turns it off.

Students are graded concurrently (`grading_concurrency`, default 8, ceiling 8).
Each lane is its own `claude` CLI call, so this changes how long a section takes,
not what it costs. Lower it if you see rate-limit errors against individual
students in the job log.

---

## Privacy

By default `pseudonymize` is **on**:

- Students are relabeled `S-001`, `S-002`… in stable user-id order
- Each student's own name is stripped from their submission text
- Emails, phone numbers, SIS ids and login ids are scrubbed
- The identity map is written to `data/<course>/<assignment>/map.json` and
  **never leaves this machine**

This is best effort, not a guarantee — a name written into an essay, or a name
visible in a screenshot, can survive. What it does guarantee is that the roster
mapping stays local.

Everything downloaded or produced lives under `data/`, which is gitignored along
with `config.json` and `*.token`. Nothing in this repo should ever contain
student data. Check `git status` before your first push.

If you run Anthropic's `canvas-pii-guard` plugin, note that its own documentation
says not to install it on a dedicated grading machine — it blocks the Canvas
submission endpoints this tool exists to read. That guard protects *content*
workflows; this is the sanctioned grading path it carves out.

---

## Selecting several students

Click a student to review them. **Shift-click** another to take the range between
them, **ctrl-click** (cmd on a Mac) to add or drop one, a plain click to go back
to reviewing one, **Escape** to clear. The selection is by student, so it
survives a filter or search change that reorders the list underneath it.

With a selection, a bar at the bottom of the roster offers:

| Action | What it does |
|---|---|
| **Re-grade** | Grades only these students |
| **Mark reviewed** | Clears the `needs_human` block so these can be pushed. Flips to *Un-mark* when they are all already marked |
| **Insights** | The charts and the written read, computed over this selection instead of the class |
| **Overlap check** | Compares what these students wrote for shared wording (below) |
| **Push…** | The usual plan-then-confirm push, limited to these students |

"Mark reviewed" only lifts the review block. A student with no score still has
nothing to post, and the status line says so rather than reporting a silent
success.

---

## Term schedule

**Term schedule** on the courses screen lists every dated assignment across the
term's courses, grouped by week. It reads straight from Canvas. The five tiles
at the top follow the course chips (and the Tests &amp; exams / Waiting to grade
filters): two courses selected is the stats for those two courses.

| Tile | What it counts |
|---|---|
| **Still due this term** | Dated items not yet due |
| **Due in the next 7 days** | Those still due inside a week |
| **Tests & exams remaining** | Tests, exams and finals still due |
| **Waiting to grade** | Submissions Canvas says are waiting, including past due |
| **Dated items this term** | Every dated item in the selection, past and upcoming |

Courses are labelled by **name**, not catalogue code — "Game Theory and
Mechanics", not "IMT 1213". A Canvas course name is mostly filing:
`202630 MAT 1313 002 College Algebra (Online)` becomes **College Algebra**, with
the term code, subject, catalogue number, section and delivery mode all removed.
A parenthetical that carries meaning stays, so "Chemistry I (Lab)" keeps its
"(Lab)".

Two courses can therefore share a label — MAT 1313 and MAT 1314 are both
College Algebra. Only the words are shared: they keep separate badge colours,
separate filter chips with their own counts, and their own rows, and the badge's
tooltip carries the full Canvas name if you need the code.

Each week is a card with its item count, total points and how many submissions
are waiting; the current week is highlighted and scrolled to on open. Rows carry
the due day and time, a colour-coded course badge, the item, its type, the
number of submissions Canvas says are waiting, and the points. Past-due rows are
struck through and hidden by default.

Filter by course, or by:

| Filter | Shows |
|---|---|
| **Hide past due** | On by default; only what is still ahead |
| **Tests & exams only** | Real assessments, for planning proctoring |
| **Waiting to grade** | Everything with submissions waiting — *including past due*, because that is where a backlog lives |

Clicking an item name opens **what the assignment asks for** — the description
straight from Canvas, with its rubric, points, submission types and how many
submissions are waiting. Reading the prompt is usually what you want from a
schedule, so grading is a button in that panel (**Open in the grader** / **Grade
N waiting**) rather than what a click does. The small arrow opens the item in
Canvas, and the back button returns to the schedule with your filters intact.

Descriptions are read on demand rather than cached with the schedule: a term's
worth is hundreds of kilobytes of HTML, and the description is the thing most
likely to have changed since the last sync. They are sanitised on the way in —
a real description from these courses carries a remote `<script>` and an
account-wide stylesheet, and neither has any business running inside this tool.
Structure, tables, images and links survive; `class` attributes are dropped so a
description cannot pick up the app's own styling, relative links are made
absolute so images work, and an embedded video becomes a link.

### Acting from the schedule

**Announce** sits in each row (and in the description panel). It drafts a
reminder from what Canvas knows — the dates, the points, whether it is
proctored, the assignment description — and you can add a steer of your own
("mention the review session"). The draft is editable before it goes anywhere,
with **Copy** if you would rather paste it yourself.

**Remind N tests** sits in each week's header, on any week that has an
assessment in it. It drafts a reminder for every test, exam and final in that
week — as the filters currently leave them, so narrowing to one course narrows
the button with it — and gives you one screen with all the drafts side by side.
That is the point of doing them together: a week of five reminders written one
at a time is where duplicated or contradictory wording appears. Untick any you
do not want, edit the rest, then post the ticked ones in one go, each to its own
course. Quizzes and practice tests are left out; the count in the button is
exactly what will be drafted.

**Tell it what to change…** takes a plain sentence about the assignments on the
schedule: *"unlock my Test 1 for Game Theory today at 8am"*, *"push the College
Algebra 1.1 homework to Friday at 11:59pm"*, *"publish the Week 2 quiz"*.

It comes back with a plan, not a change. Each item shows the field, the value
now, and the value after, with a tick box; **Dry run** prints the exact payload
that would go to Canvas without sending it, and **Apply** asks for confirmation.

The design is deliberately narrow, because these are live courses with students
in them:

- **The model proposes, the tool decides.** It picks from a fixed vocabulary —
  set the opens/due/closes dates, publish, unpublish, post an announcement — and
  names assignments by id from the list it was shown. It cannot name an
  endpoint, a field, or a course it was not given. Anything else it returns is
  refused by name and shown to you.
- **Every operation is checked twice**, once when planned and again on the
  server before anything is sent, so a reshaped request cannot slip past the
  allow list.
- **It asks rather than guessing.** "Unlock all my tests today" comes back as a
  question, because that would open exams that are not due until November.
  "Delete the final exam" and "give everyone 100" are refused outright — there
  is no operation for either.
- **Times stay in your timezone.** The model writes a local wall clock and never
  an offset; the browser converts to UTC. A classic quiz keeps its own copy of
  these dates, so the quiz is written alongside the assignment — otherwise a
  test can look unlocked and still be closed.
- **Nothing is written on one click.** Apply is refused once, shown to you in
  full, and sent only on the second click. Drafting, planning and dry runs never
  touch Canvas at all.

**Freshness.** The cached copy paints immediately (about 70ms for 341 items) and
the footer says how old it is — *"Canvas synced 3 min ago (Sep 9, 12:29 PM)"* —
next to a Refresh button. Opening a schedule older than `schedule_max_age_min`
(30 by default) kicks off a refresh in the background, with the footer showing
the progress, so the view is never blocked on the network and the timestamp is
always honest about what you are looking at.

Two details worth knowing, because both are easy to get wrong:

- **Times are yours, and so are the weeks.** Canvas stores due dates in UTC, and
  an 11:59 PM Central deadline comes back as `04:59Z` *the next day*. Grouping
  those into weeks on the server would file a Sunday-night deadline under the
  following week. The browser does the grouping, in its own timezone, so a
  deadline lands in the week you would put it in.
- **Types come from the name.** Canvas's `is_quiz_assignment` describes delivery,
  not meaning: it is False for "Unit 1 Test" (run through a publisher tool) and
  True for "Test: Chapter 8 - Proctored Exam". The name is what everyone
  actually goes by. Three cases are handled deliberately so the exam count can
  be trusted: "Schedule Proctored Final Exam" is an errand, not the exam; a
  "(Practice Test)" is not an assessment; and "Course Review" is a topic, so
  those quizzes stay quizzes.

Items with no due date are not listed, the same as Canvas's own calendar. A
course that fails to load does not lose the rest of the schedule; the footer
names it.

---

## Two zones: grading on top, teaching underneath

The bar along the **top** of the window acts on grades: sync, auto-grade, curve,
export, push. The bar along the **bottom** is a different job. Nothing in it
changes a grade or touches Canvas — it reads the same results back as evidence
about the assignment and the teaching, which is the question you ask once the
grading is done. It is a different colour on purpose.

The teaching bar fills in on its own as soon as anything is graded, because all
of it is measured locally and costs nothing:

**Non-submissions are not zeros.** A student who submitted nothing has *no
score*: they are reported as a non-submission, with their name, and left out of
the class average, the letter distribution, the rubric analysis, every curve, and
the push. A fabricated zero would turn them into an F and drag down numbers that
are supposed to describe the students who did the work. Whether a missing
submission eventually earns a zero is a policy decision — set it by hand, or let
your Canvas missing-submission policy handle it. The same applies to a submission
the tool could not read: it has no score until you look at it.

The insights view has an **also count non-submissions as zero** tick for the
"what if I zeroed these" view. It is off by default, and while it is on the
charts say so.

**Rubric health.** Ordinary item analysis on each rubric row: average, spread,
how many students hit full marks, how many landed near zero, and how well the row
tracks the rest of the grade. The point is the rows that are *not doing their
job*:

- a row every student aces is not measuring anything
- a row nobody clears is usually untaught or unclearly worded, not a class-wide
  failure of character
- a row whose scores do not track the rest of the grade is measuring something
  the other rows are not — sometimes exactly what you wanted, sometimes a sign
  students read the wording differently

The correlation is against the *rest* of the score rather than the total (a row
is part of its own total and would otherwise look better connected than it is),
and it is withheld entirely below eight graded students, where it would be noise.

**Student voice.** Sentences where a student is talking about their own
experience rather than about the subject — pulled from what they wrote and from
their Canvas submission comments, grouped by what kind of problem it was: did not
understand something, the instructions or rubric, software trouble, ran out of
time, could not find a resource, asked you a question. Canvas comments were being
fetched and thrown away before this; they are now kept and read.

This is deliberately conservative about what counts. A submission that says "the
player gets confused by the map" is analysis, not a student telling you they were
lost, so a first-person subject is required. Only the student's own writing is
searched, never a Blender scene report or anything else this tool generated.

**Course patterns.** The same rubric row coming out weak across more than one
graded assignment in the course, matched on the row's wording. One weak row is an
assignment; the same row weak three times is the course.

**What should I change?** The one button down there that costs anything. It hands
Claude exactly the evidence above — the item analysis, the students' own
sentences, and the grading rationales for the students who lost the most on the
weakest rows — and asks for a plan. What comes back is a headline, a ranked
reteach list where each item is tagged with its likely **cause** (not taught /
taught but not practised / the assignment was unclear / tooling and logistics)
and a concrete next-class action that fits in ten minutes, a list of changes to
the prompt or rubric wording, what clearly landed, and the one thing to watch in
the next assignment to tell whether the fix worked.

It refuses under three graded submissions: below that it is individual students,
not a pattern. Distinguishing "they did not learn it" from "the assignment did
not ask clearly" is the whole point, and the evidence usually says which — so
when the answer is that the prompt was at fault, it says so.

---

## Letter grades and curves

The insight charts show the letter distribution with **both** the count and the
share of the graded pool, and they keep empty bands: "no one got an A" is a
finding, not something to hide. The bands come from `grade_scale` in
`config.json` (`{"A": 90, "B": 80, "C": 70, "D": 60}` by default, anything below
the lowest cutoff is an F), so set it once to match your institution.

**Curve grades…** sits in the insights bar, and in the roster bar when you have
students selected. Everything is previewed before anything is written: you see
the class average before and after, the letter distribution shifting band by
band, and which students move — including which ones change letter.

| Curve | What it does | When |
|---|---|---|
| **Raise the average to…** | Everyone gains the same points, enough to move the mean to your target | A rubric criterion the class did badly on |
| **Square-root curve** | 49% → 70%, 90% → 95%: helps the bottom most, the top barely | The whole class did poorly across the board |
| **Lift everyone below…** | Nobody ends below your floor; students above it are untouched | Pulling a tail up off the floor |
| **Add a flat number of points** | Same points for everyone | A question that was unfair or mis-keyed |
| **Curve to the top score** | Adds what the best paper was missing to everyone | A hard assignment where the top is the ceiling |
| **Scale every score up by…** | Multiplies each score | Proportional adjustment |

Any of them can be aimed at **the whole score** or at **one rubric criterion** —
which is the point of the criterion option: if the class fell apart on one row of
the rubric and that is on your teaching rather than on them, you can raise that
row and leave the rest of the grade alone. And any of them can apply to the whole
class or to just the students you have selected.

Two guarantees hold throughout:

- **A curve never lowers anyone.** If you ask for something that would take
  points away — a target below the current average, a negative percentage — it
  does nothing and tells you how many students it would have affected, instead of
  quietly cutting grades.
- **The earned score is never overwritten.** Curves are stored as deltas beside
  the scores, so the student's own score stays visible ("earned 85.1, curved
  +4.6"), curves stack in the order you applied them, and **Remove curves** puts
  everything back exactly. A curve also survives a re-grade of an individual
  student, since it was a decision about the class, not about that run — but the
  *reviewed* flag does not, because that new score is one nobody has read yet.

Scores are capped at the criterion's points and at the assignment's points
possible, so a curve can never invent a score above full marks; the preview says
how many students hit that ceiling and therefore gained less than the rest.

The curved score is the one that gets pushed to Canvas, and the push plan
shows it alongside the earned score and the adjustment. The CSV export carries
`earned`, `curve`, `total`, `percent` and `letter` columns.

---

## Overlap check

Pick two or more students and the overlap check looks for wording their
submissions share. It runs in two stages.

**A local pass, no model involved.** Text is compared as six-word windows, after
masking out everything the group has in common: the assignment description, your
grading instructions, the rubric, and any wording that shows up in more than a
third of the selected submissions. So a class that all restated the prompt, or
all quoted the same line of the reading, does not light up. What survives is
wording these two submissions share and the others do not. Only the student's
own writing is compared — a `.blend` submission's extracted text is a scene
report *this tool* generated, identical across the class by construction, and
comparing it would flag everyone for something none of them typed. Students with
nothing written are listed as not compared, with the reason.

**Then, only if something stands out,** one Claude call reads the strongest pairs
and says what the overlap looks like, with the most likely innocent explanation
next to any reason for concern. If nothing crosses the threshold there is no
model call and no cost.

**What this is not.** It measures shared wording. It is not a plagiarism service
and not a finding of misconduct, and it is deliberately built so it cannot read
like one: no score, no verdict, no percentage of "originality". Students overlap
for ordinary reasons — the same source, a narrow prompt, collaboration you
allowed, the same tutorial. The output is a reading list with the passages
quoted, ordered by how much two submissions have in common, so that you read the
work yourself. Anything further belongs in your institution's process, with the
students present.

---

## Editing a test, and who gets longer on it

Clicking a test - from the schedule row's **edit**, or **Edit test...** in the
assignment reader - opens one menu rather than a wall of fields:

| | |
|---|---|
| **Dates and availability** | due, opens, closes |
| **Password and access** | quiz password, IP filter, published |
| **Timing and attempts** | time limit, attempts, keep highest/latest, one question at a time, shuffle |
| **What students see afterwards** | hide results, show correct answers, one-time results |
| **Accommodations** | who gets longer, and applying it |

Each row says what it currently holds, so the menu reads without opening
anything. Picking one replaces the body with just that concern - four fields and
a Save - and a back arrow returns. A quiz has fifteen settings across four
unrelated concerns; shown together they are a wall.

Only what actually moved is sent. A password is never echoed back, so the box
starts empty and an empty box means *untouched* - there is a **remove it**
button beside it for when you really do want the password gone. Illegal
combinations are refused before anything is sent, with the reason: a quiz that
opens after it closes, `0` attempts, locking each question when the quiz is not
one-at-a-time.

**Classic quizzes only.** New Quizzes and publisher tests (Pearson, ALEKS) keep
their settings in their own systems and no Canvas API reaches them, so the
**edit** button does not appear on those rows.

### Accommodations

Canvas takes extra time as **minutes**, never a multiplier. A student approved
for time-and-a-half needs +23 minutes on a 45-minute test, +18 on a 35-minute
one, +13 on a 25-minute one. Doing that by hand, per student, per test, per
course, is the chore this replaces.

The accommodation is stored the way the college grants it - *+50% time*, *+20
minutes*, *one extra attempt*, *can start while locked* - and the minutes are
worked out per test from that test's own limit, **rounded up**: an approval is a
floor, not a target.

Three scopes, from the same screen:

- **this test**
- **every quiz in this course** - for the blanket approvals
- **every course I teach**, this term - because a college approval applies
  everywhere, and this is one pass instead of dozens

**Student roster...** (on the schedule, or from the accommodations screen) pulls
every student across every course you teach, deduplicated. A Canvas user id is
the same person in every course, so one entry reaches all of them - and the list
lives in the data directory rather than in a course, so it survives the term.
Students repeat; their approvals do too.

Nothing is written blind. Every scope plans first, showing each student against
each test with the minutes it worked out, what Canvas already holds, and what it
is skipping: students not enrolled in that course, quizzes with no time limit,
anything already correct. Re-running is a no-op - one Canvas call per quiz, and
rows that already hold the right value are never re-sent.

The screen also surfaces **students who have an extension in Canvas but are not
on your list**. That is usually someone set up by hand a term ago; adding them
means it happens by itself from then on.

> **SmarterProctoring is separate.** Where an institution proctors through it, an
> extended-time approval has to be entered there as well - its accommodations
> live in SmarterServices, behind an API key only an institution admin can
> issue. This handles the Canvas half.

---

## Nothing reaches Canvas on one click

Every write is refused once. The server hands back a plain sentence saying what
would happen, plus a one-time token; the page shows the sentence, and only a
second click sends the token. This is not a `confirm()` in the browser - it is a
rule on the server, so it holds for a stale page, a replayed request, or a
button someone forgets to guard later.

The token is bound to a fingerprint of the request, which is what makes it worth
having:

- agreeing to one change cannot apply a different one - edit anything after the
  review screen and it asks again;
- a token is spent on use, so nothing can be replayed;
- a token expires after ten minutes.

Dates are shown back in your own timezone on that screen, not as the UTC
instants Canvas stores.

`allow_canvas_writes` is still in config.json and still a hard lock, but it now
defaults to **true**: the tool exists to change live courses, and a flag you
flip once is not a safety feature after the first day. Set it to `false` to bolt
the doors shut anyway - useful on a shared machine - and the drafting, planning
and export features all keep working.

---

## Pushing grades

Writing grades is gated twice:

1. The plan in the push dialog, which lists exactly what would be written, per
   student, and who is held back and why. It is worked out the moment the dialog
   opens, reads only, and cannot be skipped on the way to the write
2. The server's own confirmation — see
   [Nothing reaches Canvas on one click](#nothing-reaches-canvas-on-one-click) —
   which names the number of grades and whether students see them on arrival

Students flagged `needs_human` are skipped by the push until you clear them with
**Mark reviewed** (one student from their detail pane, or a whole selection from
the roster bar).

A push writes scores into the gradebook. Whether students **see** them is a
separate question, and Canvas answers it with the course's *posting policy*:

- **Automatic** — a student sees a grade the moment it is written.
- **Manual** — grades sit in the gradebook hidden until you press Post.

The plan tells you which applies ("LANDS: hidden until you make them live"),
and if the assignment is automatic the push dialog offers to switch it to manual
first. That switch is the difference between "push" meaning *save* and "push"
meaning *publish*.

---

## Two machines, one gradebook

Set the course to **manual posting** and Canvas becomes a save point. Push what
you have from the home PC — half a class graded, sliders still to check — and it
lands in the gradebook where no student can see it. Open the same assignment on
the work PC and it is waiting for you.

**Grade posting toggle.** On a course's assignment list, the header shows the
course posting policy with a dropdown to change it. Canvas applies a course
policy to every assignment in the course. Inside an assignment, a pill in the
header shows the *effective* policy for that assignment (an assignment can
override its course); click it to change just that one.

Switching **to automatic** releases every hidden grade at once. The
confirmation says so in those words before anything moves.

**Make live…** is Canvas's own Post button, from here. It lists who has a grade
in the gradebook, split into hidden and visible, and posts the hidden ones —
for the whole assignment, or for a selection from the roster bar. **Hide again**
is the reverse. Neither changes a score.

In the roster, `◌` beside a score means it is in Canvas but hidden; `●` means
the student can see it.

**Pull.** When you open an assignment, and then every `pull_interval_s` seconds
while it is open (default 120, `0` for off), the tool re-reads the gradebook side
of every student — score, rubric breakdown, whether it is posted — and merges it
into the local draft. Sync does the same on a fresh machine. This is one paged
call with no attachments, so it is cheap.

**It never overwrites work in progress.** Every entry remembers the score Canvas
held the last time this machine and Canvas agreed. A pull compares three things —
your local score, Canvas's score, and that baseline:

| Local | Canvas | Result |
|---|---|---|
| same as Canvas | — | in step; baseline refreshed |
| unchanged since baseline | moved | the other machine graded it: **taken** |
| moved since baseline | unchanged | your unpushed work: **kept**, silently |
| moved | moved (or no baseline yet) | **conflict**: both recorded, nothing changed |

Conflicts show a `!` in the roster and a **Conflicts** filter chip. The detail
pane shows both numbers with **Take Canvas** and **Keep mine**; the roster bar
does the same for a selection. *Keep mine* marks Canvas's score as acknowledged,
so the next pull is quiet and the next push overwrites it on purpose.

A grade pulled in is tagged *pulled from Canvas* and is treated like one you set
by hand: a re-grade will not replace it. If Canvas has a total with no rubric
breakdown (a number typed into the gradebook), the entry carries the total as-is
and says so; moving a slider replaces it with your rubric score.

One thing a pull will not adopt: a Canvas score for a student who **submitted
nothing**. That is almost always your missing-submission policy's zero, and
this tool keeps non-submissions out of the averages unless you ask (see
*Non-submissions are not zeros* above). The score still shows beside the
student ("Canvas: 0 · visible to student"); it just is not counted as a grade
here. A zero you set by hand for a non-submission is a scored entry and syncs
like any other.

The comparison is against the baseline, not against clocks, so two machines with
drifting clocks, or a grade typed straight into SpeedGrader, still resolve
correctly.

---

## Configuration

| Key | Default | Notes |
|---|---|---|
| `base_url` | `https://mgccc.instructure.com` | Your Canvas host |
| `token_path` | *(auto)* | Only if you keep the token somewhere unusual; see [step 2](#2-put-your-canvas-token-in-a-file). The token itself never goes in `config.json` |
| `data_dir` | `./data` | All cached student work and drafts |
| `port` | `8900` | Bound to `127.0.0.1` only |
| `enrollment_types` | `["teacher","ta"]` | Which enrollments to list |
| `excluded_courses` | `["Game Engine"]` | Hidden in the picker; matches id or name substring |
| `model` | `opus` | `opus`, `sonnet`, or `haiku` |
| `grading_concurrency` | `8` | Parallel Claude calls, 1-8 |
| `claude_timeout_s` | `600` | Per-student timeout |
| `humanize` | `true` | Run every comment, rationale, summary, teaching read, overlap triage, answer, announcement and inbox draft through the `/humanizer` skill before showing it. See [The second pass](#the-second-pass-the-humanizer) |
| `humanize_model` | `sonnet` | The editing model. Sonnet is enough; opus costs more for the same edit |
| `humanize_timeout_s` | `180` | Per-pass timeout |
| `humanize_skill_path` | *(auto)* | Empty means the newest of the refreshed copy, your installed `~/.claude/skills/humanizer/SKILL.md`, and the bundled copy. A path pins one file |
| `humanize_auto_update` | `true` | Fetch the current skill from its GitHub repository once a day and use it from the next grade on, with no click and no restart. `false` freezes the installed copy |
| `humanize_update_s` | `86400` | How often to check, in seconds. Never less than an hour |
| `pseudonymize` | `true` | See Privacy |
| `grade_scale` | `{"A":90,"B":80,"C":70,"D":60}` | Minimum percent per letter; below the lowest is an F |
| `schedule_max_age_min` | `30` | Opening the term schedule refreshes it from Canvas when the cache is older than this |
| `pull_interval_s` | `120` | How often an open assignment re-reads its Canvas grades; `0` turns the timer off |
| `assignment_max_age_min` | `15` | Opening an assignment re-syncs it when the local copy is older than this; `0` syncs on every open |
| `allow_canvas_writes` | `true` | A hard read-only lock. Every write is confirmed separately regardless; `false` blocks them entirely |

`CANVAS_TOKEN`, `CANVAS_BASE_URL` and `CANVAS_GRADER_PORT` override the file.

---

## Blender (.blend) submissions

For 3D modeling courses, a **Blender pass** button appears on any assignment with
`.blend` attachments. It opens each file headless and produces three things:

- **A scene report.** Poly counts, quad/tri/n-gon mix, non-manifold edges, loose
  geometry, unapplied scale and rotation per object, objects and materials still
  on Blender default names, missing texture files, UV coverage, modifier stacks,
  and the Blender version the file was saved with. This is measured, not asserted,
  and it flows into the grading prompt as text.
- **A contact sheet.** Five neutral Workbench renders (3/4, front, right, top,
  wireframe) composited into one image, attached to the grading call so the model
  can judge form and proportion. Workbench because a student scene with no lights
  renders black under EEVEE or Cycles, and a black tile looks the same as a broken
  model.
- **A glTF export**, shown in an orbitable 3D viewer in the review pane, with
  clay/normals/wireframe modes, a grid and axes for scale, and a triangle count.
  It loads automatically when you open a student, after a short delay so that
  arrowing down the roster does not fetch a model for everyone you pass. Anything
  over 25 MB still waits for a click.

The prompt states explicitly that the renders came from this tool, not the
student, and that lighting, background and framing must never be commented on or
deducted for. Without that, the model reliably critiques *your* renderer and the
text lands in a student's Canvas inbox.

Requires Blender on the machine. It is found automatically on PATH, in the
standard install directories, or via the Windows registry; set `blender_path` in
`config.json` to override. `python -m courseforge doctor` reports what it found.

**Safety.** A `.blend` can carry Python in drivers and handlers, and this opens
files submitted by students, so every invocation passes `--factory-startup` and
`--disable-autoexec`. Before anything is evaluated, image paths pointing at UNC
shares or outside the file's own folder are blanked (a texture path of
`\\attacker\share\x.png` would otherwise make Windows open an SMB session and leak
an NTLM hash), and Subdivision levels are clamped to 2 so a level-8 modifier
cannot exhaust memory. Blocked paths and clamped modifiers are reported, since
both are gradeable findings in their own right. The residual risk is a bug in
Blender's own parser, which is the same risk class as opening a student PDF.

## What it does not handle

- **Unity projects and compiled builds.** There is no sane way to grade a build
  from text; those courses are listed but their submissions are not analyzed.
- **Handwriting and scans.** Images are surfaced in the review pane and flagged
  for you, not guessed at.
- **Grading video.** Video plays in the review pane and is never sent to a model.
  A `.avi`, `.wmv` or `.mkv` -- or a `.mov` holding something a browser cannot
  decode, such as ProRes -- offers a download link instead of a player.
- **Images embedded in the Canvas text box.** Those live in the student's personal
  files rather than as submission attachments; the tool detects them and links you
  to SpeedGrader.
- **New Quizzes.** Canvas exposes them through a different API.

---


## License

MIT. See `LICENSE`.

Grades produced by this tool are drafts. You are the instructor of record; check
the work before it counts.
