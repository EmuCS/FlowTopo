"""Spike: SAM regions -> rings -> guided QRemeshify vs unguided, on the head scan.

blender --background --python spike/e2e_lee.py -- [mesh_name]
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLOWTOPO_DATA", os.path.join(ROOT, "out"))

import bpy  # noqa: E402
import numpy as np  # noqa: E402

from flowtopo import api, core, guides, remesh, render, sam, viz  # noqa: E402

name = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "lee_scan"
OUT = os.path.join(ROOT, "out", "spike_e2e", name)
os.makedirs(OUT, exist_ok=True)
T = time.time()

bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.wm.obj_import(filepath=os.path.join(ROOT, "assets", "meshes", name + "_messy.obj"))
src = bpy.context.selected_objects[0]
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
src.name = name

views = render.render_views(src, os.path.join(OUT, "views"))
meta = json.load(open(os.path.join(OUT, "views", "views.json")))
md = core.MeshData(src)
vis = {v["name"]: core.visible_faces(md, v, meta["radius"]) for v in views}
print("T vis %.1fs" % (time.time() - T))

face_views = [v for v in views if v["name"] in ("front", "front_l", "front_r", "left", "right", "top_front", "low_front")]
regions = []
for prompt in ("eye", "mouth"):
    masks = {}
    for v in face_views:
        inst, _ = sam.segment(v["path"], prompt)
        inst = [i for i in inst if i["score"] >= 0.3]
        masks[v["name"]] = core.mask_frame(inst, v["res"])[0] if inst else np.zeros((v["res"], v["res"]), bool)
    lab, ratio = core.vote_labels(md, face_views, vis, masks, min_ratio=0.5, min_pos=1)
    lab = core.closing(md, lab, k=max(2, int(0.02 / max(md.mean_edge, 1e-6))))
    comps = sorted(core.components(md, lab), key=len, reverse=True)
    total_area = md.area.sum()
    for comp in comps:
        a = md.area[comp].sum()
        if a < 0.0005 * total_area:
            continue
        reg = core.clean_region(md, comp)
        regions.append({"prompt": prompt, "faces": int(reg.sum()), "area": float(md.area[reg].sum()), "mask": reg})
    print("PROMPT", prompt, "components", [len(c) for c in comps][:6])
print("SPENT %.4f" % api.spent_usd())

allf = np.zeros(md.nf, bool)
for r in regions:
    allf |= r["mask"]
FC = md.cent[allf].mean(0) if allf.any() else None
FR = float(np.linalg.norm(md.cent[allf] - FC, axis=1).max() * 1.6) if allf.any() else None
colors = np.full((md.nf, 3), 0.8)
for i, r in enumerate(regions):
    colors[r["mask"]] = viz.PALETTE[i % len(viz.PALETTE)]
viz.set_face_colors(src, colors)
viz.render_object(src, os.path.join(OUT, "regions_front.png"), colors=True)

fields, plans = [], []
for r in regions:
    sd = core.signed_distance(md, r["mask"])
    sd = core.smooth_field(md, sd, iterations=8)
    scale = (r["area"] / np.pi) ** 0.5
    spec = {"rings_out": 3 if r["prompt"] == "mouth" else 2, "rings_in": 0, "spacing": 0.45, "boundary": True}
    fields.append(sd)
    plans.append(guides.ring_levels(spec, scale))
    print("REGION", r["prompt"], r["faces"], "scale %.3f" % scale, "levels", [round(x, 3) for x in plans[-1]])

guided_src = src.copy()
guided_src.data = src.data.copy()
guided_src.name = name + "_guides"
bpy.context.scene.collection.objects.link(guided_src)
n_seams = guides.cut_guides(guided_src, fields, plans)
print("SEAMS", n_seams, guides.cut_guides.last_stats)
print("SEAM EDGES IN MESH", sum(1 for e in guided_src.data.edges if e.use_seam))
viz.render_object(guided_src, os.path.join(OUT, "guides_front.png"), seams=True)
viz.render_object(guided_src, os.path.join(OUT, "guides_face.png"), seams=True, center=FC, radius=FR)

res = {}
for label, obj, use in (("guided", guided_src, True), ("unguided", src, False)):
    for pre in (False, True):
        try:
            out, secs = remesh.qremeshify(obj, use_seams=use, preprocess=pre, name="%s_%s_pre%d" % (name, label, pre))
        except Exception as e:  # noqa: BLE001
            print("REMESH FAIL", label, pre, e)
            continue
        nf = len(out.data.polygons)
        q = sum(1 for p in out.data.polygons if len(p.vertices) == 4)
        res["%s_pre%d" % (label, pre)] = {"faces": nf, "quads": q, "secs": round(secs, 1)}
        print("RESULT", label, "pre", pre, nf, q, "%.1fs" % secs)
        viz.render_object(out, os.path.join(OUT, "%s_pre%d_front.png" % (label, pre)), wire=True)
        viz.render_object(out, os.path.join(OUT, "%s_pre%d_face.png" % (label, pre)), wire=True,
                          center=FC, radius=FR, wire_thickness=FR * 0.0025)

json.dump(res, open(os.path.join(OUT, "result.json"), "w"), indent=1)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "scene.blend"))
print("DONE %.1fs" % (time.time() - T))
