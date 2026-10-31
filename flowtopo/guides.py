"""Turn semantic regions into edge-flow guides: exact isoline cuts marked as seams.

For each region we have a signed geodesic distance field d (negative inside). Its
level sets d = L are smooth closed curves around the feature (concentric rings
around an eye or mouth). We cut the triangle mesh exactly along those curves and
mark the new edges as seams; QRemeshify/QuadWild treats seams as feature lines
that quad edges must follow.
"""
import bmesh
import numpy as np


def ring_levels(spec, region_scale):
    """Levels (in mesh units) for one region from a plan entry.

    spec: {"rings_out": int, "rings_in": int, "spacing": float (x region_scale), "boundary": bool}
    """
    s = max(spec.get("spacing", 0.35), 0.05) * region_scale
    levels = []
    if spec.get("boundary", True):
        levels.append(0.0)
    levels += [s * (k + 1) for k in range(int(spec.get("rings_out", 2)))]
    levels += [-s * 0.5 * (k + 1) for k in range(int(spec.get("rings_in", 0)))]
    return levels


def cut_guides(obj, fields, plans, other_zone_guard=True, closed_only=False):
    """fields: list of per-vertex arrays (original vertex indexing).
    plans: list of level lists, one per field (same order).
    Cuts the mesh in place and marks the cut edges as seams. Returns number of seam edges.
    closed_only: drop seam pieces that don't form closed rings (arcs cut short by the
    Voronoi guard). QuadWild puts a pole at every open end, which breaks the edge loops.
    """
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    layers = []
    for i, f in enumerate(fields):
        lay = bm.verts.layers.float.new("ft_field_%d" % i)
        for v in bm.verts:
            v[lay] = float(f[v.index])
        layers.append(lay)

    for e in bm.edges:
        e.seam = False

    # guard: a ring of region i is not cut inside another region's interior (d_j < 0)
    seam_count = 0
    stats = []
    for i, levels in enumerate(plans):
        lay = layers[i]
        for L in levels:
            n = _cut_level(bm, lay, L, layers, i if other_zone_guard else None)
            stats.append((i, round(L, 4), n))
            seam_count += n
    cut_guides.last_stats = stats
    cut_guides.last_dropped = _drop_open_pieces(bm) if closed_only else 0
    seam_count -= cut_guides.last_dropped

    bm.to_mesh(me)
    me.update()
    bm.free()
    return seam_count


def _drop_open_pieces(bm):
    """Unmark every connected seam piece that has an end (a vertex with one seam edge).

    Rings that close keep all their vertices at seam-degree 2. Returns edges unmarked.
    """
    seam_edges = [e for e in bm.edges if e.seam]
    deg = {}
    for e in seam_edges:
        for v in e.verts:
            deg[v] = deg.get(v, 0) + 1
    seen, dropped = set(), 0
    for e0 in seam_edges:
        if e0 in seen:
            continue
        piece, stack = [], [e0]
        seen.add(e0)
        while stack:
            e = stack.pop()
            piece.append(e)
            for v in e.verts:
                for e2 in v.link_edges:
                    if e2.seam and e2 not in seen:
                        seen.add(e2)
                        stack.append(e2)
        verts = {v for e in piece for v in e.verts}
        if any(deg[v] == 1 for v in verts):
            for e in piece:
                e.seam = False
            dropped += len(piece)
    return dropped


def _cut_level(bm, lay, L, layers, guard_index):
    # bmesh float layers are float32: use the float32-rounded level everywhere
    L = float(np.float32(L))
    vals = {v: v[lay] for v in bm.verts}
    rng = max(vals.values()) - min(vals.values()) or 1.0
    snap = 1e-4 * rng
    for v in bm.verts:
        if abs(v[lay] - L) < snap:
            v[lay] = L

    def guarded(v):
        # geodesic Voronoi: a ring belongs only to vertices closer to this feature than
        # to any other, so rings of neighbouring features never cross each other
        if guard_index is None:
            return False
        own = v[lay]
        return any(v[o] < own for k, o in enumerate(layers) if k != guard_index)

    # 1) split every edge that strictly crosses the level
    crossing = []
    for e in bm.edges:
        a, b = e.verts
        fa, fb = a[lay] - L, b[lay] - L
        if fa * fb < 0:
            crossing.append(e)
    for e in crossing:
        if not e.is_valid:
            continue
        a, b = e.verts
        fa, fb = a[lay], b[lay]
        t = (L - fa) / (fb - fa)
        others = [(o, a[o], b[o]) for o in layers]
        _ne, nv = bmesh.utils.edge_split(e, a, t)
        for o, oa, ob in others:
            nv[o] = oa + t * (ob - oa)
        nv[lay] = L

    # 2) split faces between pairs of on-level vertices; mark on-level edges as seams
    on = lambda v: v[lay] == L  # noqa: E731
    count = 0
    for f in list(bm.faces):
        if not f.is_valid:
            continue
        vs = [v for v in f.verts if on(v)]
        if len(vs) < 2:
            continue
        if len(vs) == 2:
            a, b = vs
            if b in [e.other_vert(a) for e in a.link_edges if f in e.link_faces]:
                continue  # already an edge of this face
            if guarded(a) or guarded(b):
                continue
            try:
                res = bmesh.utils.face_split(f, a, b)
            except (ValueError, RuntimeError):
                continue
            # face_split returns (new_face, new_loop); the loop's edge is the cut
            new_edge = res[1].edge if res and res[1] is not None else None
            if new_edge is not None:
                new_edge.seam = True
                count += 1
    for e in bm.edges:
        a, b = e.verts
        if on(a) and on(b) and not e.seam:
            if guarded(a) or guarded(b):
                continue
            e.seam = True
            count += 1
    return count
