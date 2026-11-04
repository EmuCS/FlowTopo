"""Render the edge-loop test for the demo: closed loops around the eyes and mouth.

blender --background out/runs/<name>_<tag>/scene.blend --python scripts/render_loops.py -- <name> <tag> [zoom] [--out DIR]
Writes out/demo/<DIR or name>/08_loops_<variant>.png (1920x1080 face close-ups) and loops_facts.json.
Closed loops are drawn in red. Where a mesh has no closed loop around a feature, its best
attempt (the walk that gets furthest around before a pole stops it) is drawn in orange.
"""
import json
import os
import sys

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from flowtopo import loops, viz  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
out_dir = args[args.index("--out") + 1] if "--out" in args else None
args = [a for i, a in enumerate(args) if a != "--out" and (i == 0 or args[i - 1] != "--out")]
name, tag = args[0], args[1]
zoom = float(args[2]) if len(args) > 2 else 0.78
RUN = os.path.join(ROOT, "out", "runs", "%s_%s" % (name, tag))
OUT = os.path.join(ROOT, "out", "demo", out_dir or name)
os.makedirs(OUT, exist_ok=True)
result = json.load(open(os.path.join(RUN, "result.json")))
RED, ORANGE = (0.90, 0.10, 0.20), (1.0, 0.55, 0.05)


def regions_from_colors(work):
    """[(region name, centre, normal, scale)] from the SAM region colours on the working mesh."""
    me = work.data
    attr = me.color_attributes.get("ft_col")
    col = np.empty(len(me.loops) * 4, np.float32)
    attr.data.foreach_get("color", col)
    col = col.reshape(-1, 4)[:, :3]
    fcol = col[np.array([p.loop_start for p in me.polygons])]
    cent = np.array([p.center for p in me.polygons])
    nrm = np.array([p.normal for p in me.polygons])
    area = np.array([p.area for p in me.polygons])
    names = []
    for d in result.get("detections", []):
        names += ["%s_%d" % (d["feature"], k) for k in range(d["regions"])]
    pal = np.array(viz.PALETTE, np.float32)
    lin = np.where(pal <= 0.04045, pal / 12.92, ((pal + 0.055) / 1.055) ** 2.4)
    out = []
    for i, nm in enumerate(names[:len(pal)]):
        best = None
        for cand in (pal[i], lin[i]):
            m = np.abs(fcol - cand).max(1) < 0.03
            if m.sum() and (best is None or m.sum() > best.sum()):
                best = m
        if best is None:
            continue
        a = area[best]
        c = (cent[best] * a[:, None]).sum(0) / a.sum()
        n = (nrm[best] * a[:, None]).sum(0)
        out.append((nm, c, n / np.linalg.norm(n), float((a.sum() / np.pi) ** 0.5)))
    return out


work = bpy.data.objects[name + "_ft_work"]
regs = [r for r in regions_from_colors(work) if r[0].split("_")[0] in ("eyes", "eye", "mouth")]
variants = {"flowtopo": result["output"], "quadriflow": name + "_QuadriFlow", "artist": name + "_artist_reference"}
FC, FR = np.array(result["focus"]["center"]), result["focus"]["radius"] * zoom
facts = {}
for key, oname in variants.items():
    o = bpy.data.objects.get(oname)
    if o is None:
        continue
    o.location = (0, 0, 0)  # centred working space, like make_demo.py
    lines, counts = [], {}
    for nm, c, n, s in regs:
        closed = loops.closed_loops(o, c, n, s)
        counts[nm] = len(closed)
        if closed:
            for vs in (closed[:3] if key == "artist" else closed):
                lines.append((vs, True, RED))
        else:
            path, w = loops.best_attempt(o, c, n, s)
            counts[nm + "_best_winding"] = round(w, 2)
            if path:
                lines.append((path, False, ORANGE))
    facts[key] = counts
    path = os.path.join(OUT, "08_loops_%s.png" % key)
    viz.render_object(o, path, size=(1920, 1080), background=(0.0080, 0.0091, 0.0123), wire=True, wire_thickness=FR * 0.0022,
                      base_color=(0.80, 0.81, 0.84), center=FC, radius=FR, polylines=lines)
    print("LOOPS", key, counts, path)
json.dump(facts, open(os.path.join(OUT, "loops_facts.json"), "w"), indent=1)
