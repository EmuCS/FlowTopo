import bpy
for o in bpy.data.objects:
    if o.type == 'MESH':
        me = o.data
        print("OBJ|%s|verts=%d|faces=%d|mods=%s|dims=%s" % (o.name, len(me.vertices), len(me.polygons), [m.type for m in o.modifiers], tuple(round(d,3) for d in o.dimensions)))
