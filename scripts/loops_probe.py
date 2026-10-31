"""Count closed edge loops around each detected feature, for every mesh variant in a finished run.

blender --background out/runs/<name>_eval/scene.blend --python scripts/loops_probe.py -- <name> [tag]
Feature centres come from the SAM region colours stored on the working mesh, so nothing is
re-run and nothing calls the API. Writes loops.json into the run folder.
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
name = args[0]
tag = args[1] if len(args) > 1 else "eval"
RUN = os.path.join(ROOT, "out", "runs", "%s_%s" % (name, tag))
result = json.load(open(os.path.join(RUN, "result.json")))


def regions_from_colors(work):
    """[(region name, centre, normal, scale)] from the per-face palette colours."""
    me = work.data
    attr = me.color_attributes.get("ft_col")
    nl = len(me.loops)
    col = np.empty(nl * 4, np.float32)
    attr.data.foreach_get("color", col)
    col = col.reshape(-1, 4)[:, :3]
    starts = np.array([p.loop_start for p in me.polygons])
    fcol = col[starts]
    cent = np.array([p.center for p in me.polygons])
    nrm = np.array([p.normal for p in me.polygons])
    area = np.array([p.area for p in me.polygons])
    names = []
    for d in result.get("detections", []):
        names += ["%s_%d" % (d["feature"], k) for k in range(d["regions"])]
    pal = np.array(viz.PALETTE, np.float32)
    # byte colours are sRGB-encoded: compare in the same space
    lin = np.where(pal <= 0.04045, pal / 12.92, ((pal + 0.055) / 1.055) ** 2.4)
    out = []
    for i, nm in enumerate(names):
        target = pal[i % len(pal)]
        best = None
        for cand in (target, lin[i % len(pal)]):
            m = np.abs(fcol - cand).max(1) < 0.03
            if m.sum() and (best is None or m.sum() > best.sum()):
                best = m
        if best is None or not best.any():
            continue
        if i >= len(pal):  # colour reused: keep the component nearest the colour's first use? skip for clarity
            continue
        a = area[best]
        c = (cent[best] * a[:, None]).sum(0) / a.sum()
        n = (nrm[best] * a[:, None]).sum(0)
        out.append((nm, c, n / np.linalg.norm(n), float((a.sum() / np.pi) ** 0.5)))
    return out


work = bpy.data.objects.get(name + "_ft_work")
regs = regions_from_colors(work)
variants = {"flowtopo": result["output"], "quadriflow": name + "_QuadriFlow",
            "qremeshify": name + "_QRemeshify_plain", "artist": name + "_artist_reference"}
report = {"regions": [r[0] for r in regs], "variants": {}}
for key, oname in variants.items():
    o = bpy.data.objects.get(oname)
    if o is None:
        continue
    per = {}
    for nm, c, n, s in regs:
        ls = loops.closed_loops(o, c, n, s)
        per[nm] = len(ls)
    report["variants"][key] = per
    print("LOOPS", key, per)
json.dump(report, open(os.path.join(RUN, "loops.json"), "w"), indent=1)
