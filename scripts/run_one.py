"""Run FlowTopo headless on one eval mesh.

blender --background --python scripts/run_one.py -- <mesh_name> [--no-spark] [--no-critique] [--baselines] [--tag X]
    [--scale X] [--smooth N] [--closed-rings] [--relax N] [--keep-both] [qr_setting=json_value ...]   e.g. isometry=false alpha=0.0
Outputs go to out/runs/<mesh_name>[_tag]/ (renders, result.json, scene.blend).
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLOWTOPO_DATA", os.path.join(ROOT, "out"))

import bpy  # noqa: E402

from flowtopo import api, pipeline  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["lee_scan"]
name = args[0]
tag = args[args.index("--tag") + 1] if "--tag" in args else ""
opts = pipeline.Options(
    use_spark="--no-spark" not in args,
    critique="--no-critique" not in args,
    baselines="--baselines" in args,
    preprocess="--pre1" in args,
    reference=os.path.join(ROOT, "assets", "meshes", name + "_ref.obj"),
    scale=float(args[args.index("--scale") + 1]) if "--scale" in args else 1.5,
    field_smooth=int(args[args.index("--smooth") + 1]) if "--smooth" in args else 10,
    closed_rings="--closed-rings" in args,
    relax=int(args[args.index("--relax") + 1]) if "--relax" in args else 3,
    keep_both="--keep-both" in args,
    qr={k: json.loads(v) for k, v in (a.split("=", 1) for a in args if "=" in a and not a.startswith("-"))},
)
out_dir = os.path.join(ROOT, "out", "runs", name + ("_" + tag if tag else ""))

bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.wm.obj_import(filepath=os.path.join(ROOT, "assets", "meshes", name + "_messy.obj"))
src = bpy.context.selected_objects[0]
src.name = name

t0 = time.time()
run = pipeline.FlowTopoRun(src, out_dir, opts)
for frac, msg in run.steps():
    print("STEP %3d%% %s (%.0fs, $%.3f total)" % (frac * 100, msg, time.time() - t0, api.spent_usd()))
r = run.result
print("PLAN", r.get("plan", {}).get("subject"), [(f["name"], f["sam_prompt"], f["loop_type"], f["rings_out"], f["spacing"]) for f in r.get("plan", {}).get("features", [])])
print("DETECT", r.get("detections"))
print("METRICS", {k: v for k, v in r["metrics"].items() if k != "regions"})
for reg in r["metrics"]["regions"]:
    print("  REGION", reg)
for b in ("baseline_qremeshify", "baseline_quadriflow", "reference"):
    if b in r:
        print("BASE", b, {k: v for k, v in r[b].items() if k != "regions"})
if "critique" in r:
    c = r["critique"]
    print("CRITIQUE score=%s rerun=%s kept=%s" % (c["score"], c["rerun"], r.get("pass2_kept")))
    print("  good:", c["good"])
    print("  problems:", c["problems"])
print("SPENT run $%.4f, total $%.4f" % (r["spent_usd"], api.spent_usd()))
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, "scene.blend"))
