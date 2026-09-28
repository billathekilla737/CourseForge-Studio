# Third-party notices

CourseForge's own source is MIT licensed (see `LICENSE`). The **CourseForge
PDF Fixer installer** is different: it is a bundle that redistributes several
third-party programs so the app needs no separate installs. Those programs
keep their own licences, and some of those licences place obligations on
whoever distributes the installer. This file records what is inside and what
those obligations are.

> **Read this before publishing installer binaries.** Publishing the source
> under MIT is unencumbered. Publishing the *installer* redistributes GPL'd
> software, and item 1 below carries a source-offer obligation.

---

## Bundled as separate executables inside the installer

### 1. veraPDF — GPLv3 **or** MPLv2 (dual licensed) — ⚠️ action required

| | |
|---|---|
| Version | 1.30.2 (`verapdf/bin/cli-1.30.2.jar`; SHA-256 of the shipped files in `installer/bundled-tools.json`) |
| Home | https://verapdf.org/ |
| Source | https://github.com/veraPDF |
| Licence | GNU GPL v3 **or** Mozilla Public Licence v2, at the recipient's option |

Used unmodified, invoked as a separate process (`verapdf.bat`) by the "Prove
compliance" step. It is not linked into CourseForge code.

**What distributing the installer requires.** Choose the MPLv2 arm, which is
the lighter of the two and is satisfied by distribution as an unmodified
separate program:

- [ ] Ship the full text of MPLv2 (and GPLv3) inside the installer, e.g.
      `licenses\verapdf\`, and keep this notice with it.
- [ ] State that veraPDF is unmodified and name where to get its source
      (the GitHub URL above satisfies the MPLv2 source-availability term).
- [ ] Do **not** describe the installer as "MIT licensed" as a whole. The
      right phrasing is: *CourseForge is MIT; the installer also contains
      third-party software under its own licences — see this file.*

If instead the GPLv3 arm is ever relied on (e.g. veraPDF gets patched), the
obligation escalates to offering complete corresponding source for the
modified version.

### 2. Tesseract OCR — Apache-2.0

| | |
|---|---|
| Version | 5.4.0.20240606 (with leptonica 1.84.1) |
| Home | https://github.com/tesseract-ocr/tesseract |
| Licence | Apache License 2.0 |

Used unmodified, invoked as a separate process for the OCR text layer.
Bundled `tessdata` language files (`eng`) are Apache-2.0.

- [ ] Ship the Apache-2.0 text and Tesseract's `NOTICE` in
      `licenses\tesseract\`.

Its own bundled image libraries (libgif, libjpeg-turbo, libpng, libtiff,
zlib, libwebp, libopenjp2) carry permissive licences that require only
attribution; Tesseract's own NOTICE covers them.

### 3. Eclipse Temurin JRE 21 — GPLv2 with Classpath Exception

| | |
|---|---|
| Version | 21.0.12.1+1-LTS (Eclipse Adoptium) |
| Home | https://adoptium.net/ |
| Licence | GPL v2 **with** the Classpath Exception |

Bundled solely to run veraPDF. Used unmodified. The Classpath Exception is
what makes it safe to ship alongside non-GPL software without that software
becoming GPL.

- [x] The JRE's own `NOTICE` file ships inside `jre/` already.
- [ ] Also place the GPLv2+CPE text in `licenses\temurin\`.

---

## Python libraries frozen into the executable

Bundled by PyInstaller into `courseforge-pdf.exe`. All permissive; attribution
is the only obligation.

| Library | Version | Licence |
|---|---|---|
| PyMuPDF (`pymupdf`) | 1.28.2 | **AGPL-3.0** or commercial — see below |
| pikepdf | 10.12.0 | MPL-2.0 |
| pypdf | 6.14.2 | BSD-3-Clause |
| CustomTkinter | 6.0.0 | MIT (its package metadata says CC0-1.0; both permissive) |
| fontTools | 4.63.0 | MIT |
| Pillow | 12.3.0 | MIT-CMU |
| PyInstaller (build tool) | 6.10.0 | GPLv2+ with an exception permitting proprietary frozen apps |

### ⚠️ PyMuPDF is AGPL-3.0 — the biggest open question here

PyMuPDF and its MuPDF core are **AGPL-3.0 or a paid commercial licence** — the
installed package's own metadata reads `Dual Licensed - GNU AFFERO ...`.
Unlike veraPDF, PyMuPDF is *imported directly* by `pdf_fastlane.py`, so the
AGPL's copyleft plausibly reaches CourseForge's own code when the combined
work is distributed.

Practical readings, in order of least effort:

1. **Internal use only** — distributing the installer to MGCCC staff for
   institutional use is arguably not "distribution to the public", and the
   AGPL's network clause is not triggered (no server). This is the current
   deployment and is the lowest-risk status quo.
2. **Publish source under AGPL-3.0 instead of MIT** — fully compliant, but
   changes the licence of this repo and is contagious for anyone reusing it.
3. **Buy an Artifex commercial licence** for PyMuPDF/MuPDF — removes the
   question entirely; costs money.
4. **Replace PyMuPDF** with pikepdf (MPL-2.0) plus a permissively licensed
   renderer. A significant rewrite: PyMuPDF does the OCR rasterisation,
   figure clip-rendering and the render-diff verification.

- [ ] Decide between 1–4 **before publishing installer binaries publicly**.
      Publishing the *source* repo under MIT while it imports an AGPL library
      is the combination most likely to be challenged.

---

## Bundled as text inside the Studio (no build step involved)

### Humanizer skill — MIT

| | |
|---|---|
| Version | 2.7.0 (`courseforge/knowledge/humanizer.md`, a verbatim copy of the skill's `SKILL.md`) |
| Source | https://github.com/blader/humanizer |
| Licence | MIT, copyright (c) 2025 Siqi Chen |

The editor's brief for the second pass over everything the grader writes
(`courseforge/humanize.py`). It is a Markdown prompt, not code, and it is not
modified: the file is byte-for-byte the upstream `SKILL.md`, frontmatter
included, and `humanize.py` strips the frontmatter when it reads it. When the
person has the skill installed in `~/.claude/skills/humanizer/`, that copy is
used instead and this one is the fallback.

The MIT licence asks that the copyright and permission notice travel with the
copy, so here it is:

> MIT License. Copyright (c) 2025 Siqi Chen.
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions: The above copyright
> notice and this permission notice shall be included in all copies or
> substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
> WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED
> TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
> FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
> TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR
> THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Nothing to do: MIT places no obligation on distribution beyond this notice.

---

## Summary of what is still to do

| Item | Blocking public binaries? | Blocking a source-only push? |
|---|---|---|
| veraPDF licence texts + source pointer | Yes | No |
| Tesseract licence + NOTICE | Yes | No |
| Temurin GPLv2+CPE text | Yes | No |
| PyMuPDF AGPL decision | Yes | Worth deciding first |
| Installer described accurately (not "MIT") | Yes | No |

A source-only push to GitHub (no `installer/Output/`, no bundled tools, no
`.exe`) clears everything except the PyMuPDF question. `.gitignore` already
excludes all of that build output.

*This file records licence facts and obligations; it is not legal advice. For
anything beyond internal MGCCC use, have the college review it.*
