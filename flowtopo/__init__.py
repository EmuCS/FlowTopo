"""FlowTopo: animation-ready retopology. Muse Spark plans the edge loops, SAM 3.1 finds
the features, FlowTopo cuts guide rings, and QRemeshify (QuadWild) builds the quads.

Works both as a Blender extension (sidebar panel, N-panel > FlowTopo) and as a plain
package for headless scripts (scripts/run_one.py).
"""
import json
import os
import time
import traceback

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

_running = {}


def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _configure_env():
    from . import api
    p = _prefs()
    if p is not None:
        if p.data_dir:
            os.environ["FLOWTOPO_DATA"] = bpy.path.abspath(p.data_dir)
        if p.api_key:
            api.set_api_key(p.api_key)
        api.BUDGET_USD = p.budget_usd
    return api


class FLOWTOPO_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    api_key: StringProperty(name="Meta Model API key", subtype="PASSWORD",
                            description="From dev.meta.ai. Leave empty to read MODEL_API_KEY from the environment or a .env next to the data folder")
    data_dir: StringProperty(name="Data folder", subtype="DIR_PATH", default="",
                             description="Cache, renders and reports. Empty = ~/.flowtopo")
    budget_usd: FloatProperty(name="Spend cap (USD)", default=38.0, min=0.0,
                              description="FlowTopo refuses API calls once the logged spend reaches this")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "api_key")
        col.prop(self, "data_dir")
        col.prop(self, "budget_usd")
        try:
            api = _configure_env()
            col.label(text="Logged spend so far: $%.3f" % api.spent_usd(), icon="INFO")
        except Exception:  # noqa: BLE001
            pass


class FLOWTOPO_Settings(bpy.types.PropertyGroup):
    use_spark: BoolProperty(name="Muse Spark plans the loops", default=True,
                            description="Let Muse Spark decide which features need edge loops; off = eyes + mouth defaults")
    critique: BoolProperty(name="Muse Spark reviews the result", default=True,
                           description="One review pass: Spark looks at the wireframe and adjusts the guides")
    baselines: BoolProperty(name="Compare with plain remeshers", default=False,
                            description="Also run unguided QRemeshify and QuadriFlow and score them")
    quad_size: FloatProperty(name="Quad size", default=1.5, min=0.5, max=6.0,
                             description="Relative to the input edge length; larger = fewer quads")
    symmetry: EnumProperty(name="Symmetry", items=[("auto", "Auto", "Muse Spark decides"),
                                                   ("on", "Mirror X", ""), ("off", "Off", "")], default="auto")
    status: StringProperty(default="")
    progress: FloatProperty(default=0.0, min=0.0, max=1.0, subtype="FACTOR")
    last_result: StringProperty(default="")


class FLOWTOPO_OT_run(bpy.types.Operator):
    """Retopologise the active mesh with AI-placed edge loops"""
    bl_idname = "flowtopo.run"
    bl_label = "FlowTopo: Retopologise"
    bl_options = {"REGISTER"}

    _timer = None
    _gen = None
    _run = None

    @classmethod
    def poll(cls, context):
        o = context.active_object
        return o is not None and o.type == "MESH" and not _running

    def invoke(self, context, event):
        from . import pipeline, remesh
        if not remesh.qremeshify_available():
            self.report({"ERROR"}, "QRemeshify is required: install it from extensions.blender.org or github.com/ksami/QRemeshify")
            return {"CANCELLED"}
        api = _configure_env()
        try:
            api.api_key()
        except api.ApiError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        s = context.scene.flowtopo
        obj = context.active_object
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        opts = pipeline.Options(use_spark=s.use_spark, critique=s.critique and s.use_spark,
                                baselines=s.baselines, scale=s.quad_size, symmetry=s.symmetry)
        workdir = os.path.join(api.data_dir(), "runs", "%s_%s" % (bpy.path.clean_name(obj.name), time.strftime("%Y%m%d_%H%M%S")))
        self._run = pipeline.FlowTopoRun(obj, workdir, opts)
        self._gen = self._run.steps()
        _running["op"] = True
        s.status, s.progress = "Starting...", 0.0
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            return self._finish(context, cancelled=True)
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        s = context.scene.flowtopo
        try:
            frac, msg = next(self._gen)
            s.progress, s.status = frac, msg
            for area in context.screen.areas:
                area.tag_redraw()
            return {"RUNNING_MODAL"}
        except StopIteration:
            return self._finish(context)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            s.status = "Error: %s" % e
            self.report({"ERROR"}, "FlowTopo failed: %s" % e)
            return self._finish(context, cancelled=True)

    def _finish(self, context, cancelled=False):
        context.window_manager.event_timer_remove(self._timer)
        if self._gen is not None:
            self._gen.close()  # runs the pipeline's finally: restores the user's render settings
        _running.clear()
        s = context.scene.flowtopo
        if not cancelled and self._run is not None:
            out = self._run.guided_obj
            s.last_result = json.dumps(self._run.result, default=str)
            for o in context.view_layer.objects:
                o.select_set(False)
            out.hide_set(False)
            out.select_set(True)
            context.view_layer.objects.active = out
            self._run.src.hide_set(True)
            self.report({"INFO"}, s.status)
        return {"FINISHED"} if not cancelled else {"CANCELLED"}


class FLOWTOPO_OT_toggle_source(bpy.types.Operator):
    """Toggle between the original mesh and the FlowTopo result"""
    bl_idname = "flowtopo.toggle_source"
    bl_label = "Compare with original"

    def execute(self, context):
        s = context.scene.flowtopo
        if not s.last_result:
            return {"CANCELLED"}
        r = json.loads(s.last_result)
        src, out = bpy.data.objects.get(r.get("input", "")), bpy.data.objects.get(r.get("output", ""))
        if src and out:
            show_src = not src.visible_get()
            src.hide_set(not show_src)
            out.hide_set(show_src)
        return {"FINISHED"}


class FLOWTOPO_OT_open_report(bpy.types.Operator):
    """Open the folder with renders and the JSON report of the last run"""
    bl_idname = "flowtopo.open_report"
    bl_label = "Open report folder"

    def execute(self, context):
        r = json.loads(context.scene.flowtopo.last_result or "{}")
        renders = r.get("renders", {})
        if renders:
            bpy.ops.wm.path_open(filepath=os.path.dirname(next(iter(renders.values()))))
        return {"FINISHED"}


class FLOWTOPO_PT_panel(bpy.types.Panel):
    bl_label = "FlowTopo"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FlowTopo"

    def draw(self, context):
        layout = self.layout
        s = context.scene.flowtopo
        col = layout.column(align=True)
        col.prop(s, "use_spark")
        sub = col.column()
        sub.enabled = s.use_spark
        sub.prop(s, "critique")
        col.prop(s, "baselines")
        col.separator()
        col.prop(s, "quad_size")
        col.prop(s, "symmetry")
        layout.separator()
        row = layout.row()
        row.scale_y = 1.6
        row.operator("flowtopo.run", icon="MOD_REMESH", text="Retopologise" if not _running else "Running... (Esc)")
        if s.status:
            if hasattr(layout, "progress"):
                layout.progress(factor=s.progress, type="BAR", text=s.status)
            else:
                layout.label(text=s.status)
        if s.last_result:
            self._draw_result(layout, json.loads(s.last_result), _chars_per_line(context))

    def _draw_result(self, layout, r, width):
        box = layout.box()
        plan = r.get("plan", {})
        box.label(text=plan.get("subject", "Result"), icon="OUTLINER_OB_ARMATURE")
        m = r.get("metrics", {})
        if m:
            box.label(text="%d quads, %s%% quads" % (m.get("faces", 0), m.get("quad_pct")))
            if m.get("aligned_pct") is not None:
                box.label(text="Edge flow aligned: %s%%" % m["aligned_pct"], icon="CHECKMARK")
                box.label(text="(random grid: 33%)")
        for key, label in (("baseline_qremeshify", "QRemeshify, unguided"), ("baseline_quadriflow", "QuadriFlow")):
            if key in r and r[key].get("aligned_pct") is not None:
                box.label(text="%s: %s%%" % (label, r[key]["aligned_pct"]))
        feats = plan.get("features", [])
        if feats:
            fb = box.box()
            fb.label(text="Muse Spark's loop plan", icon="LIGHT")
            for f in feats:
                fb.label(text="%s: %s, %d rings" % (f["name"], f["loop_type"], f["rings_out"]))
                if f.get("why"):
                    _wrap(fb, f["why"], width - 4, icon="BLANK1")
        c = r.get("critique")
        if c:
            cb = box.box()
            cb.label(text="Spark review: %s/10" % c.get("score"), icon="VIEWZOOM")
            _wrap(cb, c.get("good", ""), width)
            _wrap(cb, c.get("problems", ""), width)
        cost = r.get("spent_usd")
        if cost == 0:
            box.label(text="API cost of this run: $0 (answers cached)")
        else:
            box.label(text="API cost of this run: $%s" % cost)
        row = box.row()
        row.operator("flowtopo.toggle_source", icon="ARROW_LEFTRIGHT")
        row.operator("flowtopo.open_report", icon="FILE_FOLDER", text="")


def _chars_per_line(context):
    """Characters that fit on one panel line, from the sidebar width (labels clip, they don't wrap)."""
    scale = context.preferences.system.ui_scale
    return max(16, int((context.region.width - 50 * scale) / (7.5 * scale)))


def _wrap(layout, text, width, icon="NONE"):
    words, line = text.split(), ""
    first = True
    for w in words:
        if len(line) + len(w) + 1 > width:
            layout.label(text=line, icon=icon if first else "BLANK1")
            line, first = w, False
        else:
            line = (line + " " + w).strip()
    if line:
        layout.label(text=line, icon=icon if first else "BLANK1")


_classes = (FLOWTOPO_Preferences, FLOWTOPO_Settings, FLOWTOPO_OT_run, FLOWTOPO_OT_toggle_source,
            FLOWTOPO_OT_open_report, FLOWTOPO_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.flowtopo = bpy.props.PointerProperty(type=FLOWTOPO_Settings)


def unregister():
    del bpy.types.Scene.flowtopo
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
