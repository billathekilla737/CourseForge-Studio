"""The grader reads a .blend from the scene report, not from a picture."""
import ast
import copy
import unittest
from pathlib import Path

from courseforge import blender, grader


class SceneReportCarriesTheFile(unittest.TestCase):
    def test_materials_textures_and_size_are_in_the_report(self):
        report = blender.scene_report({
            "ok": True,
            "stats": {
                "file_version": [4, 2, 0],
                "counts": {"objects": 1, "by_type": {"MESH": 1},
                           "collections": 1, "materials": 1, "images": 1},
                "totals": {"verts": 8, "faces": 6, "tris_render": 12,
                           "quads": 6, "tris": 0, "ngons": 0, "quad_ratio": 1,
                           "non_manifold_edges": 0, "loose_verts": 0,
                           "interior_faces": 0, "zero_area_faces": 0},
                "hygiene": {},
                "scene": {"unit_system": "METRIC", "scale_length": 1,
                          "engine": "BLENDER_EEVEE", "bounds_size": [2, 1, 0.5]},
                "materials": [{
                    "name": "Wood", "default_name": False,
                    "node_types": ["BSDF_PRINCIPLED", "TEX_IMAGE"],
                    "images": ["planks.png"],
                }],
                "images": [{"name": "planks.png", "packed": True, "filepath": ""}],
                "objects": [{
                    "name": "Crate", "type": "MESH",
                    "dimensions": [2, 1, 0.5], "origin_offset": 0.1,
                    "materials": ["Wood"], "vertex_groups": 2,
                    "mesh": {"faces": 6, "ngons": 0, "has_uvs": True,
                             "uv_outside_0_1": 0, "shade_smooth": False},
                }],
            },
        }, "crate.blend")
        self.assertIn("Material graphs", report)
        self.assertIn("planks.png", report)
        self.assertIn("Images packed into the .blend: 1 (planks.png)", report)
        self.assertIn("Scene size in world units", report)
        self.assertIn("Final Tris (Statistics overlay count): 12.", report)
        self.assertIn("Mesh audit from the file: 0 non-manifold edges", report)
        self.assertIn("Merge by Distance does not leave its removed-vertex count", report)
        self.assertIn("size 2 x 1 x 0.5", report)
        self.assertIn("materials: Wood", report)


class BlendGradingIgnoresThePicture(unittest.TestCase):
    def test_the_prompt_trusts_the_report_and_drops_the_sheet(self):
        entry = {
            "user_id": "7", "status": "submitted", "words": 80,
            "text": "Blender scene report for crate.blend\nObjects: 1.",
            "sheets": ["7_crate.sheet.png"],
            "images": ["C:/shots/screen.png"],
            "filenames": ["crate.blend"],
        }
        prompt = grader.build_prompt(
            {"name": "Crate", "points_possible": 10, "description": ""},
            [{"id": "c1", "label": "Model", "points": 10, "detail": "", "ratings": []}],
            entry, "", "S-001")
        self.assertIn("Never say you could not open", prompt)
        self.assertIn("lacks a stated Tris number", prompt)
        self.assertIn("lacks a merge count", prompt)
        self.assertIn("Face Orientation", prompt)
        self.assertNotIn("Use the images only to judge form", prompt)
        self.assertTrue(grader.blend_report_present(entry))

    def test_the_prompt_says_settings_come_from_the_file(self):
        entry = {"user_id": "7", "status": "submitted", "words": 80,
                 "text": "Blender scene report for crate.blend\nObjects: 1.",
                 "filenames": ["crate.blend"]}
        prompt = grader.build_prompt(
            {"name": "Crate", "points_possible": 10, "description": ""},
            [{"id": "c1", "label": "Lighting", "points": 10, "detail": "", "ratings": []}],
            entry, "", "S-001")
        self.assertIn("key:fill ratio", prompt)
        self.assertIn("Students no longer type a self-check, lighting notes or an "
                      "export log", prompt)
        self.assertIn("Doubles measured in the file", prompt)


# Stats shaped like the extractor's output on a three-point lighting scene.
SETUP_STATS = {
    "file_version": [5, 2, 0],
    "counts": {"objects": 6, "by_type": {"MESH": 1, "LIGHT": 3, "CAMERA": 1, "EMPTY": 1},
               "collections": 1, "materials": 1, "images": 2},
    "totals": {"verts": 112, "faces": 114, "tris_render": 224, "quads": 108, "tris": 6,
               "ngons": 0, "quad_ratio": 0.947, "non_manifold_edges": 0,
               "loose_verts": 0, "interior_faces": 0, "zero_area_faces": 0,
               "doubles": 0},
    "hygiene": {"live_modifiers": ["BEVEL", "MIRROR"]},
    "scene": {"unit_system": "METRIC", "scale_length": 1, "engine": "BLENDER_EEVEE"},
    "materials": [{
        "name": "CrateWood", "default_name": False,
        "node_types": ["BSDF_PRINCIPLED", "NORMAL_MAP", "TEX_IMAGE"],
        "images": ["crate_base.png", "crate_normal.png"],
        "principled": [{"node": "Principled BSDF", "base_color": "linked",
                        "metallic": 0.0, "roughness": 0.65, "normal_linked": True,
                        "normal_via_normal_map": True, "normal_via_bump": False}],
        "textures": [
            {"node": "Image Texture", "image": "crate_base.png",
             "colorspace": "sRGB", "feeds": ["Base Color"]},
            {"node": "Image Texture.001", "image": "crate_normal.png",
             "colorspace": "Non-Color", "feeds": ["Normal via NORMAL_MAP"]}],
    }],
    "images": [
        {"name": "crate_base.png", "packed": True, "filepath": "", "missing": False,
         "size": [1024, 1024], "power_of_two": True, "colorspace": "sRGB",
         "source": "FILE"},
        {"name": "ref_front.png", "packed": False, "filepath": "//ref_front.png",
         "missing": False, "size": [1000, 600], "power_of_two": False,
         "colorspace": "sRGB", "source": "FILE"}],
    "lights": [
        {"name": "Key", "type": "AREA", "energy": 1000.0, "color": [1.0, 0.95, 0.9],
         "shadow_soft_size": 0.0, "location": [-4, -4, 4], "hide_render": False,
         "shape": "RECTANGLE", "size": 2.0, "size_y": 1.0,
         "distance_to_subject": 7.81, "aim_off_subject_deg": 11.5,
         "elevation_deg": 22.6, "angle_from_camera_deg": 43.8},
        {"name": "Rim", "type": "SPOT", "energy": 500.0, "color": [1, 1, 1],
         "shadow_soft_size": 0.0, "location": [0, 6, 4], "hide_render": False,
         "spot_size_deg": 40.0, "spot_blend": 0.3, "distance_to_subject": 7.0,
         "elevation_deg": 25.4, "angle_from_camera_deg": 149.0},
        {"name": "Fill", "type": "POINT", "energy": 250.0, "color": [1, 1, 1],
         "shadow_soft_size": 0.5, "location": [5, -4, 2], "hide_render": False,
         "distance_to_subject": 5.1, "elevation_deg": 11.3,
         "angle_from_camera_deg": 49.4}],
    "lighting": {"count": 3, "render_enabled": 3, "strongest": "Key", "second": "Rim",
                 "key_fill_ratio": 2.0, "back_lights": ["Rim"],
                 "key_fill_ratio_front": 4.0, "front_pair": ["Key", "Fill"],
                 "ratio_mixes_sun": False},
    "cameras": {"count": 1, "names": ["ShotCam"], "active": {
        "name": "ShotCam", "type": "PERSP", "lens_mm": 50.0, "sensor_width_mm": 36.0,
        "location": [0, -9, 3], "rotation_deg": [80, 0, 0], "dof": False,
        "subject_corners_in_frame": 8}},
    "render": {"engine": "BLENDER_EEVEE", "eevee_samples": 32, "cycles_samples": 256,
               "cycles_denoising": True, "resolution": [1920, 1080],
               "resolution_percentage": 50, "file_format": "PNG", "color_mode": "RGBA",
               "film_transparent": False, "view_transform": "AgX", "look": "None",
               "exposure": 0.5, "gamma": 1.0, "display_device": "sRGB"},
    "world": {"name": "StudioWorld", "hdri": True, "hdri_image": "studio_env.exr",
              "hdri_size": [2048, 1024], "background_color": "linked", "strength": 0.8},
    "empties": [{"name": "RefFront", "display_type": "IMAGE", "display_size": 4.0,
                 "hide_select": True, "image": "ref_front.png", "image_size": [1000, 600],
                 "image_depth": "BACK", "use_alpha": True, "opacity": 0.5}],
    "objects": [{
        "name": "Crate", "type": "MESH", "dimensions": [2, 2, 2],
        "materials": ["CrateWood"], "modifier_types": ["BEVEL", "MIRROR"],
        "modifiers": [
            {"type": "MIRROR", "name": "Mirror", "show_render": True,
             "settings": {"use_clip": True, "use_axis": [True, False, False],
                          "use_bisect_axis": [False, False, False]}},
            {"type": "BEVEL", "name": "Bevel", "show_render": True,
             "settings": {"width": 0.05, "segments": 3, "limit_method": "ANGLE",
                          "use_clamp_overlap": True}}],
        "mesh": {"faces": 114, "ngons": 0, "has_uvs": True, "doubles": 0,
                 "uv_islands": 1, "texel_density_px_per_m": 168.8,
                 "texel_image": "crate_base.png"},
    }],
}


class SceneSetupIsReadFromTheFile(unittest.TestCase):
    def report(self, stats):
        return blender.scene_report({"ok": True, "stats": stats}, "crate.blend")

    def test_lights_and_the_key_fill_ratio(self):
        report = self.report(SETUP_STATS)
        self.assertIn("Lights in the file: 3.", report)
        self.assertIn("(key:fill) 2:1 (Key over Rim)", report)
        self.assertIn("Behind the subject (rim/back): Rim.", report)
        self.assertIn("Key:fill among the front lights 4:1 (Key over Fill)", report)
        self.assertIn("Key [AREA] 1000 W; colour (1, 0.95, 0.9); rectangle 2 x 1 m", report)
        self.assertIn("43.8 deg around the subject from the camera", report)
        self.assertIn("Rim [SPOT] 500 W; cone 40 deg, blend 0.3", report)

    def test_camera_render_world_and_empties(self):
        report = self.report(SETUP_STATS)
        self.assertIn("Active camera: ShotCam (PERSP, 50 mm lens, 36 mm sensor)", report)
        self.assertIn("8 of 8 corners", report)
        self.assertIn("EEVEE samples 32, Cycles samples 256 (denoising on)", report)
        self.assertIn("resolution 1920x1080 at 50%, output PNG RGBA", report)
        self.assertIn("view transform AgX, look None, exposure 0.5, gamma 1, display sRGB",
                      report)
        self.assertIn("environment (HDRI) image studio_env.exr 2048x1024", report)
        self.assertIn("RefFront [IMAGE]; display size 4; image ref_front.png 1000x600; "
                      "depth BACK; opacity 0.5 (transparency on); locked from selection",
                      report)

    def test_textures_materials_and_mesh_extras(self):
        report = self.report(SETUP_STATS)
        self.assertIn("crate_base.png: 1024x1024, power of two, colour space sRGB, packed",
                      report)
        self.assertIn("ref_front.png: 1000x600, not power of two", report)
        self.assertIn("Base Color linked, Metallic 0, Roughness 0.65, "
                      "Normal fed through a Normal Map node", report)
        self.assertIn("image crate_normal.png (Non-Color) -> Normal via NORMAL_MAP", report)
        self.assertIn("Doubles measured in the file: 0 vertices", report)
        self.assertIn("1 UV islands; texel density 168.8 px/m (crate_base.png)", report)
        self.assertIn("stack: Mirror MIRROR(clipping on, axis X) > Bevel BEVEL(width 0.05, "
                      "segments 3, limit ANGLE, clamp overlap on)", report)

    def test_a_sun_ratio_is_flagged_and_its_position_ignored(self):
        stats = copy.deepcopy(SETUP_STATS)
        stats["lights"] = [{"name": "Sun", "type": "SUN", "energy": 4.0, "color": [1, 1, 1],
                            "sun_angle_deg": 0.5, "location": [0, 0, 0],
                            "elevation_deg": 45.0, "angle_from_camera_deg": 30.0}]
        stats["lighting"] = {"count": 1, "ratio_mixes_sun": True}
        report = self.report(stats)
        self.assertIn("Sun [SUN] 4 W/m2; sun angle 0.5 deg", report)
        self.assertNotIn("at (0, 0, 0)", report)
        self.assertIn("not like for like", report)

    def test_an_older_pass_without_the_new_sections_still_reads(self):
        stats = copy.deepcopy(SETUP_STATS)
        for key in ("lights", "lighting", "cameras", "render", "world", "empties"):
            stats.pop(key)
        stats["totals"].pop("doubles")
        for img in stats["images"]:
            for key in ("size", "power_of_two", "colorspace", "source"):
                img.pop(key)
        report = self.report(stats)
        self.assertIn("Final Tris (Statistics overlay count): 224.", report)
        for missing in ("Lights in the file", "Active camera", "Render settings",
                        "World ", "Empties:", "Image sizes", "Doubles measured"):
            self.assertNotIn(missing, report)


class TheExtractorGuardsEveryNewSection(unittest.TestCase):
    """blend_extract.py runs inside Blender, so check its structure, not its output."""

    SCRIPT = Path(blender.__file__).parent / "scripts" / "blend_extract.py"

    def test_each_new_section_goes_through_safe(self):
        tree = ast.parse(self.SCRIPT.read_text(encoding="utf-8"))
        guarded = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "safe"
                    and node.args):
                continue
            label = node.args[0]
            if isinstance(label, ast.BinOp):        # "textures %s" % mat.name
                label = label.left
            if isinstance(label, ast.Constant):
                guarded.add(str(label.value).split(" ")[0])
        for label in ("lights", "lighting", "cameras", "render", "world", "empties",
                      "materials", "images", "principled", "textures", "mesh"):
            self.assertIn(label, guarded)
