"""Smoke test for the installed extension (registration, prefs, one pipeline run).

blender --background --python scripts/test_addon.py -- [mesh_name]
"""
import importlib
import os
import sys

import addon_utils
import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
name = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "suzanne"
mod_name = "bl_ext.user_default.flowtopo"
addon_utils.enable(mod_name, default_set=True, persistent=False)
ft = importlib.import_module(mod_name)
print("REGISTERED", hasattr(bpy.types.Scene, "flowtopo"), hasattr(bpy.ops.flowtopo, "run"))
prefs = bpy.context.preferences.addons[mod_name].preferences
prefs.data_dir = os.path.join(ROOT, "out")
api = ft._configure_env()
print("DATA", api.data_dir(), "KEY", bool(api.api_key()), "SPENT %.4f" % api.spent_usd())

pipeline = importlib.import_module(mod_name + ".pipeline")
bpy.ops.wm.read_homefile(use_empty=True)
addon_utils.enable(mod_name, default_set=True, persistent=False)
bpy.ops.wm.obj_import(filepath=os.path.join(ROOT, "assets", "meshes", name + "_messy.obj"))
src = bpy.context.selected_objects[0]
src.name = name
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.render.resolution_x = 1234
run = pipeline.FlowTopoRun(src, os.path.join(ROOT, "out", "runs", name + "_addon"), pipeline.Options(baselines=True))
for frac, msg in run.steps():
    print("STEP %3d%% %s" % (frac * 100, msg))
print("RESTORED engine=%s res_x=%d camera_obj=%s" % (scene.render.engine, scene.render.resolution_x,
                                                     bpy.data.objects.get("FT_Camera")))
r = run.result
print("PLAN", r["plan"]["subject"], r["plan"]["category"], [(f["name"], f["sam_prompt"], f["loop_type"]) for f in r["plan"]["features"]])
print("DETECT", r.get("detections"))
print("METRICS aligned", r["metrics"].get("aligned_pct"), "faces", r["metrics"]["faces"])
for b in ("baseline_qremeshify", "baseline_quadriflow"):
    if b in r:
        print("BASE", b, r[b].get("aligned_pct"))
if "critique" in r:
    print("CRITIQUE", r["critique"]["score"], r["critique"]["good"], "|", r["critique"]["problems"])
print("SPENT run $%.4f" % r["spent_usd"])
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ROOT, "out", "runs", name + "_addon", "scene.blend"))
