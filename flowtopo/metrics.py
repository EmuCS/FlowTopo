"""Topology metrics: does the edge flow follow the features?

flow_alignment: around each feature (within its ring zone) take every output edge,
project it onto the surface, and measure its angle to the feature's distance-field
gradient. Edges within 15 deg of "radial" (spoke) or "tangential" (ring) count as
aligned. A random grid scores ~33%; perfect concentric loops score 100%.
"""
import numpy as np
from mathutils import Vector

from . import loops

ALIGN_DEG = 15.0


def face_gradients(md, field):
    """Per-face gradient of a per-vertex scalar field (first triangle of each face)."""
    tri = np.array([fv[:3] for fv in md.face_verts], np.int64)
    p0, p1, p2 = md.co[tri[:, 0]], md.co[tri[:, 1]], md.co[tri[:, 2]]
    f0, f1, f2 = field[tri[:, 0]], field[tri[:, 1]], field[tri[:, 2]]
    n = np.cross(p1 - p0, p2 - p0)
    a2 = np.linalg.norm(n, axis=1)
    nh = n / np.maximum(a2, 1e-12)[:, None]
    g = ((f1 - f0)[:, None] * np.cross(nh, p0 - p2) + (f2 - f0)[:, None] * np.cross(nh, p1 - p0))
    g /= np.maximum(a2, 1e-12)[:, None]
    return g, nh


def _out_mesh_arrays(obj):
    me = obj.data
    nv, ne = len(me.vertices), len(me.edges)
    co = np.empty(nv * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    M = np.array(obj.matrix_world)
    co = co @ M[:3, :3].T + M[:3, 3]
    ed = np.empty(ne * 2, np.int32)
    me.edges.foreach_get("vertices", ed)
    ed = ed.reshape(-1, 2)
    return co, ed


def _nearest_faces(md, pts):
    idx = np.empty(len(pts), np.int64)
    dist = np.empty(len(pts))
    for i, p in enumerate(pts):
        loc, _n, tri, d = md.bvh.find_nearest(Vector(p))
        idx[i] = md.tri_face[tri] if tri is not None else 0
        dist[i] = d if d is not None else 0.0
    return idx, dist


def evaluate(md, out_obj, regions, _unused=None):
    """regions: list of dicts with 'field' (per input vertex) and 'zone_max' (outer ring distance).

    Returns dict with overall and per-region numbers.
    """
    co, ed = _out_mesh_arrays(out_obj)
    me = out_obj.data
    nf = len(me.polygons)
    quads = sum(1 for p in me.polygons if len(p.vertices) == 4)
    # valence + boundary
    val = np.zeros(len(co), np.int32)
    np.add.at(val, ed[:, 0], 1)
    np.add.at(val, ed[:, 1], 1)
    edge_faces = np.zeros(len(ed), np.int32)
    key = {(min(a, b), max(a, b)): i for i, (a, b) in enumerate(ed)}
    for p in me.polygons:
        vs = list(p.vertices)
        for i in range(len(vs)):
            a, b = vs[i], vs[(i + 1) % len(vs)]
            k = key.get((min(a, b), max(a, b)))
            if k is not None:
                edge_faces[k] += 1
    boundary_v = np.zeros(len(co), bool)
    be = ed[edge_faces < 2]
    boundary_v[be.ravel()] = True
    irregular = (val != 4) & ~boundary_v

    mid = 0.5 * (co[ed[:, 0]] + co[ed[:, 1]])
    fidx, fdist = _nearest_faces(md, mid)
    vidx, vdist = _nearest_faces(md, co)
    diag = float(np.linalg.norm(md.co.max(0) - md.co.min(0)))

    out = {
        "faces": nf,
        "quad_pct": round(100.0 * quads / max(nf, 1), 1),
        "irregular_verts": int(irregular.sum()),
        "irregular_pct": round(100.0 * irregular.sum() / max(len(co), 1), 1),
        "dist_mean_pct_diag": round(100.0 * float(vdist.mean()) / diag, 3),
        "dist_max_pct_diag": round(100.0 * float(vdist.max()) / diag, 3),
        "regions": [],
    }
    edir = co[ed[:, 1]] - co[ed[:, 0]]
    all_aligned, all_n = 0, 0
    for r in regions:
        g, nh = face_gradients(md, r["field"])
        # field value at edge midpoints ~ mean of the nearest face's vertex values
        fv = np.array([r["field"][md.face_verts[f]].mean() for f in fidx])
        band = (fv >= -0.25 * r["zone_max"]) & (fv <= r["zone_max"])
        if band.sum() < 5:
            out["regions"].append({"name": r["name"], "edges": int(band.sum()), "aligned_pct": None})
            continue
        gi = g[fidx[band]]
        ni = nh[fidx[band]]
        d = edir[band]
        d = d - (d * ni).sum(1)[:, None] * ni  # tangent plane
        gn = np.linalg.norm(gi, axis=1)
        dn = np.linalg.norm(d, axis=1)
        ok = (gn > 1e-9) & (dn > 1e-12)
        cosang = np.abs((d[ok] * gi[ok]).sum(1) / (dn[ok] * gn[ok]))
        ang = np.degrees(np.arccos(np.clip(cosang, 0, 1)))  # 0 = radial spoke, 90 = ring tangent
        aligned = (ang <= ALIGN_DEG) | (ang >= 90 - ALIGN_DEG)
        vband_field = np.array([r["field"][md.face_verts[f]].mean() for f in vidx])
        vzone = (vband_field >= -0.25 * r["zone_max"]) & (vband_field <= r["zone_max"])
        out["regions"].append({
            "name": r["name"],
            "edges": int(ok.sum()),
            "aligned_pct": round(100.0 * aligned.mean(), 1),
            "ring_pct": round(100.0 * (ang >= 90 - ALIGN_DEG).mean(), 1),
            "irregular_in_zone": int((irregular & vzone).sum()),
            "closed_loops": _closed_loops(md, out_obj, r),
        })
        all_aligned += int(aligned.sum())
        all_n += int(ok.sum())
    out["aligned_pct"] = round(100.0 * all_aligned / all_n, 1) if all_n else None
    return out


def _closed_loops(md, out_obj, r):
    """Closed edge loops that wind around this feature (see loops.py). None for limb rings."""
    if r.get("loop_type") == "limb_rings":
        return None
    inside = r.get("mask")
    if inside is None:
        inside = np.array([r["field"][fv].mean() for fv in md.face_verts]) < 0
    if not inside.any():
        return None
    a = md.area[inside]
    c = (md.cent[inside] * a[:, None]).sum(0) / a.sum()
    _g, nh = face_gradients(md, r["field"])
    n = (nh[inside] * a[:, None]).sum(0)
    scale = float((a.sum() / np.pi) ** 0.5)
    Minv = out_obj.matrix_world.inverted()  # loops are walked in the object's local space
    c_loc = np.array(Minv @ Vector(c))
    n_loc = np.array(Minv.to_3x3() @ Vector(n))
    return len(loops.closed_loops(out_obj, c_loc, n_loc, scale))


def primary_score(m):
    """Eyes + mouth aligned % (edge-weighted) when the mesh has them, else the overall aligned %."""
    regs = [r for r in m.get("regions", []) if r.get("aligned_pct") is not None
            and r["name"].split("_")[0] in ("eyes", "eye", "mouth")]
    n = sum(r["edges"] for r in regs)
    if n:
        return sum(r["aligned_pct"] * r["edges"] for r in regs) / n
    return m.get("aligned_pct") or 0.0


def random_baseline_pct():
    return round(100.0 * (2 * ALIGN_DEG) / 90.0, 1)
