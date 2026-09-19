# Initial footprint validation — September 19, 2026

The validation harness is implemented. Building grouping and physical print
acceptance remain subsequent work. The evidence already demonstrates that a
supported, watertight building can disappear during slicing.

## Synthetic slicing evidence

All 11 controls were sliced locally with Bambu Studio **02.06.00.51**, installed
**Bambu Lab X1 Carbon 0.4 nozzle**, **0.16mm Optimal @BBL X1C**, and **Generic PLA**
presets. This is a provisional reference profile, not a confirmed user printer.
The helper resolved preset inheritance/includes and saved the complete settings
and sliced projects. It sent no printer jobs.

| Control | Actual layers | Last layer Z (mm) | What this establishes |
|---|---:|---:|---|
| 0.2 mm square, 4 mm tall | 10 | 1.64 | Column disappears above the base |
| 0.4 mm square | 35 | 5.64 | Paths reach the intended height |
| 0.6 mm square | 35 | 5.64 | Paths reach the intended height |
| 0.8 mm square | 35 | 5.64 | Paths reach the intended height |
| 1.2 mm square | 35 | 5.64 | Paths reach the intended height |
| 8 × 0.3 mm rectangle | 10 | 1.64 | Rectangle disappears above the base |
| Broad masses with 0.3 mm neck | 35 | 5.64 | Height survives; neck continuity needs inspection |
| 0.3 mm courtyard walls | 10 | 1.64 | Walls disappear above the base |
| Two close 0.4 mm squares | 35 | 5.64 | Height survives; separation needs inspection |
| Boundary-cut 0.2 mm strip | 10 | 1.64 | Strip disappears above the base |
| Broad 4 mm body beside thin 8 mm tower | 60 | 9.64 | Paths reach tower height |

The slicer lifts the 1.6 mm base onto the bed. Thus a 4 mm column would reach
approximately 5.6 mm total Z. Layer heights here are read from actual `Z_HEIGHT`
records in the archived G-code, not model thumbnails or the planned layer count.
Small rounding to 5.64/9.64 mm follows the slicing layers. No physical strength
claim follows from reaching a height. The 0.8 mm screen is deliberately conservative;
the 0.4 and 0.6 mm controls show why it is not an exact slicer pass/fail boundary.

Local artifacts: `outputs/synthetic-v1/`, including
`bambu-x1c-pla/slicing.json` and per-specimen `sliced.3mf`, settings, commands and logs.
Slicing-report SHA-256:
`0cd488ac7d31e8c5adf7acae12458fd7ae3bcde59f23bb5ebddcb6108b01464c`.

## Chicago source snapshot

The provisional 2025 community course and OSM data were frozen locally at
`downloads/chicago-2025-provisional/snapshot/`. They contain **9,925 building
features, 11,392 road features and 43 water features** selected through three
test windows plus a halo. This is not a complete city inventory or a recovered
copy of the earlier Chicago experiment. See the manifest's coverage note.

The full GPX determines a horizontal scale of **0.01686364 mm/m**. A 0.8 mm
structural target therefore corresponds to approximately **47.44 real-world meters**.
This explains why ordinary individual neighborhood buildings require investigation
at this scale. The figure is a scale conversion, not a proposed merge distance.

Manifest SHA-256:
`e6f38bdd93ea7a6dbd4d113d55046425f6aa7d6187c1dd31b12999303dc45858`.

The initial full-map export rejected two floating building shells, the largest with
508 faces, after removing two tiny unsupported shells. Generated source diagnostics
contained 9,881 retained polygon records, 48 omitted records and two unresolved
records (polygon counts differ from source-feature counts because of multipolygons).
This is a separate structural blocker from minimum footprint width.

The two unresolved source records are `b000011-p1` (OSM ID `15951428`) and
`b001197-p1` (OSM ID `148164112`), both recorded as `mesh_failed`. Two elevated
508-face source elements are `b000343-p1` / `1177923717` and `b009351-p1` /
`284822924`; these are useful investigation candidates, not independently established
root causes of the support failures.

The completed baseline is `outputs/chicago-2025-baseline-v4/`. Crops retain the
full-course horizontal scale and generated building heights.

| Specimen | Source polygon records | Width-screen flags | Export / slicing outcome |
|---|---:|---:|---|
| Downtown | 1,208 | 1,201 | Rejected: two floating building shells, largest 508 faces |
| Dense neighborhood | 3,130 | 3,128 | Exported without building-face removal; Bambu generated 85 layers, last Z 13.64 mm |
| Sparse neighborhood | 3,623 | 3,623 | Blocked: cropping a building shell produced a non-watertight mesh |

Flags identify risk for inspection, not proven slicer failures. The dense specimen's
successful slice does not establish that individual buildings or connecting necks
survived. Its sliced project and resolved reference presets are retained under
`outputs/chicago-2025-baseline-v4/bambu-x1c-pla/`. The sparse failure was reproduced
from the saved `building-mesh.npz`, isolating it to the building crop rather than
the base or roads. The harness preserves this failure instead of exporting an open
shell or silently dropping it.

Automated regression validation passed **84 tests** across the benchmark,
printability, generation integration, pipeline, and building-parts/roof suites.
These tests validate the harness and existing geometry behavior; they do not
substitute for per-feature toolpath inspection or physical printing.

## Confirmed course and geometry repairs

The user confirmed the X1 Carbon, 0.4 mm nozzle, PLA and 190 × 240 mm model.
The supplied GPX has the same 163 ordered route coordinates as the provisional
course. Its file SHA-256 is
`ee42f2729d4fe337e27738effabf92a5f8632460cbc592a8af7eb5f4fd055ea1`.
The 5 mm margin means clearance from the **outer route edge** to the model edge.
The benchmark now records `route_clearance_mm` separately from physical trim:
map content can reach the edge, while route scale reserves 5 mm plus half the
1.2 mm route width. It preserves aspect ratio, so clearance can be larger on one axis.

The corrected snapshot is `downloads/chicago-2025-confirmed/snapshot-clearance/`.
Its manifest SHA-256 is
`cf060dffb36fca5d403318f9fe338a1a3aff67dc8976c8a52de78c7d86672aa6`.
Earlier snapshots and failed runs remain unchanged. In the corrected model, route-edge
clearances are approximately 5.00 mm north/south and 55.00 mm east/west; the wider
side clearance follows from preserving the course's proportions.

The final generation retained 9,883 polygon records and omitted 48 replaced/clipped
records, with **zero unresolved source geometries**. Numerical cleanup affected two
boolean remnants of approximately 2.75e-16 and 1.70e-17 square millimeters; the
other geometry from both source records remains retained.

The Chicago investigation produced these repairs:

- Partition a courtyard that touches its exterior at one point into closed solids,
  preserving the courtyard and total volume.
- Preserve positive-area triangulation slivers needed to close near-collinear footprints.
- Remove collapsed crop triangles only when the cut shell is open; closed shells
  retain their existing connectivity.
- Verify occupied volume when a support overlap exceeds the audit's 5 mm shortcut.
  The large downtown parts overlap grounded solids; their earlier floating-shell
  reports were false positives. Tests still reject a floating part beneath an arch.
- Preserve the thickness of elevated parts when height mapping requires lowering
  them to meet a supporting body. This separate regression was caught by the crown test.
- Record and remove boolean-operation residue at coordinate-roundoff precision,
  before extrusion. This is not a minimum printable footprint filter.

All layer meshes are archived in `layer-meshes.npz` for repeatable diagnostics.
Automated validation passed **99 tests**, including the new Chicago regressions,
route-edge clearance, deep-overlap and unsupported-arch cases.

The completed run is `outputs/chicago-2025-confirmed-v4/`. The full-map model and
all three crops export successfully, with **zero building faces removed by export**.
The original scale and crop sizes are preserved. This supersedes the initial
geometry blockers above, but does not establish small-feature survival in slicing
or physical printing.

All three corrected crops generated toolpaths with Bambu Studio 02.06.00.51,
X1 Carbon / 0.4 mm / 0.16 mm Optimal / Generic PLA. Archived sliced projects,
resolved settings, G-code and `slicing.json` are under the run's `bambu-x1c-pla/`.

| Crop | Actual layers | Last layer Z (mm) |
|---|---:|---:|
| Downtown | 124 | 19.88 |
| Dense neighborhood | 85 | 13.64 |
| Sparse neighborhood | 41 | 6.60 |

Known printer/slicer details and artifact paths are populated in `evidence.json`.
All per-feature observations remain pending. The acceptance checker still returns
`not_accepted`, with no unresolved/export/model-identity/profile failures: toolpath
inspection, omission acknowledgment and physical evidence are still required.

## What remains before capability acceptance

- Review paths at narrow necks, upper sections and roofs; preserve street/route/water
  separation rather than relying on layer-count summaries.
- Print the synthetic controls and full-route-scale Chicago specimens using the actual
  printer/material. Record physical outcomes and photographs in the evidence files.
- Validate the opt-in grouping candidate against the same snapshot, then repeat
  toolpath inspection and physical printing.

All physical-print observations remain pending. Generated/downloaded artifacts are
local and ignored by Git; retain the snapshot and output folders with this record.


## Opt-in building grouping implementation

Studio now exposes **Group small buildings (0.4 mm nozzle)**. Eligible low buildings
are combined into bounded masses targeting 0.8 mm width. Public roads, route, water,
protected buildings and existing courtyards constrain expansion. Tagged alleys and
driveways are omitted in grouping mode; local streets use 0.4 mm width. Isolated or
barrier-constrained sources remain individual and are counted in diagnostics.
Acute tips that fail the local-width opening are replaced by a containing oriented
rectangle only when the same area, span and barrier limits permit it.

The full automated suite passes **316 tests**. The final candidate benchmark and
Bambu slicing results will be recorded below when complete. Physical acceptance
remains pending; geometry screens alone are not proof of printable toolpaths.
