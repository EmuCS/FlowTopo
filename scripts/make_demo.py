"""Render demo footage for one finished run (stills + turntables at 1920x1080).

blender --background out/runs/<name>_eval/scene.blend --python scripts/make_demo.py -- <name> [run_tag] [zoom] [--stills]
Writes out/demo/<name>/*.png and turntable frame folders.
"""
import json
import os
import sys

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("FLOWTOPO_DATA", os.path.join(ROOT, "out"))

from flowtopo import sam, viz  # noqa: E402

args = [a for a in sys.argv[sys.argv.index("--") + 1:] if not a.startswith("--")]  # flags: --stills
name = args[0]
tag = args[1] if len(args) > 1 else "eval"
RUN = os.path.join(ROOT, "out", "runs", "%s_%s" % (name, tag))
OUT = os.path.join(ROOT, "out", "demo", name)
os.makedirs(OUT, exist_ok=True)
result = json.load(open(os.path.join(RUN, "result.json")))
SIZE = (1920, 1080)
BG = (0.0080, 0.0091, 0.0123)  # #16181d in linear (Standard view transform): the video's dark surface
WIRE_BASE = (0.80, 0.81, 0.84)


def obj(n):
    return bpy.data.objects.get(n)


src = obj(name + "_ft_work")
final = obj(result["output"])
guides_obj = obj(name + "_ft_guides_" + ("pass2" if result.get("pass2_kept") else "pass1"))
qf = obj(name + "_QuadriFlow")
qr = obj(name + "_QRemeshify_plain")
ref = obj(name + "_artist_reference")
offset = np.array(final.location) if final else np.zeros(3)
for o in (final, qf, qr, ref):
    if o:
        o.location = (0, 0, 0)  # everything in the centred working space for rendering

if "focus" in result:
    FC, FR = np.array(result["focus"]["center"]), result["focus"]["radius"]
else:  # derive from the guide seams
    me = guides_obj.data
    vs = sorted({v for e in me.edges if e.use_seam for v in e.vertices})
    pts = np.array([tuple(me.vertices[v].co) for v in vs])
    FC = pts.mean(0)
    FR = float(np.linalg.norm(pts - FC, axis=1).max() * 1.7)
FR *= float(args[2]) if len(args) > 2 else 0.78  # focus spans the ears too: frame tighter for 16:9
FACE = {"center": FC, "radius": FR}
print("FOCUS", FC, FR)


def still(key, o, **kw):
    path = os.path.join(OUT, key + ".png")
    viz.render_object(o, path, size=SIZE, background=BG, **kw)
    print("STILL", path)
    return path


wt = FR * 0.0022
still("01_input_face", src, wire=True, wire_thickness=wt, base_color=WIRE_BASE, **FACE)
still("01_input_full", src, wire=True, wire_thickness=wt * 1.5, base_color=WIRE_BASE, az=25, el=8)
still("03_guides_face", guides_obj, seams=True, **FACE)
still("04_result_face", final, wire=True, wire_thickness=wt, base_color=WIRE_BASE, **FACE)
FACE_WIDE = {"center": FC, "radius": FR * 1.45}
still("04_result_3q", final, wire=True, wire_thickness=wt, base_color=WIRE_BASE, az=35, el=8, **FACE_WIDE)
# the "other meshes" shots: the whole model filling the frame (the feature focus can be skewed by a stray region)
still("04_result_3q_close", final, wire=True, base_color=WIRE_BASE, az=30, el=6, zoom=1.45)
still("04_result_full", final, wire=True, wire_thickness=wt * 1.5, base_color=WIRE_BASE, az=25, el=8)
if qf:
    still("05_quadriflow_face", qf, wire=True, wire_thickness=wt, base_color=WIRE_BASE, **FACE)
if qr:
    still("05_qremeshify_face", qr, wire=True, wire_thickness=wt, base_color=WIRE_BASE, **FACE)
if ref:
    still("06_reference_face", ref, wire=True, wire_thickness=wt, base_color=WIRE_BASE, **FACE)

# 02: what SAM 3.1 sees: masks over the front render, padded to 16:9
front = os.path.join(RUN, "views", "view_front.png")
img = bpy.data.images.load(front)
w, h = img.size
px = np.array(img.pixels[:], np.float32).reshape(h, w, 4)[::-1]  # top-down
BG_SRGB = np.array((0.086, 0.094, 0.114), np.float32)  # image pixels here are display values, not linear
px[(px[..., :3] > 0.97).all(-1), :3] = BG_SRGB  # the analysis renders have a white background
feats = result.get("plan_final") or result.get("plan", {}).get("features", [])
for i, f in enumerate(feats):
    inst, _ = sam.segment(front, f["sam_prompt"])  # cached: no new API cost
    col = np.array(viz.PALETTE[i % len(viz.PALETTE)], np.float32)
    for it in inst:
        if it["score"] < 0.3:
            continue
        x, y, mh, mw, bits = it["mask"]
        m = np.frombuffer(bits, np.uint8).reshape(mh, mw).astype(bool)
        sub = px[y:y + mh, x:x + mw, :3]
        mm = m[:sub.shape[0], :sub.shape[1]]
        sub[mm] = 0.45 * sub[mm] + 0.55 * col
        x1, y1, x2, y2 = it["box"]
        for (a, b, c, d) in ((y1, y1 + 2, x1, x2), (y2 - 1, y2 + 1, x1, x2), (y1, y2, x1, x1 + 2), (y1, y2, x2 - 1, x2 + 1)):
            px[max(a, 0):min(b, h), max(c, 0):min(d, w), :3] = col
scale = SIZE[1] / h
big = np.kron(px, np.ones((int(round(scale)), int(round(scale)), 1), np.float32)) if abs(scale - round(scale)) < 1e-6 else None
if big is None:  # nearest-neighbour resize to 1080 high
    ys = (np.arange(SIZE[1]) / scale).astype(int).clip(0, h - 1)
    xs = (np.arange(int(w * scale)) / scale).astype(int).clip(0, w - 1)
    big = px[ys][:, xs]
canvas = np.ones((SIZE[1], SIZE[0], 4), np.float32)
canvas[:, :, :3] = BG_SRGB
x0 = (SIZE[0] - big.shape[1]) // 2
canvas[:, x0:x0 + big.shape[1]] = big
outimg = bpy.data.images.new("sam_overlay", SIZE[0], SIZE[1])
outimg.pixels.foreach_set(canvas[::-1].ravel())
outimg.filepath_raw = os.path.join(OUT, "02_sam_masks.png")
outimg.file_format = "PNG"
outimg.save()
print("STILL", outimg.filepath_raw)

# turntables (frames): result and messy input (skip with --stills)
for key, o, base in ((("tt_result", final, WIRE_BASE), ("tt_input", src, WIRE_BASE)) if "--stills" not in sys.argv else ()):
    d = os.path.join(OUT, key)
    os.makedirs(d, exist_ok=True)
    for k, az in enumerate(range(-60, 61, 3)):
        viz.render_object(o, os.path.join(d, "%04d.png" % k), size=SIZE, background=BG, wire=True,
                          wire_thickness=wt, base_color=base, az=az, el=6, **FACE_WIDE)
    print("TURNTABLE", d)
json.dump({"name": name, "subject": result.get("plan", {}).get("subject"), "metrics": result.get("metrics"),
           "baseline_qremeshify": result.get("baseline_qremeshify"), "baseline_quadriflow": result.get("baseline_quadriflow"),
           "reference": result.get("reference"), "critique": result.get("critique"), "plan": result.get("plan")},
          open(os.path.join(OUT, "facts.json"), "w"), indent=1, default=str)
