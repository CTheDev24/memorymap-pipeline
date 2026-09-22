# Peachtree exported-model comparison

Compared user-supplied `peachtree.3mf` and `peachtree-grouping.3mf` on 2026-09-21.
Both use millimetres, a 190 x 240 mm base and identical route bounds. The user
confirmed reducing maximum building height to 16 mm in the grouped export.
This is therefore a comparison of the two delivered models, not a controlled
measurement of grouping alone; height mapping also affects grouping eligibility.

| Metric | Original | Grouped |
| --- | ---: | ---: |
| Connected building mesh components | 27,290 | 18,513 |
| Components with measurable projected footprint | 25,295 | 16,556 |
| Measurable footprints without a 0.4 mm core | 21,598 | 9,607 |
| Measurable footprints without a 0.8 mm core | 24,532 | 11,593 |
| Summed component footprint area (mm²) | 7,569.7 | 10,119.4 |
| Highest building Z above base (mm) | 25.92 | 13.83 |

Measurable footprint count falls 34.5%; the count below the 0.8 mm screening width
falls 52.7%. Approximately 70.0% of measurable grouped-model footprints still fail
that width screen. Grouping is effective but does not eliminate the small-feature
problem on this full-course map. Added footprint area is consistent with joining
and widening masses; it is not a count of newly introduced buildings.

Method: read the building object directly from each 3MF, weld coincident vertices,
find face-adjacent connected components, project nonvertical triangles to XY and
union them per component. An empty inward buffer at half the stated width flags a
component. Components without projected area are excluded from width counts.
These are mesh-component metrics, not OSM building counts or sliced extrusion
islands. Separate roof pieces and coincident mesh contacts can affect counts.
No source classifications or generation configuration were present in the 3MF,
so these counts cannot establish how many components are residential homes.
No slicer or physical-print acceptance is inferred from this geometry screen.

Local analysis output and script are in `outputs/peachtree-comparison.json` and
`outputs/compare_peachtree.py`; the original files were not modified.

## Follow-up controls

Road categories now select visible roads while hidden public streets remain
barriers for grouping. The optional residential width filter runs after grouping
and defaults off, with 0.8 mm suggested for testing. It preserves unclassified
buildings, landmarks, building parts, elevated buildings and courtyards, and records
intentional omissions. Remaining-small reason counts before filtering are labeled
`remaining_small_by_reason_before_filter` when omissions occur.

Layer colors use only Finish and Route. Gallery is stone (#CAC6BC); Nocturne is
charcoal (#292A2B) with dark-gray roads (#505254). Neutral uses charcoal on Gallery
and stone on Nocturne. Accents: Signal Orange #F7591F, Volt Lime #C7FF00, Pulse Pink
#FF3B8D, plus a custom gradient picker. The generation action explicitly changes
to Retry generation on failure and Regenerate map on success. Single-layer
regeneration remains deferred.

Input SHA-256 hashes:

- peachtree.3mf: `41f43d91c494641add01fcfbc987f0dd803d4191625bb5b157c2f30928308212`
- peachtree-grouping.3mf: `e8d87a41ce13fbee32990bd07bf091342d26081b3c5ec2d025aeb28fef04f202`

Validation of the control/filter additions: **353 tests passed** (65 dependency
warnings), including all supported finish/accent combinations, post-grouping
omission, preserved protected structures, hidden-road barriers, and retry state.
A Qt controls-only smoke run verified the actual widget payloads and rendered
layout. The supplied Peachtree files were analyzed, not regenerated with the new
residential filter; omission counts for that next run are not yet known.
