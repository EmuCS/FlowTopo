"""Render Spark's review loop for the demo: pass-1 vs pass-2 guide rings and meshes, plus facts.

blender --background out/runs/<name>_<tag>/scene.blend --python scripts/render_pass2.py -- <name> <tag> [zoom]
Needs a run made with --keep-both (both passes kept in the scene). Writes
out/demo/<name>/09_pass1_guides.png, 09_pass2_guides.png, 09_pass1_result.png, 09_pass2_result.png
and pass2_facts.json (Spark's critique, its adjustments, and the scores of both passes).
"""
import json
import os
import sys

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from flowtopo import metrics, viz  # noqa: E402

args = [a for a in sys.argv[sys.argv.index("--") + 1:] if not a.startswith("--")]
name, tag = args[0], args[1]
zoom = float(args[2]) if len(args) > 2 else 0.78
RUN = os.path.join(ROOT, "out", "runs", "%s_%s" % (name, tag))
OUT = os.path.join(ROOT, "out", "demo", name)
os.makedirs(OUT, exist_ok=True)
result = json.load(open(os.path.join(RUN, "result.json")))
BG = (0.0080, 0.0091, 0.0123)
FC, FR = np.array(result["focus"]["center"]), result["focus"]["radius"] * zoom

for k in (1, 2):
    g = bpy.data.objects.get("%s_ft_guides_pass%d" % (name, k))
    r = bpy.data.objects.get("%s_FlowTopo_pass%d" % (name, k))
    if r is None and k == 2 and result.get("pass2_kept"):
        r = bpy.data.objects.get(result["output"])  # the kept pass is renamed to the final output
    for o in (g, r):
        if o:
            o.location = (0, 0, 0)
    if g:
        viz.render_object(g, os.path.join(OUT, "09_pass%d_guides.png" % k), seams=True, size=(1920, 1080),
                          background=BG, center=FC, radius=FR)
    if r:
        viz.render_object(r, os.path.join(OUT, "09_pass%d_result.png" % k), wire=True, wire_thickness=FR * 0.0022,
                          base_color=(0.80, 0.81, 0.84), size=(1920, 1080), background=BG, center=FC, radius=FR)

c = result.get("critique", {})
facts = {"score": c.get("score"), "problems": c.get("problems"), "adjustments": c.get("adjustments"),
         "plan_pass1": [(f["name"], f.get("rings_out"), f.get("spacing")) for f in result["plan"]["features"]],
         "plan_pass2": [(f["name"], f.get("rings_out"), f.get("spacing")) for f in result.get("plan_final") or []],
         "pass1": round(metrics.primary_score(result["metrics_pass1"]), 1) if "metrics_pass1" in result else None,
         "pass2": round(metrics.primary_score(result["metrics_pass2"]), 1) if "metrics_pass2" in result else None,
         "kept": result.get("pass2_kept")}
json.dump(facts, open(os.path.join(OUT, "pass2_facts.json"), "w"), indent=1)
print("PASS2", facts)
