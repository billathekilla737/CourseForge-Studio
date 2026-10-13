# CourseForge Studio

An app for Canvas instructors that runs on your own computer. It uses Claude (AI)
to help you grade, make your course accessible, and build course content.

## What it does

| Tab | What it's for |
|---|---|
| **Grade** | Claude drafts a grade and a short comment for each student, using your rubric. You review everything before it posts. |
| **Accessibility** | Fixes pages, PowerPoints, Word files and PDFs to meet ADA and Ally standards. |
| **Build** | Drafts pages, assignments, quizzes and study guides, then places them in a module. |
| **Tools** | Moves due dates to a new term, backs up or copies a course, and tidies the course menu. |
| **Assistant** | Ask for what you want in plain English. It asks before it changes anything. |
| **Attendance** | Mark who was here, late, or absent. |
| **Record** | A log of every change the app makes in your course. |
| **Reports** | A before-and-after accessibility score, and a check that each syllabus has the statements your college requires. |

## Is it safe?

- **You approve every change.** Nothing changes in your Canvas course until you confirm it.
- **Student names stay private.** Names are swapped for codes like `Student-1` before anything goes to the AI. The list of who's who never leaves your computer.

## What you need

- A Windows PC
- [Python 3.10 or newer](https://www.python.org/downloads/). When installing, check **Add Python to PATH**.
- [Claude Code](https://claude.com/claude-code), installed and signed in
- A Canvas access token (the app shows you how to get one)

## Install (one time)

1. [Download the ZIP](https://github.com/billathekilla737/CourseForge-Studio/archive/refs/heads/main.zip) and unzip it anywhere.
2. Open the folder that has **CourseForge Studio.vbs** in it.
3. Click the folder's address bar, type `cmd`, and press **Enter**. A black window opens.
4. Paste this line into it and press **Enter**:

   ```
   python -m pip install -r requirements.lock
   ```

## Start it

1. Double-click **CourseForge Studio.vbs**.
2. In the small window that opens, click **Open CourseForge Studio**.
3. First time only: click **Connect to Canvas** and paste your token.

Closing the small window shuts the app down.

**Updates:** when a new version is out, an **Update** button appears in that small window. Your settings and work are kept.

**Something not working?** Open the black window again (Install, step 3) and run `python -m courseforge doctor`. It checks your Canvas token, your Claude login and the optional tools.

## Learn more

- [Grading guide](docs/GRADING.md)
- [How student names are protected](docs/NAMES.md)
- [The Record, and how to check it](docs/RECORD.md)
- [Everything else: setup from git, command line, settings, developer notes](docs/ADVANCED.md)

---

MIT License. See [LICENSE](LICENSE) and the [third-party notices](docs/THIRD-PARTY-NOTICES.md).

Grades it produces are drafts, and course changes are yours to confirm. You are the instructor of record.
