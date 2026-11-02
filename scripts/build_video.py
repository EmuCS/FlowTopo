"""Assemble the demo video in Blender's Video Sequence Editor.

blender --background --factory-startup --python scripts/build_video.py -- [hero] [extra1 extra2 ...]
Needs out/demo/<name>/ from make_demo.py (+ render_loops.py, capture_ui.py for the optional
shots). Writes demo/flowtopo_demo.mp4 (no audio; the voice-over follows VIDEO_SCRIPT.md).

Style: dark surface like Blender's viewport, one accent colour, short captions (the voice-over
explains), chapter tags for the pipeline steps, and an animated bar chart for the numbers.
"""
import json
import os
import re
import sys

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "out", "demo")
OUTDIR = os.path.join(ROOT, "demo")
FPS = 30
W, H = 1920, 1080
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
HERO = args[0] if args else "head_realistic"
EXTRAS = [a for a in args[1:] if not a.startswith("--")]

# palette (sRGB): dark surface, ink, muted ink, accent. Accent vs neutral bar grey checked with the
# dataviz validator on the dark surface (CVD dE 15, contrast >= 3:1).
SURFACE = (0.086, 0.094, 0.114)   # #16181d
PLATE = (0.05, 0.055, 0.07)
INK = (0.95, 0.95, 0.96, 1)
MUTED = (0.63, 0.65, 0.69, 1)
ACCENT = (0.224, 0.529, 0.898)    # #3987e5
NEUTRAL = (0.431, 0.455, 0.510)   # #6e7482

os.makedirs(OUTDIR, exist_ok=True)
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = W, H
scene.render.resolution_percentage = 100
scene.render.fps = FPS
scene.view_settings.view_transform = "Standard"
se = scene.sequence_editor_create()
STRIPS = se.strips if hasattr(se, "strips") else se.sequences
FONT = None
_fonts = os.path.join(os.path.dirname(bpy.app.binary_path), "%d.%d" % bpy.app.version[:2], "datafiles", "fonts")
try:
    FONT = bpy.data.fonts.load(os.path.join(_fonts, "Inter.woff2"))
except RuntimeError:
    FONT = None
facts = json.load(open(os.path.join(DEMO, HERO, "facts.json")))
t = 0  # current frame


def sec(s):
    return int(round(s * FPS))


def new_effect(name, kind, ch, start, dur):
    try:  # Blender 5.x
        return STRIPS.new_effect(name=name, type=kind, channel=ch, frame_start=start, length=dur)
    except TypeError:  # Blender 4.x
        return STRIPS.new_effect(name=name, type=kind, channel=ch, frame_start=start, frame_end=start + dur)


def fade(s, start, dur, n=8, peak=1.0):
    """Fade a strip in and out over n frames via blend_alpha keyframes."""
    n = min(n, dur // 3)
    for f, a in ((start, 0.0), (start + n, peak), (start + dur - n, peak), (start + dur, 0.0)):
        s.blend_alpha = a
        s.keyframe_insert("blend_alpha", frame=f)


def image(path, start, dur, ch=2, dim=1.0):
    s = STRIPS.new_image(name=os.path.basename(path), filepath=path, channel=ch, frame_start=start)
    s.frame_final_duration = dur
    if dim != 1.0 and hasattr(s, "color_multiply"):
        s.color_multiply = dim
    return s


def frames(folder, start, dur, ch=2, hold=2, dim=1.0):
    """Turntable: one strip per frame (robust across Blender versions), ping-pong, looped to fill dur."""
    files = sorted(f for f in os.listdir(folder) if f.endswith(".png"))
    seq = files + list(reversed(files[1:-1]))
    f, i = start, 0
    while f < start + dur:
        n = min(hold, start + dur - f)
        image(os.path.join(folder, seq[i % len(seq)]), f, n, ch=ch, dim=dim)
        f += n
        i += 1


def rect(start, dur, x, y, w, h, color, ch, alpha=1.0, fade_in=True):
    """A solid rectangle (COLOR strip scaled down). x, y = bottom-left corner in pixels."""
    s = new_effect("rect", "COLOR", ch, start, dur)
    s.color = color
    s.blend_type = "ALPHA_OVER"
    tr = s.transform
    tr.scale_x, tr.scale_y = max(w, 1) / W, max(h, 1) / H
    tr.offset_x, tr.offset_y = x + w / 2 - W / 2, y + h / 2 - H / 2
    if fade_in:
        fade(s, start, dur, peak=alpha)
    else:
        s.blend_alpha = alpha
    return s


def text(body, start, dur, ch=6, size=46, x=0.5, y=0.5, color=INK, align="CENTER", anchor_y="CENTER",
         wrap=0.9, shadow=False, fade_in=True):
    s = new_effect("txt", "TEXT", ch, start, dur)
    s.text = body
    s.font_size = size
    s.location = (x, y)
    s.color = color
    s.wrap_width = wrap
    s.blend_type = "ALPHA_OVER"
    if FONT is not None and hasattr(s, "font"):
        s.font = FONT
    for attr, val in (("alignment_x", align), ("anchor_x", align), ("anchor_y", anchor_y)):
        if hasattr(s, attr):
            try:
                setattr(s, attr, val)
            except TypeError:
                pass
    if shadow and hasattr(s, "use_shadow"):
        s.use_shadow = True
        s.shadow_color = (0, 0, 0, 0.8)
    if fade_in:
        fade(s, start, dur)
    return s


def caption(body, start, dur):
    """Lower third: accent bar + short line of text on a dark plate."""
    w = int(len(body) * 25 + 90)
    rect(start, dur, 60, 70, w, 84, PLATE, ch=4, alpha=0.82)
    rect(start, dur, 60, 70, 8, 84, ACCENT, ch=5)
    text(body, start, dur, size=44, x=100 / W, y=112 / H, align="LEFT", anchor_y="CENTER", wrap=0.9)


def chip(body, start, dur, cx, y=H - 80, accent=False):
    w = int(len(body) * 22 + 60)
    rect(start, dur, int(cx - w / 2), y - 32, w, 64, ACCENT if accent else PLATE, ch=4, alpha=0.9)
    text(body, start, dur, size=38, x=cx / W, y=y / H)


STEPS = [("Muse Spark", "plans the loops", True), ("SAM 3.1", "finds the features", True),
         ("FlowTopo", "cuts guide rings", False), ("QuadWild", "builds the quads", False),
         ("Muse Spark", "reviews the result", True)]


def pipeline(start, dur, stagger=0):
    """The whole flow as five boxes with arrows; Meta models carry an accent bar. stagger = frames between boxes."""
    bw, bh, y = 300, 170, H // 2 - 40
    xs = [W / 2 + (i - 2) * 370 for i in range(len(STEPS))]
    text("How FlowTopo works", start, dur, size=56, y=0.82)
    for i, (name, what, meta) in enumerate(STEPS):
        s0 = start + i * stagger
        d = dur - i * stagger
        x = int(xs[i] - bw / 2)
        rect(s0, d, x, y, bw, bh, PLATE, ch=4, alpha=0.95)
        if meta:
            rect(s0, d, x, y + bh - 8, bw, 8, ACCENT, ch=5)
        text(name, s0, d, size=44, x=xs[i] / W, y=(y + 105) / H)
        text(what, s0, d, size=30, x=xs[i] / W, y=(y + 52) / H, color=MUTED)
        if i:
            text("→", s0, d, size=54, x=(xs[i] - 185) / W, y=(y + bh / 2) / H, color=MUTED)
    s0 = start + len(STEPS) * stagger
    rect(s0, dur - len(STEPS) * stagger, int(xs[0]), y - 70, int(xs[-1] - xs[0]), 3, MUTED[:3], ch=4, alpha=0.7)
    text("If the review finds problems, Spark adjusts the plan and FlowTopo runs once more",
         s0, dur - len(STEPS) * stagger, size=30, y=(y - 110) / H, color=MUTED)
    rect(start, dur, int(W / 2 - 330), 120, 22, 22, ACCENT, ch=5)
    text("= Meta model", start, dur, size=30, x=(W / 2 - 296) / W, y=131 / H, color=MUTED, align="LEFT")


def step_bar(active, start, dur):
    """Mini pipeline across the top: the current step highlighted."""
    bw, bh, y = 300, 64, H - 100
    xs = [W / 2 + (i - 2) * 340 for i in range(len(STEPS))]
    for i, (name, what, meta) in enumerate(STEPS):
        on = i == active
        rect(start, dur, int(xs[i] - bw / 2), y, bw, bh, ACCENT if on else PLATE, ch=4, alpha=0.95 if on else 0.8)
        text(name, start, dur, size=32 if on else 28, x=xs[i] / W, y=(y + bh / 2) / H, color=INK if on else MUTED)


def side_by_side(paths, out):
    """Crop the centre of each 1920x1080 still and place them in one frame, with thin dividers."""
    cols = []
    cw = W // len(paths)
    for p in paths:
        im = bpy.data.images.load(p)
        px = np.array(im.pixels[:], np.float32).reshape(H, W, 4)
        x0 = (W - cw) // 2
        cols.append(px[:, x0:x0 + cw])
        bpy.data.images.remove(im)
    canvas = np.concatenate(cols, axis=1)
    if canvas.shape[1] < W:
        pad = np.zeros((H, W - canvas.shape[1], 4), np.float32)
        pad[..., 3] = 1
        canvas = np.concatenate([canvas, pad], axis=1)
    for k in range(1, len(paths)):
        canvas[:, k * cw - 1:k * cw + 1, :3] = 0.25
    img = bpy.data.images.new("sbs", W, H)
    img.pixels.foreach_set(canvas.ravel())
    img.filepath_raw = out
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)
    return out


def ui_clip(folder, start, dur):
    """Screen capture from capture_ui.py: real frames, the waiting parts sped up to fit dur."""
    meta = json.load(open(os.path.join(folder, "times.json")))["frames"]
    share = {"before": 0.12, "running": 0.38, "after": 0.32, "compare_src": 0.09, "compare_out": 0.09}
    by_phase = {}
    for fr in meta:
        by_phase.setdefault(fr["phase"], []).append(fr["i"])
    f = start
    for ph in ("before", "running", "after", "compare_src", "compare_out"):
        ids = by_phase.get(ph, [])
        if not ids:
            continue
        n_frames = max(1, int(round(share[ph] * dur)))
        for k in range(len(ids)):
            a = f + int(round(k * n_frames / len(ids)))
            b = f + int(round((k + 1) * n_frames / len(ids)))
            if b > a:
                image(os.path.join(folder, "%04d.png" % ids[k]), a, b - a)
        f += n_frames


def latest_capture(name):
    root = os.path.join(ROOT, "out", "ui_capture")
    if not os.path.isdir(root):
        return None
    takes = sorted(d for d in os.listdir(root) if d.startswith(name + "_")
                   and os.path.isfile(os.path.join(root, d, "times.json")))
    return os.path.join(root, takes[-1]) if takes else None


def bar_chart(rows, start, dur, title, subtitle, ref=None, note=None):
    """Horizontal bars from zero, the highlighted row in the accent, values as direct labels.

    rows: [(label, value_pct, highlight)]. ref: (value, label) drawn as a thin vertical line.
    Bars grow in over the first second.
    """
    x0, x1, vmax = 520, 1700, 50.0
    px = lambda v: x0 + (x1 - x0) * v / vmax  # noqa: E731
    text(title, start, dur, size=60, x=120 / W, y=(H - 130) / H, align="LEFT")
    text(subtitle, start, dur, size=36, x=120 / W, y=(H - 200) / H, color=MUTED, align="LEFT")
    bh, gap, ytop = 92, 48, H - 330
    grow = sec(1.0)
    for i, (label, v, hi) in enumerate(rows):
        y = ytop - i * (bh + gap) - bh
        text(label, start, dur, size=44, x=(x0 - 30) / W, y=(y + bh / 2) / H,
             color=INK if hi else MUTED, align="RIGHT")
        s = rect(start, dur, x0, y, px(v) - x0, bh, ACCENT if hi else NEUTRAL, ch=5)
        tr = s.transform  # grow from the baseline: animate width and centre together
        full_w = px(v) - x0
        for f, w in ((start, 2), (start + grow, full_w)):
            tr.scale_x = w / W
            tr.offset_x = x0 + w / 2 - W / 2
            tr.keyframe_insert("scale_x", frame=f)
            tr.keyframe_insert("offset_x", frame=f)
        text("%.1f%%" % v, start + grow, dur - grow, size=44, x=(px(v) + 22) / W, y=(y + bh / 2) / H,
             color=INK, align="LEFT")
    if ref is not None:
        rv, rl = ref
        ybot = ytop - len(rows) * (bh + gap) + gap - 20
        rect(start, dur, int(px(rv)) - 1, ybot, 3, ytop - ybot + 30, MUTED[:3], ch=6, alpha=0.9)
        text(rl, start, dur, size=30, x=px(rv) / W, y=(ytop + 55) / H, color=MUTED)
    if note:
        text(note, start, dur, size=38, x=120 / W, y=150 / H, color=INK, align="LEFT")


# plain, neutral captions (Spark's own descriptions mention gender; the video doesn't need that)
NEUTRAL_NAMES = {"lee_scan": ("3D head scan", "em"), "suzanne": ("Cartoon monkey, no template", "em"),
                 "hand_realistic": ("Hand: rings around each finger", "all"), "head_sculpt": ("Sculpted head", "em"),
                 "head_generic": ("Low-poly head", "em"), "head_realistic": ("Realistic head", "em"),
                 "dragon": ("AI-generated dragon, no template", "all")}

D = lambda name, f: os.path.join(DEMO, name, f)  # noqa: E731


def em(metrics):
    """Eyes + mouth aligned % (edge-weighted), the number the video talks about."""
    if not metrics:
        return None
    regs = [r for r in metrics.get("regions", []) if r.get("aligned_pct") is not None
            and r["name"].split("_")[0] in ("eyes", "eye", "mouth")]
    n = sum(r["edges"] for r in regs)
    return round(sum(r["aligned_pct"] * r["edges"] for r in regs) / n, 1) if n else metrics.get("aligned_pct")


m = facts["metrics"]
ft, qf, qr, ref = em(m), em(facts.get("baseline_quadriflow")), em(facts.get("baseline_qremeshify")), em(facts.get("reference"))
avg = None
eval_md = os.path.join(ROOT, "eval", "EVAL.md")
if os.path.isfile(eval_md):
    mm = re.search(r"Eyes and mouth, average over (\d+) meshes: FlowTopo ([\d.]+)%, QRemeshify ([\d.]+)%, QuadriFlow ([\d.]+)%",
                   open(eval_md, encoding="utf-8").read())
    if mm:
        avg = {"n": int(mm.group(1)), "ft": float(mm.group(2)), "qr": float(mm.group(3)), "qf": float(mm.group(4))}

# 1. title over the result turning
dur = sec(7)
frames(D(HERO, "tt_result"), t, dur, dim=0.35)
text("META GLOBAL AI DEVELOPER HACKATHON  ·  26/10 – 4/11", t, dur, size=30, y=0.92, color=MUTED)
text("Built from scratch during the hackathon. Uses open-source QuadWild for quad meshing.", t, dur, size=30,
     y=0.08, color=MUTED)
text("FlowTopo", t, dur, size=170, y=0.56, shadow=True)
text("Animation-ready retopology for Blender", t, dur, size=50, y=0.43, color=INK)
text("Muse Spark plans the edge loops  ·  SAM 3.1 finds the features", t, dur, size=36, y=0.36, color=MUTED)
t += dur

# 2. the problem
dur = sec(7)
frames(D(HERO, "tt_input"), t, dur)
caption("Scans and AI-generated meshes: triangle soup", t, dur)
t += dur

# 3. plain remeshers
p = D(HERO, "05_quadriflow_face.png")
if os.path.isfile(p):
    dur = sec(7)
    image(p, t, dur)
    caption("Auto-remeshers: an even grid, no loops where the face moves", t, dur)
    t += dur

# 3b. live in Blender
cap = latest_capture(HERO)
if cap:
    dur = sec(16)
    ui_clip(cap, t, dur)
    caption("Live in Blender: one click  (waiting sped up)", t, sec(5))
    t += dur

# 3b2. what it saves (manual time: forum reports of ~3-4 h for a simple head, days for production heads)
dur = sec(6)
text("Retopology of one head", t, dur, size=40, y=0.80, color=MUTED)
text("By hand", t, dur, size=40, x=0.30, y=0.62, color=MUTED)
text("hours", t, dur, size=130, x=0.30, y=0.48)
text("FlowTopo", t, dur, size=40, x=0.70, y=0.62, color=(*ACCENT, 1))
text("~1 minute", t, dur, size=130, x=0.70, y=0.48)
text("under $0.10 of API calls", t, dur, size=36, x=0.70, y=0.36, color=MUTED)
rect(t, dur, W // 2 - 1, 330, 3, 420, MUTED[:3], ch=4, alpha=0.5)
t += dur

# 3c. how it works: the whole flow once
dur = sec(8)
pipeline(t, dur, stagger=sec(0.4))
t += dur

# 4. Spark's plan
dur = sec(9)
im = image(D(HERO, "01_input_full.png"), t, dur)
im.transform.offset_x = 300  # make room for the plan panel on the left
step_bar(0, t, dur)
plan = facts.get("plan") or {}
rect(t, dur, 60, 230, 700, 600, PLATE, ch=4, alpha=0.85)
text("Spark's plan for this mesh", t, dur, size=44, x=100 / W, y=770 / H, align="LEFT")
y = 680
for f in (plan.get("features") or [])[:4]:
    rect(t, dur, 100, y - 34, 14, 68, ACCENT, ch=5)
    text("%s  ·  %d rings" % (f["name"], f["rings_out"]), t, dur, size=42, x=140 / W, y=y / H, align="LEFT")
    y -= 95
why = (plan.get("features") or [{}])[0].get("why", "")
if why:
    text('"%s"' % why, t, dur, size=30, x=100 / W, y=(y - 10) / H, color=MUTED, align="LEFT", anchor_y="TOP", wrap=0.33)
t += dur

# 5. SAM masks
dur = sec(7)
image(D(HERO, "02_sam_masks.png"), t, dur)
step_bar(1, t, dur)
caption("SAM 3.1 on untextured grey renders: masks lifted into 3D", t, dur)
t += dur

# 6. guides
dur = sec(7)
image(D(HERO, "03_guides_face.png"), t, dur)
step_bar(2, t, dur)
caption("Concentric rings that stop where two features meet", t, dur)
t += dur

# 7. result turntable
dur = sec(10)
frames(D(HERO, "tt_result"), t, dur)
step_bar(3, t, dur)
caption("Edge loops around the eyes and the mouth", t, dur)
t += dur

# 8. side by side
dur = sec(9)
paths = [D(HERO, "05_quadriflow_face.png"), D(HERO, "04_result_face.png")]
labels = ["QuadriFlow", "FlowTopo"]
if os.path.isfile(D(HERO, "06_reference_face.png")):
    paths.append(D(HERO, "06_reference_face.png"))
    labels.append("Artist, by hand")
image(side_by_side(paths, os.path.join(DEMO, HERO, "07_side_by_side.png")), t, dur)
for i, lab in enumerate(labels):
    chip(lab, t, dur, cx=(i + 0.5) * W / len(labels), accent=(lab == "FlowTopo"))
t += dur

# 8b. the Alt+click test on the hero head
if os.path.isfile(D(HERO, "08_loops_flowtopo.png")) and os.path.isfile(D(HERO, "08_loops_quadriflow.png")):
    dur = sec(9)
    image(side_by_side([D(HERO, "08_loops_quadriflow.png"), D(HERO, "08_loops_flowtopo.png")],
                       os.path.join(DEMO, HERO, "08_loops_side_by_side.png")), t, dur)
    chip("QuadriFlow: the loop runs off", t, dur, cx=W / 4)
    chip("FlowTopo: the loop closes", t, dur, cx=3 * W / 4, accent=True)
    caption("The Alt+click test: select the edge loop next to the mouth", t, dur)
    t += dur

# 9. numbers
dur = sec(10)
if avg:
    rows = [("QuadriFlow", avg["qf"], False), ("QRemeshify", avg["qr"], False), ("FlowTopo", avg["ft"], True)]
    sub = "Share of edges that follow the eyes and mouth  ·  average over %d meshes" % avg["n"]
else:
    rows = [("QuadriFlow", qf, False), ("QRemeshify", qr, False), ("FlowTopo", ft, True)]
    sub = "Share of edges that follow the eyes and mouth"
note = ("Realistic head: FlowTopo %.1f%%, the artist's own mesh %.1f%%" % (ft, ref)) if ref is not None else None
bar_chart(rows, t, dur, "Edge flow around the eyes and mouth", sub, ref=(33.3, "random grid 33%"), note=note)
t += dur

# 10. Spark's review loop, on the mesh where it made a measurable difference
REVIEW = "lee_scan"
pf = D(REVIEW, "pass2_facts.json")
if os.path.isfile(pf) and os.path.isfile(D(REVIEW, "09_pass2_guides.png")):
    p2 = json.load(open(pf))
    reruns = kept = 0
    for d in os.listdir(os.path.join(ROOT, "out", "runs")):
        rp = os.path.join(ROOT, "out", "runs", d, "result.json")
        if d.endswith("_eval3") and os.path.isfile(rp):
            rj = json.load(open(rp))
            if (rj.get("critique") or {}).get("rerun"):
                reruns += 1
                kept += bool(rj.get("pass2_kept"))
    dur = sec(10)
    image(side_by_side([D(REVIEW, "09_pass1_guides.png"), D(REVIEW, "09_pass2_guides.png")],
                       os.path.join(DEMO, REVIEW, "09_pass_side_by_side.png")), t, dur)
    step_bar(4, t, dur)
    chip("First plan", t, dur, cx=W / 4, y=H - 190)
    chip("After Spark's review", t, dur, cx=3 * W / 4, y=H - 190, accent=True)
    before = {n: r for n, r, _s in p2["plan_pass1"]}
    changes = ["%s %d→%d rings" % (n, before[n], r) for n, r, _s in p2["plan_pass2"] if n in before and r != before[n]]
    rect(t, dur, 60, 50, W - 120, 190, PLATE, ch=4, alpha=0.88)
    text("%s/10" % p2["score"], t, dur, size=80, x=100 / W, y=145 / H, color=(*ACCENT, 1), align="LEFT")
    quote = p2["problems"].split(". ")[1] if ". " in p2["problems"] else p2["problems"]
    quote = quote.split(", mouth")[0].replace("/fragmented", "") if len(quote) > 95 else quote
    text('Spark: "%s"' % quote, t, dur, size=32, x=330 / W, y=185 / H, align="LEFT", wrap=0.78)
    text("New plan: %s.  Kept: eyes and mouth %.1f%% → %.1f%%.  Proposals that don't help are rejected (%d of %d in the eval)." % (
        ", ".join(changes) or "adjusted spacing", p2["pass1"], p2["pass2"], reruns - kept, reruns), t, dur, size=30, x=330 / W, y=105 / H,
        color=MUTED, align="LEFT", wrap=0.78)
    t += dur

# 11. other meshes
for name in EXTRAS:
    p = D(name, "04_result_3q_close.png")
    if not os.path.isfile(p):
        p = D(name, "04_result_3q.png")
    if not os.path.isfile(p):
        continue
    dur = sec(4)
    image(p, t, dur)
    f2 = json.load(open(D(name, "facts.json")))
    label, kind = NEUTRAL_NAMES.get(name, (name, "em"))
    if kind == "em":
        caption("%s  ·  eyes and mouth %s%%  (QuadriFlow %s%%)" % (
            label, em(f2["metrics"]), em(f2.get("baseline_quadriflow"))), t, dur)
    else:
        caption("%s  ·  %s%% aligned  (QuadriFlow %s%%)" % (
            label, f2["metrics"].get("aligned_pct"), (f2.get("baseline_quadriflow") or {}).get("aligned_pct")), t, dur)
    t += dur

# 12. close
dur = sec(8)
frames(D(HERO, "tt_result"), t, dur, dim=0.35)
text("FlowTopo", t, dur, size=150, y=0.58, shadow=True)
text("Open-source Blender add-on  ·  under $0.10 of API calls per head", t, dur, size=42, y=0.45)
text("For character artists, studios, and AI-3D platforms whose meshes need to move.", t, dur, size=34, y=0.38,
     color=MUTED)
credits = ["Test meshes: Blender Studio Human Base Meshes (CC0)"]
if "lee_scan" in EXTRAS:
    credits.append('"Infinite, 3D Head Scan" by Lee Perry-Smith (CC BY 3.0)')
if "dragon" in EXTRAS:
    credits.append("Dragon head generated with Sloyd (CC BY 4.0)")
text("  ·  ".join(credits), t, dur, size=24, y=0.06, color=MUTED, wrap=0.95)
t += dur

scene.frame_start, scene.frame_end = 0, t - 1
bg = new_effect("bg", "COLOR", 1, 0, t)  # dark surface under everything
bg.color = SURFACE
scene.render.filepath = os.path.join(OUTDIR, "flowtopo_demo.mp4")
ims = scene.render.image_settings
try:
    if hasattr(ims, "media_type"):
        ims.media_type = "VIDEO"
    ims.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
except Exception as e:  # noqa: BLE001
    print("FFMPEG setup issue:", e)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUTDIR, "flowtopo_demo_edit.blend"))
print("TIMELINE %.1fs" % (t / FPS))
if "--no-render" not in sys.argv:
    bpy.ops.render.render(animation=True)
    print("VIDEO", scene.render.filepath)
