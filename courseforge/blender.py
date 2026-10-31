"""Drive Blender headless to read a student's .blend file.

Finds the Blender executable, runs `scripts/blend_extract.py` inside it, composites
the rendered views into one contact sheet, and turns the raw stats into a report a
grader can actually read.

Every invocation passes --factory-startup and --disable-autoexec. A .blend can carry
Python in drivers and registered handlers, and this opens files submitted by students,
so neither flag is optional.
"""
from __future__ import annotations

import concurrent.futures
import glob
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "scripts" / "blend_extract.py"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VIEW_ORDER = ["persp", "front", "right", "top", "wire"]

_ACTIVE: set[subprocess.Popen] = set()
_LOCK = threading.Lock()
_FOUND: str | None = None


class BlenderError(RuntimeError):
    pass


class BlenderMissing(BlenderError):
    def __init__(self):
        super().__init__(
            "Blender was not found. Install it from https://www.blender.org/download/ "
            'or set "blender_path" in config.json to the full path of blender.exe.')


# --------------------------------------------------------------- locating it
def find_blender(configured: str = "") -> str:
    """Configured path, then PATH, then the standard install dirs, then registry."""
    global _FOUND
    if configured and Path(configured).is_file():
        return configured
    if _FOUND and Path(_FOUND).is_file():
        return _FOUND

    found = shutil.which("blender")
    if not found:
        patterns = [
            r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
            r"C:\Program Files (x86)\Blender Foundation\Blender *\blender.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Blender Foundation\Blender *\blender.exe"),
            "/Applications/Blender.app/Contents/MacOS/Blender",
            "/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender",
        ]
        hits: list[str] = []
        for pattern in patterns:
            hits.extend(glob.glob(pattern))
        # Newest version last in sort order, so prefer the tail.
        found = sorted(hits)[-1] if hits else None

    if not found:
        found = _from_registry()
    if not found:
        raise BlenderMissing()
    _FOUND = found
    return found


def _from_registry() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    for root, key in ((winreg.HKEY_LOCAL_MACHINE,
                       r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),):
        try:
            with winreg.OpenKey(root, key) as parent:
                for i in range(winreg.QueryInfoKey(parent)[0]):
                    try:
                        with winreg.OpenKey(parent, winreg.EnumKey(parent, i)) as sub:
                            name = winreg.QueryValueEx(sub, "DisplayName")[0]
                            if "blender" not in str(name).lower():
                                continue
                            loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                            exe = Path(loc) / "blender.exe"
                            if exe.is_file():
                                return str(exe)
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def probe(configured: str = "") -> dict:
    """For the doctor command. Never raises."""
    info: dict = {"path": None, "version": None, "ok": False, "detail": ""}
    try:
        info["path"] = find_blender(configured)
    except BlenderMissing as exc:
        info["detail"] = str(exc)
        return info
    try:
        out = subprocess.run([info["path"], "--version"], capture_output=True, text=True,
                             timeout=90, creationflags=NO_WINDOW)
        first = (out.stdout or out.stderr).strip().splitlines()
        info["version"] = first[0].strip() if first else ""
        info["ok"] = "blender" in info["version"].lower()
    except Exception as exc:  # noqa: BLE001
        info["detail"] = f"{type(exc).__name__}: {exc}"
    return info


def shutdown_all() -> int:
    """Kill any running Blender we started, so quitting does not orphan renders."""
    with _LOCK:
        procs = list(_ACTIVE)
    for proc in procs:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    return len(procs)


# ------------------------------------------------------------------ running
def extract(blend_path: Path, out_dir: Path, configured: str = "",
            timeout_s: int = 180, render: bool = True, glb: bool = True) -> dict:
    """Open one .blend headless and return the parsed result."""
    exe = find_blender(configured)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [exe, "-b", str(blend_path), "--factory-startup", "--disable-autoexec",
           "--python", str(SCRIPT), "--", "--out", str(out_dir)]
    if not render:
        cmd.append("--no-render")
    if not glb:
        cmd.append("--no-glb")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW)
    with _LOCK:
        _ACTIVE.add(proc)
    try:
        out, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise BlenderError(
            f"Blender did not finish within {timeout_s}s on {blend_path.name}. "
            "The scene may be very heavy; raise blend_timeout_s or grade it by hand.")
    finally:
        with _LOCK:
            _ACTIVE.discard(proc)

    stats_path = out_dir / "stats.json"
    if not stats_path.is_file():
        tail = "\n".join((out or "").strip().splitlines()[-12:])
        raise BlenderError(f"Blender produced no stats for {blend_path.name}.\n{tail}")

    try:
        result = json.loads(stats_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BlenderError(f"Unreadable stats for {blend_path.name}: {exc}") from None

    result["contact_sheet"] = _contact_sheet(out_dir)
    return result


def _contact_sheet(out_dir: Path) -> str | None:
    """Composite the rendered angles into one labelled PNG.

    One image rather than five: one Read for the model, one <img> for the reviewer.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    tiles = [(name, out_dir / f"view_{name}.png") for name in VIEW_ORDER]
    tiles = [(n, p) for n, p in tiles if p.is_file()]
    if not tiles:
        return None

    try:
        images = [(n, Image.open(p).convert("RGB")) for n, p in tiles]
        w, h = images[0][1].size
        cols = 3 if len(images) > 2 else len(images)
        rows = (len(images) + cols - 1) // cols
        pad, band = 6, 22
        sheet = Image.new("RGB", (cols * w + (cols + 1) * pad,
                                  rows * (h + band) + (rows + 1) * pad), (24, 27, 32))
        draw = ImageDraw.Draw(sheet)
        for i, (name, img) in enumerate(images):
            cx, cy = i % cols, i // cols
            x = pad + cx * (w + pad)
            y = pad + cy * (h + band + pad)
            sheet.paste(img, (x, y))
            draw.text((x + 4, y + h + 4), name.upper(), fill=(190, 198, 210))
        dest = out_dir / "contact.png"
        sheet.save(dest, "PNG", optimize=True)
        return str(dest)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------- report
# The sections below were added so the grader reads lighting, camera, render and
# texture facts from the file instead of from a student's typed notes. Each one
# prints nothing when the stats predate it, so an older cached pass still reads.
_REPORT_LIGHTS = 8
_REPORT_IMAGES = 10
_REPORT_EMPTIES = 8


def _n(value) -> str:
    """A number without float noise: 2.0 -> 2, 0.12345 -> 0.123."""
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, (int, float)):
        f = round(float(value), 3)
        return str(int(f)) if f.is_integer() else str(f)
    return str(value)


def _vec(values) -> str:
    if isinstance(values, (list, tuple)):
        return "(" + ", ".join(_n(v) for v in values) + ")"
    return _n(values)


def _material_detail(mat: dict) -> list[str]:
    out = []
    for bsdf in (mat.get("principled") or [])[:2]:
        normal = ("fed through a Normal Map node" if bsdf.get("normal_via_normal_map")
                  else "fed through a Bump node" if bsdf.get("normal_via_bump")
                  else "linked" if bsdf.get("normal_linked") else "not connected")
        out.append(f"      {bsdf.get('node')}: Base Color {_vec(bsdf.get('base_color'))}, "
                   f"Metallic {_vec(bsdf.get('metallic'))}, "
                   f"Roughness {_vec(bsdf.get('roughness'))}, Normal {normal}")
    for tex in (mat.get("textures") or [])[:6]:
        feeds = ", ".join(tex.get("feeds") or []) or "nothing (not connected to the shader)"
        out.append(f"      image {tex.get('image') or 'none'} "
                   f"({tex.get('colorspace') or '?'}) -> {feeds}")
    return out


def _image_detail(images: list) -> list[str]:
    rows = []
    for img in images[:_REPORT_IMAGES]:
        if "size" not in img:
            continue                      # stats from before these fields existed
        size = img.get("size") or [0, 0]
        dims = f"{size[0]}x{size[1]}" if any(size) else "no pixels loaded"
        pot = "" if not any(size) else (
            ", power of two" if img.get("power_of_two") else ", not power of two")
        rows.append(f"  - {img.get('name')}: {dims}{pot}, colour space "
                    f"{img.get('colorspace')}, "
                    + ("packed" if img.get("packed") else "external file")
                    + (f", source {img.get('source')}" if img.get("source") not in
                       (None, "FILE") else ""))
    if not rows:
        return []
    more = len(images) - _REPORT_IMAGES
    return (["Image sizes and colour spaces:"] + rows
            + ([f"  - {more} more images omitted"] if more > 0 else []))


_MOD_LABELS = {
    "width": "width", "segments": "segments", "limit_method": "limit",
    "use_clamp_overlap": "clamp overlap", "offset_type": "offset type",
    "use_clip": "clipping", "use_axis": "axis", "use_bisect_axis": "bisect",
    "count": "count", "fit_type": "fit", "use_constant_offset": "constant offset",
    "use_relative_offset": "relative offset", "thickness": "thickness",
    "levels": "viewport levels", "render_levels": "render levels",
    "sculpt_levels": "sculpt levels", "operation": "operation", "solver": "solver",
    "object": "object",
}


def _modifier_stack(mods: list) -> str:
    """Modifiers top to bottom with their key settings: Mirror(clipping on) > Bevel(...)."""
    if not any("settings" in m for m in mods):
        return ""
    parts = []
    for mod in mods[:8]:
        settings = mod.get("settings") or {}
        bits = []
        for key, val in settings.items():
            label = _MOD_LABELS.get(key, key)
            if key in ("use_axis", "use_bisect_axis") and isinstance(val, list):
                axes = "".join(a for a, on in zip("XYZ", val) if on)
                if key == "use_bisect_axis" and not axes:
                    continue
                bits.append(f"{label} {axes or 'none'}")
            elif isinstance(val, bool):
                bits.append(f"{label} {'on' if val else 'off'}")
            else:
                bits.append(f"{label} {_n(val)}")
        off = "" if mod.get("show_render", True) else " render off"
        parts.append(f"{mod.get('name')} {mod.get('type')}"
                     + (f"({', '.join(bits)})" if bits else "") + off)
    return " > ".join(parts)


def _light_line(light: dict) -> str:
    bits = [f"{light.get('name')} [{light.get('type')}] {_n(light.get('energy'))} "
            + ("W/m2" if light.get("type") == "SUN" else "W")]
    color = light.get("color")
    if color and any(abs(c - 1) > 1e-3 for c in color):
        bits.append(f"colour {_vec(color)}")
    if light.get("type") == "AREA":
        size = _n(light.get("size"))
        if light.get("size_y") is not None:
            size += f" x {_n(light.get('size_y'))}"
        bits.append(f"{(light.get('shape') or '').lower()} {size} m")
    elif light.get("type") == "SPOT":
        bits.append(f"cone {_n(light.get('spot_size_deg'))} deg, blend "
                    f"{_n(light.get('spot_blend'))}")
    if light.get("type") in ("POINT", "SPOT"):
        bits.append(f"radius/soft size {_n(light.get('shadow_soft_size'))}")
    elif light.get("type") == "SUN" and light.get("sun_angle_deg") is not None:
        bits.append(f"sun angle {_n(light['sun_angle_deg'])} deg")
    if light.get("distance_to_subject") is not None:
        bits.append(f"{_n(light['distance_to_subject'])} m from the subject")
    if light.get("angle_from_camera_deg") is not None:
        bits.append(f"{_n(light['angle_from_camera_deg'])} deg around the subject "
                    "from the camera")
    if light.get("elevation_deg") is not None:
        bits.append(f"{_n(light['elevation_deg'])} deg above the subject")
    if light.get("aim_off_subject_deg") is not None:
        bits.append(f"aimed {_n(light['aim_off_subject_deg'])} deg off the subject")
    if light.get("type") != "SUN":          # a sun's position does not matter
        bits.append(f"at {_vec(light.get('location'))}")
    if light.get("hide_render"):
        bits.append("disabled in renders")
    return "  - " + "; ".join(bits)


def _scene_setup_lines(stats: dict) -> list[str]:
    """Lights, camera, render, world and empties, read from the file."""
    lines: list[str] = []
    lights = stats.get("lights")
    summary = stats.get("lighting") or {}
    if lights is not None:
        count = summary.get("count", len(lights))
        head = f"Lights in the file: {count}."
        if summary.get("key_fill_ratio") is not None:
            head += (f" Strongest to second strongest (key:fill) "
                     f"{_n(summary['key_fill_ratio'])}:1 "
                     f"({summary.get('strongest')} over {summary.get('second')}).")
        if summary.get("back_lights"):
            head += (" Behind the subject (rim/back): "
                     + ", ".join(summary["back_lights"]) + ".")
            if summary.get("key_fill_ratio_front") is not None:
                pair = summary.get("front_pair") or []
                head += (f" Key:fill among the front lights "
                         f"{_n(summary['key_fill_ratio_front'])}:1"
                         + (f" ({pair[0]} over {pair[1]})" if len(pair) == 2 else "") + ".")
        if summary.get("ratio_mixes_sun"):
            head += " The ratio compares a sun (W/m2) with a lamp (W), so it is not like for like."
        lines.append(head)
        if lights:
            lines.append("  Angles are measured around the subject in the ground plane: "
                         "0 deg is beside the camera, 90 deg is to the side, 180 deg is "
                         "directly behind the subject.")
        for light in lights[:_REPORT_LIGHTS]:
            lines.append(_light_line(light))
        if len(lights) > _REPORT_LIGHTS:
            lines.append(f"  - {len(lights) - _REPORT_LIGHTS} weaker lights omitted")

    cams = stats.get("cameras")
    if cams is not None:
        active = cams.get("active")
        if active:
            text = (f"Active camera: {active.get('name')} ({active.get('type')}, "
                    f"{_n(active.get('lens_mm'))} mm lens, "
                    f"{_n(active.get('sensor_width_mm'))} mm sensor"
                    + (f", ortho scale {_n(active['ortho_scale'])}" if active.get("ortho_scale")
                       else "")
                    + (", depth of field on" if active.get("dof") else "")
                    + f") at {_vec(active.get('location'))}, rotation "
                    f"{_vec(active.get('rotation_deg'))} deg.")
            if active.get("subject_corners_in_frame") is not None:
                text += (f" {active['subject_corners_in_frame']} of 8 corners of the "
                         "subject's bounding box are inside the frame.")
        else:
            text = "Active camera: none set."
        lines.append(text + f" Cameras in the file: {cams.get('count', 0)}.")

    render = stats.get("render")
    if render:
        res = render.get("resolution") or ["?", "?"]
        lines.append(
            f"Render settings: engine {render.get('engine')}, EEVEE samples "
            f"{render.get('eevee_samples')}, Cycles samples {render.get('cycles_samples')} "
            f"(denoising {'on' if render.get('cycles_denoising') else 'off'}), "
            f"resolution {res[0]}x{res[1]} at {render.get('resolution_percentage')}%, "
            f"output {render.get('file_format')} {render.get('color_mode') or ''}"
            .rstrip() + ("" if not render.get("film_transparent") else ", transparent film")
            + ". Colour management: view transform "
            f"{render.get('view_transform')}, look {render.get('look')}, exposure "
            f"{_n(render.get('exposure'))}, gamma {_n(render.get('gamma'))}, display "
            f"{render.get('display_device')}.")

    world = stats.get("world")
    if world:
        if world.get("hdri"):
            size = world.get("hdri_size")
            text = (f"World {world.get('name')}: environment (HDRI) image "
                    f"{world.get('hdri_image')}"
                    + (f" {size[0]}x{size[1]}" if size else ""))
        elif world.get("name"):
            text = (f"World {world.get('name')}: no environment image, background colour "
                    f"{_vec(world.get('background_color'))}")
        else:
            text = "World: none assigned to the scene"
        if world.get("strength") is not None:
            text += f", strength {_vec(world.get('strength'))}"
        lines.append(text + ".")

    empties = stats.get("empties")
    if empties:
        rows = []
        for e in empties[:_REPORT_EMPTIES]:
            bits = [f"{e.get('name')} [{e.get('display_type')}]",
                    f"display size {_n(e.get('display_size'))}"]
            if e.get("display_type") == "IMAGE":
                size = e.get("image_size")
                bits.append(f"image {e.get('image') or 'none'}"
                            + (f" {size[0]}x{size[1]}" if size else ""))
                bits.append(f"depth {e.get('image_depth')}")
                bits.append(f"opacity {_n(e.get('opacity'))}"
                            + (" (transparency on)" if e.get("use_alpha")
                               else " (transparency off)"))
            bits.append("locked from selection" if e.get("hide_select")
                        else "selectable")
            rows.append("  - " + "; ".join(bits))
        lines.append(f"Empties: {len(empties)}"
                     + (" (reference images first)" if any(
                         e.get("display_type") == "IMAGE" for e in empties) else "") + ":")
        lines.extend(rows)
    return lines


def scene_report(result: dict, filename: str) -> str:
    """Turn the raw stats into compact prose the model and the instructor can read.

    This is what flows into the grading prompt, so it stays short and states facts
    without judging them. The rubric decides what a number is worth.
    """
    stats = result.get("stats") or {}
    if not stats:
        errs = "; ".join(result.get("errors") or []) or "unknown error"
        return f"Blender could not read {filename}. {errs}"

    counts = stats.get("counts", {})
    totals = stats.get("totals", {})
    hyg = stats.get("hygiene", {})
    scene = stats.get("scene", {})
    lines: list[str] = [f"Blender scene report for {filename}"]

    ver = ".".join(str(v) for v in (stats.get("file_version") or []))
    lines.append(f"Saved with Blender {ver or 'unknown'}. Units {scene.get('unit_system')}, "
                 f"scale {scene.get('scale_length')}. Render engine {scene.get('engine')}.")

    by_type = ", ".join(f"{v} {k.lower()}" for k, v in sorted(counts.get("by_type", {}).items()))
    lines.append(f"Objects: {counts.get('objects', 0)} ({by_type or 'none'}), "
                 f"{counts.get('collections', 0)} collections, "
                 f"{counts.get('materials', 0)} materials, "
                 f"{counts.get('images', 0)} images.")

    tris = totals.get("tris_render", 0) or 0
    lines.append(
        f"Geometry: {totals.get('verts', 0)} verts, {totals.get('faces', 0)} faces "
        f"({tris} tris at render). "
        f"Face mix: {totals.get('quads', 0)} quads, {totals.get('tris', 0)} tris, "
        f"{totals.get('ngons', 0)} n-gons "
        f"({int(round(100 * (totals.get('quad_ratio') or 0)))}% quads).")
    lines.append(f"Final Tris (Statistics overlay count): {tris}.")

    objects = stats.get("objects") or []
    interior = totals.get("interior_faces")
    if interior is None:
        interior = sum((obj.get("mesh") or {}).get("interior_faces") or 0 for obj in objects)
    inward = totals.get("inward_faces")
    lines.append(
        "Mesh audit from the file: "
        f"{totals.get('non_manifold_edges', 0) or 0} non-manifold edges, "
        f"{totals.get('loose_verts', 0) or 0} loose vertices, "
        f"{totals.get('loose_edges', 0) or 0} loose edges, "
        f"{totals.get('zero_area_faces', 0) or 0} zero-area faces, "
        f"{interior} interior faces."
        + (f" Inward faces: {inward}." if inward is not None else "")
        + " This is the Face Orientation and Select Non Manifold check.")
    lines.append(
        "Merge by Distance does not leave its removed-vertex count in the file. "
        "Doubles that remain are the loose vertices and non-manifold edges above.")
    if totals.get("doubles") is not None:
        lines.append(f"Doubles measured in the file: {totals.get('doubles')} vertices sit "
                     "within 0.0001 m of another vertex (0 means none are left to merge).")

    def listing(items, limit=6):
        items = items or []
        head = ", ".join(items[:limit])
        return f"{len(items)}" + (f" ({head}{', ...' if len(items) > limit else ''})" if items else "")

    lines.append(f"Unapplied scale on {listing(hyg.get('objects_with_unapplied_scale'))} objects; "
                 f"unapplied rotation on {listing(hyg.get('objects_with_unapplied_rotation'))}.")
    lines.append(f"Objects still using Blender default names: "
                 f"{listing(hyg.get('default_named_objects'))}.")

    mats = stats.get("materials") or []
    default_mats = [m["name"] for m in mats if m.get("default_name")]
    lines.append(f"Materials: {len(mats)} in use, {len(default_mats)} still default-named"
                 + (f" ({', '.join(default_mats[:5])})" if default_mats else "") + ".")
    if mats:
        lines.append("Material graphs (read from the file, not from a picture):")
        for mat in mats[:12]:
            nodes = ", ".join(mat.get("node_types") or []) or "no nodes"
            images = ", ".join(mat.get("images") or []) or "no image texture"
            lines.append(f"  - {mat.get('name')}: {nodes}; textures: {images}")
            lines.extend(_material_detail(mat))
        if len(mats) > 12:
            lines.append(f"  - {len(mats) - 12} more materials omitted")

    images = stats.get("images") or []
    if images:
        packed = [i.get("name") for i in images if i.get("packed")]
        found = [i.get("name") for i in images
                 if i.get("filepath") and not i.get("packed") and not i.get("missing")]
        lines.append(f"Images packed into the .blend: {listing(packed)}.")
        if found:
            lines.append(f"Images linked from disk and present: {listing(found)}.")
        lines.extend(_image_detail(images))

    bounds = (scene or {}).get("bounds_size")
    if bounds:
        lines.append("Scene size in world units (width, depth, height): "
                     + ", ".join(str(v) for v in bounds) + ".")

    lines.append(f"UVs: {listing(hyg.get('meshes_without_uvs'))} meshes have no UV map.")
    missing = hyg.get("missing_textures") or []
    if missing:
        lines.append(f"Missing texture files: {listing(missing)}. "
                     "These are linked but not on disk and not packed into the .blend.")

    mods = hyg.get("live_modifiers") or []
    lines.append("Modifiers left live: " + (", ".join(mods) if mods else "none") + ".")
    lines.extend(_scene_setup_lines(stats))

    anim = stats.get("animation") or {}
    if anim.get("actions"):
        lines.append(f"Animation: {len(anim['actions'])} actions, "
                     f"{anim.get('keyframes', 0)} keyframes, "
                     f"frames {scene.get('frame_start')}-{scene.get('frame_end')}.")

    detail = stats.get("objects") or []
    omitted = stats.get("objects_omitted") or 0
    if detail:
        lines.append("")
        lines.append(f"Largest objects (showing {len(detail)}"
                     + (f", {omitted} smaller ones omitted" if omitted else "") + "):")
        for obj in detail[:12]:
            mesh = obj.get("mesh") or {}
            bits = [f"{obj['name']} [{obj['type']}]"]
            if mesh.get("faces") is not None:
                bits.append(f"{mesh['faces']} faces, {mesh.get('ngons', 0)} n-gons")
            if obj.get("dimensions"):
                bits.append("size " + " x ".join(str(v) for v in obj["dimensions"]))
            if obj.get("origin_offset"):
                bits.append(f"origin offset {obj['origin_offset']}")
            slots = [name for name in (obj.get("materials") or []) if name]
            if slots:
                bits.append("materials: " + ", ".join(slots[:4]))
            if obj.get("vertex_groups"):
                bits.append(f"{obj['vertex_groups']} vertex groups")
            if obj.get("modifier_types"):
                bits.append("mods: " + "+".join(obj["modifier_types"]))
            if obj.get("unapplied_scale"):
                bits.append(f"scale {obj['scale']}")
            if obj.get("parent"):
                bits.append(f"parent {obj['parent']}")
            if obj.get("hidden"):
                bits.append("hidden")
            if mesh and not mesh.get("has_uvs"):
                bits.append("no UVs")
            elif mesh.get("uv_outside_0_1"):
                bits.append(f"{mesh['uv_outside_0_1']} UV loops outside 0-1")
            if mesh.get("uv_overlap_estimate"):
                bits.append(f"UV overlap {mesh['uv_overlap_estimate']}")
            if mesh.get("shade_smooth"):
                bits.append("shade smooth")
            if mesh.get("doubles"):
                bits.append(f"{mesh['doubles']} doubled verts")
            if mesh.get("uv_islands") is not None:
                bits.append(f"{mesh['uv_islands']} UV islands")
            if mesh.get("texel_density_px_per_m") is not None:
                bits.append(f"texel density {_n(mesh['texel_density_px_per_m'])} px/m "
                            f"({mesh.get('texel_image')})")
            stack = _modifier_stack(obj.get("modifiers") or [])
            if stack:
                bits.append("stack: " + stack)
            lines.append("  - " + "; ".join(bits))

    if result.get("default_scene"):
        lines.insert(1, "WARNING: this looks like Blender's untouched startup file "
                        "(a single default Cube, Camera and Light).")
    if result.get("empty_scene"):
        lines.insert(1, "WARNING: the file opens but contains no mesh objects at all.")
    if result.get("notes"):
        lines.append("")
        lines.append("Automatic safety changes made before reading the file:")
        for note in result["notes"][:8]:
            lines.append("  - " + note)

    if result.get("errors"):
        lines.append("")
        lines.append("Extraction warnings: " + " | ".join(
            e.splitlines()[-1] for e in result["errors"])[:400])
    return "\n".join(lines)


def looks_like_blend(path: Path) -> tuple[bool, str]:
    """Cheap header sniff so obvious garbage never launches Blender."""
    try:
        head = path.open("rb").read(12)
    except OSError as exc:
        return False, f"cannot read the file ({exc})"
    if head[:7] == b"BLENDER":
        return True, ""
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return True, ""          # zstd-compressed, Blender 3.0+
    if head[:2] == b"\x1f\x8b":
        return True, ""          # gzip-compressed, older Blender
    printable = head[:8].decode("ascii", "replace")
    return False, f"not a Blender file (header {printable!r})"


# ------------------------------------------------------- reusing a past pass
# Rendering a class of 3D scenes is the slowest thing this tool does: minutes of
# Blender per assignment. Everything it produces is written into the assignment
# folder, so a finished pass survives a sync, a new tab and a restarted server.
# What loses it is sync_assignment, which rebuilds extracted.json from Canvas and
# knows nothing about parts a later step filled in. attach_cached puts them back
# from what is already on disk.

def result_dir(adir: Path, uid: str, stem: str) -> Path:
    return adir / "blend" / str(uid) / stem


def cached_result(out_dir: Path, source: Path | None = None) -> dict | None:
    """A finished extraction already on disk, or None if there is none to reuse.

    A .blend modified after the render was written invalidates it. A student who
    resubmits under the same filename must not be graded on last week's pictures.
    """
    stats = out_dir / "stats.json"
    if not stats.is_file():
        return None
    try:
        if source and source.is_file() and source.stat().st_mtime > stats.stat().st_mtime:
            return None
        result = json.loads(stats.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict) or "stats" not in result:
        return None
    # A pass that did not fully succeed is not worth keeping. Reusing it would
    # serve the same broken report forever, and the usual cause is a Blender
    # version that has moved on, which the next run may already handle.
    if not result.get("ok"):
        return None
    sheet = out_dir / "contact.png"
    result["contact_sheet"] = str(sheet) if sheet.is_file() else _contact_sheet(out_dir)
    return result


def apply_result(part: dict, result: dict, adir: Path, uid: str, stem: str,
                 filename: str, home: Path | None = None) -> dict:
    """Turn one extraction into the part the work pane and the prompt read.

    The one place that decides what a Blender result looks like, so a reused
    pass and a fresh one are indistinguishable downstream.
    """
    out_dir = result_dir(adir, uid, stem)
    # Artifacts must sit under files/ to be servable by the existing route:
    # beside the .blend in the student's folder (`home`), or flat as
    # <uid>_<stem> next to a .blend an older sync left flat.
    files = adir / "files"
    home = Path(home) if home else files
    if home != files and files not in home.parents:
        home = files
    lead = f"{uid}_" if home == files else ""
    served: dict = {}
    for key, src, suffix in (("sheet", out_dir / "contact.png", "sheet.png"),
                             ("model", out_dir / "model.glb", "preview.glb")):
        dest = home / f"{lead}{stem}.{suffix}"
        if not dest.is_file() and src.is_file():
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.read_bytes())
            except OSError:
                continue
        if dest.is_file():
            served[key] = dest.relative_to(files).as_posix()

    part["kind"] = "blend"
    part["note"] = ""
    part["text"] = scene_report(result, filename)
    part["data"] = {
        "status": "ok" if result.get("ok") else "partial",
        "sheet": served.get("sheet"),
        "model": served.get("model"),
        "glb_bytes": result.get("glb_bytes") or 0,
        "default_scene": bool(result.get("default_scene")),
        "empty_scene": bool(result.get("empty_scene")),
        "notes": result.get("notes") or [],
        "errors": result.get("errors") or [],
        "stats": _ui_stats(result.get("stats") or {}),
    }
    return part["data"]


def blend_parts(entry: dict):
    """Every part of one student's submission that is a .blend file."""
    for part in entry.get("parts") or []:
        path = part.get("path") or ""
        if path.lower().endswith((".blend", ".blend1")):
            yield part, Path(path)


def attach_cached(store, course_id, assignment_id, extracted: dict) -> int:
    """Re-attach Blender results already on disk. Returns how many came back.

    Called at the end of a sync. Without it a sync silently throws away a
    finished pass, and the instructor pays for the rendering a second time to
    get back what is sitting in the folder.
    """
    adir = store.assignment_dir(course_id, assignment_id)
    found = 0
    for uid, entry in (extracted or {}).items():
        touched = False
        for part, path in blend_parts(entry):
            result = cached_result(result_dir(adir, str(uid), path.stem), path)
            if not result:
                continue
            apply_result(part, result, adir, str(uid), path.stem, path.name, path.parent)
            touched = True
            found += 1
        if touched:
            refresh_entry(entry)
    return found


# ------------------------------------------------------------- the whole job
def process_assignment(cfg, store, course_id, assignment_id,
                       only: list[str] | None = None,
                       progress=lambda *a, **k: None,
                       force: bool = False) -> dict:
    """Run Blender over every unprocessed .blend in one assignment.

    Deliberately a separate job rather than part of sync: re-syncing is routine
    and re-rendering 23 scenes every time is not, Blender may not be installed at
    all, and a per-student failure should be retryable on its own.
    """
    exe = find_blender(getattr(cfg, "blender_path", ""))
    adir = store.assignment_dir(course_id, assignment_id)
    extracted = store.extracted(course_id, assignment_id)
    if not extracted:
        raise BlenderError("Nothing synced for this assignment yet. Run Sync first.")

    targets = []
    for uid, entry in extracted.items():
        if only and uid not in set(only):
            continue
        for part, path in blend_parts(entry):
            targets.append((uid, entry, part, path))

    total = len(targets)
    if not total:
        progress("no .blend files in this assignment", 0, 0)
        return {"processed": 0, "failed": 0}
    progress(f"Blender {Path(exe).parent.name}: {total} file(s) to read", 0, total)

    done = failed = reused = 0
    lock = threading.Lock()
    workers = max(1, min(int(getattr(cfg, "blend_concurrency", 2)), 4))

    def one(job):
        uid, entry, part, path = job
        name = path.name
        out_dir = result_dir(adir, uid, path.stem)
        if not force:
            # Already rendered and still current: minutes of Blender saved, and
            # the part comes out identical to a freshly rendered one.
            earlier = cached_result(out_dir, path)
            if earlier:
                apply_result(part, earlier, adir, uid, path.stem, name, path.parent)
                return True, f"{name}: already read, reused", True
        ok, why = looks_like_blend(path)
        if not ok:
            part["kind"] = "error"
            part["note"] = why
            return False, f"{name}: {why}", False
        size_mb = path.stat().st_size / 1e6
        cap = float(getattr(cfg, "blend_max_mb", 300))
        if size_mb > cap:
            part["kind"] = "error"
            part["note"] = f"file is {size_mb:.0f} MB, over the {cap:.0f} MB limit"
            return False, f"{name}: too large", False
        try:
            result = extract(path, out_dir, getattr(cfg, "blender_path", ""),
                             timeout_s=int(getattr(cfg, "blend_timeout_s", 180)))
        except BlenderError as exc:
            part["kind"] = "error"
            part["note"] = str(exc)[:400]
            return False, f"{name}: {exc}", False

        data = apply_result(part, result, adir, uid, path.stem, name, path.parent)
        return True, f"{name}: {data['stats'].get('faces', 0)} faces", False

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, job): job for job in targets}
        for future in concurrent.futures.as_completed(futures):
            uid = futures[future][0]
            try:
                good, message, from_cache = future.result()
            except Exception as exc:  # noqa: BLE001
                good, message, from_cache = False, f"{type(exc).__name__}: {exc}", False
            with lock:
                done += 1
                if from_cache:
                    reused += 1
                if not good:
                    failed += 1
                    progress(f"  WARNING {message}")
            progress(f"read {done}/{total}", done, total)

    # Parts changed, so the derived text and the servable artifact lists must be
    # recomputed the same way sync does it.
    for uid, entry in extracted.items():
        refresh_entry(entry)
    store.write(adir / "extracted.json", extracted)
    if reused:
        progress(f"reused {reused} already-rendered file(s); "
                 f"rendered {max(0, total - reused - failed)}")
    progress("done", total, total)
    return {"processed": done - failed, "failed": failed, "total": total,
            "reused": reused}


def _ui_stats(stats: dict) -> dict:
    """The handful of numbers the work pane shows as a chip row."""
    totals = stats.get("totals") or {}
    hyg = stats.get("hygiene") or {}
    counts = stats.get("counts") or {}
    return {
        "objects": counts.get("objects", 0),
        "verts": totals.get("verts", 0),
        "faces": totals.get("faces", 0),
        "tris": totals.get("tris_render", 0),
        "ngons": totals.get("ngons", 0),
        "quad_pct": int(round(100 * (totals.get("quad_ratio") or 0))),
        "unapplied_scale": len(hyg.get("objects_with_unapplied_scale") or []),
        "unapplied_rotation": len(hyg.get("objects_with_unapplied_rotation") or []),
        "default_names": len(hyg.get("default_named_objects") or []),
        "no_uvs": len(hyg.get("meshes_without_uvs") or []),
        "missing_textures": len(hyg.get("missing_textures") or []),
        "non_manifold": totals.get("non_manifold_edges", 0),
        "materials": counts.get("materials", 0),
        "saved_with": ".".join(str(v) for v in (stats.get("file_version") or [])),
    }


def refresh_entry(entry: dict) -> dict:
    """Recompute the derived fields on one extracted entry after parts change.

    Sync computes these too; keeping the logic in one place stops a graded
    discussion that also has a .blend from silently losing its discussion text.
    """
    parts = entry.get("parts") or []
    chunks = [f"--- {p.get('label')} ---\n{p.get('text')}" for p in parts
              if p.get("kind") in ("text", "blend") and (p.get("text") or "").strip()]
    text = "\n\n".join(chunks).strip()

    disc = entry.get("discussion")
    if disc:
        from .grader import _discussion_text
        text = (text + "\n\n" + _discussion_text(disc)).strip()

    entry["text"] = text
    entry["words"] = len(text.split())
    entry["unreadable"] = [p.get("label") for p in parts
                           if p.get("kind") in ("binary", "error")]
    entry["images"] = [p.get("path") for p in parts if p.get("kind") == "image"]
    entry["sheets"] = [(p.get("data") or {}).get("sheet") for p in parts
                       if p.get("kind") == "blend" and (p.get("data") or {}).get("sheet")]
    entry["models"] = [(p.get("data") or {}).get("model") for p in parts
                       if p.get("kind") == "blend" and (p.get("data") or {}).get("model")]
    return entry
