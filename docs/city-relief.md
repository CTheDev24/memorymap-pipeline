# City relief implementation and acceptance

City relief is an opt-in representation for large, single-piece maps. Existing remains the compatibility default. The first target is 240 × 190 mm with a 0.4 mm nozzle; the LA prototypes use the actual A1 profile's 0.42 mm outer-wall line width.

## Confirmed failure in the supplied LA export

The supplied 3MF matches its audit hash and contains no building object. Acquisition failed before optimization. The old installed executable lacks the checked-out tiled download recovery and relation assembly modules. The missing buildings therefore cannot establish a height or grouping defect.

The frame covers 22.908 × 18.135 km, or 95.45 metres per printed millimetre. A 10 m footprint is only 0.105 mm wide. Successful downloads alone cannot make individual houses printable at this scale.

## Changes

- Building acquisition and processing errors stop generation. Successful empty responses and disabled layers are distinct states. A failed attempt preserves the previous successful file and cannot export it as the new result.
- Export audits and About include a normalized source/module and asset fingerprint. The package verifier compares bundled executable modules and assets against the checkout.
- City relief uses deterministic, source-oriented neighborhood representatives, bounded occupied envelopes, geographic barriers, shared assembly height mapping, and verified identity-based landmark emphasis. Source-to-output provenance and explicit omissions remain in the audit.
- Low-rise relief is expressed in physical millimetres. Optional multiplier and minimum/maximum controls apply once. Automatic slenderness limits yield to explicit height controls.
- Major roads have 0.6 mm visible relief and local streets 0.3 mm. Local streets are shown within 3 mm of the route; internal street barriers remain independently available. Service roads and cycleways are excluded from the preset's visible roads.
- Project schema 4 persists appearance, neighborhood detail, emphasis, preset version, and the Existing settings snapshot. Older projects migrate to Existing. Appearance changes invalidate the current result.
- Courtyard meshing handles oblique point contacts, and park transforms preserve multipolygon components and holes.

## Versioned starting preset

`city-relief-v1` uses a preferred representative width of 2.5 times effective line width in Balanced, 2.125 in Dense, and 3 in Sparse. At 0.42 mm, these are 1.05, 0.8925, and 1.26 mm. Robust fallback width is 0.84 mm; Balanced gap is 0.42 mm and cluster span is 4.2 mm. Representative direction follows source footprints; dimensions and heights do not use random variation.

Known LA identities are City Hall, Walt Disney Concert Hall, Beverly Hills City Hall, and TCL Chinese Theatre. Their source IDs and proximity were checked against the supplied GPX. The user believes the course is 2025 or 2026; the edition remains unconfirmed. No edition-specific attribution is inferred.

## Validation evidence

Artifacts are under `benchmarks/outputs/city-relief/`, which is deliberately ignored by Git because cached geometry and generated artifacts are large. `review.html` embeds the nine actual WebGL comparisons and switches identical top, perspective, and side cameras. Each prototype is 24 × 24 mm at the full map scale.

The latest automated suite passed 492 tests, including deterministic source ordering, traceable typification and stepped massing, barriers, assembly scaling, control precedence, old project migration, stale-result handling, acquisition failures, exact area-mask tolerance checks, arc direction and full-circle sampling, and closed courtyard meshes with preserved volume. `performance/tests-checkpoint.log` records the latest run; `tests-final.log` preserves the earlier 489-test run. Connected parent/part heights retain their source ratios through automatic slenderness caps and explicit user controls. The packaged startup smoke covers Qt, System ICU, offline exports, City relief, proportional heights, and WebGL canvas readiness. `package-provenance.json` verifies bundled code and assets.

Full-frame profiling exposed repeated overlays between tiny cleanup candidates and the complete road-barrier geometry. Cleanup now intersects exact spatial partitions and unions the local clips, with an original-mask reference check near the existing `1e-8` threshold. Tests cover overlapping masks, partition boundaries, source exclusion, and decisions just below and above that threshold. This optimization applies to both appearance modes and does not relax clearance rules.

Grouping also uses exact prepared topology to establish zero intersection area for disjoint interiors before attempting a reference overlay. A regression distinguishes touching boundaries from a positive overlap of only `1e-11` mm². A 380-query synthetic boundary sample improved from 0.343 to 0.115 seconds; this is not a full-map performance acceptance result.

Prototype generation and slicing use matching model/audit hashes. `slicer/toolpath-survival.json` checks positive-extrusion G1 centerline midpoints and G2/G3 relative-I/J arc samples spaced at most 0.05 mm within audited building footprints above the base. Arc conventions follow [the documented G2/G3 geometry](https://marlinfw.org/docs/gcode/G002-G003.html), and all extruding arcs in these slices use that supported format. This establishes sampled centerline presence, not complete silhouette preservation, clearance, strength, or physical appearance. An unmatched form is not proof of absent toolpaths.

The low-rise neighborhood represents 201 sources in Existing, 1,103 in Balanced, and 1,251 in Dense. Balanced produces 227 output forms, of which 28 are neighborhood representatives and 199 are unresolved preserved structures. All 28 representatives have detected above-base toolpaths, as do 85 of the 199 unresolved structures. Protected thin geometry remains an explicit limitation, not a passed printability claim.

The three Balanced prototype slices use A1, PLA Basic, 0.16 mm layers, a 0.4 mm nozzle, and 0.42 mm outer walls. Estimated times are approximately 17 min downtown, 7 min neighborhood, and 12 min landmark. These are monochrome test coupons; route/color production settings require separate review.

The landmark coupon exposes an unmet target: City Hall and Disney Concert Hall remain at 1.0× footprint scale because their assemblies cannot safely enlarge to 1.5× around the protected corridors. City Hall retains two of eight assembly elements after route exclusion, with a 3.54 mm assembly top above ground; Disney retains all four, with a 4.94 mm top. Their height audits flag preset aspect limits. These are feasible representations, not proof of landmark recognition; inspect their silhouettes before accepting the preset.

All three packaged coupon exports have valid model/audit hashes and exactly the same exported vertex and triangle payloads as their source-generated counterparts. 3MF archive hashes differ between runs because object UUIDs and archive metadata are generated anew; each individual audit still binds to its own exact archive.

The source-Python full Existing baseline completed in 5,329 seconds with a 6.36 GB OS peak working set. Its 3MF/audit hash matched. Acquisition returned 421,196 elements and decoding produced 421,240 polygons; 420,116 elements reached the frame. The optimizer reported 11,557 represented sources in 9,758 forms, with 409,679 omissions and 9,527 unresolved records. These figures use different stage units and should not be subtracted as one conservation equation. The streamed disposition breakdown identifies 401,946 isolated polygons below the print target, 6,428 excluded by the route, 663 removed by the frame clip threshold, 457 outside the frame, 185 replaced by parts, and four unresolved/null geometries. These account for the 409,679 omitted records plus four unresolved records. Export removed four tiny unsupported shells; that original baseline predates the export/preview reconciliation fix. Its original audit remains unchanged for provenance.

The first packaged full City run explicitly failed on sculpture-garden relation 19748327: valid boundary ways were accompanied by descriptive artwork nodes. City context decoding now ignores artwork/label nodes and still rejects malformed boundary members. All 937 full-frame park/garden geometries passed preflight. Export now returns cleaned meshes to generation, remaps audit face ranges, and records unsupported shell omissions so future previews match exports.

## Remaining acceptance gates

The approved full packaged Balanced run has now completed but failed acceptance because its requested Roads layer was silently omitted. See [the October 11 validation report](city-relief-full-validation.md) for measured timings, coverage, source accounting, the mixed-geometry road diagnosis and the next checkpoint. Its model/audit and renders are preserved as an incomplete diagnostic candidate. The subsequent road guard is covered by 497 passing tests; prior packaged coupon and slicer evidence predates that correction.

1. Resolve exact road triangulation failures and the expensive neighborhood fallback on frozen representative inputs before requesting approval for another full packaged LA run. Optimization and extrusion exceed twice their historical Existing stage times; investigate before release. Historical baseline timings are diagnostic, not a controlled packaged benchmark.
2. Inspect slicer gaps, merged forms, slender preserved structures, and unsupported sections, then physically print the three small coupons. No physical print has been performed by this implementation task.
3. Review full-map route prominence, neighborhood coverage, landmarks, and height distribution. A successful slice alone does not satisfy visual or physical acceptance.

For physical review, open the three `slicer/*-final-sliced.3mf` files in Bambu Studio and confirm the machine, material, and bed match the printer. Print these small monochrome coupons, then inspect them at normal viewing distance and under side lighting. Check whether neighborhood masses remain distinct, the smallest preserved structures survive handling, streets stay subordinate, and landmark silhouettes remain recognizable. Record actual time, failed or merged structures, and visual judgment before approving a full map. Do not infer acceptance from the renderer or the slicer's lack of warnings.

Do not merge or replace the installed application automatically. The original executable under `dist/city-relief-reviewed/` produced the incomplete full candidate and remains preserved for provenance. The separate `dist/city-relief-roadfix-reviewed/` executable is for bounded road-failure and coupon verification, not production acceptance. Keep physical acceptance and unmet emphasis/printability targets visible before approving another full LA map.

## Reproduction tools

- `tools/run_la_city_relief.py`: full-frame frozen/native-cache generation and timing/memory capture.
- `tools/freeze_la_neighborhoods.py`: freeze source geometry at the LA frame scale.
- `tools/compare_la_city_relief.py`: Existing/Balanced/Dense coupon generation.
- `tools/render_city_relief_preview.py`: actual GLB WebGL screenshots with common camera bounds.
- `tools/review_city_relief.py`: portable embedded comparison review.
- `tools/verify_city_relief_package.py`: normalized bundled module and asset verification.
- `tools/slice_city_relief_prototypes.ps1`: final audit-matched A1 slices.
- `tools/check_city_relief_toolpaths.py`: traceable above-base toolpath presence diagnostics.
- `tools/finalize_la_city_relief.py`: serial full-frame validation after an active baseline; writes explicit export/render and performance gate status.
- `tools/validate_city_relief_package.ps1`: hidden packaged generation with OS peak-memory measurements.
- Packaged `--validate-map MANIFEST`: run the application generation path with a saved frame and source inputs.
