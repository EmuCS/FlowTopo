"""Multi-view grey renders of one object, plus the camera data needed to project back."""
import json
import math
import os

import bpy
from mathutils import Vector

RES = 768
# (name, azimuth deg, elevation deg). Azimuth 0 = camera in front of the model (on -Y) looking at +Y.
VIEWS_FULL = [
    ("front", 0, 5), ("front_l", -40, 5), ("front_r", 40, 5),
    ("left", -90, 5), ("right", 90, 5), ("back_l", -140, 5), ("back_r", 140, 5),
    ("back", 180, 5), ("top_front", 0, 45), ("low_front", 0, -30),
]


_RENDER_ATTRS = ("engine", "resolution_x", "resolution_y", "resolution_percentage", "film_transparent", "filepath")
_SHADING_ATTRS = ("light", "color_type", "single_color", "show_cavity", "cavity_type", "show_object_outline",
                  "background_type", "background_color")


def snapshot_settings(scene):
    """Capture the render/display settings FlowTopo touches, so the user's scene is left as it was."""
    snap = {"render": {a: getattr(scene.render, a) for a in _RENDER_ATTRS},
            "image": {a: getattr(scene.render.image_settings, a) for a in ("file_format", "color_mode")},
            "shading": {a: _copy(getattr(scene.display.shading, a)) for a in _SHADING_ATTRS},
            "aa": scene.display.render_aa, "camera": scene.camera,
            "view_transform": getattr(scene.view_settings, "view_transform", None)}
    return snap


def restore_settings(scene, snap):
    for group, target in (("render", scene.render), ("image", scene.render.image_settings), ("shading", scene.display.shading)):
        for a, v in snap[group].items():
            try:
                setattr(target, a, v)
            except (AttributeError, TypeError, ValueError):
                pass
    scene.display.render_aa = snap["aa"]
    if snap["view_transform"] is not None:
        scene.view_settings.view_transform = snap["view_transform"]
    cam = bpy.data.objects.get("FT_Camera")
    if cam is not None:
        data = cam.data
        bpy.data.objects.remove(cam, do_unlink=True)
        if data.users == 0:
            bpy.data.cameras.remove(data)
    scene.camera = snap["camera"] if snap["camera"] and snap["camera"].name in bpy.data.objects else scene.camera


def _copy(v):
    return tuple(v) if hasattr(v, "__len__") and not isinstance(v, str) else v


def _ensure_camera(scene):
    cam = bpy.data.objects.get("FT_Camera")
    if cam is None:
        cam = bpy.data.objects.new("FT_Camera", bpy.data.cameras.new("FT_Camera"))
        scene.collection.objects.link(cam)
    cam.data.lens_unit = "FOV"
    cam.data.angle = math.radians(30)
    cam.data.clip_start = 0.01
    cam.data.clip_end = 100
    scene.camera = cam
    return cam


def setup_render(scene, wireframe=False):
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = RES
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    # No metadata stamps: identical renders must give identical bytes (API cache keys).
    scene.render.use_stamp = False
    for attr in dir(scene.render):
        if attr.startswith("use_stamp_"):
            try:
                setattr(scene.render, attr, False)
            except (AttributeError, TypeError):
                pass
    sh = scene.display.shading
    sh.light = "STUDIO"
    sh.color_type = "SINGLE"
    sh.single_color = (0.72, 0.72, 0.72)
    sh.show_cavity = True
    sh.cavity_type = "BOTH"
    sh.show_object_outline = False
    sh.background_type = "VIEWPORT"
    sh.background_color = (1.0, 1.0, 1.0)
    scene.display.render_aa = "8"
    if hasattr(scene.view_settings, "view_transform"):
        scene.view_settings.view_transform = "Standard"


def frame_object(obj):
    """Bounding-sphere centre and radius in world space."""
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    center = sum(corners, Vector()) / 8.0
    radius = max((c - center).length for c in corners)
    return center, radius


def place_camera(cam, center, radius, az_deg, el_deg, margin=1.08, aspect=1.0):
    """aspect = width / height. For landscape frames the vertical FOV is the tight one."""
    half = cam.data.angle / 2
    if aspect > 1.0:
        half = math.atan(math.tan(half) / aspect)
    dist = radius * margin / math.sin(half)
    az, el = math.radians(az_deg), math.radians(el_deg)
    offset = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    cam.location = center + offset * dist
    direction = center - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def render_views(obj, out_dir, views=VIEWS_FULL, hide_others=True, prefix="view", center=None, radius=None):
    """Render each view to out_dir/<prefix>_<name>.png and write views.json with camera data.

    center/radius frame a part of the object (close-ups); default = the whole object.
    """
    scene = bpy.context.scene
    os.makedirs(out_dir, exist_ok=True)
    setup_render(scene)
    cam = _ensure_camera(scene)
    hidden = []
    if hide_others:
        for o in scene.objects:
            if o is not obj and o.type in ("MESH", "CURVE", "EMPTY") and not o.hide_render:
                o.hide_render = True
                hidden.append(o)
    if center is None:
        center, radius = frame_object(obj)
    center = Vector(tuple(center))
    records = []
    try:
        for name, az, el in views:
            place_camera(cam, center, radius, az, el)
            bpy.context.view_layer.update()
            path = os.path.join(out_dir, "%s_%s.png" % (prefix, name))
            scene.render.filepath = path
            bpy.ops.render.render(write_still=True)
            records.append({
                "name": name, "az": az, "el": el, "path": path,
                "matrix_world": [list(r) for r in cam.matrix_world],
                "angle": cam.data.angle, "res": RES,
            })
    finally:
        for o in hidden:
            o.hide_render = False
    with open(os.path.join(out_dir, "views.json"), "w") as f:
        json.dump({"center": list(center), "radius": radius, "views": records}, f, indent=1)
    return records
