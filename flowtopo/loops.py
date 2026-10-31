"""Closed edge loops around a feature: the test an artist does with Alt+click.

Walk quad edge loops (through valence-4 vertices, always taking the edge opposite the one
we came from) starting from edges near the feature. A loop counts if it closes on itself,
stays near the feature and winds once around its centre. Artist topology has several such
rings around each eye and the mouth; an even remesh grid usually has none.
"""
import math

import bmesh
import numpy as np


def _opposite(e, v):
    """The edge across valence-4 vertex v from edge e (shares no face with e), or None."""
    if len(v.link_edges) != 4 or v.is_boundary:
        return None
    fe = {f.index for f in e.link_faces}
    for e2 in v.link_edges:
        if e2 is e:
            continue
        if not fe.intersection(f.index for f in e2.link_faces):
            return e2
    return None


def walk_loop(e0, max_len=2000):
    """Return (edge indices, vertex indices, closed) for the loop through e0."""
    edges, verts = [e0.index], [e0.verts[0].index]
    e, v = e0, e0.verts[1]
    for _ in range(max_len):
        if v is e0.verts[0]:
            return edges, verts, True
        verts.append(v.index)
        n = _opposite(e, v)
        if n is None:
            return edges, verts, False
        edges.append(n.index)
        e, v = n, n.other_vert(v)
    return edges, verts, False


def _walk_open(e0, v, near, max_len=400):
    """Vertices from v onward along the loop through e0, until a pole or leaving `near`."""
    out, e = [], e0
    for _ in range(max_len):
        out.append(v.index)
        if not near[v.index]:
            break
        n = _opposite(e, v)
        if n is None:
            break
        e, v = n, n.other_vert(v)
        if v is e0.verts[0] or v is e0.verts[1]:
            break
    return out


def best_attempt(obj, center, normal, scale, ring=(1.2, 2.0), max_r=4.0):
    """For display: from tangent seed edges at about the first-ring radius, the loop walk
    (both directions) that winds furthest around the feature. Returns (vertex list, winding)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    co = np.array([v.co for v in bm.verts])
    center, normal = np.asarray(center, float), np.asarray(normal, float) / np.linalg.norm(normal)
    dist = np.linalg.norm(co - center, axis=1)
    near = dist <= max_r * scale
    best, best_w = [], -1.0
    for e in bm.edges:
        a, b = e.verts[0].index, e.verts[1].index
        if not (ring[0] * scale <= dist[a] <= ring[1] * scale):
            continue
        mid = 0.5 * (co[a] + co[b])
        radial = mid - center
        radial -= (radial @ normal) * normal
        d = co[b] - co[a]
        if abs(d @ radial) / (np.linalg.norm(d) * np.linalg.norm(radial) + 1e-12) > 0.5:
            continue
        fwd = _walk_open(e, e.verts[1], near)
        back = _walk_open(e, e.verts[0], near)
        path = list(reversed(back)) + fwd
        if len(path) < 3:
            continue
        w = _open_winding(co[path], center, normal)
        if w > best_w:
            best, best_w = path, w
    bm.free()
    return best, best_w


def _open_winding(pts, center, normal):
    a = np.cross(normal, [1.0, 0.0, 0.0])
    if np.linalg.norm(a) < 0.1:
        a = np.cross(normal, [0.0, 1.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(normal, a)
    d = pts - center
    ang = np.unwrap(np.arctan2(d @ b, d @ a))
    return abs(ang[-1] - ang[0]) / (2 * math.pi)


def _winding(pts, center, normal):
    a = np.cross(normal, [1.0, 0.0, 0.0])
    if np.linalg.norm(a) < 0.1:
        a = np.cross(normal, [0.0, 1.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(normal, a)
    d = pts - center
    ang = np.arctan2(d @ b, d @ a)
    step = np.diff(np.append(ang, ang[0]))
    step = (step + math.pi) % (2 * math.pi) - math.pi
    return abs(step.sum()) / (2 * math.pi)


def closed_loops(obj, center, normal, scale, max_r=3.5, min_r=0.3):
    """Distinct closed loops winding once around the feature at `center`.

    scale: feature radius (sqrt(area/pi)); loops must stay within max_r*scale of the centre
    and not dip inside min_r*scale. Coordinates in obj's local space.
    Returns a list of loops, each a list of vertex indices, sorted from inner to outer.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    co = np.array([v.co for v in bm.verts])
    center = np.asarray(center, float)
    normal = np.asarray(normal, float)
    normal /= max(np.linalg.norm(normal), 1e-12)
    dist = np.linalg.norm(co - center, axis=1)
    near = dist <= max_r * scale
    seen, loops = set(), []
    for e in bm.edges:
        if e.index in seen:
            continue
        a, b = e.verts[0].index, e.verts[1].index
        if not (near[a] and near[b]):
            continue
        # seed only with edges roughly tangent to circles around the centre
        mid = 0.5 * (co[a] + co[b])
        radial = mid - center
        radial -= (radial @ normal) * normal
        d = co[b] - co[a]
        if np.linalg.norm(radial) < 1e-12 or abs(d @ radial) / (np.linalg.norm(d) * np.linalg.norm(radial) + 1e-12) > 0.5:
            continue
        edges, verts, closed = walk_loop(e)
        seen.update(edges)
        if not closed or len(verts) < 6:
            continue
        r = dist[verts]
        if r.max() > max_r * scale or r.min() < min_r * scale:
            continue
        if abs(_winding(co[verts], center, normal) - 1.0) > 0.15:
            continue
        loops.append(verts)
    bm.free()
    loops.sort(key=lambda vs: float(dist[vs].mean()))
    return loops
