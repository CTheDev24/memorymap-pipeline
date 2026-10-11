# Map Style review fixes — October 11, 2026

## Code completion

Started on `codex/map-style-audit` at the exact reviewed head
`5290b7deb01b383f92c09ef4da05a057eed61fce`, with a clean working tree. No newer
work was present or overwritten. Established Urban/Landscape engines, the three
style mappings, opt-in City Relief, independent urban road classes, uniform road
relief and route separation are unchanged.

- `b242d3b`: preserve `roads_layer` in per-style snapshots, apply Road context
  defaults only on first visit, synchronize water controls without modifying
  restored values, and enable Local width from every selected member road type.
  Visible help and tooltip explain the shared Local width and mesh.
- `b58871a`: invoke Linux desktop tests through `python -m pytest` so repository
  tools are available, following the observed collection failure in the first run.
- `e39b4a6`: retain and print Windows smoke diagnostics on failure.
- `82e6b1a`: retain timeout evidence and support timeout cleanup in Windows
  PowerShell 5 as well as PowerShell 7, without weakening the smoke gate.
- Legacy Border and Route Layer Height controls remain internal and persisted.
  Project-loading synchronization no longer clears a saved water flag as a side
  effect of enabling/disabling controls. Existing project schema remains v5.
- Package provenance verification now also checks `map_styles` and
  `street_selection` against their bundled source modules.

## Tests and CI

- Focused real-Qt building-height/desktop tests: **26 passed**.
- Full local Windows suite: **526 passed**, **0 skipped**, 105 dependency/existing
  parsing warnings, 30.33 seconds. XML confirms execution of the stale-result,
  per-style Road context, water synchronization and both Local member-type cases.
- Linux CI at `b58871a`:
  [run 38175724763](https://github.com/CTheDev24/memorymap-pipeline/actions/runs/38175724763)
  passed both core and desktop jobs. Core: **509 passed, 17 intentionally skipped**
  desktop-only cases. The desktop job installs PySide6, imports the real window,
  runs **526 tests without skips** and rejects skipped/missing review regressions.
  Linux CI also passed at `e39b4a6` in
  [run 38176266832](https://github.com/CTheDev24/memorymap-pipeline/actions/runs/38176266832).
- Local core CLI route/base export passed without downloading map data.
- Scoped Ruff checks and `git diff --check` passed.
- Windows hosted CI passed all 526 tests and built the executable, but the strict
  packaged preview smoke failed because WebGL was unavailable on the runner:
  [diagnostic run 38176266847](https://github.com/CTheDev24/memorymap-pipeline/actions/runs/38176266847).
  The retained report verifies real window creation, model/preview export, correct
  system ICU loading and page load, then explicitly reports no WebGL canvas.

## Windows review executable

`dist/map-style-reviewed/Trace Studio.exe` was built separately from the installed
application. SHA-256:
`75de864759a1cd5fec81af6fcbfc8607523b866d667fe1394f2f73108328fa97`.
`package-provenance.json` confirms every checked bundled module/asset matches the
current source, including both Map Style algorithms and the fixed desktop window.

The first local packaged smoke completed with **exit 0**, real Qt 6.11.2,
proportional-height and City Relief fixture generation, system ICU loading, and
a ready WebEngine canvas. The standard **120-second exit gate did not pass** in
the second cold run, despite a passing rendering/generation report. Windows
PowerShell 5 initially also hit the inherited unsupported `Kill(bool)` overload;
the script fix above preserves that timeout as a failure and cleans up correctly.
The specifically identified review process was stopped; the installed Trace Studio
processes were not touched. Packaging is therefore **not fully qualified**.

Remaining package checks: run the unchanged strict rendering gate on a GPU-capable
Windows runner (or validate a controlled software-WebGL CI environment); investigate
local cold-start/shutdown time and rerun the bounded exit gate. Do not relabel the
hosted no-WebGL failure or local timeout as a package pass.

## Representative exports and slicer evidence

Artifacts live under `benchmarks/outputs/map-style-review-fixes/` (generated files
are not committed). `representative/review.html` and `map-style-review.zip` provide
portable top/perspective/side baseline comparisons. `ui/` contains actual Qt
screenshots. Fixtures are representative, not Houston/Big Sur/Chicago race maps.

| Style | 3MF SHA-256 | Components |
|---|---|---:|
| Cityscape | `56c9730f34163820c7dcec67e581f1a0e072a7e985ada6e1897f4494a46766f8` | 7 |
| Terrain | `a3f5fc465935592f80b2c76032ca8d47fcc166dcc0d11bf1d034c834c9d5f496` | 4 |
| Street Grid | `daae0374c8cbc8d7345a0269dc2c08d9b0a05cd0a64958e9313a485a3318d143` | 6 |

All audit hashes match their source 3MF. Baseline/candidate vertices and faces for
base, route, buildings, terrain and water are identical where present. Cityscape
includes buildings; Street Grid excludes them. Urban road bodies are independently
named in the source package and use 0.8 mm uniform visible relief.

Bambu Studio **02.06.00.51** CLI sliced all three candidates successfully with the
existing **Bambu Lab A1 0.4 nozzle**, **0.16 mm High Quality** profile and **0.42 mm
outer wall line width**. `slicer/registration.json` verifies:

- One registered assembly and unchanged component counts.
- 240 × 190 mm dimensions; Terrain is rotated together to 190 × 240 on the plate.
- Component vertex registration within 0.00002 mm, before the whole-assembly bed
  placement/rotation. Parts were not independently auto-arranged.
- All components successfully sliced on the same filament. This is CLI evidence
  for shared material, not an interactive independent-assignment test.

**Slicer qualification is incomplete.** Bambu CLI rewrites standard object names
to `MemoryMap`, `MemoryMap_2`, etc. Source names and ordinal correspondence are
recorded in `registration.json`; recognizable GUI names and independent filament
assignment have not been accepted.

`slicer/{cityscape,street_grid}/road-toolpaths.png` renders actual positive-extrusion
G1 and sampled relative-I/J G2/G3 centerlines in above-base layers through the road
top, alongside imported footprints. Final base skin at Z1.64 is excluded. Visual
inspection shows the wider roads and route, but the provisional **0.4 mm local
and service road corridors are missing from the above-base road toolpaths** with
this profile. Successful slicing therefore does not qualify the current widths.
Do not approve neighborhood-grid continuity from these fixtures. Width/profile
tuning and a new slice are required before manufacturing acceptance; no geometry
engine or agreed width defaults were changed in this fix assignment.

## Exact remaining owner/GUI acceptance steps

The Computer Use skill was read and its supported `@oai/sky` initialization was
attempted twice. Both attempts failed before app access with:
`failed to write kernel assets: The system cannot find the path specified. (os error 3)`.
No alternative UI automation or printer interaction was performed.

1. Open each `representative/{style}/candidate.3mf` in Bambu Studio as one assembly
   at 100%. Check component labels, dimensions, frame registration, water/terrain
   alignment, and route position. Determine whether GUI import retains the names;
   CLI name rewriting means name usability remains unverified.
2. Assign motorway, main, local and service components independently, then assign
   several classes the same filament. Assign Route independently. Save a Bambu
   review project and record actual printer/nozzle/material/profile settings.
3. Review `slicer/{style}/sliced.3mf` layer by layer. Inspect every road class,
   intersection ownership, route clearance and transition continuity. Resolve
   missing thin local/service toolpaths through the existing width/profile
   controls and re-slice before acceptance. The current shared-filament slice
   does not establish boundaries between different filaments.
4. Judge real extensive-network neighborhood context and omitted connectors;
   representative fixture performance is not Chicago visual acceptance.
5. Physical print qualification remains an owner-controlled gate. No printer job
   was started; no strength, silhouette survival or viewing-distance acceptance
   is inferred from mesh support or toolpath evidence.

## Extensive real-network review

Used frozen OSM LA roads from the earlier acquisition recovery, not synthetic
streets and not a new building/map generation. The supplied full frame is
22.908 × 18.135 km at approximately 95.45 metres per printed millimetre.
The diagnostic uses all supported public road classes, including optional
cycleways/footpaths/service roads; it does not claim to be the default UI's narrower
class selection. Source SHA-256:
`1e2eeaf3a7fd117d62b110152c6c432080b528e41f2a33ca56ddcf60f8b527bb`.
Route SHA-256:
`9a25496f4f314e25844a08a376d416f3cc25daaebf79c1e7bd25b03e3a14281e`.

The diagnostic batches the same exact coordinate projection. Sampled real source
segments compare bit-for-bit with separate projection calls (maximum difference
0). This is a review-tool improvement only; the generation engine and selection
algorithm are unchanged. The first slow per-segment diagnostic was stopped before
producing results. The batched full-frame run projects 124,187 clipped records in
25.36 seconds. It was stopped during selection after **748.01 seconds**, with a
measured peak process working set of **843,014,144 bytes** (approximately 804 MiB),
once the bounded connector/width blockers were established. Its `incomplete.json`
explicitly records incomplete processing; no final full-frame retention counts,
connector acceptance or full-map mesh/export are claimed. Timings are diagnostic
wall times on a host with concurrent work, not an isolated release benchmark.

The bounded `--crop-mm 80 60 160 120` neighborhood covers approximately
**7.636 × 5.727 km at the original printed scale**. `real-neighborhood/report.json`
and identical-view `full.png` / `detail.png` record:

- 19,748 source records; 18,176 retained; 1,572 omitted.
- Projection 7.56 seconds; selection checkpoint 91.45 seconds; endpoint graph
  diagnostics complete at 92.77 seconds (rendering not included).
- Zero omissions classified as protected corridors.
- **89 omitted source segments join different retained endpoint components**.
  Examples include South Mansfield Avenue, South Orange Drive, West 1st Street
  and Westbourne Drive. This graph uses exact source endpoints; crossings are not
  noded, so it is a connector-loss diagnostic rather than a routability proof.
- Component counts fall from 5,381 to 4,953 as some components disappear. That
  decrease must not be interpreted as improved connectivity.

Visual review: major structure, the highlighted route and recognizable grid
orientations remain, but thinning produces broken rows and isolated short stubs
in several neighborhood blocks. **Adaptive visual acceptance is not passed.**
This real LA assessment does not establish Chicago visual acceptance.

Proposed targeted follow-up, before changing the selection algorithm: protect
source connectors joining retained graph components, examine short protected
stubs alongside their omitted parent corridors, and compare continuity at the
same frame/spacing. Keep source-based placement and exact exclusion rules; no
synthetic infill or geographic distortion is proposed. Broader density/width
tuning and a real Chicago snapshot remain review gates.

Reference availability: `BigSur.gpx` and `Chicago Marathon.gpx` exist in the known
Trace GPX folder. The inspected Chicago benchmark manifest is a placeholder,
not a verified frozen road/water dataset; no corresponding frozen Chicago road
or Big Sur terrain benchmark was validated here. Houston 10K source assets were
not established. Future location-reference acceptance needs those source snapshots
and course identities, not location-specific generator logic.

## Reproduce independent evidence

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
.venv/Scripts/python.exe tools/validate_map_styles.py --output <fresh-output>
.venv/Scripts/python.exe tools/package_map_style_review.py <fresh-output>
powershell -NoProfile -File tools/slice_map_style_review.ps1 -SourceRoot <fresh-output> -OutputRoot <fresh-slicer-output>
.venv/Scripts/python.exe tools/check_map_style_slicer.py <fresh-output> <fresh-slicer-output>
.venv/Scripts/python.exe tools/plot_map_style_toolpaths.py <fresh-slicer-output>
.venv/Scripts/python.exe tools/review_street_selection.py benchmarks/outputs/city-relief/supported-full-roads-manifest.json <fresh-network-output>
```

No merge, release publication, installed-application update, Shopify change,
printer job or unrelated full LA building validation is authorized or performed.
