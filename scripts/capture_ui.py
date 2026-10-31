"""Record the real add-on in the Blender UI: window screenshots before, during and after a run.

blender --factory-startup --window-fullscreen --enable-event-simulate out/ui_capture/start.blend \
    --python scripts/capture_ui.py -- [mesh_name]
(start.blend = an empty factory scene, so no splash screen; event simulation also ignores real input)
Uses the installed extensions (FlowTopo + QRemeshify) and the project out/ folder as data dir,
so a mesh from the eval is served from the API cache. Factory startup keeps your own
preferences untouched. Writes out/ui_capture/<mesh>/NNNN.png plus times.json, then quits.
"""
import json
import os
import sys
import time

import bpy
from mathutils import Quaternion

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
NAME = args[0] if args else "head_realistic"
OUT = os.path.join(ROOT, "out", "ui_capture", NAME + time.strftime("_%H%M%S"))  # one folder per take
os.makedirs(OUT, exist_ok=True)

TAB_XY = (1905, 1080 - 510)  # FlowTopo sidebar tab, window coords (origin bottom-left), 1920x1080 at ui_scale 1.3
state = {"step": 0, "t0": time.time(), "n": 0, "times": [], "mark": 0.0, "phase": "setup"}


def win_area():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == "WINDOW")
    return win, area, region


def shot(label=""):
    win, area, region = win_area()
    path = os.path.join(OUT, "%04d.png" % state["n"])
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot(filepath=path, check_existing=False)
    state["times"].append({"i": state["n"], "t": round(time.time() - state["t0"], 2), "phase": state["phase"], "label": label})
    state["n"] += 1


def setup():
    prefs = bpy.context.preferences
    prefs.view.ui_scale = 1.3
    for mod in ("bl_ext.user_default.qremeshify", "bl_ext.user_default.flowtopo"):
        bpy.ops.preferences.addon_enable(module=mod)
    ap = prefs.addons["bl_ext.user_default.flowtopo"].preferences
    ap.data_dir = os.path.join(ROOT, "out")
    # empty the default scene, import the messy mesh
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.wm.obj_import(filepath=os.path.join(ROOT, "assets", "meshes", NAME + "_messy.obj"))
    obj = bpy.context.selected_objects[0]
    obj.name = NAME
    bpy.context.view_layer.objects.active = obj
    win, area, region = win_area()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screen_full_area()  # only the 3D view, like Ctrl+Space
    win, area, region = win_area()
    sp = area.spaces.active
    sp.show_region_ui = True
    sp.show_region_toolbar = False
    sp.overlay.show_wireframes = True
    sp.overlay.wireframe_threshold = 1.0
    sp.overlay.show_floor = False
    sp.overlay.show_axis_x = sp.overlay.show_axis_y = False
    sp.shading.type = "SOLID"
    sp.shading.light = "STUDIO"
    for r in area.regions:
        if r.type == "UI":
            try:
                r.active_panel_category = "FlowTopo"
            except Exception as e:  # noqa: BLE001
                print("tab:", e)
    r3d = sp.region_3d
    r3d.view_perspective = "PERSP"
    r3d.view_rotation = Quaternion((0.0, 0.0, 1.0), 0.45) @ Quaternion((1.0, 0.0, 0.0), 1.45)
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.view3d.view_selected()
    r3d.view_distance *= 0.8


def tick():
    s = state
    now = time.time() - s["t0"]
    try:
        import bl_ext.user_default.flowtopo as ft
    except ImportError:
        ft = None
    if s["phase"] == "setup":
        setup()
        s["phase"], s["mark"] = "tab", now
        return 1.0
    if s["phase"] == "tab":  # sidebar tabs can't be set from Python: simulate a click (needs --enable-event-simulate)
        win = bpy.context.window_manager.windows[0]
        x, y = TAB_XY
        for ev, val in (("MOUSEMOVE", "NOTHING"), ("LEFTMOUSE", "PRESS"), ("LEFTMOUSE", "RELEASE")):
            win.event_simulate(ev, val, x=x, y=y)
        win.event_simulate("MOUSEMOVE", "NOTHING", x=900, y=40)  # park the cursor
        s["phase"], s["mark"] = "before", now
        return 1.0
    if s["phase"] == "before":
        shot()
        if now - s["mark"] > 3.0:
            win, area, region = win_area()
            with bpy.context.temp_override(window=win, area=area, region=region):
                bpy.ops.flowtopo.run("INVOKE_DEFAULT")
            s["phase"], s["mark"] = "running", now
        return 0.25
    if s["phase"] == "running":
        shot(bpy.context.scene.flowtopo.status)
        if ft is not None and not ft._running and now - s["mark"] > 2.0:
            s["phase"], s["mark"] = "after", now
        return 0.3
    if s["phase"] == "after":
        shot()
        if 2.0 < now - s["mark"] < 3.2:  # scroll the sidebar so Spark's review comes into view
            win = bpy.context.window_manager.windows[0]
            win.event_simulate("MOUSEMOVE", "NOTHING", x=1720, y=500)
            for _ in range(3):
                win.event_simulate("WHEELDOWNMOUSE", "PRESS", x=1720, y=500)
        if now - s["mark"] > 6.0:
            win, area, region = win_area()
            with bpy.context.temp_override(window=win, area=area, region=region):
                bpy.ops.flowtopo.toggle_source()
            s["phase"], s["mark"] = "compare_src", now
        return 0.25
    if s["phase"] == "compare_src":
        shot()
        if now - s["mark"] > 2.5:
            win, area, region = win_area()
            with bpy.context.temp_override(window=win, area=area, region=region):
                bpy.ops.flowtopo.toggle_source()
            s["phase"], s["mark"] = "compare_out", now
        return 0.25
    if s["phase"] == "compare_out":
        shot()
        if now - s["mark"] > 2.5:
            json.dump({"name": NAME, "frames": s["times"], "status": bpy.context.scene.flowtopo.status,
                       "result": json.loads(bpy.context.scene.flowtopo.last_result or "{}").get("spent_usd")},
                      open(os.path.join(OUT, "times.json"), "w"), indent=1)
            bpy.ops.wm.quit_blender()
            return None
        return 0.25
    return None


bpy.app.timers.register(tick, first_interval=2.0)
