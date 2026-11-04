"""FlowTopo pipeline as a step generator (so the Blender UI can show progress).

    run = FlowTopoRun(obj, workdir, Options())
    for frac, msg in run.steps(): ...
    run.result  # dict, run.guided_obj  # bpy object
"""
import json
import os
import time
from dataclasses import asdict, dataclass, field

import bpy
import numpy as np

from . import api, core, guides, metrics, plan, remesh, render, sam, viz

FACE_VIEWS = ("front", "front_l", "front_r", "left", "right", "top_front", "low_front")
DEFAULT_FEATURES = [
    {"name": "eyes", "sam_prompt": "eye", "loop_type": "concentric", "rings_out": 2, "rings_in": 0, "spacing": 0.45, "why": "default"},
    {"name": "mouth", "sam_prompt": "mouth", "loop_type": "concentric", "rings_out": 3, "rings_in": 0, "spacing": 0.45, "why": "default"},
]


@dataclass
class Options:
    use_spark: bool = True          # inventory: let Muse Spark decide features
    critique: bool = True           # one Spark review pass with adjustments
    baselines: bool = False         # also run unguided QRemeshify + QuadriFlow and score them
    scale: float = 1.5              # QuadWild quad size factor (relative to input edge length)
    preprocess: bool = False        # QuadWild's own remesh weakens our guides; keep off
    symmetry: str = "auto"          # auto | on | off
    min_score: float = 0.3          # SAM confidence threshold
    features: list = field(default_factory=list)  # override plan features
    reference: str = ""             # optional OBJ with artist topology to score the same way
    render_extras: bool = True
    field_smooth: int = 10          # smoothing passes on each distance field (rounder rings)
    relax: int = 3                  # relax rounds after remeshing (smooth + snap back to the surface)
    density: float = 0.0            # >0: inflate around eyes/mouth before remeshing, so quads there come out smaller
    keep_both: bool = False         # demo: keep the pass-1 mesh next to pass 2 instead of deleting it
    head_pass: bool = True          # full bodies: find the head with SAM, look for face features in close-ups
    qr: dict = field(default_factory=dict)  # extra QuadPatches settings for the guided remesh
    closed_rings: bool = False     # keep only closed guide rings; tested 31 Oct: no extra loops, slightly lower score


def _dup(obj, name):
    cp = obj.copy()
    cp.data = obj.data.copy()
    cp.name = name
    for c in obj.users_collection[:1]:
        c.objects.link(cp)
    if not cp.users_collection:
        bpy.context.scene.collection.objects.link(cp)
    return cp


def _select_only(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


class FlowTopoRun:
    def __init__(self, obj, workdir, options=None):
        self.src = obj
        self.opts = options or Options()
        self.workdir = workdir
        os.makedirs(workdir, exist_ok=True)
        self.result = {"input": obj.name, "options": asdict(self.opts), "timings": {}, "renders": {}}
        self.guided_obj = None
        self._t0 = time.time()
        self._spent0 = api.spent_usd()

    # -- helpers ---------------------------------------------------------------
    def _tick(self, key, t):
        self.result["timings"][key] = round(time.time() - t, 2)

    def _render(self, key, obj, **kw):
        if not self.opts.render_extras and key not in ("result_face", "regions", "guides_face"):
            return
        path = os.path.join(self.workdir, key + ".png")
        viz.render_object(obj, path, **kw)
        self.result["renders"][key] = path

    # -- pipeline ----------------------------------------------------------------
    def steps(self):
        """Run the pipeline; the user's render settings are restored even on error or cancel."""
        scene = bpy.context.scene
        snap = render.snapshot_settings(scene)
        try:
            yield from self._steps()
        finally:
            render.restore_settings(scene, snap)

    def _steps(self):
        t = time.time()
        yield 0.02, "Preparing a working copy"
        work = _dup(self.src, self.src.name + "_ft_work")
        work.matrix_world = self.src.matrix_world.copy()
        _select_only(work)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        for m in list(work.modifiers):
            work.modifiers.remove(m)
        # centre the mesh at the origin (QuadWild symmetry mirrors about local X=0)
        co = np.empty(len(work.data.vertices) * 3, np.float32)
        work.data.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        offset = (co.min(0) + co.max(0)) / 2.0
        co -= offset
        work.data.vertices.foreach_set("co", co.ravel())
        work.data.update()
        self.offset = offset
        bm_tris = sum(len(p.vertices) - 2 for p in work.data.polygons)
        if bm_tris > 120000:
            dec = work.modifiers.new("ft_dec", "DECIMATE")
            dec.ratio = 120000 / bm_tris
            _select_only(work)
            bpy.ops.object.modifier_apply(modifier=dec.name)
        self.work = work
        self._tick("prepare", t)

        t = time.time()
        yield 0.06, "Rendering 10 views"
        views = render.render_views(work, os.path.join(self.workdir, "views"))
        meta = json.load(open(os.path.join(self.workdir, "views", "views.json")))
        self.views = views
        self._tick("render", t)
        if self.opts.render_extras:
            self._render("input_face", work, wire=True)

        t = time.time()
        yield 0.14, "Muse Spark: deciding which features need edge loops"
        if self.opts.features:
            inv = {"subject": "user-specified", "category": "other", "symmetric_x": True, "density": "medium",
                   "features": self.opts.features, "notes": ""}
        elif self.opts.use_spark:
            paths = [v["path"] for v in views if v["name"] in ("front", "front_r", "right")]
            inv, _raw = plan.inventory(paths)
        else:
            inv = {"subject": "default", "category": "human_head", "symmetric_x": True, "density": "medium",
                   "features": DEFAULT_FEATURES, "notes": ""}
        self.plan = inv
        self.result["plan"] = inv
        self._tick("inventory", t)

        t = time.time()
        md = core.MeshData(work)
        self.md = md
        vis = {v["name"]: core.visible_faces(md, v, meta["radius"]) for v in views}
        body_like = inv["category"] in ("human_body", "stylized_character")
        use_views = views if body_like else [v for v in views if v["name"] in FACE_VIEWS]
        self._tick("visibility", t)

        regions = []
        feats = [f for f in inv["features"] if f.get("rings_out", 0) + f.get("rings_in", 0) > 0 or f["loop_type"] == "boundary"]
        head_views = None
        for fi, feat in enumerate(feats):
            t = time.time()
            frac = 0.18 + 0.30 * fi / max(len(feats), 1)
            step = 0.30 / max(len(feats), 1)
            found, n_inst = yield from self._detect(feat, use_views, vis, md, meta, md.area.sum(), frac, step)
            if not found and body_like and self.opts.head_pass and feat["loop_type"] != "limb_rings":
                # on a full body the face can be a few pixels wide: find the head, look again in close-ups
                if head_views is None:
                    yield frac, "SAM 3.1: finding the head for close-up views"
                    head_views = self._head_views(md, views, vis, meta) or []
                if head_views:
                    found, n2 = yield from self._detect(feat, head_views, vis, md, meta, self._head_area, frac, step)
                    n_inst += n2
                    self.result.setdefault("head_pass_features", []).append(feat["name"])
            for kept, reg in enumerate(found):
                regions.append({"feature": feat, "name": "%s_%d" % (feat["name"], kept),
                                "mask": reg, "area": float(md.area[reg].sum())})
            self.result.setdefault("detections", []).append(
                {"feature": feat["name"], "prompt": feat["sam_prompt"], "sam_instances": n_inst, "regions": len(found)})
            self._tick("sam_" + feat["name"], t)
        self.regions = regions

        colors = np.full((md.nf, 3), 0.8)
        for i, r in enumerate(regions):
            colors[r["mask"]] = viz.PALETTE[i % len(viz.PALETTE)]
        viz.set_face_colors(work, colors)
        self._render("regions", work, colors=True)
        self._focus = self._feature_focus()
        if self._focus:
            self.result["focus"] = {"center": [float(x) for x in self._focus["center"]],
                                    "radius": self._focus["radius"]}

        yield 0.52, "Building edge-flow guides (%d regions)" % len(regions)
        t = time.time()
        for r in regions:
            if r["feature"]["loop_type"] == "limb_rings":
                sd = core.signed_distance(md, r["mask"])  # along-the-surface for limbs
            else:
                sd = core.signed_distance_euclid(md, r["mask"])  # round rings for local features
            r["field"] = core.smooth_field(md, sd, iterations=self.opts.field_smooth)
            r["scale"] = (r["area"] / np.pi) ** 0.5
        self._tick("fields", t)

        sym = self.opts.symmetry == "on" or (self.opts.symmetry == "auto" and inv.get("symmetric_x"))
        self.result["symmetry"] = bool(sym)
        guided, levels = self._guided_pass(inv["features"], sym, self.opts.scale, tag="pass1")
        yield 0.75, "Scoring the result"
        m = self._score(guided, levels)
        self.result["metrics"] = m
        self.guided_obj = guided
        self.levels = levels

        if self.opts.critique and self.opts.use_spark and regions:
            yield 0.80, "Muse Spark: reviewing the wireframe"
            t = time.time()
            self._render("result_face", guided, wire=True, **self._focus)
            self._render("guides_face", self.guided_src, seams=True, **self._focus)
            summ = "aligned edge flow around features %s%% (random grid ~%s%%), quads %s%%, irregular vertices %s%%" % (
                m.get("aligned_pct"), metrics.random_baseline_pct(), m["quad_pct"], m["irregular_pct"])
            crit, _raw = plan.critique([self.result["renders"]["result_face"], self.result["renders"]["guides_face"]], inv, summ)
            self.result["critique"] = crit
            self._tick("critique", t)
            if crit.get("rerun") and (crit.get("adjustments") or abs(crit.get("density_scale", 1.0) - 1.0) > 0.05):
                yield 0.86, "Applying Spark's adjustments and remeshing"
                feats2 = []
                adj = {a["name"]: a for a in crit["adjustments"]}
                for f in inv["features"]:
                    f2 = dict(f)
                    a = adj.get(f["name"])
                    if a:
                        if not a["keep"]:
                            continue
                        f2["rings_out"] = max(0, min(4, int(a["rings_out"])))
                        f2["spacing"] = max(0.2, min(1.0, float(a["spacing"])))
                    feats2.append(f2)
                scale2 = self.opts.scale * max(0.6, min(1.5, float(crit.get("density_scale", 1.0))))
                guided2, levels2 = self._guided_pass(feats2, sym, scale2, tag="pass2")
                m2 = self._score(guided2, levels2)
                self.result["metrics_pass2"] = m2
                self.result["metrics_pass1"] = m
                # keep Spark's second pass only if it measurably helps the deformation-critical loops
                better = metrics.primary_score(m2) > metrics.primary_score(m)
                if better:
                    if not self.opts.keep_both:
                        self._discard(guided)
                    self.guided_obj, self.levels, self.result["metrics"] = guided2, levels2, m2
                    self.result["plan_final"] = feats2
                elif not self.opts.keep_both:
                    self._discard(guided2)
                self.result["pass2_kept"] = bool(better)

        if self.opts.baselines:
            yield 0.90, "Baselines: unguided QRemeshify and QuadriFlow"
            t = time.time()
            ung, secs = remesh.qremeshify(self.work, use_seams=False, preprocess=self.opts.preprocess, scale=self.opts.scale,
                                          symmetry_x=sym, name=self.src.name + "_QRemeshify_plain")
            remesh.relax(ung, self.work, rounds=self.opts.relax)  # same post-processing as FlowTopo: fair comparison
            self.result["baseline_qremeshify"] = self._score(ung, self.levels)
            self.result["baseline_qremeshify"]["secs"] = round(secs, 1)
            qf, secs = remesh.quadriflow(self.work, len(self.guided_obj.data.polygons), name=self.src.name + "_QuadriFlow")
            remesh.relax(qf, self.work, rounds=self.opts.relax)
            self.result["baseline_quadriflow"] = self._score(qf, self.levels)
            self.result["baseline_quadriflow"]["secs"] = round(secs, 1)
            self._render("baseline_qremeshify_face", ung, wire=True, **self._focus)
            self._render("baseline_quadriflow_face", qf, wire=True, **self._focus)
            self.baselines = (ung, qf)
            self._tick("baselines", t)

        if self.opts.reference and os.path.isfile(self.opts.reference):
            yield 0.93, "Scoring the artist reference topology"
            before = set(bpy.data.objects)
            bpy.ops.wm.obj_import(filepath=self.opts.reference)
            ref = [o for o in bpy.data.objects if o not in before][0]
            _select_only(ref)
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
            co = np.empty(len(ref.data.vertices) * 3, np.float32)
            ref.data.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3) - self.offset
            ref.data.vertices.foreach_set("co", co.ravel())
            ref.data.update()
            ref.name = self.src.name + "_artist_reference"
            ref.data.shade_smooth()  # display only: same shading as the remeshed results
            self.result["reference"] = self._score(ref, self.levels)
            self._render("reference_face", ref, wire=True, **self._focus)
            self.reference_obj = ref

        yield 0.95, "Final renders"
        out = self.guided_obj
        out.name = self.src.name + "_FlowTopo"
        self._render("result_face", out, wire=True, **self._focus)
        self._render("result_front", out, wire=True)
        self._render("result_3q", out, wire=True, az=35, el=10)
        self._render("guides_face", self.guided_src, seams=True, **self._focus)
        # results were built in the centred working space: shift them back onto the source
        extra = list(getattr(self, "baselines", ())) + ([self.reference_obj] if hasattr(self, "reference_obj") else [])
        for o in [out] + extra:
            o.location = tuple(float(x) for x in self.offset)
        self.result["spent_usd"] = round(api.spent_usd() - self._spent0, 4)
        self.result["total_secs"] = round(time.time() - self._t0, 1)
        self.result["output"] = out.name
        with open(os.path.join(self.workdir, "result.json"), "w") as f:
            json.dump(self.result, f, indent=1, default=str)
        self._cleanup()
        yield 1.0, "Done: %d quads, %s%% aligned flow" % (len(out.data.polygons), self.result["metrics"].get("aligned_pct"))

    # -- passes ------------------------------------------------------------------
    def _guided_pass(self, features, sym, scale, tag):
        t = time.time()
        by_name = {f["name"]: f for f in features}
        fields, plans, levels = [], [], []
        for r in self.regions:
            f = by_name.get(r["feature"]["name"])
            if f is None:
                continue
            lv = self._levels_for(r, f)
            if not lv:
                continue
            fields.append(r["field"])
            plans.append(lv)
            levels.append({"name": r["name"], "field": r["field"], "levels": lv, "loop_type": f["loop_type"], "mask": r["mask"],
                           "zone_max": max(max(lv), 0.0) + 0.5 * max(f.get("spacing", 0.4), 0.2) * r["scale"]})
        src = _dup(self.work, self.src.name + "_ft_guides_" + tag)
        n = guides.cut_guides(src, fields, plans, closed_only=self.opts.closed_rings) if fields else 0
        self.result.setdefault("seams", {})[tag] = n
        if fields:
            self.result.setdefault("seams_dropped_open", {})[tag] = getattr(guides.cut_guides, "last_dropped", 0)
        self.guided_src = src
        orig = None
        if self.opts.density > 0:
            cs, ws = self._density_targets(levels)
            if cs:
                orig = remesh.inflate(src, cs, ws, self.opts.density)
        out, secs = remesh.qremeshify(src, use_seams=True, preprocess=self.opts.preprocess, scale=scale, symmetry_x=sym,
                                      name=self.src.name + "_FlowTopo_" + tag, qr=self.opts.qr)
        if orig is not None:
            remesh.relax(out, src, rounds=self.opts.relax)  # relax on the inflated surface so the density survives
            remesh.deflate(out, src, orig)
        else:
            remesh.relax(out, self.work, rounds=self.opts.relax)
        self._tick("remesh_" + tag, t)
        return out, levels

    def _density_targets(self, levels):
        """Center and Gaussian width for each concentric feature: the region's centroid, and its
        radius plus the ring zone, so the quads shrink across the whole ring zone."""
        md, cs, ws = self.md, [], []
        for lv in levels:
            if lv["loop_type"] != "concentric":
                continue
            m = lv["mask"]
            a = md.area[m]
            c = (md.cent[m] * a[:, None]).sum(0) / a.sum()
            cs.append(c)
            ws.append(float(np.linalg.norm(md.cent[m] - c, axis=1).max()) + lv["zone_max"])
        return cs, ws

    def _levels_for(self, r, f):
        if f["loop_type"] == "limb_rings":
            return self._limb_levels(r, f)
        spec = {"rings_out": f.get("rings_out", 2), "rings_in": f.get("rings_in", 0),
                "spacing": f.get("spacing", 0.45), "boundary": True}
        if f["loop_type"] == "boundary":
            spec["rings_out"] = min(spec["rings_out"], 1)
        return guides.ring_levels(spec, r["scale"])

    def _limb_levels(self, r, f):
        """Rings that march up a limb from the extremity until the cross-section jumps."""
        md, fld = self.md, r["field"]
        s = max(f.get("spacing", 0.6), 0.2) * r["scale"]
        a, b = fld[md.edges[:, 0]], fld[md.edges[:, 1]]
        levels, counts = [0.0], []
        for k in range(1, 40):
            L = k * s
            c = int(((a - L) * (b - L) < 0).sum())
            if c == 0:
                break
            if len(counts) >= 3 and c > 1.7 * float(np.median(counts[:3])):
                break
            counts.append(c)
            levels.append(L)
        return levels

    def _detect(self, feat, fviews, vis, md, meta, total_area, frac, step):
        """SAM on each view, vote onto the mesh, clean up. Yields progress; returns (regions, n_instances).

        total_area sizes the noise filter (the whole mesh, or the head for close-ups)."""
        masks, n_inst = {}, 0
        for vi, v in enumerate(fviews):
            yield (frac + step * vi / len(fviews),
                   "SAM 3.1: finding '%s' in %d views" % (feat["sam_prompt"], len(fviews)) if not vi else
                   "SAM 3.1: finding '%s' (view %d/%d)" % (feat["sam_prompt"], vi + 1, len(fviews)))
            inst, _ = sam.segment(v["path"], feat["sam_prompt"])
            inst = [i for i in inst if i["score"] >= self.opts.min_score]
            n_inst += len(inst)
            masks[v["name"]] = core.mask_frame(inst, v["res"])[0] if inst else np.zeros((v["res"], v["res"]), bool)
        lab, _ratio = core.vote_labels(md, fviews, vis, masks, min_ratio=0.5, min_pos=2)
        k = max(2, int(round(0.015 * meta["radius"] / max(md.mean_edge, 1e-9))))
        lab = core.closing(md, lab, k=k)
        comps = sorted(core.components(md, lab), key=len, reverse=True)
        found = []
        for comp in comps:
            a = md.area[comp].sum()
            if a < 0.0004 * total_area or (found and a < 0.15 * md.area[comps[0]].sum()):
                continue
            found.append(core.clean_region(md, comp))
            if len(found) >= 6:
                break
        return found, n_inst

    def _head_views(self, md, views, vis, meta):
        """SAM 'head' on the body views -> head region -> close-up renders of it (same view names
        prefixed head_). Adds their visibility to vis. Returns the view records, or None."""
        probe = [v for v in views if v["name"] in ("front", "front_l", "front_r", "left", "right")]
        masks = {}
        for v in probe:
            inst, _ = sam.segment(v["path"], "head")
            inst = [i for i in inst if i["score"] >= self.opts.min_score]
            masks[v["name"]] = core.mask_frame(inst, v["res"])[0] if inst else np.zeros((v["res"], v["res"]), bool)
        lab, _ratio = core.vote_labels(md, probe, vis, masks, min_ratio=0.5, min_pos=2)
        comps = sorted(core.components(md, lab), key=len, reverse=True)
        if not comps:
            self.result["head_pass"] = "no head found"
            return None
        head = np.array(comps[0])
        a = md.area[head]
        c = (md.cent[head] * a[:, None]).sum(0) / a.sum()
        r = float(np.linalg.norm(md.cent[head] - c, axis=1).max() * 1.15)
        if r > 0.6 * meta["radius"]:  # the "head" covers most of the model: close-ups add nothing
            self.result["head_pass"] = "head too large (%.2f of model)" % (r / meta["radius"])
            return None
        self._head_area = float(md.area[np.linalg.norm(md.cent - c, axis=1) < r].sum())
        spec = [("head_" + n, az, el) for n, az, el in render.VIEWS_FULL if n in FACE_VIEWS]
        recs = render.render_views(self.work, os.path.join(self.workdir, "views_head"), views=spec, center=c, radius=r)
        for v in recs:
            vis[v["name"]] = core.visible_faces(md, v, meta["radius"])
        self.result["head_pass"] = {"center": [float(x) for x in c], "radius": r, "views": len(recs)}
        return recs

    def _score(self, obj, levels):
        return metrics.evaluate(self.md, obj, levels, None)

    def _feature_focus(self):
        allf = np.zeros(self.md.nf, bool)
        for r in self.regions:
            if r["feature"]["loop_type"] != "limb_rings":
                allf |= r["mask"]
        if not allf.any():
            return {}
        c = self.md.cent[allf].mean(0)
        rad = float(np.linalg.norm(self.md.cent[allf] - c, axis=1).max() * 1.7)
        return {"center": c, "radius": max(rad, 0.05)}

    def _discard(self, obj):
        me = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)

    def _cleanup(self):
        for o in (self.work,):
            if o and o.name in bpy.data.objects:
                o.hide_set(True)
                o.hide_render = True
        for o in bpy.data.objects:
            if o.name.startswith(self.src.name + "_ft_guides_"):
                o.hide_set(True)
                o.hide_render = True
        self.src.hide_set(False)
