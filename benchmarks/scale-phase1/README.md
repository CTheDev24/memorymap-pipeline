# Scale-aware building generalization: Phase 1

Implemented on `codex/scale-aware-building-generalization-phase1`, based on
`dev` commit `71de412`. Phase 1 adds physical scale, printer configuration and
read-only diagnostics. No intentional geometry changes in Phase 1. No automatic
simplification, grouping activation, typification, omission or route-clearance
clipping is enabled. No UI controls or dependencies were added.

## Architecture

`MapFrame.x_mm_per_meter` and `y_mm_per_meter` are the authoritative ratios used
by the final XY transformation. `mm_per_meter` is their minimum and
`meters_per_mm` its reciprocal. Fitted frames have matching axis scales. Existing
explicit frames may stretch axes differently; that behavior is preserved and
reported with both axis scales and `anisotropic`, rather than silently changing
geometry. The inverse transformation retains its existing arithmetic.

`PrintScaleContext.from_frame(frame, config)` reads the effective generation
frame **after** the physical-border adjustment. Printer resolution is explicit
line width when supplied, otherwise nozzle diameter. Each physical threshold is
resolution multiplied by its configurable ratio. The immutable context and its
validation are in `print_scale.py`; `config.py` imports the defaults and validates
loaded configuration. Direct generation requests also validate the profile.

The older CLI uses `compute_normalize_center_transform`. Its diagnostics adapter
reads the **actual existing transform's scale** without fitting a second frame or
changing its geometry. This older route-fitting path remains separate from the
Studio frame path and should be considered before future policy integration.

`buildings.py` collects source dispositions and passes the existing ordinary
footprints to `building_generalization.py`. Measurements operate on final print
millimeters after frame clipping and building-part subtraction, but before the
existing grouping and residential-filter decisions. Registered landmarks and
explicit building parts are excluded from this width population. The original
footprints remain available after meshing, so measurement does not modify or
replace any manufactured geometry.

Width is the maximum surviving core under mitred inward buffering, estimated by
binary search to 0.0001 mm. This adapts the grouping/benchmark erosion concept.
Bounding dimensions only bracket the search. Holes and multipart footprints take
part in erosion. This is **not** a minimum-neck-width measurement or assurance
that every narrow wall/tip survives slicing. Threshold checks allow the existing
benchmark-style 0.0001 mm radius tolerance. Failed measurements are counted and
excluded from percentile/percentage denominators; an empty population reports
null percentiles and percentages.

`source_building_count` counts source polygon diagnostic records (multipart
features can have multiple records; unsupported sources have an unresolved
record), not unique OSM entities. `measured_building_count` identifies the narrower
width population. Source counts include outside-frame and other existing
omissions. Successful groups, grouped sources, individual sources, omissions and
unresolved records are reported separately. Existing diagnostic keys and source
mappings remain present. Existing statuses map to the new disposition vocabulary;
unresolved records receive null rather than an invented successful disposition.

The service exposes these values in
`GenerationResult.stats['building_generalization']` and an INFO log named
`BUILDING GENERALIZATION CONTEXT`. The CLI logs the same context. Benchmark reports
retain the new block alongside their existing metrics. Disabled/unavailable
building data is labeled, rather than reported as successfully measured zeroes.

## Configuration

| Field | Default |
|---|---|
| `nozzle_diameter_mm` | `0.4` |
| `line_width_mm` | `null` |
| `layer_height_mm` | `null` |
| `building_generalization_mode` | `"manual"` (only supported mode in Phase 1) |
| `building_marginal_width_ratio` | `1.25` |
| `building_robust_width_ratio` | `2.0` |
| `building_simplify_tolerance_ratio` | `0.375` |
| `building_merge_gap_ratio` | `1.0` |
| `building_group_span_ratio` | `10.0` |
| `building_route_clearance_ratio` | `2.0` |

Defaults yield 0.5 mm marginal width, 0.8 mm robust width, 0.15 mm simplification
tolerance, 0.4 mm merge gap, 4.0 mm group span and 0.8 mm route clearance.
Nonfinite/nonpositive printer dimensions, missing both XY dimensions, negative
ratios, robust width below marginal width and group span below merge gap are
rejected. A known positive line width permits an unknown nozzle diameter.

These thresholds are **advisory**. Existing absolute `building_grouping_*`
configuration still controls manual grouping, including user overrides.
`layer_height_mm` is diagnostic metadata; it does not override the existing
`route_layer_height_mm` or change Z geometry. Old JSON configurations and v1/v2
DesktopProject files continue to load. Printer fields currently live in JSON
configuration/GenerationRequest; no Studio project-format migration is included.

## Samples and reproducibility

Run `python -m tools.scale_diagnostics_samples --output benchmarks/scale-phase1`.
The samples use only local fixture data and generated synthetic geometry; no
network downloads or slicer calls are needed. Peachtree source GPX/OSM data was
not available as a complete local fixture; its existing 3MF comparison cannot
supply authoritative source-population metrics.

| Metric | Compact Houston fixture | Synthetic 20.9 km linear route |
|---|---:|---:|
| mm per meter | 1.850208 | 0.010947 |
| meters per mm | 0.540480 | 91.346154 |
| Source records | 3 | 100 |
| Measured ordinary footprints | 2 | 100 |
| Width p10 / p50 / p90, mm | 28.254 / 31.589 / 34.924 | 0.109 / 0.328 / 0.799 |
| Below 0.5 mm marginal | 0 (0%) | 75 (75%) |
| Below 0.8 mm robust | 0 (0%) | 100 (100%) |
| Individual / grouped sources / omitted | 2 / 0 / 1 | 100 / 0 / 0 |

The tiny Houston route fixture tests plumbing, not typical city-scale density.
The synthetic sample repeats 10, 20, 40 and 73 m square footprints along a long
route. Manual mode deliberately retains them despite the diagnostics.

`measurement-performance.json` records approximately 11.2 seconds for 10,000
simple synthetic rectangles on the local Windows runtime. Real OSM polygons may
be considerably more expensive. Width measurement currently performs multiple
GEOS erosions per source. Before Phase 2, consider caching by final geometry and
metric version, or bounded approximation; invalidation must include frame,
rotation, border settings, source edits and part subtraction. Threshold statistics
also depend on the printer profile. No diagnostic cache is introduced here.

## Validation

- Full suite: 392 passed (70 dependency warnings), no failures or skips.
- 38 new cases cover scale/axis transformations, portrait/landscape/long extents,
  width/height-constrained fitting, rotation/padding, printer fallback and line
  width, custom ratios, invalid config, legacy config/transform adapters,
  thin courtyard cores, deterministic distributions, empty/failed measurements,
  source/group/filter counts, all-omitted results, disabled buildings and exact
  geometry equality with diagnostics and changed printer thresholds.
- Existing grouping, barriers, classification, roofs/parts, generation,
  benchmark, terrain and desktop project tests are included in the full suite.
- An isolated archive of `dev` commit `71de412` and this branch were run through
  `tools/check_scale_compatibility.py`. All six cases matched exactly for raw mesh
  vertex/face hashes, source dispositions, group membership/face ranges, grouping
  stats and filtering stats. Cases span 200 m and 20 km extents, grouping off/on
  and filtering. See `compatibility.json`. Dependencies were identical for both
  runs; hashes need not be portable across GEOS/trimesh versions.
- New Python modules, tests and tools pass Ruff; `git diff --check` passes.

## Files

| File | Role |
|---|---|
| `AGENTS.md` | Persist the dev/sub-branch workflow rule |
| `memorymap_pipeline/map_frame.py` | Authoritative XY and effective scale properties |
| `memorymap_pipeline/print_scale.py` | Frozen context, shared defaults and validation |
| `memorymap_pipeline/config.py` | Merge and validate printer/generalization configuration |
| `memorymap_pipeline/building_generalization.py` | Core-width metrics and disposition contract |
| `memorymap_pipeline/buildings.py` | Measure existing footprints and preserve source dispositions |
| `memorymap_pipeline/generation.py` | Build the context from the effective frame; return/log diagnostics |
| `memorymap_pipeline/cli.py` | Adapt existing CLI transform to the context and log diagnostics |
| `memorymap_pipeline/footprint_benchmark.py` | Reuse frame scale and retain generalization metrics |
| `tests/test_print_scale.py` | Scale, configuration, fitting and legacy-adapter coverage |
| `tests/test_building_generalization.py` | Width/disposition/integration and geometry-preservation coverage |
| `tools/check_scale_compatibility.py` | Reproducible offline parent/branch geometry fingerprints |
| `tools/scale_diagnostics_samples.py` | Reproducible compact/long examples and timing |
| `benchmarks/scale-phase1/houston-fixture-compact.json` | Compact generation diagnostic sample |
| `benchmarks/scale-phase1/synthetic-linear-20.9km.json` | Explicitly synthetic long-route sample |
| `benchmarks/scale-phase1/measurement-performance.json` | Local width-measurement timing |
| `benchmarks/scale-phase1/compatibility.json` | Parent comparison metadata and exact fingerprints |
| `benchmarks/scale-phase1/README.md` | Architecture, configuration, validation and limitations |

## Phase 2 considerations

Manual grouping still has separate absolute controls; selecting derived values
in a future mode must be explicit. Protected landmarks/parts, source records and
mesh components are different populations. Current height mapping also uses map
scale and vertical exaggeration; this phase leaves that coupling intact. Explicit
anisotropic frames need an agreed policy before automatic thresholds can safely
be converted back to geographic meters. The maximum-core metric does not catch
all thin-neck or tip failures, so later policy needs semantic and local-loss
information as well. No Phase 2 behavior is activated or accepted by this report.
