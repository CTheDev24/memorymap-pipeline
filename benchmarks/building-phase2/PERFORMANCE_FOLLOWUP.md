# Frozen Peachtree performance follow-up

The paired, fully profiled frozen-input replay improved from **912.87 s (15m13s)
to 675.01 s (11m15s)**: **237.86 seconds / 26.06% less time**. This is a full
generation through the normal 3MF export, support/printability gate and audit.

| Stage | Before (s) | After (s) |
|---|---:|---:|
| Full generation, cProfile enabled | 912.87 | 675.01 |
| Building source loading | 160.44 | 57.18 |
| Grouping, including its barrier union | 219.56 | 150.78 |
| Width diagnostics | 66.16 | 58.78 |
| Export, audit and sidecar | 198.15 | 132.42 |

One pair on a shared Windows workstation is not a throughput guarantee. cProfile
adds disproportionate overhead to Python loops; do not present these as unprofiled
production latency or compare them directly with the historical 1,091.55 s run.
Short regression checks and a small mask benchmark overlapped parts of the baseline;
the after run had no concurrent tests or heavy benchmark. Unchanged simplification
and extrusion were somewhat slower in the after run, illustrating timing variation.
The modest width gain is not an interactive-preview performance claim.

This review contains performance changes only. Architectural-relief defaults,
height controls, grouping rules, filter behavior and clearance thresholds are
unchanged. Work remains on `codex/building-performance-fix`; no merge or publish.

## Measured baseline

A new full replay with cProfile completed in **912.87 seconds**, using the same
frozen GPX/OSM sources, configuration and native scalar/list tag adapter as the
previous 1,091.55-second run. Every exported layer's ordered vertices/faces and all
non-runtime audit fields match that saved run exactly. The earlier wall time is
historical context, not the denominator for this comparison.

The profile found:

- Grouping: 219.56 seconds including barrier preparation for grouping;
  `group_footprints` itself took 215.62 seconds, with 125.21 seconds in `blocked`.
  Global-mask intersections dominate, not spatial-index rebuilding (5.64 seconds
  across all STRtree constructions in the entire generation).
- Width diagnostics: 66.16 seconds; scalar erosion searches account for 57.98
  seconds of the `measure_footprints` calls. These are diagnostic measurements,
  not a reason to omit validation or replace the metric with bounding boxes.
- Export cleanup: 64.00 seconds; final printability audit: 70.64 seconds.
  Together they repeat support graph construction twice (79.53 seconds total)
  and connected-component extraction 12 times (49.33 seconds total).
- 3MF serialization: 13.31 seconds; material rewrite: 21.43 seconds. JSON encoding
  from generation, largely the full audit sidecar, took 27.97 seconds. Serialization
  alone is not the dominant export cost. Timings are nested where indicated and
  must not all be added together.
- Building tag extraction: 133.60 seconds and 13.14 million pandas scalar lookups
  inside 160.44 seconds of source loading. cProfile magnifies Python-heavy costs.

After the changes, grouping's `blocked` calls fell from **125.21 to 30.27 s**.
Export executes support-graph construction **once instead of twice** (41.11 s),
and component extraction **six instead of twelve times** (24.44 s). Tag extraction
fell from **133.60 to 33.19 s**. These call-count reductions substantiate the causes
of the observed gains. Remaining costs include grouping mass construction, roads,
exact width erosion, the one required support analysis and sidecar JSON encoding.
No validation rules or height/filter controls were changed to obtain the results.

## Changes to review

1. `spatial_area.py` partitions the already-unioned, unchanged grouping obstacle
   mask with exact Boolean intersections. A spatial index limits each candidate's
   area check to intersecting pieces. Tile interiors are disjoint, and results
   close to the original 1e-10 mmÂ² decision threshold fall back to the original
   whole-mask calculation. Group growth, ordering, width, span and barrier policy
   remain unchanged. There is no rounded-coordinate or simplified-mask shortcut.
2. Width diagnostics process up to 128 footprints per vectorized batch, retaining
   each footprint's exact binary-search bounds, midpoint sequence, mitre settings,
   48-iteration limit and 0.0001 mm stopping tolerance. Exact WKB cache keys remain
   scoped to a generation. A failed batch falls back to the scalar diagnostic path.
3. One export-scoped support cache reuses components and geometric reachability
   between cleanup and audit. Complete vertex/face buffers and support settings
   key reuse; geometry edits, removals and tolerance changes invalidate it. Full
   topology, winding, volume and support validation still run under the same rules.
4. Building tags use one object-array read per row instead of per-column pandas
   indexing, preserving native values, missing-value handling and OSM index IDs.
5. The replay harness supports separate result directories, strict frozen-input
   reuse, cProfile output and implementation hashes. The comparison harness checks
   ordered exported geometry and full source/decision audits, excluding only model
   ZIP hashes and runtime telemetry; it separately validates each model/audit hash.

## Validation

- Full suite: **421 passed**, 76 dependency/deprecation warnings, in 26.93 seconds.
- All six stored manual-mode mesh/source-record fingerprints match exactly.
- Regression coverage includes tiled boundaries/holes/tiny intersections, exact
  group geometry and ordering, scalar/batched width equality and failure counts,
  mixed OSM tags, and support-cache invalidation after in-place geometry changes,
  cleanup and tolerance changes. Existing floating-cycle, arch/cavity, deeply
  embedded part, cleanup, manifold and route-overhang tests remain in force.
- Full replay completed: all six layers are watertight and consistently wound;
  production support/printability validation passed and progress was monotonic.
- **Exact match** for all six layers' ordered exported vertices/faces, all source
  records/decisions, and the complete audit after excluding runtime fields and
  separately verified model ZIP hashes. No building-face loss: 185,260 exported
  building faces; 3,891 groups containing 12,639 source footprints.
- Projected route overlap is unchanged at **1.6186989095877036e-13 mm²**, below
  the unchanged 1e-6 mm² export gate. Omissions, width statistics, unresolved cases
  and the three existing stadium fallbacks are unchanged.
- Ruff passes for new benchmark/test/helper files. Modified production files add
  no lint findings relative to published HEAD. `git diff --check` passes.

Compact profiles, environment versions, input hashes, full-output fingerprints
and stage timings are in [performance-followup-results.json](performance-followup-results.json).
The fresh model is [peachtree-fixed.3mf](../outputs/peachtree-profile-after/peachtree-fixed.3mf)
with its [matching audit](../outputs/peachtree-profile-after/peachtree-fixed.audit.json).

## Reproduce

Run from the repository root. Use a fresh output directory for each run:

```powershell
.venv/Scripts/python.exe benchmarks/building-phase2/run_peachtree_gpx.py benchmarks/outputs/peachtree-gpx-performance/route.gpx --snapshot benchmarks/outputs/peachtree-gpx-performance --output benchmarks/outputs/peachtree-profile-after --profile
.venv/Scripts/python.exe benchmarks/building-phase2/compare_peachtree_runs.py benchmarks/outputs/peachtree-profile-before benchmarks/outputs/peachtree-profile-after --output benchmarks/outputs/peachtree-followup-comparison.json
.venv/Scripts/python.exe -m pytest -q --basetemp=benchmarks/outputs/pytest-building-followup-review
```

The follow-up baseline is the inherited uncommitted performance work at the start
of this task, **not** the original slow Phase 2 commit. Its completed run is retained
in `benchmarks/outputs/peachtree-profile-before/`. Both runs retain inputs, progress,
profiles, model, audit and source hashes locally. Original saved artifacts remain
in `benchmarks/outputs/peachtree-gpx-performance/`. No new source downloads occurred. `dev` remains at
`71de412b4da99e61cf4da7ebcaa1feaf2a3dc1aa`. Changes remain uncommitted for review.

## Later work remains planned only

The separate [height-controls plan](BUILDING_HEIGHT_CONTROLS_PLAN.md) retains legacy
relief defaults and proposes opt-in proportional scaling, explicit multiplier and
final-mm limits, plus source/assembly overrides. The [filter plan](BUILDING_FILTER_PLAN.md)
classifies and resolves dependencies before extrusion, keeps simplification and
grouping independently optional, and uses one resolved group manifest for preview
and export. Neither feature is implemented by this change.

Performance measurements do not establish interactive slider latency. Implement
height/selection contracts and immutable group manifests before incremental caches
or UI controls. A future default change still requires DC with recovered source
settings, Peachtree, a full Houston example and a rural/hilly case. No slicing or
physical-print validation is claimed by this performance pass.
