"""Smoke test: does QRemeshify run headless in this Blender?

Run (no --factory-startup, the extension must load):
  blender --background --python spike/qr_smoke.py
"""
import time

import bpy
import bmesh


def mesh_stats(obj):
    me = obj.data
    n = len(me.polygons)
    quads = sum(1 for p in me.polygons if len(p.vertices) == 4)
    return n, quads, (100.0 * quads / n) if n else 0.0


bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.mesh.primitive_monkey_add()
obj = bpy.context.active_object
obj.modifiers.new("sub", "SUBSURF").levels = 2
bpy.ops.object.modifier_apply(modifier="sub")
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.quads_convert_to_tris()
bpy.ops.object.mode_set(mode="OBJECT")
print("INPUT  faces=%d quads=%d (%.1f%%)" % mesh_stats(obj))

props = bpy.context.scene.quadwild_props
props.enableRemesh = False
props.enableSharp = True
props.sharpAngle = 35

t = time.time()
result = bpy.ops.qremeshify.remesh()
print("OPERATOR", result, "%.1fs" % (time.time() - t))

out = bpy.context.active_object
print("OUTPUT object=%s faces=%d quads=%d (%.1f%%)" % ((out.name,) + mesh_stats(out)))
