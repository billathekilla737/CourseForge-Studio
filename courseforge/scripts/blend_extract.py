"""Runs INSIDE Blender's Python. Never imported by the app.

    blender -b student.blend --factory-startup --disable-autoexec \
            --python blend_extract.py -- --out <dir> [--no-render] [--no-glb]

Writes into <dir>:
    stats.json      everything a 3D modeling rubric can check mechanically
    view_*.png      front / right / persp / top solid renders, plus a wireframe
    model.glb       glTF for the browser viewer

Nothing here touches the network, and the caller always passes --factory-startup
and --disable-autoexec so a student file cannot run its own Python.
"""
import hashlib
import json
import math
import os
import sys
import traceback

import bpy
import bmesh
from mathutils import Vector

RENDER_SIZE = 640
# A Minecraft-style scene can hold hundreds of objects. Keep full detail only for
# the biggest ones; the aggregate totals and hygiene lists already cover the rest,
# and an unbounded list makes stats.json hundreds of KB and unusable in a prompt.
MAX_OBJECT_DETAIL = 25
DEFAULT_NAMES = ("cube", "sphere", "cylinder", "cone", "torus", "plane", "circle",
                 "icosphere", "suzanne", "material", "mesh", "object", "empty",
                 "camera", "light", "armature", "text", "curve", "nurbspath")


# --------------------------------------------------------------------- utils
def argv_after_dashes():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def is_default_name(name):
    """True for names Blender generated: Cube, Cube.001, Material.003."""
    base = name.split(".")[0].strip().lower()
    return base in DEFAULT_NAMES


def rounded(v, n=4):
    return [round(float(x), n) for x in v]


def safe(label, fn, default, sink):
    """Run one stats section, recording a failure instead of losing every section.

    A single attribute that moved between Blender versions used to abort the whole
    collection, so the grader got an empty report and blamed the student's file.
    """
    try:
        return fn()
    except Exception:
        sink.append("%s: %s" % (label, traceback.format_exc(limit=2)[-300:]))
        return default


def action_fcurves(action):
    """Every F-Curve in one action, on both the old and the new animation API.

    Blender 4.4 moved F-Curves down into layers > strips > channelbags, and 5.0
    removed Action.fcurves outright. Reading only the old attribute meant any
    student who actually animated something crashed the entire stats pass.
    """
    curves = []
    for layer in getattr(action, "layers", None) or []:
        for strip in getattr(layer, "strips", None) or []:
            for bag in getattr(strip, "channelbags", None) or []:
                curves.extend(getattr(bag, "fcurves", None) or [])
    # Only if nothing is layered, so a 4.4 file is not counted twice.
    return curves or list(getattr(action, "fcurves", None) or [])


def animation_report():
    curves = [fc for a in bpy.data.actions for fc in action_fcurves(a)]
    return {
        "actions": [a.name for a in bpy.data.actions],
        "fcurves": len(curves),
        "keyframes": sum(len(fc.keyframe_points) for fc in curves),
    }


# --------------------------------------------------------------------- stats
def mesh_stats(obj, depsgraph, trouble=None):
    """Topology of the EVALUATED mesh, i.e. what the modifiers actually produce."""
    trouble = trouble if trouble is not None else []
    out = {"eval_error": None}
    try:
        ev = obj.evaluated_get(depsgraph)
        me = ev.to_mesh()
    except Exception as exc:
        out["eval_error"] = f"{type(exc).__name__}: {exc}"
        return out

    try:
        tris = quads = ngons = 0
        for poly in me.polygons:
            n = len(poly.vertices)
            if n == 3:
                tris += 1
            elif n == 4:
                quads += 1
            else:
                ngons += 1
        faces = len(me.polygons)

        bm = bmesh.new()
        bm.from_mesh(me)
        non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
        loose_verts = sum(1 for v in bm.verts if not v.link_edges)
        loose_edges = sum(1 for e in bm.edges if not e.link_faces)
        interior = sum(1 for f in bm.faces
                       if all(len(e.link_faces) > 2 for e in f.edges) and len(f.edges) > 0)
        zero_area = sum(1 for f in bm.faces if f.calc_area() < 1e-9)
        bm.normal_update()
        outward = [(f, f.normal.copy()) for f in bm.faces]
        try:
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            inward = sum(1 for f, n in outward if f.normal.dot(n) < 0)
        except Exception:
            inward = None
        bm.free()

        out.update({
            "verts": len(me.vertices), "edges": len(me.edges), "faces": faces,
            "tris": tris, "quads": quads, "ngons": ngons,
            "tri_count_render": sum(max(0, len(p.vertices) - 2) for p in me.polygons),
            "quad_ratio": round(quads / faces, 3) if faces else 0.0,
            "ngon_ratio": round(ngons / faces, 3) if faces else 0.0,
            "non_manifold_edges": non_manifold,
            "loose_verts": loose_verts, "loose_edges": loose_edges,
            "interior_faces": interior, "zero_area_faces": zero_area,
            "inward_faces": inward,
            "uv_layers": [uv.name for uv in me.uv_layers],
            "has_uvs": len(me.uv_layers) > 0,
            "shade_smooth": any(p.use_smooth for p in me.polygons),
            "custom_normals": me.has_custom_normals,
        })
        out.update(uv_stats(me))
        out.update(safe("mesh extras %s" % obj.name,
                        lambda: mesh_extras(obj, me), {}, trouble))
    finally:
        try:
            ev.to_mesh_clear()
        except Exception:
            pass
    return out


def uv_stats(me):
    """UVs outside 0-1, and a coarse overlap estimate by grid bucketing."""
    if not me.uv_layers:
        return {"uv_outside_0_1": 0, "uv_overlap_estimate": 0.0}
    layer = me.uv_layers.active or me.uv_layers[0]
    outside = 0
    buckets = {}
    for loop in layer.data:
        u, v = loop.uv
        if u < -1e-6 or u > 1 + 1e-6 or v < -1e-6 or v > 1 + 1e-6:
            outside += 1
        buckets[(int(u * 64), int(v * 64))] = buckets.get((int(u * 64), int(v * 64)), 0) + 1
    total = max(1, len(layer.data))
    # If many loops land in the same small cell, islands are probably stacked.
    crowded = sum(c - 1 for c in buckets.values() if c > 1)
    return {"uv_outside_0_1": outside,
            "uv_overlap_estimate": round(crowded / total, 3)}


# Above this many evaluated vertices the doubles search is skipped (reported as
# null) so one huge sculpt cannot eat the whole Blender timeout.
MAX_DOUBLES_VERTS = 400_000
DOUBLES_DISTANCE = 0.0001


def largest_object_image(obj):
    """(name, w, h) of the biggest image texture on the object's materials, or None."""
    best = None
    for slot in getattr(obj, "material_slots", None) or []:
        mat = slot.material
        tree = getattr(mat, "node_tree", None) if mat else None
        if not tree:
            continue
        for node in tree.nodes:
            img = getattr(node, "image", None) if node.type == "TEX_IMAGE" else None
            if img is None:
                continue
            try:
                w, h = int(img.size[0]), int(img.size[1])
            except Exception:
                continue
            if w > 0 and h > 0 and (best is None or w * h > best[1] * best[2]):
                best = (img.name, w, h)
    return best


def mesh_extras(obj, me):
    """Doubles, UV islands and texel density, measured in world space.

    Runs on a bmesh copy of the evaluated mesh so the topology numbers above are
    untouched. Students no longer type these in; the file has to say them.
    """
    out = {"doubles": None, "uv_islands": None, "uv_area_fraction": None,
           "surface_area_m2": None, "texel_density_px_per_m": None, "texel_image": None}
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        bmesh.ops.transform(bm, matrix=obj.matrix_world, verts=bm.verts)
        bm.verts.ensure_lookup_table()

        if len(bm.verts) <= MAX_DOUBLES_VERTS:
            from mathutils import kdtree
            tree = kdtree.KDTree(len(bm.verts))
            for i, v in enumerate(bm.verts):
                tree.insert(v.co, i)
            tree.balance()
            out["doubles"] = sum(1 for v in bm.verts
                                 if len(tree.find_range(v.co, DOUBLES_DISTANCE)) > 1)

        area = sum(f.calc_area() for f in bm.faces)
        out["surface_area_m2"] = round(area, 6)

        uv = bm.loops.layers.uv.active
        if uv is None and len(bm.loops.layers.uv):
            uv = bm.loops.layers.uv[0]
        if uv is not None:
            # Union-find over faces: two faces are on one island when they share
            # a vertex at the same UV coordinate.
            parent = list(range(len(bm.faces)))

            def root(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            seen = {}
            uv_area = 0.0
            for fi, face in enumerate(bm.faces):
                pts = []
                for loop in face.loops:
                    u, v = loop[uv].uv
                    pts.append((u, v))
                    key = (loop.vert.index, round(u, 5), round(v, 5))
                    other = seen.setdefault(key, fi)
                    if other != fi:
                        a, b = root(fi), root(other)
                        if a != b:
                            parent[a] = b
                # Shoelace area of the face in UV space.
                acc = 0.0
                for k in range(len(pts)):
                    x1, y1 = pts[k]
                    x2, y2 = pts[(k + 1) % len(pts)]
                    acc += x1 * y2 - x2 * y1
                uv_area += abs(acc) / 2.0
            out["uv_islands"] = len({root(i) for i in range(len(bm.faces))})
            out["uv_area_fraction"] = round(uv_area, 4)

            img = largest_object_image(obj)
            if img and area > 1e-12 and uv_area > 0:
                out["texel_image"] = img[0]
                out["texel_density_px_per_m"] = round(
                    math.sqrt(uv_area * img[1] * img[2] / area), 1)
    finally:
        bm.free()
    return out


MODIFIER_SETTINGS = {
    "BEVEL": ("width", "segments", "limit_method", "use_clamp_overlap", "offset_type"),
    "MIRROR": ("use_clip", "use_axis", "use_bisect_axis"),
    "ARRAY": ("count", "fit_type", "use_constant_offset", "use_relative_offset"),
    "SOLIDIFY": ("thickness",),
    "SUBSURF": ("levels", "render_levels"),
    "BOOLEAN": ("operation", "solver"),
    "MULTIRES": ("levels", "render_levels", "sculpt_levels"),
}


def modifier_settings(mod):
    """The few settings a rubric asks about, for the modifier types it names."""
    out = {}
    for attr in MODIFIER_SETTINGS.get(mod.type, ()):
        if not hasattr(mod, attr):
            continue
        try:
            val = getattr(mod, attr)
            if isinstance(val, float):
                val = round(val, 5)
            elif not isinstance(val, (bool, int, str)) and val is not None:
                val = [bool(x) if isinstance(x, bool) else x for x in val]
            out[attr] = val
        except Exception:
            continue
    if mod.type == "BOOLEAN":
        target = getattr(mod, "object", None)
        out["object"] = target.name if target else None
    return out


def modifier_record(mod):
    rec = {"type": mod.type, "name": mod.name,
           "show_render": bool(getattr(mod, "show_render", True))}
    if mod.type in MODIFIER_SETTINGS:
        try:
            rec["settings"] = modifier_settings(mod)
        except Exception:
            pass
    return rec


def object_report(obj, depsgraph, trouble=None):
    rec = {
        "name": obj.name,
        "type": obj.type,
        "default_name": is_default_name(obj.name),
        "hidden": not obj.visible_get() if hasattr(obj, "visible_get") else None,
        "parent": obj.parent.name if obj.parent else None,
        "collections": [c.name for c in obj.users_collection],
        "location": rounded(obj.location),
        "dimensions": rounded(obj.dimensions, 3),
        "scale": rounded(obj.scale),
        "rotation_deg": rounded([math.degrees(a) for a in obj.rotation_euler], 2),
        "unapplied_scale": any(abs(s - 1.0) > 1e-4 for s in obj.scale),
        "unapplied_rotation": any(abs(a) > 1e-4 for a in obj.rotation_euler),
        # Stack order, top to bottom, with key settings for the common types.
        "modifiers": [modifier_record(m) for m in obj.modifiers],
        "modifier_types": sorted({m.type for m in obj.modifiers}),
        "vertex_groups": len(obj.vertex_groups) if hasattr(obj, "vertex_groups") else 0,
        "materials": [ms.material.name if ms.material else None for ms in obj.material_slots],
    }
    if obj.type == "MESH" and obj.data:
        rec["mesh"] = mesh_stats(obj, depsgraph, trouble)
        try:
            bb = [Vector(c) for c in obj.bound_box]
            centre = sum(bb, Vector()) / 8.0
            rec["origin_offset"] = round((centre).length, 4)
        except Exception:
            rec["origin_offset"] = None
    if obj.type == "ARMATURE" and obj.data:
        rec["bones"] = len(obj.data.bones)
    return rec


def material_report(trouble=None):
    trouble = trouble if trouble is not None else []
    out = []
    for mat in bpy.data.materials:
        if mat.users == 0:
            continue
        # use_nodes is deprecated in 5.x and goes away in 6.0; node_tree is the
        # forward-compatible check.
        uses_nodes = bool(getattr(mat, "node_tree", None))
        rec = {"name": mat.name, "default_name": is_default_name(mat.name),
               "use_nodes": uses_nodes, "node_types": [], "images": []}
        if mat.node_tree:
            rec["node_types"] = sorted({n.type for n in mat.node_tree.nodes})
            for n in mat.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image:
                    rec["images"].append(n.image.name)
            # Added sections. Each is guarded on its own so a node type that
            # moved in a new Blender costs this one field, not the material.
            tree = mat.node_tree
            rec["principled"] = safe("principled %s" % mat.name,
                                     lambda: principled_report(tree), [], trouble)
            rec["textures"] = safe("textures %s" % mat.name,
                                   lambda: texture_report(tree), [], trouble)
        out.append(rec)
    return out


MAX_BSDF_PER_MATERIAL = 4
MAX_TEXTURES_PER_MATERIAL = 8


def socket_value(sock):
    """A socket's default value, or "linked" when something drives it."""
    if sock is None:
        return None
    if sock.is_linked:
        return "linked"
    val = getattr(sock, "default_value", None)
    if isinstance(val, float):
        return round(val, 4)
    try:
        return rounded(val, 4)
    except Exception:
        return None


def upstream_types(sock, depth=0, seen=None):
    """Node types feeding one input socket, walking back through reroutes etc."""
    seen = seen if seen is not None else set()
    found = []
    if sock is None or depth > 8:
        return found
    for link in sock.links:
        node = link.from_node
        if node.as_pointer() in seen:
            continue
        seen.add(node.as_pointer())
        found.append(node.type)
        for inp in node.inputs:
            if inp.is_linked:
                found.extend(upstream_types(inp, depth + 1, seen))
    return found


def principled_report(tree):
    out = []
    for node in tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        normal = node.inputs.get("Normal")
        feeding = upstream_types(normal) if normal is not None else []
        out.append({
            "node": node.name,
            "base_color": socket_value(node.inputs.get("Base Color")),
            "metallic": socket_value(node.inputs.get("Metallic")),
            "roughness": socket_value(node.inputs.get("Roughness")),
            "normal_linked": bool(normal is not None and normal.is_linked),
            "normal_via_normal_map": "NORMAL_MAP" in feeding,
            "normal_via_bump": "BUMP" in feeding,
        })
        if len(out) >= MAX_BSDF_PER_MATERIAL:
            break
    return out


def downstream_targets(node, depth=0, via=(), seen=None):
    """Where one node's output ends up: 'Base Color', 'Normal via NORMAL_MAP', ..."""
    seen = seen if seen is not None else set()
    hits = []
    if depth > 8:
        return hits
    for output in node.outputs:
        for link in output.links:
            to = link.to_node
            if to.type in ("BSDF_PRINCIPLED", "OUTPUT_MATERIAL"):
                label = link.to_socket.name
                if to.type == "OUTPUT_MATERIAL":
                    label = "Material Output " + label
                if via:
                    label += " via " + ">".join(via)
                hits.append(label)
                continue
            if to.type == "GROUP":
                tree = getattr(to, "node_tree", None)
                hits.append("node group %s" % (tree.name if tree else to.name))
                continue
            key = (to.as_pointer(), output.name)
            if key in seen:
                continue
            seen.add(key)
            step = via if to.type == "REROUTE" else via + (to.type,)
            hits.extend(downstream_targets(to, depth + 1, step, seen))
    return hits


def texture_report(tree):
    out = []
    for node in tree.nodes:
        if node.type != "TEX_IMAGE":
            continue
        img = node.image
        targets = []
        for hit in downstream_targets(node):
            if hit not in targets:
                targets.append(hit)
        out.append({
            "node": node.name,
            "image": img.name if img else None,
            "colorspace": img.colorspace_settings.name if img else None,
            "feeds": targets[:6],
        })
        if len(out) >= MAX_TEXTURES_PER_MATERIAL:
            break
    return out


def is_power_of_two(n):
    return n > 0 and (n & (n - 1)) == 0


def image_report():
    out = []
    for img in bpy.data.images:
        if img.users == 0 or img.type != "IMAGE":
            continue
        path = ""
        try:
            path = bpy.path.abspath(img.filepath)
        except Exception:
            pass
        rec = {
            "name": img.name,
            "packed": bool(img.packed_file),
            "filepath": img.filepath,
            "missing": bool(img.filepath) and not img.packed_file and not os.path.exists(path),
        }
        # Added fields: what a texture rubric checks without asking the student.
        try:
            w, h = int(img.size[0]), int(img.size[1])
            rec["size"] = [w, h]
            rec["power_of_two"] = is_power_of_two(w) and is_power_of_two(h)
        except Exception:
            rec["size"] = None
            rec["power_of_two"] = None
        rec["colorspace"] = getattr(getattr(img, "colorspace_settings", None), "name", None)
        rec["source"] = getattr(img, "source", None)
        out.append(rec)
    return out


# ---------------------------------------------------------- lights & cameras
MAX_LIGHTS = 16
MAX_CAMERAS = 8
MAX_EMPTIES = 16


def subject_bounds():
    """(lo, hi) of the visible, renderable meshes; all meshes if none are visible."""
    def gather(only_visible):
        lo = Vector((1e18, 1e18, 1e18))
        hi = Vector((-1e18, -1e18, -1e18))
        found = False
        for obj in bpy.data.objects:
            if obj.type != "MESH":
                continue
            if only_visible:
                try:
                    if not obj.visible_get() or obj.hide_render:
                        continue
                except Exception:
                    pass
            for corner in obj.bound_box:
                p = obj.matrix_world @ Vector(corner)
                lo = Vector((min(lo.x, p.x), min(lo.y, p.y), min(lo.z, p.z)))
                hi = Vector((max(hi.x, p.x), max(hi.y, p.y), max(hi.z, p.z)))
                found = True
        return (lo, hi) if found else None
    return gather(True) or gather(False)


def subject_centre():
    bounds = subject_bounds()
    return (bounds[0] + bounds[1]) / 2.0 if bounds else None


def horizontal_angle(a, b):
    """Angle in degrees (0-180) between two vectors in the ground (XY) plane."""
    a2 = Vector((a.x, a.y))
    b2 = Vector((b.x, b.y))
    if a2.length < 1e-9 or b2.length < 1e-9:
        return None
    return round(math.degrees(a2.angle(b2)), 1)


def light_report(scene):
    centre = subject_centre()
    cam = scene.camera
    cam_to_subject = None
    if cam is not None and centre is not None:
        cam_to_subject = centre - cam.matrix_world.translation
    out = []
    for obj in bpy.data.objects:
        if obj.type != "LIGHT" or obj.data is None:
            continue
        light = obj.data
        loc = obj.matrix_world.translation
        aim = (obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
        rec = {
            "name": obj.name,
            "type": light.type,
            "energy": round(float(getattr(light, "energy", 0.0)), 3),
            "color": rounded(getattr(light, "color", (1, 1, 1)), 3),
            "shadow_soft_size": round(float(getattr(light, "shadow_soft_size", 0.0)), 4),
            "location": rounded(loc),
            "rotation_deg": rounded([math.degrees(a) for a in obj.matrix_world.to_euler()], 2),
            "hide_render": bool(getattr(obj, "hide_render", False)),
        }
        if light.type == "AREA":
            rec["shape"] = getattr(light, "shape", None)
            rec["size"] = round(float(getattr(light, "size", 0.0)), 4)
            if rec["shape"] in ("RECTANGLE", "ELLIPSE"):
                rec["size_y"] = round(float(getattr(light, "size_y", 0.0)), 4)
        if light.type == "SPOT":
            rec["spot_size_deg"] = round(math.degrees(getattr(light, "spot_size", 0.0)), 2)
            rec["spot_blend"] = round(float(getattr(light, "spot_blend", 0.0)), 3)
        if light.type == "SUN":
            rec["sun_angle_deg"] = round(math.degrees(getattr(light, "angle", 0.0)), 3)
        if centre is not None:
            # A sun has no position worth measuring; its direction is the light.
            to_subject = aim if light.type == "SUN" else centre - loc
            if light.type != "SUN":
                rec["distance_to_subject"] = round(to_subject.length, 4)
                if light.type in ("SPOT", "AREA") and to_subject.length > 1e-9:
                    # How far the light's beam points away from the subject.
                    rec["aim_off_subject_deg"] = round(
                        math.degrees(aim.angle(to_subject.normalized())), 1)
            from_subject = -to_subject
            flat = Vector((from_subject.x, from_subject.y)).length
            rec["elevation_deg"] = round(math.degrees(math.atan2(from_subject.z, flat)), 1)
            rec["angle_from_camera_deg"] = (
                horizontal_angle(cam_to_subject, to_subject) if cam_to_subject is not None
                else None)
        out.append(rec)
    out.sort(key=lambda r: r["energy"], reverse=True)
    return out[:MAX_LIGHTS], len(out)


# A light more than this far around the subject from the camera is behind it:
# a rim or back light, not a key or fill.
BACK_LIGHT_DEG = 110


def energy_ratio(ranked):
    if len(ranked) >= 2 and ranked[1]["energy"] > 0:
        return round(ranked[0]["energy"] / ranked[1]["energy"], 2)
    return None


def lighting_summary(lights, total):
    lit = [l for l in lights if not l.get("hide_render")]
    ranked = sorted(lit, key=lambda r: r["energy"], reverse=True)
    front = [l for l in ranked if (l.get("angle_from_camera_deg") or 0) < BACK_LIGHT_DEG]
    types = {l["type"] for l in ranked[:2]}
    return {
        "count": total,
        "render_enabled": len(lit),
        "by_energy": [[l["name"], l["type"], l["energy"]] for l in ranked],
        "strongest": ranked[0]["name"] if ranked else None,
        "second": ranked[1]["name"] if len(ranked) > 1 else None,
        # Strongest over second strongest, whatever side of the subject they are on.
        "key_fill_ratio": energy_ratio(ranked),
        # The same, leaving out lights behind the subject (rim/back lights).
        "back_lights": [l["name"] for l in ranked
                        if (l.get("angle_from_camera_deg") or 0) >= BACK_LIGHT_DEG],
        "key_fill_ratio_front": energy_ratio(front),
        "front_pair": [l["name"] for l in front[:2]],
        # Sun strength is W/m2 and the others are W, so the ratio is not like for like.
        "ratio_mixes_sun": "SUN" in types and len(types) > 1,
    }


def camera_report(scene):
    cams = [o for o in bpy.data.objects if o.type == "CAMERA"]
    out = {"count": len(cams), "names": [c.name for c in cams][:MAX_CAMERAS],
           "active": None}
    cam = scene.camera
    if cam is None or cam.type != "CAMERA" or cam.data is None:
        return out
    data = cam.data
    active = {
        "name": cam.name,
        "type": data.type,
        "lens_mm": round(float(data.lens), 3),
        "sensor_width_mm": round(float(data.sensor_width), 3),
        "sensor_fit": getattr(data, "sensor_fit", None),
        "location": rounded(cam.matrix_world.translation),
        "rotation_deg": rounded([math.degrees(a) for a in cam.matrix_world.to_euler()], 2),
        "dof": bool(getattr(getattr(data, "dof", None), "use_dof", False)),
    }
    if data.type == "ORTHO":
        active["ortho_scale"] = round(float(data.ortho_scale), 4)
    bounds = subject_bounds()
    if bounds:
        try:
            from bpy_extras.object_utils import world_to_camera_view
            lo, hi = bounds
            corners = [Vector((x, y, z)) for x in (lo.x, hi.x)
                       for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
            inside = 0
            for c in corners:
                p = world_to_camera_view(scene, cam, c)
                if p.z > 0 and -0.001 <= p.x <= 1.001 and -0.001 <= p.y <= 1.001:
                    inside += 1
            centre = world_to_camera_view(scene, cam, (lo + hi) / 2.0)
            active["subject_corners_in_frame"] = inside
            active["subject_centre_in_frame"] = rounded(centre[:2], 3) if centre.z > 0 else None
        except Exception:
            pass
    out["active"] = active
    return out


def render_report(scene):
    r = scene.render
    eevee = getattr(scene, "eevee", None)
    cycles = getattr(scene, "cycles", None)
    img = r.image_settings
    vs = scene.view_settings
    return {
        "engine": r.engine,
        "eevee_samples": getattr(eevee, "taa_render_samples", None)
        if eevee is not None else None,
        "cycles_samples": getattr(cycles, "samples", None) if cycles is not None else None,
        "cycles_denoising": getattr(cycles, "use_denoising", None)
        if cycles is not None else None,
        "resolution": [r.resolution_x, r.resolution_y],
        "resolution_percentage": r.resolution_percentage,
        "file_format": getattr(img, "file_format", None),
        "color_mode": getattr(img, "color_mode", None),
        "color_depth": getattr(img, "color_depth", None),
        "film_transparent": bool(getattr(r, "film_transparent", False)),
        "view_transform": getattr(vs, "view_transform", None),
        "look": getattr(vs, "look", None),
        "exposure": round(float(getattr(vs, "exposure", 0.0)), 4),
        "gamma": round(float(getattr(vs, "gamma", 1.0)), 4),
        "display_device": getattr(scene.display_settings, "display_device", None),
    }


def world_report(scene):
    world = scene.world
    out = {"name": world.name if world else None, "hdri": False, "hdri_image": None,
           "hdri_size": None, "background_color": None, "strength": None}
    tree = getattr(world, "node_tree", None) if world else None
    if not tree:
        if world is not None:
            out["background_color"] = rounded(getattr(world, "color", (0, 0, 0)), 3)
        return out
    for node in tree.nodes:
        if node.type == "TEX_ENVIRONMENT" and getattr(node, "image", None):
            out["hdri"] = True
            out["hdri_image"] = node.image.name
            try:
                out["hdri_size"] = [int(node.image.size[0]), int(node.image.size[1])]
            except Exception:
                pass
            break
    for node in tree.nodes:
        if node.type == "BACKGROUND":
            color = node.inputs.get("Color")
            strength = node.inputs.get("Strength")
            out["background_color"] = socket_value(color)
            out["strength"] = socket_value(strength)
            break
    return out


def empty_report():
    out = []
    for obj in bpy.data.objects:
        if obj.type != "EMPTY":
            continue
        rec = {
            "name": obj.name,
            "display_type": getattr(obj, "empty_display_type", None),
            "display_size": round(float(getattr(obj, "empty_display_size", 1.0)), 4),
            "location": rounded(obj.matrix_world.translation),
            "rotation_deg": rounded([math.degrees(a) for a in obj.matrix_world.to_euler()], 2),
            "hide_select": bool(getattr(obj, "hide_select", False)),
        }
        if rec["display_type"] == "IMAGE":
            img = obj.data if isinstance(getattr(obj, "data", None), bpy.types.Image) else None
            rec["image"] = img.name if img else None
            if img is not None:
                try:
                    rec["image_size"] = [int(img.size[0]), int(img.size[1])]
                except Exception:
                    pass
            rec["image_depth"] = getattr(obj, "empty_image_depth", None)
            rec["image_side"] = getattr(obj, "empty_image_side", None)
            rec["use_alpha"] = bool(getattr(obj, "use_empty_image_alpha", False))
            try:
                rec["opacity"] = round(float(obj.color[3]), 3)
            except Exception:
                rec["opacity"] = None
            rec["show_in_front"] = bool(getattr(obj, "show_in_front", False))
        out.append(rec)
    # Image empties first: they are the reference images a rubric asks about.
    out.sort(key=lambda r: r["display_type"] != "IMAGE")
    return out[:MAX_EMPTIES]


def collect_stats():
    trouble = []
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objects = [object_report(o, depsgraph, trouble) for o in bpy.data.objects]
    meshes = [o for o in objects if o["type"] == "MESH" and "mesh" in o]

    def total(key):
        return sum((o["mesh"].get(key) or 0) for o in meshes)

    stats = {
        "blender_version": bpy.app.version_string,
        "file_version": list(bpy.data.version),
        "filepath": bpy.data.filepath,
        "scene": {
            "name": scene.name,
            "unit_system": scene.unit_settings.system,
            "scale_length": round(scene.unit_settings.scale_length, 6),
            "engine": scene.render.engine,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "frame_start": scene.frame_start, "frame_end": scene.frame_end,
            "bounds_size": safe("bounds", scene_size, None, trouble),
        },
        "counts": {
            "objects": len(bpy.data.objects),
            "by_type": {},
            "collections": len(bpy.data.collections),
            "materials": len([m for m in bpy.data.materials if m.users]),
            "images": len([i for i in bpy.data.images if i.users and i.type == "IMAGE"]),
            "actions": len(bpy.data.actions),
        },
        "totals": {
            "verts": total("verts"), "faces": total("faces"),
            "tris_render": total("tri_count_render"),
            "ngons": total("ngons"), "quads": total("quads"), "tris": total("tris"),
            "non_manifold_edges": total("non_manifold_edges"),
            "loose_verts": total("loose_verts"), "loose_edges": total("loose_edges"),
            "zero_area_faces": total("zero_area_faces"),
            "interior_faces": total("interior_faces"),
            "inward_faces": total("inward_faces"),
        },
        "hygiene": {
            "objects_with_unapplied_scale":
                [o["name"] for o in objects if o.get("unapplied_scale")],
            "objects_with_unapplied_rotation":
                [o["name"] for o in objects if o.get("unapplied_rotation")],
            "default_named_objects": [o["name"] for o in objects if o["default_name"]],
            "meshes_without_uvs":
                [o["name"] for o in meshes if not o["mesh"].get("has_uvs")],
            "live_modifiers": sorted({m["type"] for o in objects for m in o["modifiers"]}),
        },
        "objects": objects,
        "materials": safe("materials", lambda: material_report(trouble), [], trouble),
        "images": safe("images", image_report, [], trouble),
        "animation": safe("animation", animation_report,
                          {"actions": [], "fcurves": 0, "keyframes": 0}, trouble),
    }
    # Added sections: what students used to type in by hand (lighting notes,
    # render settings, self-checks) read straight from the file instead.
    lights, light_total = safe("lights", lambda: light_report(scene), ([], 0), trouble)
    stats["lights"] = lights
    stats["lighting"] = safe("lighting", lambda: lighting_summary(lights, light_total),
                             None, trouble)
    stats["cameras"] = safe("cameras", lambda: camera_report(scene), None, trouble)
    stats["render"] = safe("render", lambda: render_report(scene), None, trouble)
    stats["world"] = safe("world", lambda: world_report(scene), None, trouble)
    stats["empties"] = safe("empties", empty_report, [], trouble)
    stats["subject_centre"] = safe(
        "subject centre", lambda: (rounded(subject_centre()) if subject_centre() else None),
        None, trouble)
    stats["totals"]["doubles"] = sum((o["mesh"].get("doubles") or 0) for o in meshes)
    for o in bpy.data.objects:
        stats["counts"]["by_type"][o.type] = stats["counts"]["by_type"].get(o.type, 0) + 1

    # Scene-level fingerprint, for spotting two students who submitted the same
    # model. One hash over evaluated geometry beats one per object.
    digest = hashlib.sha256()
    for o in sorted(meshes, key=lambda r: r["name"]):
        m = o.get("mesh") or {}
        digest.update(("%s:%s:%s;" % (m.get("verts"), m.get("faces"),
                                      m.get("tri_count_render"))).encode())
    stats["geometry_sha256"] = digest.hexdigest()[:16]

    # Trim per-object detail to the biggest meshes. Aggregates above already
    # describe the whole scene.
    detail = sorted(objects, key=lambda o: (o.get("mesh") or {}).get("faces", 0), reverse=True)
    stats["objects"] = detail[:MAX_OBJECT_DETAIL]
    stats["objects_omitted"] = max(0, len(objects) - MAX_OBJECT_DETAIL)

    faces = stats["totals"]["faces"]
    stats["totals"]["quad_ratio"] = round(stats["totals"]["quads"] / faces, 3) if faces else 0.0
    stats["totals"]["ngon_ratio"] = round(stats["totals"]["ngons"] / faces, 3) if faces else 0.0
    stats["hygiene"]["missing_textures"] = [i["name"] for i in stats["images"] if i["missing"]]
    stats["section_errors"] = trouble
    return stats


# ------------------------------------------------------------------- render
def scene_size():
    """World-axis size of the renderable scene: width, depth, height.

    This is the measurement a screenshot cannot be trusted for. None when
    the file has nothing with a bounding box.
    """
    lo = Vector((1e18, 1e18, 1e18))
    hi = Vector((-1e18, -1e18, -1e18))
    found = False
    for obj in bpy.data.objects:
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            continue
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            lo = Vector((min(lo.x, point.x), min(lo.y, point.y), min(lo.z, point.z)))
            hi = Vector((max(hi.x, point.x), max(hi.y, point.y), max(hi.z, point.z)))
            found = True
    if not found:
        return None
    size = hi - lo
    return [round(size.x, 4), round(size.y, 4), round(size.z, 4)]


def scene_bounds():
    """World-space bounding box of everything renderable."""
    lo = Vector((1e18, 1e18, 1e18))
    hi = Vector((-1e18, -1e18, -1e18))
    found = False
    for obj in bpy.data.objects:
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            continue
        for corner in obj.bound_box:
            p = obj.matrix_world @ Vector(corner)
            lo = Vector((min(lo.x, p.x), min(lo.y, p.y), min(lo.z, p.z)))
            hi = Vector((max(hi.x, p.x), max(hi.y, p.y), max(hi.z, p.z)))
            found = True
    if not found:
        return Vector((0, 0, 0)), 2.0
    centre = (lo + hi) / 2.0
    radius = max((hi - lo).length / 2.0, 0.001)
    return centre, radius


def look_at(cam, target):
    direction = target - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def pick_engine(scene, preferred=("BLENDER_WORKBENCH", "BLENDER_EEVEE_NEXT",
                                  "BLENDER_EEVEE", "CYCLES")):
    """Engine identifiers churn between versions; read the valid set."""
    try:
        valid = {i.identifier for i in
                 bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    except Exception:
        valid = set()
    for name in preferred:
        if not valid or name in valid:
            try:
                scene.render.engine = name
                return name
            except Exception:
                continue
    return scene.render.engine


def render_views(out_dir):
    """Solid renders from four angles plus a wireframe, using Workbench.

    Workbench synthesizes its own studio lighting, so a student scene with no
    lights still shows geometry. Under EEVEE or Cycles it would render black and
    a black tile is indistinguishable from a broken model.
    """
    scene = bpy.context.scene
    centre, radius = scene_bounds()
    dist = radius * 3.2

    cam_data = bpy.data.cameras.new("cg_cam")
    # Student scenes turn up at 0.001 m and at 400 m. Default clip planes would
    # cut the whole model away and every tile would come out empty, with no error.
    cam_data.clip_start = max(1e-5, radius * 0.01)
    cam_data.clip_end = max(1.0, radius * 100.0)
    cam = bpy.data.objects.new("cg_cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    pick_engine(scene)
    scene.render.resolution_x = scene.render.resolution_y = RENDER_SIZE
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    # A student who set their output to video leaves image_settings restricted to
    # ("FFMPEG",), and assigning "PNG" raises TypeError. media_type, new in 5.0, is
    # what puts the still formats back on the menu.
    if hasattr(scene.render.image_settings, "media_type"):
        try:
            scene.render.image_settings.media_type = "IMAGE"
        except Exception:
            pass
    scene.render.image_settings.file_format = "PNG"
    try:
        shading = scene.display.shading
        shading.light = "STUDIO"
        shading.color_type = "OBJECT"
        shading.show_cavity = True
    except Exception:
        pass

    angles = {
        "front":  Vector((0, -dist, 0)),
        "right":  Vector((dist, 0, 0)),
        "top":    Vector((0, 0, dist)),
        "persp":  Vector((dist * 0.62, -dist * 0.62, dist * 0.45)),
    }
    written = {}
    for name, offset in angles.items():
        cam.location = centre + offset
        look_at(cam, centre)
        path = os.path.join(out_dir, "view_%s.png" % name)
        scene.render.filepath = path
        try:
            bpy.ops.render.render(write_still=True)
        except Exception as exc:
            print("RENDER_FAIL %s %s" % (name, exc))
            continue
        # The operator returns FINISHED even when nothing reached disk, so the
        # file is the only proof. Trusting the return code recorded five views
        # for a scene that wrote none of them.
        if os.path.exists(path):
            written[name] = path
        else:
            print("RENDER_FAIL %s reported success but wrote nothing" % name)

    # Wireframe pass from the same 3/4 angle, for topology.
    try:
        scene.display.shading.show_xray = False
        scene.display.shading.type = "WIREFRAME"
        cam.location = centre + angles["persp"]
        look_at(cam, centre)
        path = os.path.join(out_dir, "view_wire.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        if os.path.exists(path):
            written["wire"] = path
        else:
            print("RENDER_FAIL wire reported success but wrote nothing")
    except Exception as exc:
        print("RENDER_FAIL wire %s" % exc)
    return written


def prune_broken_textures():
    """Drop image-texture nodes whose image has no pixels, before exporting.

    Students routinely submit a .blend without the texture folder beside it, so
    the file references //textures/brown.jpg and Blender has nothing to load. The
    glTF exporter still writes a texture entry for it, but with no source, and
    three.js then does json.images[undefined] and throws
    "Cannot read properties of undefined". Removing the dead nodes first keeps
    the .glb valid. The missing files are already recorded in the stats, so this
    costs no grading signal.
    """
    removed = []
    for mat in bpy.data.materials:
        tree = getattr(mat, "node_tree", None)
        if not tree:
            continue
        for node in list(tree.nodes):
            if node.type != "TEX_IMAGE":
                continue
            img = node.image
            broken = img is None
            if not broken and not img.packed_file:
                try:
                    on_disk = os.path.exists(bpy.path.abspath(img.filepath)) if img.filepath else False
                except Exception:
                    on_disk = False
                broken = (not on_disk) or tuple(img.size) == (0, 0)
            if broken:
                removed.append("%s / %s" % (mat.name, img.name if img else "no image"))
                try:
                    tree.nodes.remove(node)
                except Exception:
                    pass
    return removed


def export_glb(out_dir):
    path = os.path.join(out_dir, "model.glb")
    full = dict(filepath=path, export_format="GLB", export_apply=True,
                export_cameras=False, export_lights=False, use_visible=True,
                export_animations=False, export_image_format="WEBP",
                export_image_quality=75)
    try:
        bpy.ops.export_scene.gltf(**full)
    except TypeError:
        # The glTF operator's keyword names move between Blender versions. Fall
        # back to the stable core so a rename costs a bigger file, not a failure.
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", export_apply=True)
    return path if os.path.exists(path) else None


def harden_scene():
    """Defuse a student file before we evaluate or render it.

    Two real problems. A .blend stores image paths, and one pointing at a UNC
    share (\\\\host\\share\\x.png) makes Windows open an SMB session and leak an
    NTLM hash when Blender resolves it. And a Subdivision modifier left at level
    8 will exhaust memory long before any wall-clock timeout fires.
    """
    notes = []
    blend_dir = os.path.dirname(bpy.data.filepath or "")

    for img in list(bpy.data.images):
        if img.packed_file or not img.filepath:
            continue
        raw = img.filepath
        try:
            resolved = bpy.path.abspath(raw)
        except Exception:
            resolved = raw
        unc = raw.startswith("//\\\\") or raw.startswith("\\\\") or resolved.startswith("\\\\")
        outside = bool(blend_dir) and not os.path.abspath(resolved).lower().startswith(
            os.path.abspath(blend_dir).lower())
        if unc or outside:
            notes.append("external reference blocked: %s -> %s" % (img.name, raw))
            try:
                img.filepath = ""
            except Exception:
                pass

    for obj in bpy.data.objects:
        for mod in getattr(obj, "modifiers", []):
            for attr, cap in (("levels", 2), ("render_levels", 2), ("sculpt_levels", 2)):
                if hasattr(mod, attr):
                    try:
                        if getattr(mod, attr) > cap:
                            notes.append("clamped %s.%s.%s from %d to %d" % (
                                obj.name, mod.name, attr, getattr(mod, attr), cap))
                            setattr(mod, attr, cap)
                    except Exception:
                        pass
    return notes


def looks_like_startup_file():
    """The untouched Blender startup scene: one default Cube, a Camera, a Light."""
    names = sorted(o.name for o in bpy.data.objects)
    if names != ["Camera", "Cube", "Light"]:
        return False
    cube = bpy.data.objects.get("Cube")
    try:
        return (len(cube.data.vertices) == 8 and len(cube.data.polygons) == 6
                and not cube.modifiers)
    except Exception:
        return False


# --------------------------------------------------------------------- main
def main():
    args = argv_after_dashes()
    out_dir = opt(args, "--out")
    if not out_dir:
        print("BLEND_EXTRACT_ERROR no --out given")
        return 2
    # Blender resolves a relative render path against its own idea of the
    # current directory, which is not the caller's. A scene whose output was
    # set to video silently wrote its stills nowhere at all under a relative
    # path while still reporting success.
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    result = {"ok": True, "errors": [], "notes": []}
    try:
        result["notes"] = harden_scene()
    except Exception:
        result["errors"].append("harden: " + traceback.format_exc()[-400:])

    try:
        result["stats"] = collect_stats()
        for note in result["stats"].pop("section_errors", []):
            result["ok"] = False
            result["errors"].append(note)
        result["default_scene"] = looks_like_startup_file()
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        result["empty_scene"] = len(meshes) == 0
    except Exception:
        result["ok"] = False
        result["errors"].append("stats: " + traceback.format_exc()[-900:])
        result["stats"] = {}

    if "--no-render" not in args:
        try:
            result["views"] = render_views(out_dir)
        except Exception:
            result["errors"].append("render: " + traceback.format_exc()[-900:])
            result["views"] = {}
        if not result["views"]:
            result["ok"] = False
            result["errors"].append(
                "render: no views were written; the scene rendered nothing")

    if "--no-glb" not in args:
        try:
            # After stats, so missing textures are still reported, but before
            # export, so the .glb does not carry dangling texture references.
            pruned = prune_broken_textures()
            if pruned:
                result["pruned_textures"] = pruned[:20]
                result["notes"].append(
                    "removed %d texture node(s) with no image data before exporting "
                    "the 3D preview (the .blend was submitted without its textures)"
                    % len(pruned))
            glb = export_glb(out_dir)
            result["glb"] = glb
            result["glb_bytes"] = os.path.getsize(glb) if glb else 0
        except Exception:
            result["errors"].append("glb: " + traceback.format_exc()[-900:])
            result["glb"] = None

    with open(os.path.join(out_dir, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print("BLEND_EXTRACT_DONE")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("BLEND_EXTRACT_ERROR " + traceback.format_exc()[-1500:])
        sys.exit(1)
