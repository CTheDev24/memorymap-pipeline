# Exact road recovery and City performance checkpoint — October 11, 2026

This follows the failed full LA validation at `521c6e7`. The original candidate remains incomplete and unchanged. Work is on `codex/city-relief`; no merge, release, installed application replacement, customer option changes or physical prints are authorized by this checkpoint.

## Road recovery

The 32 recorded failures had valid source polygons but non-watertight normal extrusions. The default extruder welds nearby coordinates, which can collapse thin Boolean fragments. Indexed recovery shares cap and wall indices directly without coordinate welding, enlargement, hole filling or precision reduction.

City recovery validates cap area, projected footprint, winding, closure and positive volume. Volume drift in normal City extrusions also triggers recovery. If recovery requires subdivision, all positive-area fragments are retained; irrecoverable geometry still fails the requested layer. City recovery does not use the legacy 0.002 mm simplification fallback. Successful Existing-mode meshes retain their original path; legacy Existing simplification remains available when recovery fails. Building and route generation rules are unchanged.

The full frozen road-region replay passes:

| Measurement | Result |
|---|---:|
| Eligible source polygon area | 10,987.243325443895 mm² |
| Mesh top projected area | 10,987.243325445925 mm² |
| Net area difference | 2.03e-9 mm² |
| Returned mesh parts | 19,918 |
| Faces | 2,325,584 |
| Failed fragments | 0 |
| Closed and consistently wound | All |
| Region extrusion time | 150.88 seconds |
| Process peak working set | 983,199,744 bytes |

This replay excludes acquisition, buffering, concatenation and export. It is not a full-map run. An earlier prototype closed all meshes but showed 0.0976 mm² aggregate area drift; it was not accepted. The stricter cap/volume checks and exact City subdivision resolve that drift. Three recorded LA failures and preservation of sub-floor positive fragments are regression fixtures.

## Measured neighborhood performance

A complete frozen neighborhood replay exposed costs that the earlier 1,000-candidate checkpoint did not exercise. Instrumentation recorded 7,540 buffers consuming 35.43 seconds. Only 196 calls exceeded 50 ms, but they consumed 32.49 seconds; these contain 49 distinct exact geometry-and-parameter inputs.

The retained optimization caches only identical occupied geometry WKB plus radius within one typification invocation. It targets MultiPolygons with at least eight components, uses at most 128 entries, and budgets eight MiB of serialized input/output geometry. Native geometry allocation is larger than serialized bytes; process peaks are measured independently. Cache decisions do not change classification, members, placement, heights, exclusions or provenance.

| Complete frozen area | Baseline median | Cached median | Change |
|---|---:|---:|---:|
| Neighborhood | 76.02 s | 49.45 s | 34.95% faster |
| Downtown | 7.568 s | 7.564 s | Essentially unchanged |
| Landmark | 7.732 s | 7.582 s | 1.94% faster |

Each measurement has three serial repeats on identical checkpoint bytes. Output digests match across all reference and candidate runs, including geometry, dispositions, source mappings and heights. Peak process memory stays comparable: neighborhood baseline 210.05 MB, candidate 208.78 MB. Full details are in [checkpoint metrics](city-relief-exact-recovery-metrics.json).

A conservative point-distance rejection screen preserved output but did not materially improve runtime; it was removed. The earlier member-envelope cache experiment was not reused blindly: the complete replay and slow-buffer capture provided new evidence for exact geometry reuse.

## Compatibility and remaining gates

The full suite passes 507 tests. Existing remains the default, City remains operator-controlled, and Sparse/Balanced/Dense remain available. No synthetic infill, nonlinear distortion, new landmark models or control redesign was introduced. Unknown source classifications and unresolved printability remain visible.

The first indexed native package reached export but rejected 41 road shells under the existing support-area threshold; no new model was written. Its failed report and executable remain preserved. Exact subdivision exposes very small positive-area road shells that are physically embedded in the flat base, rather than discarding them. The support correction certifies the actual closed, consistently wound rectangular base solid and proves a positive-area downward road triangle lies strictly within its volume. It does not infer support from arbitrary base bounds or reduce the existing surface-contact thresholds. Tests retain rejection of floating roads, tiny surface-only contacts and roads over genuine open space. Building support behavior is unchanged.

The reviewed executable is `dist/city-relief-supported-reviewed/Trace Studio.exe`, SHA-256 `37e225e370c8e5f748db397226ea1969b8b7514f50e7fe473af68bbc8d82ee1d`. Package verification now checks `printability` explicitly alongside the generation modules. The existing audit `source_sha256` covers a selected geometry-module set and does not include the support module; it consequently remains `e85755…` across the two indexed packages. Use the executable hash and verified module hashes to distinguish these validation artifacts, rather than that legacy partial fingerprint alone. The native performance reports bind each run to the executable hash and the serialized verification reports bind each model to its audit.

The new package passes startup, Qt/WebEngine and sample-generation checks. Its full-frame roads-only export and all three complete Balanced coupons pass independent serialized closure, winding, positive-volume, support, layer-presence, audit-count and GLB/3MF checks. The coupon source-to-output face ranges and represented counts also validate. All 69 frozen full-map inputs remain unchanged. The original full building map remains unaccepted; the new roads-only proof does not replace it.

| Native check | Generation time | Process peak working set |
|---|---:|---:|
| Full LA roads only | 667.94 s | 2,595,119,104 bytes |
| Downtown Balanced | 149.81 s | 382,107,648 bytes |
| Neighborhood Balanced | 347.64 s | 405,680,128 bytes |
| Landmark Balanced | 170.59 s | 369,094,656 bytes |

Native measurements include generation/export/preview; launch-to-exit and detailed building stage timings are retained in the metrics. A separately installed Trace Studio was active during native validation. Historical native comparisons are uncontrolled: downtown stages roughly doubled even though all geometry is identical. The serial three-repeat frozen typification experiments establish the measured cache improvement; native times above are recorded outcomes, not evidence of an end-to-end speedup.

| Balanced coupon | Input polygon records | Represented records | Output forms | Omitted records | Unresolved |
|---|---:|---:|---:|---:|---:|
| Downtown | 2,014 | 816 | 816 | 1,198 | 810 |
| Neighborhood | 7,668 | 1,103 | 227 | 6,565 | 199 |
| Landmark | 2,257 | 577 | 511 | 1,680 | 507 |

These are polygon/part records, not necessarily distinct OSM identities or disconnected printed solids. Input clipping and parent/part dispositions remain explicit in each audit. No new source omissions are caused by the cache. Compared with the frozen Balanced coupons, all base, route and building geometry is identical; corrected neighborhood and landmark road meshes differ as expected.

Fresh slices use the validated A1 0.4 mm nozzle / 0.42 mm outer width / 0.16 mm Quality / PLA Basic profiles. Sampled above-base toolpaths occur in 605/816 downtown forms, 114/227 neighborhood forms and 337/511 landmark forms. All 28 neighborhood and all four landmark typified masses are matched. Unmatched unresolved forms are retained in the diagnostic list; sampled presence is not silhouette, gap, strength or physical acceptance. The updated [print checklist](city-relief-print-checklist.md) points to `supported-slicer/`, preserving the old incomplete slice as historical evidence.

The Existing downtown and landmark geometry matches completely. Existing neighborhood buildings and base match; its road-only change replaces the old simplification fallback with exact indexed recovery. The old footprint differed from its frozen extrusion input by 0.17148 mm² (symmetric difference), with at most 0.00204 mm boundary displacement relative to the repaired model. The new serialized footprint matches that input exactly. This is a documented road repair, not a City-cache change to unrelated Existing geometry.

Physical coupon acceptance and landmark recognition remain owner-controlled.

The 35% replay gain cannot be applied directly to the previous 155-minute full run. Full cache hit rate may differ; acquisition, footprint preparation, width diagnostics, meshing and export are unaffected. The next full run must measure actual stage timings and peak memory using the same frozen inputs. Follow the owner handoff's stop-before-full-validation approval gate after the bounded package checks complete.

## Visual findings and targeted follow-up

The actual downtown coupon has a readable skyline and a prominent route. Narrow preserved towers remain a physical-print concern. The neighborhood coupon has clearer low-rise forms and a calmer height hierarchy than Existing, but still reads as scattered masses within a strong street grid. Its 7,668 sources become 28 typified output masses plus 199 unresolved forms; 6,378 sources have `no_safe_city_representative` dispositions. Dense adds only four typified masses. This is a specific remaining representation gap, despite 34 of 36 centroid-occupied cells being represented (94.44%). Cell coverage does not measure convincing inhabited density or toolpath survival.

Before implementing a representation change, inspect candidate rejection reasons for those occupied blocks: distinguish street/protected-building conflicts, occupied-envelope limits and insufficient candidate placement. Then prototype splitting a source-supported cluster into several compact source-derived subsets, with each feasible representative placed using that subset's actual source distribution. Preserve the exact geographic exclusions, occupied-envelope bounds, stable source mappings, parent/part relationships and height provenance. Compare against the same frozen block and actual A1 toolpaths. This is a proposed follow-up, not implemented synthetic infill or an approved new preset. Balanced remains provisional.

The landmark coupon retains a prominent route and varied building heights, but it does not establish recognizable City Hall or Concert Hall silhouettes. The City Hall parent relation's footprint is omitted for route exclusion; surviving assembly parts retain the existing 3.54 mm assembly top. Concert Hall's surviving footprint is 0.3702 mm² with a 0.4872 mm core and a 4.94 mm visible top, and remains unresolved. The accurate route's physical width and exclusion corridor remove substantial source geometry at this scale, so a taller fragment cannot be assumed to restore the source silhouette. The unmet 1.5× enlargement targets remain unchanged. Any roof or massing follow-up must use the feasible source-derived assembly and respect aspect-ratio and corridor constraints; no forced enlargement or bespoke model was added.

## Recommendation

The reviewed package is technically ready for a full LA Balanced engineering retry: exact road recovery, native coupon/export support, source accounting and unrelated Existing compatibility now pass, and a meaningful controlled neighborhood optimization is measured. Use this exact executable and unchanged frozen inputs; record full stage timings, peak memory, acquisitions, dispositions, coverage and serialized geometry. The bounded 35% improvement is not a prediction of the full runtime.

Stop at this checkpoint for the owner handoff's approval before another expensive full building run. A successful retry would establish complete full-map engineering evidence; it would not approve the provisional density, landmark recognition, fragile unresolved forms, physical prints, merge or release. Current comparison and test-print artifacts are in `benchmarks/outputs/city-relief/supported-review.html` and `city-relief-recovery-review.zip`.
