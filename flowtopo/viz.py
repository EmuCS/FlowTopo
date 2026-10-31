"""Presentation renders: wireframes, region colours, seam guides, side-by-side panels."""
import os

import bpy
import numpy as np
from mathutils import Vector

from . import render

PALETTE = [
    (0.95, 0.30, 0.25), (0.20, 0.55, 0.95), (0.25, 0.80, 0.40), (0.98, 0.70, 0.15),
    (0.70, 0.35, 0.90), (0.10, 0.80, 0.80), (0.95, 0.45, 0.70), (0.55, 0.75, 0.20),
]


def _hide_all_but(objs):
    hidden = []
    for o in bpy.context.scene.objects:
        if o not in objs and not o.hide_render:
            o.hide_render = True
            hidden.append(o)
    return hidden


def _wire_overlay(obj, thickness, color=(0.08, 0.08, 0.10)):
    w = obj.copy()
    w.data = obj.data.copy()
    w.name = obj.name + "_wire"
    bpy.context.scene.collection.objects.link(w)
    for m in list(w.modifiers):
        w.modifiers.remove(m)
    mod = w.modifiers.new("wire", "WIREFRAME")
    mod.thickness = thickness
    mod.use_relative_offset = False
    mod.use_even_offset = False
    mod.use_replace = True
    w.color = (*color, 1.0)
    w.hide_render = False  # the copy inherits hide flags from a hidden source
    return w


def set_face_colors(obj, face_rgb, name="ft_col"):
    """face_rgb: (nf, 3) floats. Stored as a face-corner byte colour attribute."""
    me = obj.data
    if name in me.color_attributes:
        me.color_attributes.remove(me.color_attributes[name])
    attr = me.color_attributes.new(name, "BYTE_COLOR", "CORNER")
    loop_face = np.empty(len(me.loops), np.int32)
    for p in me.polygons:
        loop_face[p.loop_start:p.loop_start + p.loop_total] = p.index
    rgba = np.ones((len(me.loops), 4), np.float32)
    rgba[:, :3] = face_rgb[loop_face]
    attr.data.foreach_set("color", rgba.ravel())
    me.color_attributes.active_color = attr
    return attr


def render_object(obj, path, az=0, el=5, wire=False, wire_thickness=None, colors=False,
                  seams=False, res=None, zoom=1.0, center=None, radius=None, size=None,
                  background=None, base_color=(0.78, 0.78, 0.80), margin=1.08, polylines=None):
    """Render one object from one angle. Optional wire overlay, face colours, seam tubes.

    size=(w, h) for non-square frames; background=(r, g, b).
    polylines: [(vertex index list, closed, rgb)] drawn as tubes along obj's edges.
    """
    scene = bpy.context.scene
    render.setup_render(scene)
    if res:
        scene.render.resolution_x = scene.render.resolution_y = res
    aspect = 1.0
    if size:
        scene.render.resolution_x, scene.render.resolution_y = size
        aspect = size[0] / size[1]
    if background:
        scene.display.shading.background_color = background
    sh = scene.display.shading
    sh.show_cavity = not wire
    extras = []
    sh.color_type = "VERTEX" if colors else "OBJECT"
    obj.color = (*base_color, 1.0)
    c, r = render.frame_object(obj)
    center = Vector(tuple(center)) if center is not None else c
    radius = (radius if radius is not None else r) / zoom
    if wire:
        t = wire_thickness or radius * 0.0022
        extras.append(_wire_overlay(obj, t))
    if seams:
        extras.append(_seam_tubes(obj, radius * 0.006))
    for k, (vs, closed, rgb) in enumerate(polylines or []):
        extras.append(_polyline_tube(obj, vs, closed, radius * 0.007, rgb, "%s_pl%d" % (obj.name, k)))
    hidden = _hide_all_but([obj] + extras)
    was_hidden = obj.hide_render
    obj.hide_render = False
    cam = render._ensure_camera(scene)
    try:
        render.place_camera(cam, center, radius, az, el, margin=margin, aspect=aspect)
        scene.render.filepath = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.render.render(write_still=True)
    finally:
        obj.hide_render = was_hidden
        for o in hidden:
            o.hide_render = False
        for e in extras:
            data = e.data
            bpy.data.objects.remove(e, do_unlink=True)
            if data is not None and data.users == 0:
                (bpy.data.curves if isinstance(data, bpy.types.Curve) else bpy.data.meshes).remove(data)
    return path


def _seam_tubes(obj, thickness, color=(0.95, 0.15, 0.55)):
    """A wireframe of only the seam edges, so guides show up in renders."""
    me = obj.data
    seam_edges = [e for e in me.edges if e.use_seam]
    verts_used = sorted({v for e in seam_edges for v in e.vertices})
    remap = {v: i for i, v in enumerate(verts_used)}
    co = [tuple(me.vertices[v].co) for v in verts_used]
    edges = [(remap[e.vertices[0]], remap[e.vertices[1]]) for e in seam_edges]
    m2 = bpy.data.meshes.new(obj.name + "_seams")
    m2.from_pydata(co, edges, [])
    o2 = bpy.data.objects.new(obj.name + "_seams", m2)
    bpy.context.scene.collection.objects.link(o2)
    # edge mesh -> curve with bevel = tubes; handles closed loops (Skin modifier does not)
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    o2.select_set(True)
    bpy.context.view_layer.objects.active = o2
    bpy.ops.object.convert(target="CURVE")
    o2.data.bevel_depth = thickness
    o2.data.bevel_resolution = 1
    o2.color = (*color, 1.0)
    return o2


def _polyline_tube(obj, verts, closed, thickness, color, name):
    """A tube through the given vertices of obj (e.g. one edge loop)."""
    me = obj.data
    M = obj.matrix_world
    co = [tuple(M @ me.vertices[v].co) for v in verts]
    n = len(co)
    edges = [(i, i + 1) for i in range(n - 1)] + ([(n - 1, 0)] if closed else [])
    m2 = bpy.data.meshes.new(name)
    m2.from_pydata(co, edges, [])
    o2 = bpy.data.objects.new(name, m2)
    bpy.context.scene.collection.objects.link(o2)
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    o2.select_set(True)
    bpy.context.view_layer.objects.active = o2
    bpy.ops.object.convert(target="CURVE")
    o2.data.bevel_depth = thickness
    o2.data.bevel_resolution = 2
    o2.color = (*color, 1.0)
    return o2


def compose_row(paths, labels, out_path, label_h=56):
    """Side-by-side panel with captions, using Blender's image API (no PIL needed)."""
    imgs = [bpy.data.images.load(p, check_existing=False) for p in paths]
    w, h = imgs[0].size
    n = len(imgs)
    canvas = np.ones((h + label_h, w * n, 4), np.float32)
    for i, im in enumerate(imgs):
        px = np.array(im.pixels[:], np.float32).reshape(h, w, 4)
        canvas[:h, i * w:(i + 1) * w] = px  # Blender images are bottom-up; captions go on top
    out = bpy.data.images.new("ft_compose", w * n, h + label_h, alpha=True)
    out.pixels.foreach_set(canvas.ravel())
    out.filepath_raw = out_path
    out.file_format = "PNG"
    out.save()
    for im in imgs:
        bpy.data.images.remove(im)
    bpy.data.images.remove(out)
    _write_labels_sidecar(out_path, labels)
    return out_path


def _write_labels_sidecar(path, labels):
    with open(os.path.splitext(path)[0] + "_labels.txt", "w") as f:
        f.write(" | ".join(labels))
