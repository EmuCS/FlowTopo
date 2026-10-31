"""Build the eval set: messy triangle meshes (scan / AI-output style) plus clean references.

Run:
  blender --background --disable-autoexec <human_base_meshes_bundle.blend> --python scripts/prep_assets.py

Writes assets/meshes/<name>_messy.obj (input) and <name>_ref.obj (artist topology, if any).
Every mesh is centred, faces -Y and is scaled so its largest dimension is 2.0.
"""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "meshes")
LEE = os.path.join(ROOT, "assets", "downloads", "leeperrysmith", "LeePerrySmith.glb")
# AI-generated dragon head (Sloyd, CC BY 4.0): https://www.sloyd.ai/free-3d-models/model/dragonhead-5s39xjbw
DRAGON = os.path.join(ROOT, "assets", "downloads", "sloyd_dragonhead", "dragonhead_5s39xjbw.glb")
ONLY = sys.argv[sys.argv.index("--") + 2:] if "--" in sys.argv and "--only" in sys.argv else []  # e.g. -- --only dragon

# (object name in the bundle, short name, multires level to bake, target triangle count)
BUNDLE = [
    ("GEO-head_animation_realistic", "head_realistic", 2, 24000),
    ("GEO-head_sculpting_realistic", "head_sculpt", 2, 24000),
    ("Head - Generic Topology", "head_generic", 0, 20000),
    ("GEO-body_female_realistic", "body_female_realistic", 1, 40000),
    ("GEO-body_male_stylized", "body_male_stylized", 0, 40000),
    ("GEO-body_female_stylized", "body_female_stylized", 0, 40000),
    ("Hand  - Realistic", "hand_realistic", 1, 20000),
]


def select_only(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def normalize(obj):
    select_only(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    obj.location = (0, 0, 0)
    s = 2.0 / max(obj.dimensions)
    obj.scale = (s, s, s)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def bake_modifiers(obj, multires_level):
    select_only(obj)
    for m in list(obj.modifiers):
        if m.type in ("MASK", "ARMATURE", "SOLIDIFY", "SHRINKWRAP"):
            obj.modifiers.remove(m)
            continue
        if m.type == "MULTIRES":
            m.levels = m.sculpt_levels = m.render_levels = min(multires_level, m.total_levels)
        elif m.type == "SUBSURF":
            m.levels = min(m.levels, 1)
        try:
            bpy.ops.object.modifier_apply(modifier=m.name)
        except RuntimeError as e:
            print("WARN modifier", m.name, e)
            obj.modifiers.remove(m)


def make_messy(obj, target_tris):
    """Voxel remesh (forget the artist topology) + decimate to irregular triangles."""
    select_only(obj)
    area_guess = sum(p.area for p in obj.data.polygons)
    obj.data.remesh_voxel_size = max((area_guess / (target_tris * 1.6)) ** 0.5, 0.004)
    bpy.ops.object.voxel_remesh()
    tris_now = 2 * len(obj.data.polygons)
    dec = obj.modifiers.new("dec", "DECIMATE")
    dec.ratio = min(1.0, target_tris / max(tris_now, 1))
    bpy.ops.object.modifier_apply(modifier="dec")
    tri = obj.modifiers.new("tri", "TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier="tri")


def export_obj(obj, path):
    select_only(obj)
    bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True, export_materials=False,
                          export_uv=False, export_normals=False, apply_modifiers=True)


def dup(obj, name):
    new = obj.copy()
    new.data = obj.data.copy()
    new.name = name
    bpy.context.scene.collection.objects.link(new)
    new.parent = None
    return new


def import_glb(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    for m in meshes:
        m.parent = None
    if len(meshes) > 1:
        select_only(meshes[0])
        for m in meshes[1:]:
            m.select_set(True)
        bpy.ops.object.join()
    return meshes[0]


def triangulate(obj):
    select_only(obj)
    tri = obj.modifiers.new("tri", "TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier=tri.name)


def main():
    os.makedirs(OUT, exist_ok=True)
    if "dragon" in ONLY:
        return prep_dragon()
    for src_name, short, mres, tris in BUNDLE:
        src = bpy.data.objects.get(src_name)
        if src is None:
            print("MISSING", src_name)
            continue
        ref = dup(src, short + "_ref")
        ref.matrix_world = src.matrix_world.copy()
        bake_modifiers(ref, mres)
        normalize(ref)
        export_obj(ref, os.path.join(OUT, short + "_ref.obj"))
        messy = dup(ref, short + "_messy")
        make_messy(messy, tris)
        export_obj(messy, os.path.join(OUT, short + "_messy.obj"))
        print("PREP|%s|ref_faces=%d|messy_tris=%d" % (short, len(ref.data.polygons), len(messy.data.polygons)))

    # Suzanne: a non-human "creature" with no template anywhere.
    bpy.ops.mesh.primitive_monkey_add()
    suz = bpy.context.active_object
    sub = suz.modifiers.new("sub", "SUBSURF")
    sub.levels = 2
    bpy.ops.object.modifier_apply(modifier="sub")
    suz.rotation_euler = (0, 0, 0)
    normalize(suz)
    make_messy(suz, 16000)
    export_obj(suz, os.path.join(OUT, "suzanne_messy.obj"))
    print("PREP|suzanne|messy_tris=%d" % len(suz.data.polygons))

    # Real head scan (CC BY 3.0, Lee Perry-Smith / Infinite Realities): used as scanned.
    if os.path.isfile(LEE):
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=LEE)
        meshes = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
        lee = meshes[0]
        lee.parent = None
        normalize(lee)
        tri = lee.modifiers.new("tri", "TRIANGULATE")
        select_only(lee)
        bpy.ops.object.modifier_apply(modifier="tri")
        export_obj(lee, os.path.join(OUT, "lee_scan_messy.obj"))
        print("PREP|lee_scan|messy_tris=%d" % len(lee.data.polygons))
    prep_dragon()


def prep_dragon():
    """AI-generated creature, used exactly as the generator made it (already triangle soup)."""
    if not os.path.isfile(DRAGON):
        return
    d = import_glb(DRAGON)
    normalize(d)
    triangulate(d)
    export_obj(d, os.path.join(OUT, "dragon_messy.obj"))
    print("PREP|dragon|messy_tris=%d" % len(d.data.polygons))


main()
