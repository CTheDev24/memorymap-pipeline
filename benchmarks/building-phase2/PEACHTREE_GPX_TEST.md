# Peachtree GPX full-generation test

**Completed successfully on the local performance-fix branch, 2026-09-24.**
Generation took **18m 12s (1,091.55 s)** using frozen real OSM road, water and
building inputs. Download/query preparation took an additional **3m 22s (202.23 s)**.
This is a complete generation through the normal 3MF export and printability gate,
not the earlier exported-shell proxy. No full pre-fix generation was completed, so
these results do not establish a full-model speedup ratio.

## Artifacts

- [Generated 3MF](../outputs/peachtree-gpx-performance/peachtree-fixed.3mf) — approximately 25 MB.
- [Matching source/decision audit](../outputs/peachtree-gpx-performance/peachtree-fixed.audit.json) — approximately 89 MB.
- [Compact measurements and validation](peachtree-gpx-results.json).
- Frozen inputs, progress log and detailed checks: `benchmarks/outputs/peachtree-gpx-performance/`.

## Inputs and settings

The supplied GPX contains 507 points and approximately 9.978 km of projected route.
Its generated route footprint matches the previously supplied saved 3MF exactly:
symmetric-difference area and Hausdorff distance are both zero.

The frame is 190 × 240 mm, borderless, fitted with 5 mm route-edge clearance
(5.6 mm centerline padding). The run used Urban mode, flat terrain, both route
markers, 1.2 mm route width, automatic 2 mm route height, a 26 mm maximum building
height setting, 0.42 mm line width, print optimization and grouping. Water remained
hidden but was included as a grouping barrier. These settings follow the archived
Peachtree note; not every setting of the stalled build is independently verified.

Frozen queries contain 118,220 road edges, 476 water features and 43,628 building
features over an area larger than the print frame. After clipping/part handling,
24,433 building footprints entered optimization. Source counts are not output mesh
counts. Raw source tags, acquisition times and file hashes are preserved.

The benchmark replays frozen GeoJSON through a native-property decoder so OSM list
tags retain their original types. GDAL's mixed scalar/list coercion prevented the
first local-file replay; that attempt was stopped before meaningful building work.
The successful timed run uses the normal geometry pipeline with the corrected
benchmark input adapter. Production geometry code was not changed for this test.

## Timings

| Stage | Seconds |
| --- | ---: |
| Roads | 159.10 |
| Road/water/route barrier preparation | 5.81 |
| Building source loading | 86.30 |
| Footprint preparation and classification | 55.72 |
| Building optimization, total | 421.18 |
| — simplification | 76.59 |
| — grouping | 311.61 |
| — final printability decisions | 32.37 |
| Support handling | 2.62 |
| Building extrusion | 28.52 |
| Width diagnostics | 106.68 |
| Building mesh assembly | 4.86 |
| All-triangle route-clearance check | 1.80 |
| Export, printability audit and sidecar | 218.55 |

Optimization sub-stages are included in its total. Small unlisted orchestration
costs account for the remaining difference. Progress reached 100% monotonically;
the former 55% block now reports footprint, grouping, extrusion and diagnostic work.

Grouping remains the largest building cost. Width diagnostics and the existing
export/audit work are also material. This test establishes completion and exposes
remaining costs; it does not claim generation is instantaneous or that the proxy's
21× speedup applies to this entire model.

## Geometry and policy checks

- All six generated layers are watertight and consistently wound.
- Export passed the existing support/printability gate.
- All-triangle route overlap is **1.6187e-13 mm²**, below the unchanged 1e-6 mm² gate.
- The 3MF hash matches its audit. Export retained all **185,260 building faces**;
  there was no building-face loss between the generated mesh and exported object.
- **3,891 groups** contain **12,639 source footprints**. There are **5,408 measured
  output footprints**, including groups and individual buildings.
- Optimization omitted 10,277 footprints: 10,141 below the physical print target
  and 136 consumed by route exclusion. The larger audit omission total also includes
  source records outside the frame, rejected by frame clipping, or replaced by parts.
- Of 864 protected in-frame sources, 66 were consumed by the existing route-exclusion
  rule. No protected source was omitted for width/area. Protected omissions therefore
  must not be described as all protected buildings being retained unchanged.
- The 38 source records with unresolved geometry have reason
  `unsupported_or_null_geometry`; these are distinct from physical printability flags.
- Three stadium enhancements used the existing standard-building-mass fallback.
- 1,095 optimization decisions remain flagged for printability review. Of the final
  measured footprints, 897 (16.59%) remain below the robust 0.84 mm core-width target.
  Protected/tall geometry is intentionally retained under the current policy.

No slicer or physical-print validation was performed. Watertightness and a local
core-width screen do not certify minimum walls, fine details or print quality.

## Reproduce

From the repository root, reuse the frozen snapshot without downloading again:

```powershell
.venv/Scripts/python.exe benchmarks/building-phase2/run_peachtree_gpx.py benchmarks/outputs/peachtree-gpx-performance/route.gpx
```

The harness verifies input hashes, checks grouping remains enabled, records stage
progress and exports a fresh matching model/audit. Source acquisition is reused
only when frozen files match their saved hashes. The filter redesign remains
planned only; `dev` was not changed and no merge or publication was performed.

## Additional frozen-input performance pass

A paired cProfile replay reduced full generation from 912.87 to 675.01 seconds
(26.06%), with exact exported geometry, source decisions and non-runtime audit
fields preserved. Grouping, support-analysis reuse, width batching and tag loading
are measured in [PERFORMANCE_FOLLOWUP.md](PERFORMANCE_FOLLOWUP.md). These profiled
measurements are separate from the historical unprofiled timings above.
