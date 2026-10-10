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

The automated suite passed 475 tests, including deterministic source ordering, traceable typification and stepped massing, barriers, assembly scaling, control precedence, old project migration, stale-result handling, acquisition failures, exact area-mask tolerance checks, and closed courtyard meshes with preserved volume. `tests-final.log` records the run. The packaged startup smoke covers Qt, System ICU, offline exports, City relief, proportional heights, and WebGL canvas readiness. `package-provenance.json` verifies bundled code and assets.

Prototype generation and slicing use matching model/audit hashes. `slicer/toolpath-survival.json` checks positive-extrusion G1 centerline midpoints within audited building footprints above the base. This proves toolpath presence, not complete silhouette preservation, clearance, strength, or physical appearance.

The low-rise neighborhood represents 201 sources in Existing, 1,103 in Balanced, and 1,251 in Dense. Balanced produces 227 output forms, of which 28 are neighborhood representatives and 199 are unresolved preserved structures. All 28 representatives receive above-base toolpaths. Only 62 of the 199 unresolved structures receive such toolpaths. Protected thin geometry remains an explicit limitation, not a passed printability claim.

The three Balanced prototype slices use A1, PLA Basic, 0.16 mm layers, a 0.4 mm nozzle, and 0.42 mm outer walls. Estimated times are approximately 19 min downtown, 7 min neighborhood, and 14 min landmark. These are monochrome test coupons; route/color production settings require separate review.

All three packaged coupon exports have valid model/audit hashes and exactly the same exported vertex and triangle payloads as their source-generated counterparts. 3MF archive hashes differ between runs because object UUIDs and archive metadata are generated anew; each individual audit still binds to its own exact archive.

## Remaining acceptance gates

1. Complete and hash-verify full LA Existing and packaged City relief exports; compare stage timings and peak memory on identical frozen inputs. Investigate any stage exceeding twice baseline before release. Prototype timing runs overlap other jobs and do not qualify as an isolated performance benchmark.
2. Inspect slicer gaps, merged forms, slender preserved structures, and unsupported sections, then physically print the three small coupons. No physical print has been performed by this implementation task.
3. Review full-map route prominence, neighborhood coverage, landmarks, and height distribution. A successful slice alone does not satisfy visual or physical acceptance.

For physical review, open the three `slicer/*-final-sliced.3mf` files in Bambu Studio and confirm the machine, material, and bed match the printer. Print these small monochrome coupons, then inspect them at normal viewing distance and under side lighting. Check whether neighborhood masses remain distinct, the smallest preserved structures survive handling, streets stay subordinate, and landmark silhouettes remain recognizable. Record actual time, failed or merged structures, and visual judgment before approving a full map. Do not infer acceptance from the renderer or the slicer's lack of warnings.

Do not merge or replace the installed application automatically. Use the candidate executable under `dist/city-relief-reviewed/` for review. Keep physical acceptance and any unmet emphasis/printability targets visible before approving the full LA map.

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
