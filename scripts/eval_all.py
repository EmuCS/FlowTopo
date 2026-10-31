"""Run FlowTopo + baselines on every eval mesh (3 Blender processes at a time), then summarise.

py scripts/eval_all.py            # run + summarise
py scripts/eval_all.py --summary  # only rebuild eval/EVAL.md from existing results
[--tag X]  run folder suffix, default "eval"
"""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLENDER = r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
MESHES = ["head_realistic", "lee_scan", "suzanne", "head_sculpt", "head_generic",
          "body_male_stylized", "body_female_stylized", "body_female_realistic", "hand_realistic"]
TAG = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else "eval"


def run(name):
    log = os.path.join(ROOT, "out", "eval_%s.log" % name)
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        p = subprocess.run([BLENDER, "--background", "--python", os.path.join(ROOT, "scripts", "run_one.py"),
                            "--", name, "--baselines", "--tag", TAG], stdout=f, stderr=subprocess.STDOUT)
    return name, p.returncode


def eyes_mouth(metrics):
    """Edge-weighted aligned % over the deformation-critical regions (eyes, mouth) only."""
    if not metrics:
        return None
    regs = [r for r in metrics.get("regions", []) if r.get("aligned_pct") is not None
            and r["name"].split("_")[0] in ("eyes", "eye", "mouth")]
    n = sum(r["edges"] for r in regs)
    return round(sum(r["aligned_pct"] * r["edges"] for r in regs) / n, 1) if n else None


def loops_of(metrics, kinds=("eyes", "eye", "mouth")):
    """Total closed edge loops around the given features (None if not measured)."""
    if not metrics:
        return None
    vals = [r.get("closed_loops") for r in metrics.get("regions", []) if r["name"].split("_")[0] in kinds]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def closed_loop_table(ok):
    lines = ["", "## Closed edge loops around the eyes and mouth", "",
             "The Alt+click test: walk a quad edge loop near the feature. It counts if it closes on itself and winds once",
             "around the feature (flowtopo/loops.py). Totals over all eye and mouth regions of each mesh.",
             "**Experimental and noisy:** results depend on the feature centre and radius estimates (e.g. 0 on the",
             "low-poly artist head, 23 for both FlowTopo and QRemeshify on the scan's slit eyes). Not used for claims.", "",
             "| Mesh | FlowTopo | QRemeshify | QuadriFlow | Artist reference |", "|---|---|---|---|---|"]
    tot = {"ft": 0, "qr": 0, "qf": 0}
    for n, r in ok:
        a = loops_of(r["metrics"])
        if a is None:
            continue
        b, c, d = loops_of(r.get("baseline_qremeshify")), loops_of(r.get("baseline_quadriflow")), loops_of(r.get("reference"))
        tot["ft"] += a
        tot["qr"] += b or 0
        tot["qf"] += c or 0
        lines.append("| %s | **%s** | %s | %s | %s |" % (n, a, b, c, d if d is not None else "-"))
    lines += ["", "Total: FlowTopo %d, QRemeshify %d, QuadriFlow %d. Artists place many more (see Limitations in README)." % (
        tot["ft"], tot["qr"], tot["qf"])]
    return lines


def summarise():
    rows = []
    for name in MESHES:
        path = os.path.join(ROOT, "out", "runs", "%s_%s" % (name, TAG), "result.json")
        if not os.path.isfile(path):
            rows.append((name, None))
            continue
        rows.append((name, json.load(open(path))))
    lines = [
        "# FlowTopo eval",
        "",
        "Metric: share of quad edges, within each feature's ring zone, that run along a ring or along a spoke",
        "(within 15 deg). A random grid scores 33.3%. Higher = edge loops follow the eyes/mouth/etc.",
        "",
        "| Mesh | Spark's read | Features found | FlowTopo | QRemeshify (no guides) | QuadriFlow | Artist reference | Quads | Spark review | Cost |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, r in rows:
        if r is None:
            lines.append("| %s | (failed or not run) | | | | | | | | |" % name)
            continue
        m = r.get("metrics", {})
        det = ", ".join("%s x%d" % (d["feature"], d["regions"]) for d in r.get("detections", []) if d["regions"])
        g = lambda k: (r.get(k) or {}).get("aligned_pct")  # noqa: E731
        lines.append("| %s | %s | %s | **%s%%** | %s%% | %s%% | %s | %s | %s | $%s |" % (
            name, r.get("plan", {}).get("subject", ""), det or "-", m.get("aligned_pct"),
            g("baseline_qremeshify"), g("baseline_quadriflow"),
            ("%s%%" % g("reference")) if g("reference") is not None else "-",
            m.get("faces"), (r.get("critique") or {}).get("score", "-"), r.get("spent_usd")))
    ok = [(n, r) for n, r in rows if r and r.get("metrics", {}).get("aligned_pct") is not None]
    if ok:
        avg = lambda xs: round(sum(xs) / len(xs), 1)  # noqa: E731
        ft = avg([r["metrics"]["aligned_pct"] for _, r in ok])
        qr = avg([r["baseline_qremeshify"]["aligned_pct"] for _, r in ok if r.get("baseline_qremeshify", {}).get("aligned_pct") is not None])
        qf = avg([r["baseline_quadriflow"]["aligned_pct"] for _, r in ok if r.get("baseline_quadriflow", {}).get("aligned_pct") is not None])
        lines += ["", "**Average over %d meshes: FlowTopo %s%%, QRemeshify %s%%, QuadriFlow %s%% (random 33.3%%).**" % (len(ok), ft, qr, qf)]
        # eyes + mouth only: where a face actually deforms
        lines += ["", "## Eyes and mouth only (the deformation-critical loops)", "",
                  "| Mesh | FlowTopo | QRemeshify | QuadriFlow | Artist reference |", "|---|---|---|---|---|"]
        em = {"ft": [], "qr": [], "qf": []}
        for n, r in ok:
            a, b, c = eyes_mouth(r["metrics"]), eyes_mouth(r.get("baseline_qremeshify")), eyes_mouth(r.get("baseline_quadriflow"))
            d = eyes_mouth(r.get("reference"))
            if a is None:
                continue
            em["ft"].append(a)
            if b is not None:
                em["qr"].append(b)
            if c is not None:
                em["qf"].append(c)
            lines.append("| %s | **%s%%** | %s%% | %s%% | %s |" % (n, a, b, c, ("%s%%" % d) if d is not None else "-"))
        if em["ft"]:
            lines += ["", "**Eyes and mouth, average over %d meshes: FlowTopo %s%%, QRemeshify %s%%, QuadriFlow %s%% (random 33.3%%).**" % (
                len(em["ft"]), avg(em["ft"]), avg(em["qr"]) if em["qr"] else "-", avg(em["qf"]) if em["qf"] else "-")]
        lines += closed_loop_table(ok)
    os.makedirs(os.path.join(ROOT, "eval"), exist_ok=True)
    with open(os.path.join(ROOT, "eval", "EVAL.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    if "--summary" not in sys.argv:
        with ThreadPoolExecutor(3) as ex:
            for name, code in ex.map(run, MESHES):
                print("DONE", name, "exit", code, flush=True)
    summarise()
