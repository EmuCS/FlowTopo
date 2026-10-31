# FlowTopo eval

Metric: share of quad edges, within each feature's ring zone, that run along a ring or along a spoke
(within 15 deg). A random grid scores 33.3%. Higher = edge loops follow the eyes/mouth/etc.

| Mesh | Spark's read | Features found | FlowTopo | QRemeshify (no guides) | QuadriFlow | Artist reference | Quads | Spark review | Cost |
|---|---|---|---|---|---|---|---|---|---|
| head_realistic | bald human head bust | eyes x2, mouth x1, nose x1, ears x2 | **41.6%** | 40.1% | 37.6% | 39.0% | 10760 | 7 | $0.0202 |
| lee_scan | bald male head and neck bust | eyes x2, mouth x1, nose x1, ears x2 | **37.4%** | 34.9% | 35.9% | - | 10958 | 5 | $0.0202 |
| suzanne | stylized monkey head | eyes x2, mouth x1, ears x2, nose x1 | **45.7%** | 36.0% | 37.6% | - | 8240 | 7 | $0.0202 |
| head_sculpt | bald human head and neck bust | eyes x2, mouth x1, nose x1, ears x2 | **44.4%** | 39.5% | 35.8% | 40.1% | 10542 | 7 | $0.0154 |
| head_generic | low-poly neutral human head | eyes x2, mouth x1, nose x1, ears x2 | **39.8%** | 32.3% | 32.8% | 43.1% | 12574 | 6 | $0.0154 |
| body_male_stylized | full male human body | eyes x2, mouth x1, nose x1, ears x2, hands x2, feet x2 | **40.0%** | 37.5% | 39.1% | 40.2% | 28124 | 6 | $0.0438 |
| body_female_stylized | stylized young girl full body | eyes x4, mouth x1, hands x2, feet x2, nose x1, ears x2 | **39.8%** | 38.0% | 37.0% | 39.3% | 27007 | 7 | $0.036 |
| body_female_realistic | female human full body | eyes x4, mouth x1, nose x1, ears x2, hands x2, feet x2 | **36.0%** | 35.7% | 35.8% | 36.3% | 25010 | 5 | $0.0284 |
| hand_realistic | human hand with wrist | fingers x4 | **39.5%** | 36.1% | 32.1% | 40.2% | 11457 | 4 | $0.0075 |

**Average over 9 meshes: FlowTopo 40.5%, QRemeshify 36.7%, QuadriFlow 36.0% (random 33.3%).**

## Eyes and mouth only (the deformation-critical loops)

| Mesh | FlowTopo | QRemeshify | QuadriFlow | Artist reference |
|---|---|---|---|---|
| head_realistic | **45.3%** | 40.5% | 38.1% | 41.7% |
| lee_scan | **36.9%** | 32.7% | 32.6% | - |
| suzanne | **45.9%** | 33.0% | 34.5% | - |
| head_sculpt | **43.0%** | 38.8% | 34.4% | 42.4% |
| head_generic | **40.2%** | 32.1% | 33.0% | 52.7% |
| body_male_stylized | **39.1%** | 35.7% | 37.6% | 47.2% |
| body_female_stylized | **40.8%** | 34.5% | 35.7% | 46.2% |
| body_female_realistic | **44.6%** | 41.2% | 27.7% | 42.8% |

**Eyes and mouth, average over 8 meshes: FlowTopo 42.0%, QRemeshify 36.1%, QuadriFlow 34.2% (random 33.3%).**

## Closed edge loops around the eyes and mouth

The Alt+click test: walk a quad edge loop near the feature. It counts if it closes on itself and winds once
around the feature (flowtopo/loops.py). Totals over all eye and mouth regions of each mesh.
**Experimental and noisy:** results depend on the feature centre and radius estimates (e.g. 0 on the
low-poly artist head, 23 for both FlowTopo and QRemeshify on the scan's slit eyes). Not used for claims.

| Mesh | FlowTopo | QRemeshify | QuadriFlow | Artist reference |
|---|---|---|---|---|
| head_realistic | **4** | 0 | 0 | 43 |
| lee_scan | **37** | 35 | 0 | - |
| suzanne | **1** | 2 | 0 | - |
| head_sculpt | **3** | 0 | 0 | 45 |
| head_generic | **0** | 0 | 0 | 0 |
| body_male_stylized | **0** | 0 | 0 | 10 |
| body_female_stylized | **0** | 0 | 0 | 56 |
| body_female_realistic | **2** | 0 | 0 | 13 |

Total: FlowTopo 47, QRemeshify 37, QuadriFlow 0. Artists place many more (see Limitations in README).
