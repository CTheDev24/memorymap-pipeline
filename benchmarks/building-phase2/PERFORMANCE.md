# Building performance regression

The benchmark uses local Peachtree exported shells and the actual projected road
network from `source-86acd5d422d2.3mf`. Road reconstruction cancels shared edges of
upward-facing road triangles, builds the resulting polygonal area, and checks its
area against the summed upward triangle area. Preparation and cached WKB loading
are outside the measured interval. Grouping is enabled.

Run from the repository root:

```powershell
.venv/Scripts/python.exe benchmarks/building-phase2/profile_performance.py --label sample --limit 60
.venv/Scripts/python.exe benchmarks/building-phase2/profile_performance.py --label full
```

`--limit` selects evenly spaced shells from the complete export; the entire road
mask is retained. JSON summaries and cumulative cProfile reports go to
`benchmarks/outputs/building-performance`. Geometry fingerprints include every
output footprint, group membership/geometry, decision and group height.

For a baseline, run the same harness and inputs against published commit
`f8866e169d49c05590b1fa813dd7253486e5374f`. Do not replace the roads with an empty
mask or turn off grouping when comparing timings.

## Limits

These are exported-shell policy timings, not a full Peachtree regeneration. The
original GPX/frame settings, OSM building IDs/tags/relationships and water data
are not established by the supplied exported meshes. Shell heights are observed;
above-base shells are protected as possible building parts. Source semantic class
remains unknown. Road barriers are real; the absence of reconstructed water is a
benchmark limitation, not a production rule permitting missing water barriers.
The sampled run reduces building-neighbor/grouping interactions. The full run
exercises all exported shells, but neither reconstructs original source semantics.

Download, terrain work, source parsing, extrusion, final export, slicing and
physical print quality are outside optimizer timing. The separate triangle check
uses the original exported building triangles, including known route collisions;
its positive overlap is expected and must remain detectable.
## Fix and measured evidence

The baseline profile confirms the regression: the completed 60-shell sample spent
410.83 of 414.68 seconds in `unary_union` (99.1%). The full road network was being
re-unioned for each ordinary footprint, even when no area was being added.

The fix prepares fixed obstacles once, spatially queries source neighbors only
when checking added area, intersects those obstacles with the small candidate,
and unions those local intersections. This preserves union-overlap area without
double-counting coincident road/route/neighbor masks. Grouping retains its original
barrier geometry and policy. Exact width results are reused by WKB within one
building generation, including group members and diagnostic measurements. Core
width definitions and omission/clearance thresholds are unchanged.

The final projected-triangle gate uses prepared intersection predicates and
bounded batches, followed by the original exact area-intersection test. It still
checks roofs/landmark overhangs and rejects overlap above 1e-6 mm². No footprint-only
substitute or sampling is used.

The 60-shell comparison is **414.68 s before / 19.44 s after (21.3×)**. Output WKB,
decisions and group data have the identical SHA-256 fingerprint
`9921edcaef4a26eeb4a5f78c43b07172f768b38fd9b867cc50575b28c8ea1ed5`.
This sparse sample creates no groups; it diagnoses fixed-barrier overhead, not
neighborhood grouping throughput. An earlier completed baseline profile took
262.83 s (260.28 s in unions), but its JSON summary failed on a NumPy integer;
the corrected harness was rerun for the comparison above. Timings on this shared
Windows workstation vary, and the baseline repeat overlapped test execution.
Treat the ratio as illustrative, not a controlled throughput guarantee.

Studio now reports loading, footprint preparation, optimization, grouping,
printability checks, extrusion, width diagnostics, assembly and final route
validation between 55% and 85%. Percentages indicate stage/work counts, not an ETA.
Stage seconds are logged and retained in `building_generalization.stage_seconds`
and `optimization_seconds`, including the optimized model's audit sidecar.

All six stored manual-mode mesh/source-record fingerprints match exactly. New
regression coverage checks local-overlap union semantics, the absence of per-source
global unions, generation-scoped width caching, monotonic progress and projected
roof/degenerate/boundary/hole cases. The export gate is tested against an injected
mesh crossing the route corridor.

The full post-fix exported-shell policy run completed in **223.41 s** for **5,651
shells**, producing **413 groups (1,015 member sources)** and 3,267 omissions.
Its stage split was 66.84 s simplification, 140.88 s grouping, and 15.68 s final
printability decisions. Grouping now dominates; core-width measurements accounted
for 11.77 s across 2,730 calls in the cumulative profile. These counts reflect
export-derived unknown/protected shells, not a prediction for fresh OSM inputs.
There is no completed full-size pre-fix timing, so no full-model speedup is claimed.

The filter architecture is documented separately in [BUILDING_FILTER_PLAN.md](BUILDING_FILTER_PLAN.md).
No filter redesign has been implemented in this performance change.

The all-triangle route check also improved: three-run median **8.03 s before /
2.50 s after (3.2×)** on 213,284 faces. Both methods report exactly
325.22157962381937 mm² summed overlap on the original, known-conflicting export;
the faster check still detects those collisions. Reproduce this comparison with:

```powershell
.venv/Scripts/python.exe benchmarks/building-phase2/profile_route_check.py
```

Compact machine-readable measurements, environment versions, fingerprints and
profile hotspots are retained in [performance-results.json](performance-results.json).
Raw `.prof` files and cumulative reports are local under the benchmark output folder.

Final validation: **413 passed**, 76 dependency/deprecation warnings, in 46.17 s:

```powershell
.venv/Scripts/python.exe -m pytest --basetemp=benchmarks/outputs/pytest-building-perf-full-0924b -q
```

New benchmark/test files pass Ruff. Existing production files retain pre-existing
lint findings; comparison against the published source confirms this change adds
none. `git diff --check` passes. Work is isolated on
`codex/building-performance-fix`; `dev` remains at
`71de412b4da99e61cf4da7ebcaa1feaf2a3dc1aa`. No merge or publish was performed.

## GPX follow-up (2026-09-24)

The supplied `Peachtree Road Race.gpx` is now available. Its SHA-256 is
`dc605a642cd8304a1880649400f81588ab5e40849a8c70d209a57ed6e9c234dd`.
It contains 507 route points and a projected length of 9,978.31 m. A 190 × 240 mm
borderless frame fitted with 5.6 mm centerline padding reproduces the saved model's
route footprint exactly: both symmetric-difference area and Hausdorff distance
are zero. This supersedes the earlier missing-GPX limitation for the route/frame
comparison; it does not recover original building tags, water inputs or all settings.

Local route-only generation completed in 3.37 s, with watertight, consistently wound
base, route, start-marker and finish-marker meshes and no warnings. Evidence is in
`benchmarks/outputs/peachtree-gpx-performance/route-validation.json`.
This timing is not a building-generation measurement.

`run_peachtree_gpx.py` prepares a source-backed test with frozen, hashed OSM road,
water and building GeoJSON inputs, then runs normal optimized generation and records
progress, timings, audit and output mesh checks. The user explicitly authorized the map-data download. The frozen snapshot contains
118,220 road edges, 476 water features and 43,628 building features. The full
source-backed generation test completed successfully in 1,091.55 seconds (18m 12s),
excluding 202.23 seconds of source acquisition. See [PEACHTREE_GPX_TEST.md](PEACHTREE_GPX_TEST.md)
for the full stage breakdown, exported model and validation limitations. Replay decodes GeoJSON properties
directly to preserve OSM native list tags; GDAL coerces mixed scalar/list tags to
NumPy arrays, which would otherwise change or prevent this benchmark replay.

## Additional frozen-input performance pass

A paired cProfile replay reduced full generation from 912.87 to 675.01 seconds
(26.06%), with exact exported geometry, source decisions and non-runtime audit
fields preserved. Grouping, support-analysis reuse, width batching and tag loading
are measured in [PERFORMANCE_FOLLOWUP.md](PERFORMANCE_FOLLOWUP.md). These profiled
measurements are separate from the historical unprofiled timings above.
