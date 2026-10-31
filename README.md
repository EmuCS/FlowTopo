# FlowTopo

**Animation-ready retopology for Blender. Muse Spark plans the edge loops, SAM 3.1 finds the features, and a quad remesher builds topology that bends where faces move.**

AI 3D generators and scanners produce millions of meshes that are triangle soup. Automatic remeshers turn them into even quad grids, but animation needs *edge loops*: concentric rings around the eyes and mouth, and rings around limbs. Today a character artist draws those by hand. FlowTopo places them automatically.

![FlowTopo vs QuadriFlow vs the artist's own topology](docs/side_by_side.png)

## How it works

```
mesh ──► 10 grey renders ──► Muse Spark 1.3 (vision, structured output)
                              "realistic human head: eyes = concentric, 3 rings; mouth = concentric, 3 rings; ..."
                                     │
                                     ▼
                        SAM 3.1 per feature, per view ("eye", "mouth", "ear" ...)
                        (full bodies: SAM finds the "head" first, then face features in head close-ups)
                                     │  masks back-projected onto mesh faces
                                     ▼  (BVH visibility + multi-view voting + morphological clean-up)
                        semantic regions on the mesh
                                     │  distance field per region, geodesic Voronoi so rings never cross
                                     ▼
                        exact isoline cuts = guide rings (marked as seams)
                                     │
                                     ▼
                        QRemeshify / QuadWild: quads that follow the guides, then a relax pass
                                     │
                                     ▼
                        Muse Spark reviews the wireframe ──► adjusts rings/density ──► one more pass
```

- **Muse Spark 1.3** looks at the renders and decides *what* needs loops and *how many* (structured JSON output, with an artist's reason for each). After the remesh it reviews the wireframe, scores it, and proposes adjustments that FlowTopo applies.
- **SAM 3.1** (text-prompted segmentation) finds each feature on plain, untextured grey renders. FlowTopo projects the masks back onto the mesh with ray-cast visibility and votes across views. On a full body the face is a few pixels wide, so SAM first finds the head and FlowTopo renders close-ups of it for the face features.
- **FlowTopo** turns regions into guide rings by cutting the mesh exactly along distance-field isolines. A geodesic Voronoi partition stops rings of neighbouring features from crossing. The result reads like an artist's topology "mask".
- **QRemeshify** ([QuadWild](https://github.com/nicopietroni/quadwild) inside Blender) treats the rings as feature lines and builds quads along them.

## Results

The metric: within each feature's ring zone, the share of quad edges that run along a ring or along a spoke (within 15°). A random grid scores 33.3%; perfect concentric topology scores 100%. Full table, including bodies and a hand: [eval/EVAL.md](eval/EVAL.md).

Eyes and mouth (where a face deforms), with Muse Spark planning and reviewing every run:

| Mesh | FlowTopo | QRemeshify (no guides) | QuadriFlow | Artist's own topology |
|---|---|---|---|---|
| Blender Studio realistic head | **45.3%** | 40.5% | 38.1% | 41.7% |
| Blender Studio sculpt head | **43.0%** | 38.8% | 34.4% | 42.4% |
| Blender Studio low-poly head | **40.2%** | 32.1% | 33.0% | 52.7% |
| Lee Perry-Smith head scan | **36.9%** | 32.7% | 32.6% | - |
| Suzanne (creature, no template) | **45.9%** | 33.0% | 34.5% | - |
| Stylised male body | **39.1%** | 35.7% | 37.6% | 47.2% |
| Stylised female body | **40.8%** | 34.5% | 35.7% | 46.2% |
| Realistic female body (face found in head close-ups) | **44.6%** | 41.2% | 27.7% | 42.8% |
| **Average (8 meshes)** | **42.0%** | 36.1% | 34.2% | |

All three remeshed versions get the same clean-up (a relax pass that evens out the quads and snaps them back onto the surface), so the comparison is like for like.

- The plain remeshers sit a little above random level (33.3%). FlowTopo beats both on every mesh, 9 out of 9 including the hand and the bodies.
- On the 6 meshes that ship with an artist's hand-made topology, FlowTopo closes about **70% of the gap** between automatic remeshing (QuadriFlow) and the artist. On 3 of the 6 it scores above the artist.

Cost of Meta API calls: about **$0.05-0.10 for a head**, up to about $0.50 for a full body with many features (SAM $0.0025 per image, Spark about $0.01 per call). Every response is cached, so reruns are free.

## Install (Blender 4.2+)

1. Install **QRemeshify** (free, GPL): Edit > Preferences > Get Extensions, or from [github.com/ksami/QRemeshify](https://github.com/ksami/QRemeshify/releases).
2. Install FlowTopo: Edit > Preferences > Add-ons > Install from Disk > `dist/flowtopo-0.1.0.zip`.
3. In the FlowTopo preferences, paste your Meta Model API key (from [dev.meta.ai](https://dev.meta.ai)). A **spend cap** is built in (default $38).
4. Select a mesh, open the sidebar (N) > **FlowTopo** > **Retopologise**.

The panel shows live progress, Muse Spark's loop plan (with its reasons), Spark's review, the alignment score, and the API cost of the run. "Compare with original" toggles between input and result.

## Headless / reproduce the eval

```
blender --background --python scripts/prep_assets.py      # builds the eval meshes (needs the CC0 base-mesh bundle)
py scripts/eval_all.py --tag eval3                         # FlowTopo + baselines on 9 meshes -> eval/EVAL.md
blender --background out/runs/<mesh>_eval3/scene.blend --python scripts/make_demo.py -- <mesh> eval3
blender --background out/runs/<mesh>_eval3/scene.blend --python scripts/render_loops.py -- <mesh> eval3
blender --factory-startup --window-fullscreen --enable-event-simulate out/ui_capture/start.blend --python scripts/capture_ui.py -- <mesh>
blender --background --factory-startup --python scripts/build_video.py -- head_realistic lee_scan suzanne
```

`capture_ui.py` records the real add-on in the Blender UI (window screenshots during a run) for the demo video.

Set `MODEL_API_KEY` in the environment or in `.env` (never committed).

## Repository

| Path | What |
|---|---|
| `flowtopo/` | The Blender extension (also importable headless) |
| `flowtopo/api.py` | Meta Model API client: disk cache, cost log, hard budget |
| `flowtopo/sam.py`, `sam_codec.py` | SAM 3.1 calls and mask decoding (codec adapted from Meta's `meta-sam-parser`, SAM License) |
| `flowtopo/plan.py` | Muse Spark inventory and critique prompts and schemas |
| `flowtopo/core.py` | Back-projection, voting, regions, distance fields |
| `flowtopo/guides.py` | Exact isoline cutting, Voronoi-guarded rings |
| `flowtopo/metrics.py` | Edge-flow alignment, irregular vertices, fidelity |
| `flowtopo/loops.py` | The Alt+click test: closed edge loops around a feature |
| `scripts/` | Eval set prep, eval runner, demo renders, video assembly |

## Limitations (honest)

- Faces work best. Full bodies get limb rings marching up from hands and feet; joint-specific loops (elbow, knee) are next. On full bodies the eyes are only a few quads wide.
- **Few closed loops.** FlowTopo usually gets one closed edge loop around each eye and the mouth; plain remeshers get none, and artists place 10-16. The reason is quad size: QuadWild keeps quads the same size, so it inserts poles between rings of different radius to change the edge count, and every pole breaks a loop. We tried closed-only guides, smoother fields and QuadWild's regularity settings (31 Oct): none added loops. Per-region density (smaller quads near the feature) is the next step.
- The guide rings follow SAM's mask boundary, so a poor mask gives a poor ring. Multi-view voting and Spark's review catch most of these, not all.

## Credits and licences

- FlowTopo: GPL-3.0-or-later (Blender add-on licence).
- SAM mask codec adapted from `meta-sam-parser` 0.1.0, © Meta Platforms, under the SAM License (`flowtopo/LICENSE-SAM.txt`).
- QRemeshify by ksami (GPL-3.0), QuadWild by Pietroni et al.
- Test meshes: *Infinite, 3D Head Scan* by Lee Perry-Smith, CC BY 3.0 (via three.js examples). Blender Studio *Human Base Meshes* v1.4.1, CC0. *Dragonhead*, generated with [Sloyd](https://www.sloyd.ai/free-3d-models/model/dragonhead-5s39xjbw), CC BY 4.0 (an AI-generated mesh, used as generated). Suzanne is Blender's built-in test mesh.

Built during the Meta Global AI Developer Hackathon 2026 (26 Oct - 4 Nov). All code in this repository was written during the event.
