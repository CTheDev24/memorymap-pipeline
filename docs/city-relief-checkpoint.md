# City relief performance checkpoint — October 10, 2026

**October 11 update:** the owner approved the run below. It completed but failed required Roads-layer acceptance. This is the historical pre-run checkpoint; use [the completed validation report](city-relief-full-validation.md) for current status. Another full run is not ready.

**Recommendation: approve one controlled, freshly packaged full LA Balanced validation. Do not release or merge. No new full LA run has started.** Smaller frozen benchmarks demonstrate a meaningful improvement with identical representations, but full-frame runtime, memory, visual quality, and physical acceptance remain unverified.

## 1. Current implementation

The working tree was clean at `1aac55f` when development resumed on `codex/city-relief`; no additional local implementation was found. Existing acquisition recovery, relation assembly, representatives, height controls, landmarks, schema migration, stale-result protections, and export/audit reconciliation were preserved.

New work is limited to City typification mask-query performance, exact-threshold regression coverage, a reproducible frozen-state benchmark, this report, and owner print-review preparation. Existing remains the default; City remains an operator-selected appearance with Sparse/Balanced/Dense detail. No automatic switching, UI redesign, Shopify changes, geographic distortion, synthetic infill, installation, merge, or release was performed.

The earlier packaged executable is **not** the optimized build. Source identity and detailed measurements are recorded in [checkpoint metrics](city-relief-checkpoint-metrics.json). The historical full Existing export and the paused full City checkpoint remain intact.

## 2. Bottleneck and measured optimization

The paused full-frame 10-second sample recorded 293/499 leaf samples in polygon intersection and 73/499 in spatial queries. The bounded neighborhood reference profile confirms repeated fixed-mask exclusion checks dominate: `AreaMask.exceeds` accumulated 9.27 seconds of a roughly 14-second profiled replay; overlay calls accumulated about 7 seconds. These cumulative costs overlap.

Each seed can try 18 rectangles, repeatedly overlaying small candidates against relatively complex partition polygons. The implementation also queried that mask before checking whether the candidate intersected the complete prepared exclusion geometry. Accepted representative indexes are rebuilt during placement; their growth remains a potential later full-map cost, but these measurements do not establish it as the dominant bottleneck.

City typification now:

1. Uses exact mask partitions with a 128-coordinate target, rather than 2,048. The existing depth limit and original-geometry threshold fallback remain.
2. Stops summing nonnegative partition overlap once it exceeds the rejection threshold **plus the existing numerical fallback margin**. Subsequent pieces cannot reverse rejection. Decisions near the threshold still use the original geometry.
3. Checks the prepared full mask for intersection before requesting local overlay area.

There is no simplification, snapping, approximate exclusion, changed corridor, changed source ownership, or altered candidate order. Default `AreaMask` callers retain the previous partition size and complete overlap summation; the new early exit is opt-in for City typification. Existing geometry logic is otherwise untouched.

## 3. Frozen benchmark results

Each case used the same captured pre-typification state before/after, three serial unprofiled repeats, and a separate profiled replay. The reference City module comes from local Git commit `1aac55f`, using the helper's preserved default mask behavior. Earlier exploratory uncapped/overlapping attempts were stopped and excluded from these results.

| Case | Scope | Reference median | Optimized median | Reduction | Peak working set, before → after |
| --- | --- | ---: | ---: | ---: | ---: |
| Low-rise neighborhood | First 1,000 of 7,282 eligible seeds; 7,564 source footprints | 12.59 s | 6.94 s | 44.8% | 176.56 → 177.31 MB |
| Downtown | Complete typification, including fallback; 970 eligible seeds | 17.55 s | 7.85 s | 55.3% | 136.27 → 135.88 MB |
| Landmark area | Complete typification, including fallback; 1,106 eligible seeds | 17.06 s | 8.20 s | 51.9% | 141.97 → 140.17 MB |

All output fingerprints match across reference/optimized repeats: footprint WKB, groups and membership, heights, omissions, and disposition/provenance decisions. The neighborhood checkpoint intentionally excludes later placement and connected fallback; it is not a complete neighborhood result. Peak working set includes checkpoint loading, copies, and profiling, and is not full-map generation memory.

Candidate counts were corrected during packaged validation to exclude courtyard and non-Polygon sources, matching the actual eligibility predicate. The earlier counts described all isolated-below-target dispositions. Timings and output-equivalence results are unchanged.

In the neighborhood profile, mask-query cumulative time fell from 9.27 to 3.68 seconds. Mask preparation rose from 0.12 to 0.40 seconds: smaller partitions exchange setup work for cheaper repeated overlays. Full-frame setup cost and memory need measurement.

**492 tests passed**, 84 warnings, in 28.42 seconds. Scoped Ruff checks and `git diff --check` passed. Coverage includes partition/original overlap agreement, holes and touching boundaries, threshold fallback, early rejection, City ordering/exclusions/heights, Existing compatibility, project migration, stale outputs, layer failures, support, and export/audit behavior. No new packaged provenance or full-map acceptance is claimed.

Reproduce captures with `tools/benchmark_city_typification.py AREA --capture`, using the existing local frozen fixtures. Replay with `--label baseline --reference-commit 1aac55f` or `--label optimized`. Use `--max-candidates 1000` for the neighborhood checkpoint and `--max-candidates 0` for complete downtown/landmark replays; use `--repeats 3`. Checkpoints are trusted local pickle files and must never be loaded from an untrusted source. Raw profiles and logs remain under `benchmarks/outputs/city-relief/performance/`.

## 4. Expected impact and remaining risks

The **measured** result is approximately 45–55% less typification time on these scopes, with unchanged output. The **expectation** is cheaper rejection checks across LA. There is no defensible full-map completion estimate yet: the earlier 52-minute checkpoint includes other stages, and full-frame mask size, candidate distribution, index growth, export, and memory costs differ. The 5,329-second / 6.36 GB Existing baseline is historical context, not a City target or a controlled native-vs-Python benchmark.

Geometry risks remain numerical behavior on much larger partition masks and untested later full-map candidates. Preserve exact threshold fallback and compare geometry/support/audit counts after the full run. Acquisition, source-to-output reconciliation, route clearance, and explicit failure handling must remain enforced. Do not interpret a completed export as physical acceptance.

Visual review of the existing perspective renders shows Balanced adds wider inhabited masses and reduces the extreme thin-tower effect of Existing. Dense looks similar in this neighborhood crop: it represents more sources without a proportionate visual increase in distinct forms. Thin preserved structures remain prominent and potentially fragile. Sparse has not been evaluated in this checkpoint. Neighborhood route prominence cannot be assessed where the route is outside the crop; the landmark crop's route is prominent. Full-map street hierarchy, genuine open spaces, and neighborhood coverage still need inspection at actual print size.

City Hall and Disney Hall remain constrained at feasible source scale, with unmet enlargement and thin geometry warnings. Their silhouettes are not visually approved. A targeted follow-up should inspect source part/roof envelopes and surrounding mass separation before proposing a coherent source-derived landmark mass. Simply increasing height may worsen fragility; forced footprint growth remains prohibited. No landmark representation changes were made here.

## 5. Owner print review

`benchmarks/outputs/city-relief/physical-review.zip` contains the three previously validated Balanced sliced/source 3MFs, matching source audits, slicer result records, A1/PLA/0.16 mm profiles, SHA-256 manifest, and [inspection checklist](city-relief-print-checklist.md). Outer line width is 0.42 mm with a 0.4 mm nozzle. These files preserve the earlier verified geometry; they are not a new optimized package acceptance claim.

Inspect distinct masses, unintended merging, fragile/missing details, roads/route clarity, landmark recognition, and normal-distance/side-light appearance. Record photos and approve or reject each coupon. No physical print was initiated. Toolpath presence alone does not prove silhouette survival or reliability.

## 6. Proposed approved next step

After owner approval, build a clean candidate from the optimized source, verify bundled provenance and offline smoke behavior, then perform **one** full LA Balanced generation on the exact frozen frame, route, roads, buildings/context cache, and 0.42 mm line width. Run it serially without other generation/slicing jobs. Do not invoke `build_validate_city_relief.ps1` before approval: it continues automatically into full generation.

Record setup/optimization/extrusion/validation/export timings and OS peak memory, inspect progress around the old 100,800-candidate checkpoint, and investigate stage or memory regressions rather than launching repeated full runs. Produce the hash-matched 3MF/audit, dispositions and source mappings, coverage and height distributions, support/route checks, top/perspective renders, and comparison with the actual exported Existing baseline. Keep historical baseline cleanup/provenance caveats visible.

Approval for this validation would not authorize a merge, installed executable replacement, release, or physical acceptance. Those remain separate owner gates.
