# CourseForge Studio

One local web app for Canvas instructors. It grades, it makes courses
accessible, it builds content, and it runs the chores around a course, all from
one page on your own computer, using the Claude Code login you already have.

It grew out of two tools by the same author: **canvas-grader** (the review UI
and the safety model) and **CourseForge** (the ADA remediation toolkit, the
PDF/UA engine, the content builder, the Assistant). Both are superseded by this
repository. See [docs/MIGRATION.md](docs/MIGRATION.md) for what moved where.

---

## What it does

Open a course and you get six areas. Use any of them alone.

| Area | What it is for |
|---|---|
| **Grade** | Pulls an assignment's rubric and submissions, drafts a rubric-aligned grade and a two-sentence comment for every student with Claude, runs every word it wrote back through the `/humanizer` skill so it reads as a person wrote it, and gives you a side-by-side review before a single point reaches Canvas. Insights, curves, overlap check, a teaching read, quiz settings and accommodations, the term schedule. The manual is [docs/GRADING.md](docs/GRADING.md). |
| **Accessibility** | Brings an existing course up to ADA / Ally: restyles every page, assignment, discussion, quiz description and the syllabus with a gate that proves the visible text did not change, then pushes in place. Fixes PowerPoint and Word files (alt text, slide titles, header rows). Fixes PDFs with a PDF/UA-1 engine (OCR layer, tag tree, title and language, fonts) validated with veraPDF, describes figures with Claude, and uploads over the originals so links keep working. Edits text inside PDFs and Office files (a former instructor's name in every handout). Two jobs, named as such on the tab row: **Fix accessibility** (Pages, PowerPoint, Word, PDFs) makes a file usable by a screen reader; **Find and replace** (in PDFs, in Office files) changes the words inside one and has nothing to do with accessibility. |
| **Build** | Drafts a page, syllabus, assignment, graded discussion, quiz or study guide in the school's Canvas-safe template, checks it against the style rules, and places it in the module you choose, unpublished by default. Project manifests for whole modules. Rubrics. |
| **Tools** | Export a course to `.imscc`, import a cartridge, copy a course into a sandbox. Roll due dates to a new term from the academic calendar with a week-by-week table to approve. Trim the left navigation. Back up a quiz before rewriting it. Cross-reference the course against the state learning outcomes for its program. |
| **Assistant** | A Claude Code session for the course, in the browser. Say what you want in plain English. Ask about a student by name and it answers from what the Studio has graded on this machine, by tag, without a name ever leaving it. It drives the same verbs as the buttons, and every time it wants to change Canvas a card appears asking you to Allow or Deny. No answer means no. |
| **Reports** | Two things you show somebody else. An accessibility score with a before and an after, measured from the originals kept on this machine, so "this course went from 35 to 100" is a sentence you can say to a dean. And every syllabus checked against the statements your college requires -- ADA, academic integrity, attendance, Title IX -- as one table. Neither writes anything. |
| **Record** | Every change the Studio made in the course, written down as it happened: what, when, on whose account, and for which student where a student is the point. Each entry carries the fingerprint of the one before it, so an edit after the fact shows up. Kept in your own Canvas files as well as on the PC. The manual is [docs/RECORD.md](docs/RECORD.md). |

Three rules hold everywhere:

- **Nothing reaches Canvas on one click.** Every write is refused once by the
  server, shown to you as a plain sentence, and sent only when you confirm that
  exact change. Dry run first, always.
- **The tool proves before it pushes.** Restyled HTML must pass a visible-text
  comparison; fixed PDFs must survive an independent text and render check or
  they are deleted and queued for a person; documents that fail verification
  are never uploaded.
- **Student data stays on this machine** except the few blobs copied to your
  own Canvas user files so a second computer can pick them up. Type a real
  name anywhere a model is involved and it is swapped for a tag first --
  `S-001` while grading, `Student-1` in the Assistant -- and swapped back on
  the way to your screen. The list of who is who never leaves your computer.
  Every area except grading works through a Canvas client that cannot reach
  submissions, grades or rosters at all. Type `@` in the Assistant for the
  roster, so the spelling is never yours to get wrong, and a name typed a
  keystroke off is stopped and queried rather than sent as written. The
  details are in [docs/NAMES.md](docs/NAMES.md).

### What follows you to another PC

Logging into Canvas on a second computer is not the same as having Studio
state. A token still has to be pasted there, and Claude still has to be
signed in.

| Follows you | How |
|---|---|
| Posted grades, applied quiz extras, deadline overrides, Inbox threads, pushed pages | Live Canvas |
| Standing accommodations roster | Copied to **Files → courseforge-studio → state** |
| Instructor notes on a student | Same folder, `students-<id>.json` |
| Assistant chats | Same folder, `assistant-<course>.json` (tags, not names). The transcript follows you; Claude's session id does not resume on another PC |
| Unpublished Build drafts | Same folder, `build-<course>.json` |
| Grade drafts in progress | Only if you handed that assignment off (`canvas-grader/`) |
| Identity maps (`names.json`, `map.json`) | Never. Rebuilt from the live roster |
| Accessibility work in progress | This PC only |
| Record / audit chain | Uploaded as evidence; the other PC does not yet boot from it |
- **What it did is written down.** Every Canvas write goes into a chained,
  tamper-evident record kept in your own Canvas files, with accommodations and
  grades named by student. See [docs/RECORD.md](docs/RECORD.md).

---

## Requirements

- **Python 3.10+** (3.12 recommended)
- **Claude Code CLI**, installed and signed in: <https://claude.com/claude-code>
- A **Canvas API token** for your own account (the setup screen walks you through it)
- Optional, for the PDF fixer's OCR and compliance proof: **Tesseract**, **veraPDF** and a **Java** runtime.
  The first launch offers to install all three. Tesseract and Java come from
  winget; veraPDF has no package anywhere, so the Studio fetches the project's
  own installer, checks it against a known hash and runs it unattended into
  your user folder, where no administrator rights are needed. You can decline:
  everything else works without them, and the accessibility area says what is
  missing where it matters, with an Install button on the card.
  Later, `python -m courseforge tools` reports the same thing,
  `python -m courseforge tools --install --yes` does the install, and
  `python -m courseforge tools --ask-again` brings the first-run offer back.

## Updating

The launcher window checks once, in the background, each time it starts. If
there is a newer version it shows an **Update** button beside the title; if
there is not, the version line just says *up to date*. Nothing downloads, and
nothing changes, until that button is pressed.

Pressing it shows what changed since your copy, and **Update now** fetches the
new version and writes it into the install folder. Then **Restart now** starts
a fresh copy, because the running one is still using the code it loaded at
startup.

What an update never touches:

* `config.json` — your port, models and settings;
* `data/` — every draft, extracted submission and record;
* your Canvas token, which lives outside the folder entirely;
* `.git`, if this copy is a checkout.

It also refuses to run at all on a checkout with uncommitted changes, and says
which files are in the way. Use `git pull` there instead.

If the update source is unreachable — no network, or the repository is private
and this copy has no credential — the window says so on the version line and
carries on. The check never blocks startup and never raises.

### Settings

| Key | Default | What it does |
|---|---|---|
| `check_updates` | `true` | Whether the launcher looks at all |
| `update_repo` | `billathekilla737/CourseForge-Studio-app` | Pinned. Any other value is refused |
| `update_branch` | `main` | Which branch counts as current |

A colleague who was handed a folder needs none of this: the source is public,
so the button works with no GitHub account, no git, and no terminal.

### Publishing a new version

The source is the public repository at
[billathekilla737/CourseForge-Studio-app](https://github.com/billathekilla737/CourseForge-Studio-app).
That is what every install checks. Pushing `main` publishes:

```bash
git push origin main
```

Nothing else is needed -- no tags, no release notes, no uploaded files: the
button reads the commit subjects between the user's version and the branch head.

The public repository must never carry student data, and neither must its
history. `config.json`, `data/` and every token are gitignored, which is what
keeps that true.

## Setup

```bash
git clone https://github.com/billathekilla737/CourseForge-Studio-app
cd CourseForge-Studio-app
pip install -r requirements.lock
pip install -e .
```

`requirements.lock` pins hashes for the libraries that parse student files. Re-check with `python -m pip_audit -r requirements.lock` when you bump them.

Someone who does not use git can instead take
[the latest zip](https://github.com/billathekilla737/CourseForge-Studio-app/archive/refs/heads/main.zip),
unpack it anywhere, and run `CourseForge Studio.vbs`. Every update after that
is the button in the launcher window.

Copy the example config (only `base_url` needs changing if you are not at MGCCC):

```bash
cp config.example.json config.json
```

On Windows PowerShell: `Copy-Item config.example.json config.json`

Your Canvas token does **not** go in that file. Start the app and it will show a
**Connect to Canvas** button; paste the token there. It is checked against
Canvas, then stored encrypted under your user profile
(`%APPDATA%\CourseForge-Studio\canvas.token.enc`, readable only by your Windows
account on this machine). A token saved by the old canvas-grader or CourseForge
tools is found and migrated the first time you save.

Check everything:

```bash
python -m courseforge doctor
```

To prove the whole thing works without touching a live course, work through
[docs/TEST-PLAN.md](docs/TEST-PLAN.md): preflight commands, a safety check
that nothing writes without a confirmation, and a numbered walk through every
area with what each step should show.

## Running it

**Windows, no terminal:** double-click **`CourseForge Studio.vbs`**. A small
window says it is running, with a button that opens the app in your browser.
Closing that window stops the server and any Claude work in flight.

**From a terminal:**

```bash
python -m courseforge serve      # http://127.0.0.1:8900
```

Run it from a normal terminal, not from inside a Claude Code session, so the
Claude CLI can see your login.

## From the command line

Every area's verbs are also commands, which is what the Assistant runs:

```bash
python -m courseforge a11y dump --course 734391
python -m courseforge a11y restyle --course 734391 --look clean
python -m courseforge a11y verify --course 734391
python -m courseforge a11y push --course 734391            # dry run
python -m courseforge a11y push --course 734391 --apply    # asks for a typed yes
python -m courseforge pdf fix --course 734391
python -m courseforge course due-dates --course 734391
python -m courseforge record --course 734391 --verify
python -m courseforge students show --course 734391 --who Student-14
python -m courseforge --help
```

A verb that writes to Canvas does nothing without `--apply`, and prints the plan either way.

---

## Configuration

`config.json` keys are documented inline in
[config.example.json](config.example.json). The ones people change:

| Key | Default | Notes |
|---|---|---|
| `base_url` | `https://mgccc.instructure.com` | Your Canvas host |
| `model` | `opus` | Grading model (`opus`, `sonnet`, `haiku`) |
| `describe_model` | `sonnet` | Alt text and image descriptions |
| `a11y_look` | `clean` | Restyle look; `clean` scores zero advisory flags in Ally |
| `pdf_jobs` | `0` | PDF engine worker processes; 0 = CPU count minus 2 |
| `assistant_ask_timeout_s` | `1200` | An unanswered Allow/Deny is a Deny after this long |
| `allow_canvas_writes` | `true` | Hard read-only lock when `false` |
| `pseudonymize` | `true` | Swap student names for tags before anything reaches a model |
| `audit_to_canvas` | `true` | Keep the record of actions in your own Canvas user files |
| `llm_backend` | `cli` | `api` switches to an API key (hosted build; see below) |

`CANVAS_TOKEN`, `CANVAS_BASE_URL` and `CANVAS_GRADER_PORT` override the file.

---

## Layout

```
CourseForge Studio.vbs   double-click launcher (hidden console)
run.cmd                  terminal launcher
courseforge/             the app
  server.py              local HTTP API, background jobs, the confirm gate
  routing.py, areas.py   how the areas plug in
  canvas*.py             Canvas client; canvas_policy.py decides what each area may touch
  llm.py                 one seam for every model call (claude_cli.py locally, claude_api.py hosted)
  secrets.py             encrypted token storage
  identity.py            real names in, Student-N out, for the Assistant
  audit.py               the chained record of what was done, and its Canvas copy
  a11y/ docs/ pdf/ content/ courseops/ assistant/   the areas
  record/                the record of actions
  hub/                   the course hub
  knowledge/             the remediation, style and Canvas-API knowledge the tool and the Assistant follow
  web/                   the UI (vanilla JS, no build step)
skill/                   the Claude Code skill the Assistant loads
docs/                    manuals, contracts, the school deployment plan
legacy/                  the PowerShell verbs and desktop apps this replaced
tests/                   python -m unittest discover tests
data/                    everything downloaded or produced (gitignored)
```

## Hosting it for a school

The local build uses your Claude Code login and stores work on your machine. A
school-hosted build swaps in an API key, Postgres, single sign-on and a worker
queue behind the same code. The design, the data model, the security controls
and a phased rollout are in [docs/DEPLOYMENT-SCHOOL.md](docs/DEPLOYMENT-SCHOOL.md).

## Documentation

- [docs/GRADING.md](docs/GRADING.md): the grading manual
- [docs/RECORD.md](docs/RECORD.md): the record of actions, and how to check it
- [docs/NAMES.md](docs/NAMES.md): what is swapped before anything reaches a model
- [docs/UI-DESIGN.md](docs/UI-DESIGN.md): how the UI is organised
- [docs/AREA-CONTRACT.md](docs/AREA-CONTRACT.md) and [docs/FRONTEND-CONTRACT.md](docs/FRONTEND-CONTRACT.md): how to add an area
- [docs/MIGRATION.md](docs/MIGRATION.md): from canvas-grader and CourseForge
- [docs/DEPLOYMENT-SCHOOL.md](docs/DEPLOYMENT-SCHOOL.md): the hosted build
- [docs/THIRD-PARTY-NOTICES.md](docs/THIRD-PARTY-NOTICES.md): licences of the PDF tools (PyMuPDF is AGPL; read before redistributing)
- `courseforge/knowledge/`: the ADA remediation playbook, the style guide, the Canvas API silent-failure catalogue, the academic calendar, SLO alignment

## Licence

MIT for this repository's own source. See [LICENSE](LICENSE) and the third-party notices.

Grades produced by this tool are drafts, and course changes are yours to
confirm. You are the instructor of record.
