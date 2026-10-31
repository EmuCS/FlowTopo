"""Remeshers: QRemeshify (QuadWild) with our seams as feature lines, plus baselines."""
import time

import bpy


def _select_only(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def qremeshify_available():
    return hasattr(bpy.ops, "qremeshify") and hasattr(bpy.context.scene, "quadwild_props")


def qremeshify(obj, use_seams=True, preprocess=False, scale=1.0, symmetry_x=False,
               sharp_angle=89.0, smoothing=True, name=None, qr=None):
    """Run QRemeshify on obj. With use_seams, edges marked seam become feature lines.

    sharp_angle is kept high so scan noise does not create spurious features; the
    guides come from seams only. qr: optional QuadPatches settings (e.g. {"isometry": False}),
    restored afterwards. Returns (new_object, seconds).
    """
    if not qremeshify_available():
        raise RuntimeError("QRemeshify extension is not installed/enabled.")
    scene = bpy.context.scene
    p = scene.quadwild_props
    q = scene.quadpatches_props
    p.debug = False
    p.useCache = False
    p.enableRemesh = preprocess
    p.enableSmoothing = smoothing
    p.enableSharp = True
    p.sharpAngle = sharp_angle
    p.symmetryX = symmetry_x
    p.symmetryY = False
    p.symmetryZ = False
    q.scaleFact = scale
    qr_saved = {k: getattr(q, k) for k in (qr or {})}
    for k, v in (qr or {}).items():
        setattr(q, k, v)
    saved = None
    if not use_seams:
        saved = [e.use_seam for e in obj.data.edges]
        for e in obj.data.edges:
            e.use_seam = False
    _select_only(obj)
    t0 = time.time()
    try:
        bpy.ops.qremeshify.remesh()
    finally:
        if saved is not None:
            for e, s in zip(obj.data.edges, saved):
                e.use_seam = s
        for k, v in qr_saved.items():
            setattr(q, k, v)
    out = bpy.context.view_layer.objects.active
    if out is obj or out is None:
        raise RuntimeError("QRemeshify produced no output (see console).")
    if symmetry_x:
        _select_only(out)
        for m in list(out.modifiers):
            if m.type == "MIRROR":
                bpy.ops.object.modifier_apply(modifier=m.name)
    if name:
        out.name = name
    obj.hide_set(False)
    return out, time.time() - t0


def relax(obj, target, rounds=3, factor=0.5, iterations=4, smooth_shading=True):
    """Even out the quads without changing topology: smooth, then snap back onto `target`'s surface.

    Each round runs a Smooth modifier (vertices move towards their neighbours, which also pulls
    them off the surface) and a Shrinkwrap to the nearest point on the input. Boundary vertices
    stay put. The net effect is a tangential relax, like the relax brushes in retopo tools.
    """
    if rounds <= 0:
        return obj
    _select_only(obj)
    for _ in range(rounds):
        sm = obj.modifiers.new("ft_relax", "SMOOTH")
        sm.factor = factor
        sm.iterations = iterations
        sw = obj.modifiers.new("ft_snap", "SHRINKWRAP")
        sw.target = target
        sw.wrap_method = "NEAREST_SURFACEPOINT"
        bpy.ops.object.modifier_apply(modifier=sm.name)
        bpy.ops.object.modifier_apply(modifier=sw.name)
    if smooth_shading:
        obj.data.shade_smooth()
    return obj


def quadriflow(obj, target_faces, name=None):
    """Blender's built-in QuadriFlow on a copy (baseline: even quads, no semantics)."""
    cp = obj.copy()
    cp.data = obj.data.copy()
    bpy.context.scene.collection.objects.link(cp)
    _select_only(cp)
    t0 = time.time()
    bpy.ops.object.quadriflow_remesh(target_faces=int(target_faces), use_mesh_symmetry=False,
                                     use_preserve_sharp=False, use_preserve_boundary=True,
                                     smooth_normals=False, mode="FACES", seed=0)
    if name:
        cp.name = name
    return cp, time.time() - t0
