# Footprint validation benchmark

This is the baseline and evidence harness for building grouping. The opt-in
`--group-buildings` candidate combines nearby small footprints; it does **not**
certify a 0.4 mm nozzle print. Geometry repairs discovered
through the benchmark also apply to normal generation; diagnostic collection is optional.
The confirmed Chicago specification is 190 × 240 mm, X1 Carbon, 0.4 mm nozzle and PLA.
The 5 mm margin is minimum clearance between the outer edge of the 1.2 mm route
and the model edge. It is not an extra 5 mm added outside the model dimensions;
aspect-ratio preservation can leave more clearance on the other axis.

See [initial validation findings](VALIDATION_STATUS.md) for the recorded Bambu
slicing results and Chicago source snapshot.

## Run the synthetic controls

Install development dependencies or `pip install -e ".[benchmark]"`, then run:

```powershell
python -m memorymap_pipeline.footprint_benchmark synthetic benchmarks/outputs/synthetic-v1
```

The 11 specimens include 0.2/0.4/0.6/0.8/1.2 mm squares, a long thin rectangle,
a narrow connecting neck, a thin courtyard wall, close neighbors, a boundary-cut
fragment, and adjacent buildings of different heights. Each gets a 16 × 16 mm base,
a 3MF, a numbered SVG overlay, and source records. Buildings are 4 mm tall except
the thin 8 mm tower in the height-transition control. Nozzle and layer assumptions
are 0.4 and 0.16 mm; base thickness is 1.6 mm.

## Freeze Chicago inputs

Prefer the original marathon GPX when available. Copy `chicago.example.json`, fill
the source paths, dates and attribution, and keep all vector inputs in WGS84
GeoJSON. An explicitly empty GeoJSON is permitted for water absent from a window.
The tool does not silently fetch missing inputs.

```powershell
python -m memorymap_pipeline.footprint_benchmark freeze benchmarks/my-chicago.json benchmarks/downloads/chicago-v1
python -m memorymap_pipeline.footprint_benchmark run benchmarks/downloads/chicago-v1/manifest.json benchmarks/outputs/chicago-baseline
```

Alternatively, acquire a **provisional 2025 community course** and OpenStreetMap
data with the explicitly networked capture command:

```powershell
python -m memorymap_pipeline.capture_chicago_benchmark benchmarks/downloads/chicago-2025-provisional
```

Use `--resume` after an interrupted acquisition to retain downloaded responses.
The capture saves raw Overpass replies and query provenance, and creates
`snapshot/manifest.json` when all inputs are complete. It downloads three windows
with a 2 mm halo, **not all buildings in Chicago**. Their combined population drives
the height distribution. The full GPX drives the horizontal scale. Neighborhood
labels are intended sampling roles and must be checked against the overlays.
The route is from [Go&Race](https://www.goandrace.com/en/map/2025/bank-of-america-chicago-marathon-2025-course-map-1.php),
not an organizer-certified GPX. OSM attribution: © OpenStreetMap contributors,
[ODbL 1.0](https://www.openstreetmap.org/copyright). Captures and generated artifacts
stay local under ignored directories; the source files are not bundled with the code.

The frame fits the entire GPX to a 190 × 240 mm plate, 5 mm margin, with 0.6 mm
route-centerline padding. Generation happens **once**, before mesh crops. Crops
translate XY only: no enlargement, height recalculation, or route refitting.
Flat terrain is an explicit control so elevation-service changes cannot affect the
comparison. Water and roads use frozen local inputs. This milestone does not test
terrain-dependent printability.

Snapshots and output directories must be new. Source checksums are verified before
generation; edits require a new snapshot. Retain the whole snapshot and run folders.

## Read the results

- `full-map.3mf`: full-route-scale model, with source coverage defined by the snapshot.
  An export rejected by the structural audit is recorded in `full-export.json`;
  no printable file is written for that failed model. Each crop is still assessed.
- `<specimen>.3mf` and `.svg`: cropped model and numbered footprint overlay. Roads
  are gray, water pale blue, and the route orange. Red footprints are width risks.
- `source-mapping.json`: all source polygon dispositions, including omissions and
  failures. IDs use frozen input row order and polygon number. Source OSM IDs are
  also recorded. Face ranges refer to the **pre-cleanup concatenated building mesh**;
  export face-count changes prevent acceptance until reconciled.
- `building-mesh.npz`: exact generated vertices/faces for those face ranges, retained
  for diagnosis even when the audited 3MF export is rejected.
- `report.json`: frame, source-manifest hash, per-specimen records, mesh hashes,
  screening results, Git state, Python module hashes and dependency versions.
- `evidence.json`: pending human observations. It is never populated with invented
  slicing or physical-print results.

The local-width diagnostic uses a mitred morphological opening at 0.8 mm, with
0.0001 mm numerical tolerance. It flags lost area and vanished cores, including
thin appendages missed by a global bounding box. It is only a screening heuristic:
acute corners can be flagged, and roofs or upper cross-sections are not evaluated.
Mesh topology/support validity is a separate check. Artificial crop edges can
create slivers; `cut_by_crop` identifies these, and they must not be confused with
interior neighborhood failures.

## Bambu Studio and physical acceptance

Optional local headless slicing saves resolved presets, per-specimen logs, Bambu
projects containing G-code, and `slicing.json`. It does not send anything to a printer
or mark footprint observations as passed. For example, using installed X1 Carbon
presets (choose your actual printer/material for final validation):

```powershell
python -m memorymap_pipeline.bambu_benchmark benchmarks/outputs/synthetic-v1 benchmarks/outputs/synthetic-v1/bambu-x1c-pla --executable "C:/Program Files/Bambu Studio/bambu-studio.exe" --profiles "C:/Program Files/Bambu Studio/resources/profiles/BBL" --machine "Bambu Lab X1 Carbon 0.4 nozzle" --process "0.16mm Optimal @BBL X1C" --filament "Generic PLA"
```

The helper resolves preset inheritance/includes before invoking Bambu; raw inherited
presets caused a Windows CLI crash during development. It records the executable,
settings and model hashes. Layer-count/last-Z summaries can reveal missing towers
on isolated synthetic specimens, but do not establish per-building survival in a
multi-building scene. Artifact production and human inspection remain separate.

1. Import specimens at **100% scale**. Choose the actual printer and material,
   0.4 mm nozzle and 0.16 mm layer height. Save the full Bambu project and sliced
   toolpaths in the run directory. Record Bambu Studio version, printer and material.
2. Inspect extrusion paths above the base, through building bodies, at height
   transitions, and at roofs. Use the numbered overlay to identify missing buildings,
   narrow connections, and unexpected merges across roads, route or water.
3. Print the small specimens. Photograph them and record missing/broken structures
   and whether the barriers remain recognizable. Physical printing is a manual step.
4. Fill `evidence.json`. For each footprint use `pass`, `fail`, or `pending` for
   `slicer`, `physical_print`, and `barriers`; explain failures in `notes`. A barrier
   pass for a control with no roads means no unwanted merge with its neighbors.
   Add any intentional omission to `accepted_omissions` with `specimen`, `id`, and
   a concrete `reason`. Omissions are not successful retained buildings.
5. Set `slicer_project`, `sliced_toolpaths`, and `print_photos` to relative files in
   the run folder, then run:

```powershell
python -m memorymap_pipeline.footprint_benchmark review benchmarks/outputs/chicago-baseline
```

The review returns a nonzero exit status until all retained masses have passing
observations, omissions have reasons, geometry is resolved, and required artifacts
exist. It hashes the evidence and verifies model identity. Acceptance is based on
**operator-recorded evidence**, not automated inspection of photographs/toolpaths.
An empty specimen, deleted observation, changed model, or mismatched layer profile
cannot pass. Keep failed baseline results—they are the acceptance cases for grouping.

## Evaluate the grouping implementation

Use the exact frozen manifest with a different output directory and `--label`:

```powershell
python -m memorymap_pipeline.footprint_benchmark run benchmarks/downloads/chicago-v1/manifest.json benchmarks/outputs/chicago-candidate --label candidate --group-buildings
python -m memorymap_pipeline.footprint_benchmark compare benchmarks/outputs/chicago-baseline/report.json benchmarks/outputs/chicago-candidate/report.json
```

The candidate generator must preserve source IDs and emit one disposition per
source polygon: `retained`, `grouped`, `omitted`, or `unresolved`. A grouped result
should identify its shared output geometry and face ranges for every contributing
source. Comparison refuses different snapshots and exposes missing source IDs.
Rerun slicing and physical tests; fewer geometric flags alone do not establish success.

The current candidate targets low buildings (at most 3 mm high), using a 0.8 mm
footprint target, at most 0.4 mm between neighboring source footprints, a 4 mm
maximum group span and at most 128 members. It fills a convex neighborhood mass and
uses only the bounded expansion needed to reach the width target. Groups have a
flat top at the tallest member's height. Expanded area is capped at eight times
source area and must remain inside the plate and outside protected geometry.
Landmarks, religious/civic buildings, stadiums, building parts, towers and courtyards
stay separate. Grouping mode narrows local streets to 0.4 mm; arterial widths remain
unchanged. Alleys and driveways are omitted in this mode so they do not split
neighborhood blocks into unprintable strips. Missing road or water barriers prevent grouping rather than permit
unverified merges. Sources that cannot form a qualifying group remain individual
and are counted in the generation warnings. All grouped source IDs retain shared
geometry, group IDs and face ranges in diagnostics. Do not merge the older printability-preflight
branch wholesale: it also changes export error policy, outside this benchmark scope.

Expansion incorporates touched eligible neighbors through the same gap limit before
accepting a group, preventing overlapping individual and grouped building shells.


After Bambu slicing, measure grouped footprints against deposited paths with:

```powershell
python -m memorymap_pipeline.bambu_grouping_audit benchmarks/outputs/chicago-candidate
```

This flat-benchmark diagnostic samples representative deposited layers through each
interior group's height (lower printable section, 25%, 50%, 75%, near top), selecting
the nearest recorded layer for each level, including layers with no deposited paths.
The report includes the number of distinct sampled layers; five labels may select
fewer than five distinct layers on short groups. It accounts for the machine's
single-extruder offset and extrusion widths, and writes `group-toolpath-coverage.json`.
It excludes groups cut by specimen boundaries. Coverage is approximate and does not
replace full-height, separation or physical inspection. Missing/multiple extruder
offsets are rejected instead of guessed.

Benchmark `report.json` also includes `grouping_advisory_metrics` so operators can
review scale/density/fragmentation indicators before enabling grouping. These metrics
are advisory diagnostics only; they do not auto-enable grouping or modify geometry.
