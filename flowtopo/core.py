"""Mesh arrays, adjacency, and back-projection of 2D SAM masks onto mesh faces.

Assumes the object's transforms are applied (matrix_world == identity), which the
pipeline guarantees by working on a normalised copy.
"""
import heapq
import math

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


class MeshData:
    def __init__(self, obj):
        me = obj.data
        me.calc_loop_triangles()
        nv, nf = len(me.vertices), len(me.polygons)
        self.co = _get(me.vertices, "co", nv, 3)
        self.cent = _get(me.polygons, "center", nf, 3)
        self.nrm = _get(me.polygons, "normal", nf, 3)
        self.area = _get(me.polygons, "area", nf, 1).ravel()
        ne = len(me.edges)
        self.edges = _get(me.edges, "vertices", ne, 2, np.int32)
        loop_start = _get(me.polygons, "loop_start", nf, 1, np.int32).ravel()
        loop_total = _get(me.polygons, "loop_total", nf, 1, np.int32).ravel()
        loop_vert = _get(me.loops, "vertex_index", len(me.loops), 1, np.int32).ravel()
        self.face_verts = [loop_vert[s:s + t] for s, t in zip(loop_start, loop_total)]
        self.nv, self.nf = nv, nf
        # face adjacency through shared edges
        edge_faces = {}
        for f, vs in enumerate(self.face_verts):
            for i in range(len(vs)):
                a, b = int(vs[i]), int(vs[(i + 1) % len(vs)])
                key = (a, b) if a < b else (b, a)
                edge_faces.setdefault(key, []).append(f)
        self.face_adj = [[] for _ in range(nf)]
        for fs in edge_faces.values():
            if len(fs) == 2:
                self.face_adj[fs[0]].append(fs[1])
                self.face_adj[fs[1]].append(fs[0])
        # vertex adjacency with edge lengths
        self.vert_adj = [[] for _ in range(nv)]
        lens = np.linalg.norm(self.co[self.edges[:, 0]] - self.co[self.edges[:, 1]], axis=1)
        for (a, b), L in zip(self.edges, lens):
            self.vert_adj[a].append((int(b), float(L)))
            self.vert_adj[b].append((int(a), float(L)))
        self.mean_edge = float(lens.mean()) if len(lens) else 0.0
        self.vert_faces = [[] for _ in range(nv)]
        for f, vs in enumerate(self.face_verts):
            for v in vs:
                self.vert_faces[int(v)].append(f)
        tris = [tuple(lt.vertices) for lt in me.loop_triangles]
        self.bvh = BVHTree.FromPolygons([tuple(v) for v in self.co.tolist()], tris)
        self.tri_face = np.array([lt.polygon_index for lt in me.loop_triangles], np.int32)


def _get(coll, attr, n, k, dtype=np.float64):
    a = np.empty(n * k, dtype=np.float32 if dtype == np.float64 else dtype)
    coll.foreach_get(attr, a)
    return a.reshape(n, k).astype(dtype) if k > 1 else a.reshape(n, 1).astype(dtype)


def project(view, pts):
    """World points -> (u, v) pixel coords and depth for a view record from render.py."""
    M = np.array(view["matrix_world"])
    Minv = np.linalg.inv(M)
    pc = pts @ Minv[:3, :3].T + Minv[:3, 3]
    depth = -pc[:, 2]
    t = math.tan(view["angle"] / 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        xn = pc[:, 0] / depth / t
        yn = pc[:, 1] / depth / t
    res = view["res"]
    u = (xn * 0.5 + 0.5) * res
    v = (1.0 - (yn * 0.5 + 0.5)) * res
    return u, v, depth


def visible_faces(md, view, radius):
    """Boolean per face: centroid visible from the camera (front-facing and unoccluded)."""
    M = np.array(view["matrix_world"])
    cam = M[:3, 3]
    to_cam = cam - md.cent
    dist = np.linalg.norm(to_cam, axis=1)
    facing = (md.nrm * to_cam).sum(1) > 1e-9
    vis = np.zeros(md.nf, bool)
    eps = 0.004 * radius
    camv = Vector(cam)
    tri_face = md.tri_face
    for i in np.nonzero(facing)[0]:
        d = -to_cam[i] / dist[i]
        loc, _n, tri, hd = md.bvh.ray_cast(camv, Vector(d), float(dist[i]) + eps)
        if tri is None:
            vis[i] = True
        elif tri_face[tri] == i or hd >= dist[i] - eps:
            vis[i] = True
    return vis


def mask_frame(instances, res):
    """Union of SAM instance masks as a res x res bool array, plus instance-id map."""
    full = np.zeros((res, res), bool)
    ids = np.full((res, res), -1, np.int16)
    for k, inst in enumerate(instances):
        x, y, h, w, bits = inst["mask"]
        m = np.frombuffer(bits, np.uint8).reshape(h, w).astype(bool)
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, res), min(y + h, res)
        if x1 <= x0 or y1 <= y0:
            continue
        sub = m[y0 - y:y1 - y, x0 - x:x1 - x]
        full[y0:y1, x0:x1] |= sub
        ids[y0:y1, x0:x1][sub] = k
    return full, ids


def vote_labels(md, views, vis_by_view, masks_by_view, min_ratio=0.5, min_pos=1):
    """Per-face label from multi-view votes. masks_by_view[name] -> bool frame or None."""
    pos = np.zeros(md.nf, np.int32)
    neg = np.zeros(md.nf, np.int32)
    for view in views:
        mask = masks_by_view.get(view["name"])
        if mask is None:
            continue
        vis = vis_by_view[view["name"]]
        u, v, depth = project(view, md.cent)
        res = view["res"]
        ui = np.clip(u.astype(int), 0, res - 1)
        vi = np.clip(v.astype(int), 0, res - 1)
        inframe = (u >= 0) & (u < res) & (v >= 0) & (v < res)  # close-up views don't see everything
        inside = mask[vi, ui] & vis & (depth > 0) & inframe
        pos += inside
        neg += vis & inframe & ~inside
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(pos + neg > 0, pos / np.maximum(pos + neg, 1), 0.0)
    return (ratio >= min_ratio) & (pos >= min_pos), ratio


def components(md, labels):
    """Connected components (lists of face indices) of a boolean face labelling."""
    seen = np.zeros(md.nf, bool)
    comps = []
    for f in np.nonzero(labels)[0]:
        if seen[f]:
            continue
        stack, comp = [f], []
        seen[f] = True
        while stack:
            g = stack.pop()
            comp.append(g)
            for h in md.face_adj[g]:
                if labels[h] and not seen[h]:
                    seen[h] = True
                    stack.append(h)
        comps.append(comp)
    return comps


def dilate(md, lab, k=1):
    lab = lab.copy()
    for _ in range(k):
        grow = lab.copy()
        for f in np.nonzero(lab)[0]:
            grow[md.face_adj[f]] = True
        lab = grow
    return lab


def erode(md, lab, k=1):
    return ~dilate(md, ~lab, k)


def closing(md, lab, k=3):
    """Morphological closing on the face graph: joins nearby parts (upper + lower lip)."""
    return erode(md, dilate(md, lab, k), k)


def clean_region(md, faces, iterations=2):
    """Fill holes and smooth a face region with majority filtering. Returns bool mask."""
    lab = np.zeros(md.nf, bool)
    lab[faces] = True
    # fill enclosed holes: unlabelled components that are small relative to the region
    holes = components(md, ~lab)
    holes.sort(key=len)
    for comp in holes[:-1]:  # the largest unlabelled component is "outside"
        if len(comp) < 0.6 * len(faces):
            lab[comp] = True
    for _ in range(iterations):
        new = lab.copy()
        for f in range(md.nf):
            adj = md.face_adj[f]
            if not adj:
                continue
            k = sum(lab[a] for a in adj)
            if lab[f] and k <= 1 and len(adj) >= 3:
                new[f] = False
            elif not lab[f] and k >= 2 and len(adj) >= 3:
                new[f] = True
        lab = new
    return lab


def signed_distance(md, region):
    """Geodesic (graph) distance from the region boundary; negative inside the region."""
    nv = md.nv
    in_count = np.zeros(nv, np.int32)
    tot = np.zeros(nv, np.int32)
    for f, vs in enumerate(md.face_verts):
        tot[vs] += 1
        if region[f]:
            in_count[vs] += 1
    boundary = (in_count > 0) & (in_count < tot)
    inside = (in_count == tot) & (tot > 0)
    dist = np.full(nv, np.inf)
    heap = []
    for v in np.nonzero(boundary)[0]:
        dist[v] = 0.0
        heap.append((0.0, int(v)))
    heapq.heapify(heap)
    while heap:
        d, v = heapq.heappop(heap)
        if d > dist[v]:
            continue
        for w, L in md.vert_adj[v]:
            nd = d + L
            if nd < dist[w]:
                dist[w] = nd
                heapq.heappush(heap, (nd, w))
    dist[~np.isfinite(dist)] = dist[np.isfinite(dist)].max() if np.isfinite(dist).any() else 0.0
    sd = np.where(inside, -dist, dist)
    return sd


def signed_distance_euclid(md, region):
    """Straight-line distance to the region boundary (negative inside).

    Near a feature (eye, mouth) this matches geodesic distance closely but gives
    round, smooth level sets instead of the polygonal ones of graph distances.
    """
    from mathutils.kdtree import KDTree

    nv = md.nv
    in_count = np.zeros(nv, np.int32)
    tot = np.zeros(nv, np.int32)
    for f, vs in enumerate(md.face_verts):
        tot[vs] += 1
        if region[f]:
            in_count[vs] += 1
    boundary = np.nonzero((in_count > 0) & (in_count < tot))[0]
    inside = (in_count == tot) & (tot > 0)
    if len(boundary) == 0:
        return np.zeros(nv)
    # densify the boundary with edge midpoints so distances are not bumpy
    pts = [md.co[v] for v in boundary]
    bset = set(boundary.tolist())
    for a, b in md.edges:
        if a in bset and b in bset:
            pts.append(0.5 * (md.co[a] + md.co[b]))
    kd = KDTree(len(pts))
    for i, p in enumerate(pts):
        kd.insert(p, i)
    kd.balance()
    dist = np.array([kd.find(md.co[v])[2] for v in range(nv)])
    return np.where(inside, -dist, dist)


def smooth_field(md, f, iterations=6, alpha=0.5):
    f = f.copy()
    for _ in range(iterations):
        g = f.copy()
        for v in range(md.nv):
            adj = md.vert_adj[v]
            if adj:
                g[v] = (1 - alpha) * f[v] + alpha * sum(f[w] for w, _ in adj) / len(adj)
        f = g
    return f
