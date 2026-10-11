# LA Balanced packaged validation — October 11, 2026

**Completed, but not accepted.** The approved full LA run produced buildings and a matching 3MF/audit, but silently omitted the requested Roads layer. The candidate is diagnostic evidence, not a print-ready deliverable. No merge, release, installed executable replacement, or physical print has been performed.

## Results

| Measurement | Result |
|---|---:|
| Native generation time | 9,295.31 s (154.92 min) |
| Launch-to-exit wall time | 9,328.37 s (155.47 min) |
| Maximum parent/child process peak working set | 7,068,565,504 bytes (7.07 GB) |
| Acquired unique source elements | 421,196 |
| Decoded source polygon records | 421,240 |
| In-frame records | 420,116 |
| Working footprints before optimization | 419,931 |
| Represented source polygon records | 67,790 |
| Output building forms | 11,336 |
| Sources in neighborhood masses | 58,684 |
| Neighborhood masses | 2,230 |
| Omitted polygon records | 353,446 |
| Unresolved records | 9,034 |
| Occupied source grid cells represented | 2,084 / 2,580 (80.78%) |

Coverage measures source/output centroid occupancy on a 4.2 mm grid. It does not measure silhouette preservation, area coverage, or physical strength. Polygon records and unique OSM elements are different units. Unresolved printability is retained in the audit.

The frozen GPX, roads, manifest and 66 cache files remained unchanged across the run. All 30 building tiles came from the validated cache. Every represented source has valid output face ranges; no represented records lack faces. The audit model hash matches the exported 3MF: `2dfc248cd60e6c229c456052936b4d42a73a3096999e875df0538941c75c4226`.

The main omission reasons are 341,327 records with no safe City representative, 6,428 in route exclusion, 4,385 isolated below the print target, 663 at the frame clip threshold, 457 outside the frame, 185 replaced by building parts and one unsupported export shell. Four null/unsupported records remain unresolved; another 9,030 unresolved printability decisions are retained for inspection.

Prepared-source mapped heights have median 1.92 mm, 90th percentile 2.373 mm, 99th percentile 3.543 mm and maximum 7.55 mm. These statistics cover all 419,931 prepared footprints, including omitted records; they are not the output-form height distribution. Height provenance comprises 396,004 explicit heights, 1,876 level estimates, 14,700 class estimates and 7,351 fallbacks.

Independent inspection found five present meshes closed, consistently wound, finite and of positive volume. GLB faces and float32 vertices match the exported meshes. Support validation passes for those present meshes. The independent verifier fails overall because Roads is missing. Building/route projected overlap is approximately 9.64e-14 mm², below the generation tolerance of 1e-6 mm².

## Timings and performance diagnosis

| Building stage | Seconds |
|---|---:|
| Barrier preparation | 39.87 |
| Source loading | 90.58 |
| Footprint preparation | 508.58 |
| Optimization | 7,221.46 |
| Supports and manual grouping | 21.71 |
| Extrusion | 48.94 |
| Width diagnostics | 1,012.51 |
| Assembly | 12.30 |
| Route clearance | 2.99 |

These are exclusive building pipeline stages. Nested optimization timers must not be added again. Remaining total time includes roads, route, context, export and preview work that was not separately instrumented.

The earlier spatial-mask optimization reached the same 100,800 / 400,061 candidate checkpoint in 2,261.31 seconds versus 3,144.78 seconds, a measured 28.09% reduction to that checkpoint. This is not a measured full-run speedup.

The final fallback interval lasted approximately 3,142.93 seconds after the last 400,000-candidate counter. It includes remaining first-pass candidates and bookkeeping. In a 10-second fallback sample, buffering occupied neighborhoods accounted for 84.37% of leaf samples; one occupied-envelope coverage test accounted for 81.96% of all samples. Width diagnostics also cost about 16.9 minutes.

An exact bounded occupied-envelope cache was evaluated on three frozen smaller inputs. Median time changes were +0.58%, +1.82% and -1.24% improvement, with identical output digests. The experiment was removed because it did not establish a meaningful gain. No full-run estimate is justified from those results. Next performance work should capture representative expensive fallback cases and preserve exact envelope/collision rules.

Historical Existing generation took 5,328.82 seconds and peaked at 6.36 GB. City optimization and extrusion exceed twice their historical stage times and remain investigation gates. This comparison is diagnostic: the baseline used Python, had differing concurrency and preview timing scope, and is not a controlled packaged before/after benchmark.

## Road failure and scoped correction

Both major and local road hierarchy regions were GeometryCollections containing valid polygons alongside line/point contacts. The mesher handled only Polygon/MultiPolygon containers and skipped both entire regions. The source roads-only replay confirmed 34 major polygons and 59,486 local polygons, with roughly 10,987.24 mm² total polygon area.

The correction recursively visits polygon members without altering exclusion geometry or height bands. It also records terminal triangulation failures and rejects requested road acquisition/processing failures before building optimization or export. Previous successful files remain intact. Native failure reports include layer state, build identity and bounded failed-fragment geometry diagnostics.

An exact cached-region replay now produces 2,018,392 faces in 79.57 seconds, with returned meshes closed and consistently wound. However, the established triangulation fallback still reports 32 invalid polygon fragments. Net projected area is 171.21 mm² below eligible input polygon area; that difference includes existing simplification and cannot be attributed solely to those fragments. The new guard must fail this input rather than silently accept partial roads. Further exact road meshing work is required before a full retry.

The separately packaged correction matches tested source modules and assets, with executable SHA256 `8d6d381bf87911921914f320d3e7a67fea90c2cfedc910ca4ade04d8b577d782`. Its native roads-only LA proof completed in 309.79 seconds with a 1.85 GB process peak. Acquisition was complete for all 146,546 frozen source road features. It explicitly failed Roads, retained all 32 fragment geometries (171.294 mm² total failed-fragment area), and produced neither a new 3MF nor an audit. This passes the failure-reporting check, not the full-map acceptance gate. The full test suite passes 497 tests.

The frozen Balanced neighborhood coupon also fails the new guard on one road fragment, with no new model exported. Its historical successful export/slice therefore does not establish complete road geometry. Repair and re-slice the affected coupon before physical acceptance. Failed full-road fragment areas range from 1.75e-6 to 77.46 mm²; the issue includes substantial polygons, not only microscopic contacts.

A separate synthetic offline native fixture passes with Buildings, Roads and Route complete, all eight source buildings represented, and six serialized meshes verified for closure, winding, finite vertices, positive volume, support and export/preview equality. Its 3MF hash matches its audit. The packaged Qt, generation and WebEngine smoke check also passes. These are positive compatibility checks, not substitutes for a successful real LA coupon or full map. Detailed current measurements are in [the compact metrics file](city-relief-full-validation-metrics.json).

## Visual and physical assessment

The incomplete City candidate has lower, more robust neighborhood forms and a clearly visible orange route compared with the needle-like historical Existing relief. The top view shows a readable route but weak context in some areas. Without roads, street-block identity and the coordinated visual hierarchy cannot be accepted. Sparse regions need source-based examination before any targeted representation change. Landmark recognition is not established by these full-map renders.

The prior three Balanced coupons and A1 slicing evidence remain available for owner inspection with a 0.4 mm nozzle and 0.42 mm outer line width. They predate this road correction. Toolpath presence is not silhouette or strength acceptance; changed road geometry requires renewed slicing evidence. Physical acceptance remains pending under the existing print checklist.

## Recommendation

**Do not start another full LA generation yet.** First validate exact meshing of the recorded road failures and benchmark the dominant neighborhood fallback cost on representative frozen cases. Then provide a measured checkpoint and request owner approval for another expensive full run. Existing remains the default; City remains opt-in with Sparse/Balanced/Dense. No automatic switching, geographic distortion, synthetic infill, Shopify changes or control redesign was introduced.

Local evidence lives in `benchmarks/outputs/city-relief`: `serialized-export-verification.json`, `building-loss-breakdown.json`, `full-performance-comparison.json`, `full-validation-state.json`, the two profiler samples, `performance/envelope-cache-evaluation.json`, and `road-diagnostic`. The original candidate, audit and renders are preserved under `packaged-full`; the review page marks them incomplete.
