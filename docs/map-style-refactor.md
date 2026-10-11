# Trace Studio Map Style refactor — review checkpoint

Latest follow-up: [review fixes and acceptance evidence](map-style-review-fixes.md).
This original checkpoint records the implementation at reviewed head `5290b7d`.

Date: 2026-10-11. Branch: `codex/map-style-audit` (a descendant of `dev`).
Status: software prototype implemented and representative regression gates passed.
Manufacturing qualification and real-city visual approval remain pending.

The owner clarified that Houston 10K, Big Sur, and Chicago Marathon denote design
intentions, not mandatory frozen source files. Representative fixtures were used;
no location-specific behavior was added. The earlier baseline-evidence blocker is
resolved. Existing City Relief work was reused, and no full LA run was restarted.

## Implementation

- **Cityscape** resolves to the established Urban engine with buildings enabled.
  Existing filtering, assemblies, roofs, height policy and opt-in City Relief remain.
- **Terrain** resolves to the existing Landscape/terrain engine with buildings off.
  DEM sampling, relief normalization, water/shoreline support and route draping remain.
  Explicit Terrain selection rejects failed DEM acquisition instead of silently
  producing a flat substitute. Legacy callers retain their prior fallback setting.
- **Street Grid** resolves to Urban with buildings off. Experimental deterministic
  print-space street selection retains major corridors, short connectors, sparse
  grids and detail within 2 mm of the route. Whole named corridors are retained or
  omitted; unnamed source geometries receive stable geometry identifiers. Corridors
  with at least 85% of length already covered by retained print-space context may
  be omitted. Selection dispositions and source geometry hashes are audited.
- Sparse/Balanced/Dense street detail uses 1.0/0.7/0.4 mm spacing respectively.
  No appearance switching based on race distance, city name or geographic scale.
- Both Urban-based styles export `Roads_Motorways`, `Roads_Main`, `Roads_Local` and
  optional `Roads_Service`, alongside the independent `Route_Accent` body, in one
  registered assembly. Road classes share 0.8 mm relief by default. Priority is
  Route > Motorways/Trunks > Main > Local > Service. Higher-priority footprints own
  intersections; lower bodies subtract them before exact extrusion. Selected
  roads remain continuous except established route clearance and class ownership.
- Class widths start at **1.2/0.8/0.4/0.4 mm**, explicitly experimental. New desktop
  sessions start with the proposed **1.6 mm route width**; old saved widths remain.
  No separate road-height controls. Class identity and color are independent;
  class colors may match, and every body retains its own component/material identity.
- Flat Urban water context reuses the established water/support pipeline with a
  flat support grid; it does not download or introduce topographic relief.
- Non-adaptive roads stream their projected records instead of retaining a new
  full-source list. Legacy callers still use the combined road body and existing
  processing path. Terrain retains its existing combined contextual-road path.

## Interface and persistence

Map Style is the primary selector. Cityscape building controls and advanced heights
are conditional; Terrain surfaces/relief and road context are conditional. Urban
road class enablement and width share rows. Optional paths remain available.
The Export actions remain in their own section.

Removed from the user-facing UI: border/trim controls, Route Layer Height, the
overlapping layer-inclusion group, separate grouping/narrow-residential controls,
and the legacy height ceiling. Established versus Print optimized consolidates
building processing. Advanced height controls remain because proportional height,
illustrative height, shared assembly multipliers and final limits are distinct
capabilities. Appearance remains Existing by default; City Relief is opt-in.

Desktop schema **v5** stores Map Style, per-style settings and road widths. V1–v4
projects load with `map_style=None`: their exact profile/layer/border/slope settings
remain on the compatibility path. A visible legacy hint explains that explicitly
selecting a style applies its layer policy. Stored border and route-layer settings
continue to work internally despite removal of their UI controls. New sessions use
borderless geometry and the existing internal 0.16 mm / 30% slope default.

All visible geometry controls, style changes, widths and frame adjustments invalidate
results. A revision check rejects results completed after settings changed during
generation. Failure leaves previous successful files intact and saving disabled.
Audit fields add style/road/selection identities and terrain/water layer states.
Build fingerprints now include the new modules and printability checks.

## Validation and baseline differences

Final command: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`.
**522 passed, 105 warnings, 46.32 s**. Warnings are dependency deprecations and
existing fixture parsing warnings. No failed tests. New coverage includes style
resolution, legacy migration, exact building/route compatibility, valid generated
style meshes, urban water context, deterministic corridor selection, preserved
named corridors, class ownership, uniform relief, source footprint extrusion,
single-assembly export, shared class colors, actual Qt switching and stale results.

New modules/tools pass scoped Ruff; whitespace checks pass. Existing modules have
pre-existing lint findings; no repository-wide lint cleanup was undertaken.

The reproducible offline harness generated 240 × 190 mm baseline/candidate models
with matching audits, frozen source checksums, GLBs and native depth-buffered
top/perspective/side views. Actual Qt screenshots verify conditional controls.
Each export passes the existing structural/support audit. Class tests additionally
compare triangulated caps against owned footprints and check disjoint partitions.

| Style | Candidate generation | Preserved geometry | Intentional change |
|---|---:|---|---|
| Cityscape | 0.50 s | Base, route, building arrays exactly equal | Experimental widths; four separate road bodies |
| Terrain | 1.03 s | Base, route, water and terrain-skin arrays exactly equal | Style/configuration presentation only; 9 mm mapped relief in this fixture |
| Street Grid | 0.55 s | Base and route exactly equal; buildings absent in both | Experimental widths, class bodies, 14/16 source corridors retained |

These are single-run small-fixture timings, not estimates of full Chicago or Big
Sur performance. The coarse terrain fixture contains exposed-substrate patches in
both baseline and candidate; it demonstrates invariant geometry, not finished
landscape appearance. The grid fixture tests distinct/overlapping corridors,
not all street shapes or connectivity cases in an actual city.

Tracked measurements: [map-style-regression-metrics.json](map-style-regression-metrics.json).
Source build fingerprint for all final candidate audits:
`f999868087fe1f84d231b2fcd42b16293ea465f13afc19cfc02297a1be6016bb`.
Their model SHA-256 values match their audits. The build's older `release` label
remains `city-relief-v1`; the additive `map_style_algorithm` is `map-styles-v1`.
No release or installed application update was made.

## Review artifacts and reproduction

Local outputs are ignored by Git under `benchmarks/outputs/map-style-final/`:

- `review.html`: portable identical-camera baseline/candidate comparisons.
- `map-style-review.zip`: models/audits, GLBs, views, inputs, metrics and test log.
- `{cityscape,terrain,street_grid}/candidate.3mf` and matching `.audit.json`.
- `report.json`: complete recorded statistics and checksums.
- `route.gpx`, `roads.geojson`, `buildings.geojson`, `dem.json`: frozen inputs.

Native UI screenshots are in `benchmarks/outputs/map-style-ui-final/`.
To reproduce into a fresh output directory:

```powershell
.venv/Scripts/python.exe tools/validate_map_styles.py --output benchmarks/outputs/map-style-review-new
.venv/Scripts/python.exe tools/package_map_style_review.py benchmarks/outputs/map-style-review-new
.venv/Scripts/python.exe tools/capture_map_style_ui.py benchmarks/outputs/map-style-ui-new
```

Generation and preview use source Python/Qt. The packaged executable was not
rebuilt or validated for this refactor. CLI/API capabilities are retained as they
were: the legacy CLI has separate orchestration and the API currently supports
route/base jobs only. This desktop change does not claim new style support there.

## Manual and physical acceptance gates

**Bambu Studio import/assignment has not been verified for these new exports.**
No slicer success or physical print acceptance is inferred from geometry tests.

1. Import each candidate at 100% scale as one assembly. Verify expected dimensions,
   component names, relative placement and absence of independent auto-arrangement.
2. Assign classes independently, then assign several classes to the same filament.
   Verify the Route stays separately assignable; save the Bambu project and record
   application version, actual printer, nozzle, material and resolved line widths.
3. Slice with the actual profile. Inspect deposited width, continuity at class
   boundaries, route clearance, thin local/service roads, gaps, accidental merging,
   fragile buildings and terrain/water alignment. Review warnings and toolpaths.
4. Print small representative regions. Inspect under normal viewing distance and
   side lighting. Toolpath presence does not prove silhouette survival or strength.
5. Review an actual geographically extensive street network before approving
   adaptive spacing defaults. Whole-corridor thinning is experimental and does not
   guarantee preservation of the original road graph's connectivity. Inspect
   omitted corridor records and neighborhood character; tune from evidence.

Recommendation: **GO for owner software/visual review; CONDITIONAL GO for broader
validation; no manufacturing release approval yet.** Keep experimental widths and
selection settings provisional. Do not merge into `dev`, publish a release, update
the installed executable or send a printer job without owner approval.

## Reviewable milestones

- `c59a52e`: repository audit and clarified representative regression gates.
- `3dd01fc`: style architecture and lossless project migration.
- `f35b4b6`: road-class separation, adaptive selection, water context and provenance.
- `80d06b4`: UI consolidation, per-style settings, preview class controls and staleness.
- Final documentation/tooling milestone: this report, metrics and repro/review tools.

Core changes: `map_styles.py`, `street_selection.py`, `roads.py`, `generation.py`,
`mesh.py`, `build_identity.py`, desktop `project.py`, `window.py` and viewer HTML.
Added/updated regression tests and tools are committed separately from generated
artifacts. `dev`, installed applications, Shopify, and unrelated work are unchanged.
