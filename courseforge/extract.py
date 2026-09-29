"""Turn whatever a student submitted into text and pictures a model can grade.

Handles Canvas RCE HTML, Word (.docx, including text boxes), PowerPoint
(.pptx), Excel (.xlsx), PDF (text, or page images when it is a scan), plain
text, code, and screenshots (.png, .jpg, .jpeg, .webp, .gif, and the rest of
IMAGE_EXT). Video is reported so the instructor can watch it; it is not sent
to a model.
"""
from __future__ import annotations

import html as htmllib
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote_plus, urlparse

from .canvas_policy import file_url_allowed
from .htmlclean import clean as clean_html

IMAGE_EXT = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp",
             ".heic", ".bmp", ".tif", ".tiff"}
# What the grader can hand to Claude without a conversion step.
VISION_EXT = {".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".webp", ".gif"}
# Screen recordings and phone video. Nothing reads these: they are handed to the
# browser to play, and the instructor watches them. Deliberately not sent to a
# model -- a minute of video costs many times what a whole essay does.
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".webm", ".avi", ".mkv", ".mpg", ".mpeg",
             ".wmv", ".ogv", ".3gp", ".mts", ".flv"}
# What a browser will actually play. The rest still gets a card and a download
# link, because "your player cannot open this" beats a blank panel.
VIDEO_PLAYABLE = {".mp4", ".m4v", ".webm", ".ogv", ".mov"}
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".rtf"}
# Source files are plain text and students submit them constantly. Reading them
# as text beats reporting "no extractor" and grading an empty submission.
CODE_EXT = {
    ".cs", ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".kt", ".c", ".h",
    ".cpp", ".hpp", ".cc", ".m", ".swift", ".go", ".rs", ".rb", ".php", ".lua",
    ".shader", ".hlsl", ".glsl", ".sql", ".html", ".htm", ".css", ".scss",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".ps1", ".bat",
    ".gd", ".unity", ".prefab", ".asmdef",
}


@dataclass
class Extracted:
    label: str
    text: str = ""
    kind: str = "text"          # text | image | video | binary | error | blend
    path: str = ""
    note: str = ""
    # Structured payload for kinds the UI renders specially (a .blend scene report
    # and its artifact paths, for instance). Ignored for plain text.
    data: dict = field(default_factory=dict)

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class Submission:
    parts: list[Extracted] = field(default_factory=list)

    def add(self, part: Extracted) -> None:
        self.parts.append(part)

    @property
    def text(self) -> str:
        # "blend" is included deliberately: a Blender scene report is text the
        # model should grade from, and widening here means it flows into
        # entry["text"], build_prompt and past grade_one's auto-skip for free.
        chunks = [f"--- {p.label} ---\n{p.text}" for p in self.parts
                  if p.kind in ("text", "blend") and p.text.strip()]
        return "\n\n".join(chunks).strip()

    @property
    def words(self) -> int:
        return len(self.text.split())

    @property
    def images(self) -> list[Extracted]:
        return [p for p in self.parts if p.kind == "image"]

    @property
    def videos(self) -> list[Extracted]:
        return [p for p in self.parts if p.kind == "video"]

    @property
    def unreadable(self) -> list[Extracted]:
        return [p for p in self.parts if p.kind in ("binary", "error")]


# --------------------------------------------------------------------- HTML
def html_to_text(raw: str | None) -> str:
    """Canvas rich-content HTML -> readable text, keeping table and list shape."""
    if not raw:
        return ""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.S | re.I)
    t = re.sub(r"<li[^>]*>", "\n  - ", t, flags=re.I)
    t = re.sub(r"<tr[^>]*>", "\n| ", t, flags=re.I)
    t = re.sub(r"</t[dh]>", " | ", t, flags=re.I)
    t = re.sub(r"<(br|/p|/div|/h[1-6]|/table)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = htmllib.unescape(t).replace("\xa0", " ")
    # Collapse runs of spaces inside a line but keep leading indentation. Students
    # paste code into the Canvas editor, and flattening every run of whitespace
    # destroys the structure that makes it readable.
    lines = []
    for line in t.split("\n"):
        indent = re.match(r"[ \t]*", line).group(0)
        lines.append((indent + re.sub(r"[ \t]+", " ", line.strip())).rstrip())
    t = "\n".join(lines)
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def count_inline_images(raw: str | None) -> int:
    return len(re.findall(r"<img\b", raw or "", flags=re.I))


# Canvas file references embedded in the rich-text editor, e.g.
#   <a href=".../users/USER_ID/files/FILE_ID?verifier=abc">Mover.cs</a>
#   <img src=".../users/USER_ID/files/FILE_ID/preview?verifier=abc" alt="x.png">
# These are NOT submission attachments; they live in the student's personal
# files, and the verifier in the query string is what grants access to them.
# Two patterns rather than one with an optional </a>: a combined pattern lets an
# <img> match run on and swallow a following <a>, silently losing the link.
_RCE_LINK = re.compile(
    r"""<a\b[^>]*?href\s*=\s*["'](?P<url>[^"']*?/files/(?P<fid>\d+)[^"']*)["'][^>]*?>"""
    r"""(?P<text>.*?)</a>""",
    re.I | re.S,
)
_RCE_IMG = re.compile(
    r"""<img\b[^>]*?src\s*=\s*["'](?P<url>[^"']*?/files/(?P<fid>\d+)[^"']*)["'][^>]*?>""",
    re.I | re.S,
)
_ALT = re.compile(r"""\balt\s*=\s*["']([^"']*)["']""", re.I)
_VERIFIER = re.compile(r"[?&]verifier=([A-Za-z0-9._-]+)")


def rce_file_refs(raw: str | None, base_url: str = "",
                  canvas_hosts: list[str] | None = None) -> list[dict]:
    """Find Canvas files linked or embedded in a submission body.

    Returns dicts of {file_id, url, name, tag}. `url` is rewritten to the
    /download form with the verifier preserved, which is what actually returns
    bytes; the plain link returns Canvas's file preview page instead.

    Only https URLs on the configured Canvas host, `canvas_hosts`, or a known
    Canvas file CDN are kept. A body that points at `https://evil.example/files/1`
    is ignored, not downloaded onto the instructor's PC.
    """
    out: list[dict] = []
    seen: set[str] = set()
    candidates = [("a", m) for m in _RCE_LINK.finditer(raw or "")]
    candidates += [("img", m) for m in _RCE_IMG.finditer(raw or "")]

    for tag, match in candidates:
        fid = match.group("fid")
        if fid in seen:
            continue
        seen.add(fid)
        url = htmllib.unescape(match.group("url")).strip()
        parsed = urlparse(url)
        # Protocol-relative //host/files/1 is an off-host URL, not a site-root path.
        if parsed.netloc and not parsed.scheme:
            parsed = urlparse("https:" + url)
            url = parsed.geturl()

        name = ""
        if tag == "a":
            name = htmllib.unescape(re.sub(r"<[^>]+>", "", match.group("text"))).strip()
        if not name:
            alt = _ALT.search(match.group(0))
            name = htmllib.unescape(alt.group(1)).strip() if alt else ""
        name = unquote_plus(name or "").strip() or f"file_{fid}"

        verifier = _VERIFIER.search(url)
        path = parsed.path or url.split("?")[0]
        stem = path.rstrip("/")
        stem = re.sub(r"/(preview|download)$", "", stem)
        if parsed.scheme and parsed.netloc:
            download = f"{parsed.scheme}://{parsed.netloc}{stem}/download?download_frd=1"
        else:
            download = stem + "/download?download_frd=1"
        if verifier:
            download += "&verifier=" + verifier.group(1)
        if download.startswith("/") and base_url:
            download = base_url.rstrip("/") + download

        if not file_url_allowed(download, base_url, canvas_hosts):
            continue

        out.append({"file_id": fid, "url": download, "name": name, "tag": tag})
    return out


# --------------------------------------------------------------------- DOCX
def docx_to_text(path: Path) -> str:
    """Read a .docx without python-docx so text boxes and shapes are included.

    python-docx walks only body paragraphs and tables; a lot of student work
    lives in text boxes, which makes documents look nearly empty. Pulling <w:t>
    from the raw XML in paragraph order catches everything.
    """
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist()
                 if n == "word/document.xml" or n.startswith("word/header")
                 or n.startswith("word/footer")]
        if "word/document.xml" not in names:
            raise ValueError("not a Word document (no word/document.xml)")
        xml = zf.read("word/document.xml").decode("utf-8", "replace")

    lines: list[str] = []
    for para in re.split(r"</w:p>", xml):
        runs = re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", para, flags=re.S)
        if not runs:
            continue
        line = htmllib.unescape("".join(runs)).strip()
        if line:
            # A table row in docx XML is a <w:tr> holding several <w:p>; marking
            # rows keeps grids legible once they are flattened to text.
            prefix = "| " if "<w:tc>" in para or "<w:tcPr" in para else ""
            lines.append(prefix + line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------------------------------------------------------------------- PDF
def pdf_to_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        raise RuntimeError("pypdf is not installed -- run: pip install pypdf") from None
    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(pages)).strip()


def pdf_page_images(path: Path, max_pages: int = 6) -> list[Path]:
    """Render a scanned PDF to PNGs so the grader can see the pages.

    Needs PyMuPDF. When it is not installed, returns nothing and the caller
    keeps the 'no extractable text' note.
    """
    try:
        import pymupdf
    except ImportError:
        return []
    out: list[Path] = []
    folder = path.parent / (path.stem + "-pages")
    try:
        doc = pymupdf.open(str(path))
    except Exception:  # noqa: BLE001
        return []
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(doc):
            if index >= max_pages:
                break
            pix = page.get_pixmap(dpi=110)
            target = folder / f"page-{index + 1}.png"
            pix.save(str(target))
            out.append(target)
    except Exception:  # noqa: BLE001
        return out
    finally:
        doc.close()
    return out


# -------------------------------------------------------------------- OOXML
_OFFICE_MEDIA = {
    ".docx": "word/media/",
    ".pptx": "ppt/media/",
    ".xlsx": "xl/media/",
    ".xlsm": "xl/media/",
}
_MEDIA_EXT = {".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".gif", ".webp", ".bmp", ".tif", ".tiff"}


def _xml_texts(xml: str, tag: str) -> list[str]:
    return [htmllib.unescape(t).strip()
            for t in re.findall(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", xml, flags=re.S)
            if t and t.strip()]


def pptx_to_text(path: Path) -> str:
    """Slide text in order, then speaker notes. Text boxes included."""
    lines: list[str] = []
    with zipfile.ZipFile(path) as zf:
        slides = [n for n in zf.namelist()
                  if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        slides.sort(key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        for index, name in enumerate(slides, start=1):
            bits = _xml_texts(zf.read(name).decode("utf-8", "replace"), "a:t")
            if bits:
                lines.append(f"Slide {index}")
                lines.extend(bits)
        notes = [n for n in zf.namelist()
                 if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)]
        notes.sort(key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        for index, name in enumerate(notes, start=1):
            bits = _xml_texts(zf.read(name).decode("utf-8", "replace"), "a:t")
            # The first runs are usually the slide number placeholder.
            bits = [b for b in bits if b and not b.isdigit()]
            if bits:
                lines.append(f"Notes {index}")
                lines.extend(bits)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def xlsx_to_text(path: Path, max_sheets: int = 8, max_rows: int = 250,
                 max_cols: int = 30) -> str:
    """Cell text from an .xlsx, sheet by sheet. Formulas keep their cached value."""
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            xml = zf.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            for item in re.split(r"</si>", xml):
                bits = _xml_texts(item, "t")
                if bits or "<si" in item:
                    shared.append("".join(bits))
        sheets = [n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
        sheets.sort(key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        titles = _sheet_titles(zf, names)
        blocks: list[str] = []
        for index, name in enumerate(sheets[:max_sheets]):
            title = titles[index] if index < len(titles) else f"Sheet {index + 1}"
            rows = _sheet_rows(zf.read(name).decode("utf-8", "replace"), shared,
                               max_rows, max_cols)
            if rows:
                blocks.append(title + "\n" + "\n".join(rows))
    return "\n\n".join(blocks).strip()


def _sheet_titles(zf: zipfile.ZipFile, names: set[str]) -> list[str]:
    if "xl/workbook.xml" not in names:
        return []
    xml = zf.read("xl/workbook.xml").decode("utf-8", "replace")
    return [htmllib.unescape(n) for n in re.findall(r'<sheet\b[^>]*\bname="([^"]+)"', xml)]


def _sheet_rows(xml: str, shared: list[str], max_rows: int, max_cols: int) -> list[str]:
    rows: list[str] = []
    for chunk in re.split(r"</row>", xml)[:max_rows + 1]:
        if "<c " not in chunk and "<c>" not in chunk:
            continue
        cells: list[str] = []
        for cell in re.findall(r"<c\b([^>]*)>(.*?)</c>", chunk, flags=re.S)[:max_cols]:
            attrs, body = cell
            kind = ""
            match = re.search(r'\bt="([^"]+)"', attrs)
            if match:
                kind = match.group(1)
            value = ""
            if kind == "s":
                raw = re.search(r"<v>(.*?)</v>", body)
                if raw:
                    try:
                        value = shared[int(raw.group(1))]
                    except (ValueError, IndexError):
                        value = raw.group(1)
            elif kind == "inlineStr":
                value = "".join(_xml_texts(body, "t"))
            else:
                raw = re.search(r"<v>(.*?)</v>", body)
                value = htmllib.unescape(raw.group(1)) if raw else "".join(_xml_texts(body, "t"))
            cells.append(value.replace("\n", " ").strip())
        if any(cells):
            rows.append(" | ".join(cells).rstrip(" |"))
            if len(rows) >= max_rows:
                rows.append("…")
                break
    return rows


def embedded_images(path: Path, max_images: int = 8) -> list[Path]:
    """Screenshots pasted into a Word, PowerPoint or Excel file."""
    prefix = _OFFICE_MEDIA.get(path.suffix.lower())
    if not prefix:
        return []
    out: list[Path] = []
    folder = path.parent / (path.stem + "-media")
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return []
    with zf:
        members = [n for n in zf.namelist()
                   if n.startswith(prefix) and Path(n).suffix.lower() in _MEDIA_EXT]
        for name in members:
            info = zf.getinfo(name)
            if info.file_size < 4096 or info.file_size > 15_000_000:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            target = _vision_copy(folder / Path(name).name, zf.read(name))
            if target:
                out.append(target)
            if len(out) >= max_images:
                break
    return out


def _vision_copy(dest: Path, raw: bytes) -> Path | None:
    """Write image bytes as a PNG or JPEG Claude can read."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ext = dest.suffix.lower()
    if ext in VISION_EXT and ext not in {".bmp", ".tif", ".tiff", ".heic"}:
        dest.write_bytes(raw)
        return dest
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            target = dest.with_suffix(".png")
            im.convert("RGB").save(target, "PNG")
        return target
    except Exception:  # noqa: BLE001
        return None


def _prepare_image(path: Path) -> Path:
    """PNG/JPEG stay as they are. BMP, TIFF and HEIC become a PNG beside them."""
    if path.suffix.lower() in VISION_EXT:
        return path
    try:
        prepared = _vision_copy(path.with_name(path.stem + "-vision.png"), path.read_bytes())
        return prepared or path
    except OSError:
        return path


# --------------------------------------------------------------------- file
ZIP_DOCUMENT_EXT = (".docx", ".pptx", ".xlsx", ".xlsm")
_TRANSIENT = ("BadZipFile", "EOFError", "PermissionError", "OSError")


def extract_file(path: Path, label: str | None = None) -> Extracted:
    """One file to text, with one retry for a document that was not ready.

    A Word file read while something else was still writing it (another sync,
    a cloud-sync client, an antivirus scan holding it) comes back "not a zip
    file" and stays that way in the record, and the student is graded from
    whatever else was in the submission. Waiting a second and reading again
    settles that case; a file that is truly broken fails twice and is reported
    as before.
    """
    first = _extract_file_once(path, label)
    if (first.kind == "error" and path.suffix.lower() in ZIP_DOCUMENT_EXT
            and first.note.split(":")[0] in _TRANSIENT):
        time.sleep(1.0)
        second = _extract_file_once(path, label)
        if second.kind != "error":
            return second
        second.note = second.note + " (read twice, one second apart)"
        return second
    return first


def _extract_file_once(path: Path, label: str | None = None) -> Extracted:
    label = label or path.name
    ext = path.suffix.lower()
    try:
        if ext in IMAGE_EXT:
            ready = _prepare_image(path)
            return Extracted(label, kind="image", path=str(ready),
                             note="image — graded from the picture")
        if ext in VIDEO_EXT:
            playable = ext in VIDEO_PLAYABLE
            return Extracted(
                label, kind="video", path=str(path),
                note=("video -- play it here and grade it yourself; it is never "
                      "sent to Claude" if playable else
                      f"video ({ext}) -- most browsers cannot play this one; "
                      "download it or open it in SpeedGrader"),
                data={"ext": ext, "playable": playable,
                      "bytes": path.stat().st_size if path.is_file() else 0},
            )
        if ext == ".docx":
            return Extracted(label, text=docx_to_text(path), path=str(path))
        if ext == ".pptx":
            return Extracted(label, text=pptx_to_text(path), path=str(path))
        if ext in (".xlsx", ".xlsm"):
            return Extracted(label, text=xlsx_to_text(path), path=str(path))
        if ext == ".pdf":
            text = pdf_to_text(path)
            if len(text.split()) < 5:
                pages = pdf_page_images(path)
                if pages:
                    return Extracted(label, text=text, kind="text", path=str(path),
                                     note="scanned PDF — pages are graded as images",
                                     data={"pages": [str(p) for p in pages]})
                return Extracted(label, text=text, kind="binary", path=str(path),
                                 note="PDF has no extractable text — likely a scan. "
                                      "Install PyMuPDF (pip install pymupdf) so those "
                                      "pages can be graded as images.")
            return Extracted(label, text=text, path=str(path))
        if ext in TEXT_EXT or ext in CODE_EXT:
            body = path.read_text(encoding="utf-8-sig", errors="replace").strip()
            if ext == ".rtf":
                body = re.sub(r"\\'[0-9a-fA-F]{2}", " ", body)
                body = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", body)
                body = body.replace("{", " ").replace("}", " ")
                body = re.sub(r"[ \t]+", " ", body)
                body = re.sub(r"\n{3,}", "\n\n", body).strip()
            if ext in CODE_EXT:
                lang = ext.lstrip(".")
                body = "```" + lang + "\n" + body + "\n```"
            return Extracted(label, text=body, path=str(path))
        if ext in ARCHIVE_EXT:
            return Extracted(label, kind="binary", path=str(path),
                             note="archive, not expanded yet")
        if ext in (".blend", ".blend1"):
            return Extracted(label, kind="binary", path=str(path),
                             note="Blender file, not analyzed yet - run the Blender pass")
        if ext == ".doc":
            return Extracted(label, kind="binary", path=str(path),
                             note="legacy .doc — ask the student to resubmit as .docx or PDF")
        if ext in (".xls", ".ppt"):
            return Extracted(label, kind="binary", path=str(path),
                             note=f"legacy {ext} — ask the student to resubmit as "
                                  f"{'.xlsx' if ext == '.xls' else '.pptx'}")
        return Extracted(label, kind="binary", path=str(path),
                         note=f"no text extractor for {ext or 'this file type'}")
    except Exception as exc:  # noqa: BLE001 - report, never crash a batch
        return Extracted(label, kind="error", path=str(path), note=f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------- zip
ARCHIVE_EXT = {".zip"}

# Build output, package caches and OS cruft. A zipped Unity project is mostly
# this, and pulling it in would bury the three scripts that get graded.
SKIP_DIRS = {
    "__macosx", ".git", ".svn", ".vs", ".idea", ".vscode",
    "library", "temp", "obj", "bin", "build", "logs", "usersettings",
    "node_modules", "__pycache__", ".gradle", "packages",
}
SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
SKIP_EXT = {".meta", ".pyc", ".pyo", ".pdb", ".dll", ".exe", ".so", ".dylib",
            ".obj", ".lib", ".cache", ".tmp"}


def _zip_member_wanted(name: str) -> bool:
    parts = [p.lower() for p in name.replace("\\", "/").split("/") if p]
    if not parts or name.endswith("/"):
        return False
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    leaf = parts[-1]
    if leaf in SKIP_NAMES or leaf.startswith("._"):
        return False
    return Path(leaf).suffix.lower() not in SKIP_EXT


def expand_archive(archive: Path, dest_dir: Path, prefix: str = "",
                   max_files: int = 120, max_bytes: int = 150_000_000,
                   max_ratio: int = 200) -> list[tuple[Path, str]]:
    """Unpack a .zip into dest_dir, flat, returning (path, label) pairs.

    Files land flat because the app serves attachments from one directory by
    basename; the original relative path is kept as the label so
    "Scripts/Mover.cs" still reads correctly.

    Refuses path traversal, caps the number of files and the total uncompressed
    size, and rejects an implausible compression ratio so a zip bomb cannot fill
    the disk.
    """
    out: list[tuple[Path, str]] = []
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        infos = [i for i in zf.infolist() if _zip_member_wanted(i.filename)]
        total = sum(i.file_size for i in infos)
        packed = max(1, sum(i.compress_size for i in infos))
        if total > max_bytes:
            raise ValueError(f"archive expands to {total/1e6:.0f} MB, over the "
                             f"{max_bytes/1e6:.0f} MB limit")
        if total / packed > max_ratio:
            raise ValueError(f"archive compression ratio {total/packed:.0f}x looks "
                             "like a zip bomb")

        for info in infos[:max_files]:
            rel = info.filename.replace("\\", "/").lstrip("/")
            # Zip slip: a member named ../../x would otherwise escape dest_dir.
            if ".." in Path(rel).parts:
                continue
            flat = prefix + re.sub(r"[^A-Za-z0-9._-]", "_", rel)[-100:]
            target = dest_dir / flat
            try:
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 64 * 1024)
            except Exception:  # noqa: BLE001 - one bad member must not kill the zip
                continue
            out.append((target, rel))
    return out


def unpack_file(path: Path, label: str | None = None) -> list[Extracted]:
    """The file itself, plus screenshots pasted inside it or scanned PDF pages."""
    main = extract_file(path, label)
    label = main.label
    pages = list((main.data or {}).get("pages") or [])
    parts: list[Extracted] = []
    if pages:
        if (main.text or "").strip():
            parts.append(main)
        for index, page in enumerate(pages, start=1):
            parts.append(Extracted(f"{label} page {index}", kind="image", path=page,
                                   note="page of a scanned PDF"))
    else:
        parts.append(main)
    if main.kind != "image":
        for image in embedded_images(path):
            parts.append(Extracted(f"{label} — {image.name}", kind="image", path=str(image),
                                   note="image inside the submitted file"))
    return parts


def extract_submission(body_html: str | None, files: list[Path],
                       labels: dict[str, str] | None = None) -> Submission:
    sub = Submission()
    labels = labels or {}
    body = html_to_text(body_html)
    if body:
        sub.add(Extracted("Canvas text entry", text=body))
    for path in files:
        for part in unpack_file(path, labels.get(str(path))):
            sub.add(part)

    # Only warn about images pasted into the editor if we did not manage to pull
    # them down. When the caller harvested them there are real image parts and
    # the note would send the instructor to SpeedGrader for nothing.
    inline = count_inline_images(body_html)
    if inline and not sub.images and len(body.split()) < 25:
        sub.add(Extracted(
            f"{inline} inline image(s)", kind="binary",
            note=("student pasted images directly into the Canvas text box and they "
                  "could not be downloaded; read them in SpeedGrader"),
        ))
    return sub


_IMG = re.compile(r"<img\b[^>]*?\bsrc=\"([^\"]*)\"[^>]*>", re.I)
_FILE_ID = re.compile(r"/files/(\d+)")


def present_text_entries(extracted: dict | None, submissions: list | None,
                         files_dir: Path, course_id, assignment_id,
                         base_url: str = "") -> dict:
    """Give each Canvas text entry the HTML the student actually wrote.

    Grading still reads the flat text. The work pane should not: a table in
    the editor is a table, not a line of pipes. The HTML is cleaned first.
    Pictures already downloaded out of that entry are pointed at the local
    file so they sit where the student put them. The stored extract is not
    modified.
    """
    bodies: dict[str, str] = {}
    for sub in submissions or []:
        if isinstance(sub, dict) and sub.get("user_id") is not None:
            bodies[str(sub["user_id"])] = sub.get("body") or ""
    if not bodies or not extracted:
        return extracted or {}
    out: dict = {}
    for uid, info in extracted.items():
        raw = bodies.get(str(uid)) or ""
        if not raw.strip() or not isinstance(info, dict):
            out[uid] = info
            continue
        html = _entry_html(raw, str(uid), files_dir, course_id, assignment_id, base_url)
        if not html:
            out[uid] = info
            continue
        info = dict(info)
        parts = []
        placed = False
        for part in info.get("parts") or []:
            if (not placed and isinstance(part, dict)
                    and part.get("label") == "Canvas text entry"):
                part = dict(part)
                part["html"] = html
                placed = True
            parts.append(part)
        if placed:
            info["parts"] = parts
        out[uid] = info
    return out


def _local_rce(files_dir: Path, uid: str, file_id: str) -> Path | None:
    if not files_dir.is_dir():
        return None
    matches = sorted(files_dir.glob(f"{uid}_rce{file_id}_*"))
    return matches[0] if matches else None


def _entry_html(raw: str, uid: str, files_dir: Path, course_id, assignment_id,
                base_url: str) -> str:
    html = clean_html(raw, base_url)
    if not html:
        return ""

    def replace(match: re.Match) -> str:
        src = match.group(1)
        found = _FILE_ID.search(src)
        if not found:
            return ""
        local = _local_rce(files_dir, uid, found.group(1))
        if local is None:
            return ""
        url = (f"/api/a/{course_id}/{assignment_id}/file?name="
               + quote(local.name))
        return re.sub(r"\bsrc=\"[^\"]*\"", f'src="{url}"', match.group(0), count=1)

    return _IMG.sub(replace, html).strip()
