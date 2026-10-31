"""Read a student's video the only way a model can: what is said, and what is
on screen.

Claude takes pictures and text, not video. So a recording becomes two things a
model can read. The audio becomes a transcript, made on this computer by
faster-whisper; the sound never leaves the machine. The picture becomes still
frames spread evenly through the video, tiled into a few contact sheets with
each frame's time stamped on it, so the model knows when it saw what.

That is snapshots, not watching: no motion, no tone of voice, no timing. The
prompt says so, and the grade says it came from a reading of the video.

Both are slow next to everything else the grader does, so they are kept beside
the assignment in video/ and redone only when the file changes.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

VERSION = 1                    # bump to redo every cached reading
FRAMES = 36                    # spread evenly through the video
GRID = 3                       # 3 x 3 frames on each contact sheet
TILE_W = 512                   # wide enough to read on-screen text in a game UI
MAX_SECONDS = 3 * 60 * 60      # past this it is not a class video; leave it to a person
WHISPER_MODEL = "base"         # already on this machine; small and quick on a CPU

_whisper = None
_whisper_lock = threading.Lock()


class Unreadable(RuntimeError):
    """The file could not be opened as video."""


def _stamp(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _sig(path: Path) -> str:
    st = path.stat()
    return f"{VERSION}:{st.st_size}:{st.st_mtime_ns}"


def cache_dir_for(video: Path, root: Path) -> Path:
    """video/<stem>-<hash>/ under the assignment: one folder per file."""
    digest = hashlib.sha1(str(video.name).encode("utf-8")).hexdigest()[:10]
    return root / f"{video.stem[:40]}-{digest}"


def cached(video: Path, root: Path) -> dict | None:
    """The reading already on disk for this exact file, or None. Never computes."""
    try:
        meta_path = cache_dir_for(video, root) / "reading.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("sig") != _sig(video):
            return None
        meta["sheets"] = [str(meta_path.parent / name) for name in meta.get("sheets") or []]
        return meta
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------- frames
def _rotate(image, frame):
    # Phone recordings are stored sideways with a note to turn them. Without
    # this every portrait video reads as a picture lying on its side.
    turn = int(getattr(frame, "rotation", 0) or 0) % 360
    return image.rotate(turn, expand=True) if turn else image


def _frames(video: Path, count: int) -> tuple[float, list]:
    import av                                     # ships with faster-whisper
    try:
        container = av.open(str(video))
    except Exception as exc:                      # noqa: BLE001
        raise Unreadable(f"could not open {video.name}: {exc}") from None
    with container:
        streams = [s for s in container.streams if s.type == "video"]
        if not streams:
            raise Unreadable(f"{video.name} has no picture")
        stream = streams[0]
        if stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        else:
            duration = float(container.duration or 0) / 1_000_000
        if duration <= 0:
            raise Unreadable(f"could not tell how long {video.name} is")
        if duration > MAX_SECONDS:
            raise Unreadable(f"{video.name} is {_stamp(duration)} long")
        # Centre of each slice, so the first frame is not a black title card
        # and the last is not the stop-recording click.
        wanted = [duration * (i + 0.5) / count for i in range(count)]
        out = []
        base = float(stream.start_time * stream.time_base) if stream.start_time else 0.0
        for at in wanted:
            try:
                container.seek(int((base + at) / stream.time_base), stream=stream,
                               backward=True, any_frame=False)
                for frame in container.decode(stream):
                    t = float(frame.pts * stream.time_base) - base if frame.pts is not None else at
                    if t + 1e-3 >= at or t < 0:
                        out.append((at, _rotate(frame.to_image(), frame)))
                        break
            except Exception:                     # noqa: BLE001  one bad seek is one gap
                continue
        if not out:
            raise Unreadable(f"no frames could be read from {video.name}")
        return duration, out


def _sheets(frames: list, folder: Path) -> list[str]:
    from PIL import Image, ImageDraw
    names = []
    per = GRID * GRID
    for n, start in enumerate(range(0, len(frames), per), start=1):
        batch = frames[start:start + per]
        tiles = []
        for at, image in batch:
            image = image.convert("RGB")
            h = max(1, round(image.height * TILE_W / max(1, image.width)))
            tiles.append((at, image.resize((TILE_W, min(h, TILE_W * 2)))))
        tile_h = max(t.height for _, t in tiles)
        rows = (len(tiles) + GRID - 1) // GRID
        sheet = Image.new("RGB", (TILE_W * GRID, tile_h * rows), (20, 20, 20))
        draw = ImageDraw.Draw(sheet)
        for i, (at, tile) in enumerate(tiles):
            x, y = (i % GRID) * TILE_W, (i // GRID) * tile_h
            sheet.paste(tile, (x, y))
            label = _stamp(at)
            draw.rectangle((x, y, x + 12 + 9 * len(label), y + 22), fill=(0, 0, 0))
            draw.text((x + 6, y + 5), label, fill=(255, 230, 80))
        name = f"sheet-{n}.jpg"
        sheet.save(folder / name, "JPEG", quality=82)
        names.append(name)
    return names


# --------------------------------------------------------------- transcript
def _model():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper


def _has_audio(video: Path) -> bool:
    import av
    try:
        with av.open(str(video)) as container:
            return any(s.type == "audio" for s in container.streams)
    except Exception:                             # noqa: BLE001
        return False


def _transcribe(video: Path) -> tuple[list[dict], str]:
    """Timed lines of what is said. Empty for a recording with no sound."""
    if not _has_audio(video):
        return [], ""
    # One at a time: the grader runs students in parallel, and two of these at
    # once on the same CPU is slower than one after the other.
    with _whisper_lock:
        segments, info = _model().transcribe(str(video), vad_filter=True)
        lines = [{"start": round(s.start, 1), "end": round(s.end, 1),
                  "text": s.text.strip()} for s in segments if s.text.strip()]
        return lines, getattr(info, "language", "") or ""


# --------------------------------------------------------------------- read
def read(video: Path, root: Path, progress=None) -> dict:
    """Transcript and contact sheets for one video, from the cache when the
    file has not changed. Raises Unreadable when it cannot be opened."""
    video = Path(video)
    hit = cached(video, root)
    if hit:
        return hit
    folder = cache_dir_for(video, root)
    folder.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda *_: None)
    say(f"taking still frames from {video.name}")
    duration, frames = _frames(video, FRAMES)
    sheets = _sheets(frames, folder)
    say(f"transcribing {_stamp(duration)} of audio from {video.name} on this computer")
    transcript, language = _transcribe(video)
    meta = {"sig": _sig(video), "file": video.name, "duration": round(duration, 1),
            "frames": len(frames), "sheets": sheets, "language": language,
            "transcript": transcript}
    (folder / "reading.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return cached(video, root) or meta


def as_text(label: str, reading: dict) -> str:
    """The transcript as the grader's prompt and the work pane read it."""
    head = (f"## Video: {label} ({_stamp(reading.get('duration') or 0)} long, "
            f"{reading.get('frames', 0)} still frames on the attached sheets)")
    lines = [f"[{_stamp(t['start'])}] {t['text']}" for t in reading.get("transcript") or []]
    body = "\n".join(lines) if lines else "(no speech was heard in this video)"
    return f"{head}\nTranscript of the audio:\n{body}"


def attach_cached(extracted: dict, root: Path) -> dict:
    """Each video part that has been read gets its transcript, for the work
    pane. Nothing is computed here; the stored extract is not modified."""
    if not extracted or not root.is_dir():
        return extracted
    out = dict(extracted)
    for uid, info in extracted.items():
        if not isinstance(info, dict):
            continue
        parts, changed = [], False
        for part in info.get("parts") or []:
            if (isinstance(part, dict) and part.get("kind") == "video"
                    and part.get("path")):
                path = Path(part["path"])
                hit = cached(path, root) if path.is_file() else None
                if hit:
                    part = {**part, "transcript": hit.get("transcript") or [],
                            "read_frames": hit.get("frames", 0)}
                    changed = True
            parts.append(part)
        if changed:
            out[uid] = {**info, "parts": parts}
    return out
