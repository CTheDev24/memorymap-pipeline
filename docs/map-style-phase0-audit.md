# Map Style refactor: Phase 0 checkpoint

Date: 2026-10-11. Inspected source: `56ba209c7864ca5573cca7ce74be689908cc5d7b`.
Audit branch: `codex/map-style-audit`, created from `codex/city-relief` after checking
the clean working tree and confirming `dev` is an ancestor. No generation code,
installed executable, customer options, or production defaults were changed.

## Decision: GO with representative data following owner clarification

The owner clarified that the locations denote design intentions rather than
required source snapshots, and authorized representative regression data when
references are unavailable. Cityscape is Urban with buildings; Street Grid is
Urban without buildings; Terrain is the existing terrain/landscape engine.
Adaptive street selection is new work; preserve existing filters and selection
behavior as its compatibility baseline. The initial evidence blocker below is
resolved by that clarification; it does not require reconstructing location-specific
outputs. Keep real-world visual and physical acceptance distinct from fixture gates.

The unchanged suite passes **507 tests, 87 warnings, in 35.94 seconds** using
`.venv/Scripts/python.exe -m pytest -q`. Warnings include dependency deprecations,
GeoJSON height parsing, and an unwritable pytest cache. Exit code was zero.
This establishes fixture-level regression health, not successful regeneration of
the three requested real-world reference models.

The required Houston 10K, Big Sur, and latest Chicago Street Grid baselines had
not been established at the initial audit checkpoint.
No reference model was regenerated or sliced during this audit. The block is
missing baseline identity/provenance, not a demonstrated general pipeline failure.

## Relevant history reviewed

Fetched `origin`; its City Relief head agrees with the local inspected commit.
Reviewed the recent ten City Relief commit subjects and relevant module history:

| Commit | Relevant change |
|---|---|
| `56ba209` | Exact LA road recovery and bounded cache of repeated City Relief buffers |
| `521c6e7` | Reject incomplete road layers; record failed LA validation |
| `7e7333d` | Approved Balanced validation orchestration |
| `8c1d6a0` | Exact City mask query optimization |
| `aecc0c8` | Opt-in City Relief, explicit failures, representative provenance |
| `959caa8` | Global building height controls and persistence |
| `f8866e1` | Print-scale building simplification and omission |
| `d71f4aa` | Residential/road filters and Trace finish controls |
| `6ed9f44` | Landscape ground cover and route details |
| `56091b0` | Route slopes matched to slicer layer height |
| `ad286c1` | Road clearance from routes and bridge spans |
| `3e71c48` | Terrain resampling onto the exact frame |

Searching fetched/local history for adaptive-road, street-selection, thinning,
and Street Grid changes did not identify the referenced Chicago implementation.
This is a scoped search result, not proof that no other checkout or repository has it.

## Architecture and capability inventory

| Area | Current implementation | Gap for requested refactor |
|---|---|---|
| Shared generation | Desktop worker constructs `GenerationRequest`; CLI/API share `generate_memory_map` | Add style resolution before this shared entry point; avoid duplicated pipelines |
| Coordinates | `MapFrame` handles print projection, rotation, frame size; terrain resamples onto it | Preserve one frame and shared Z support for every new component |
| Profiles | `urban` and `landscape`; independent layer switches | No Cityscape/Terrain/Street Grid primary selector or per-style state |
| Buildings | Filtering, grouping, optimization, assemblies, roofs, height policy, City Relief provenance | Retain these paths; Cityscape must not silently enable provisional City Relief |
| Terrain | DEM providers, fallback, relief normalization, conformal skins, shoreline/water support, draped route/roads | Real Big Sur replay missing; explicit flat fallback can disguise missing DEM in validation |
| Roads | Highway normalization, class/type filtering, access/service exclusions, per-type widths, clipping, union and exact extrusion | No verified Chicago adaptive neighborhood-grid selection; class identity lost in union |
| Road hierarchy | Urban/Landscape relief plus City Relief major/local height bands and route-local street restriction | New road classes need uniform relief; City Relief height bands must not leak into this policy |
| Route | Independent body, profile-specific height, terrain smoothing/slope controls, bridge treatment, markers | Retain old saved slope settings; removing the UI must not reset their geometry |
| Export | Named layers, single registered 3MF component assembly, materials, support checks, audit | Roads exported as one `Roads_Black` body; class meshes and material mapping absent |
| Preview | Fixed generated layer mesh dictionary and palette | Add class components consistently to preview, audit, inspection and export |
| Persistence | Desktop schema v4 reads v1–v4; preserves appearance and height settings | Explicit migration and per-style saved settings needed |
| Stale results | Height/appearance revision invalidates saving and rejects an old generation result | Extend to all new style/width/geometry changes; current revision is not a comprehensive configuration fingerprint |

The key road seam is `roads.py`: buffered footprints are combined with
`unary_union`, topology closing and clipping, then subtract the route footprint
with 0.03 mm clearance. City Relief optionally partitions major/local regions by
height. `generation.py` and `GenerationResult`, `mesh.py`, printability checks,
palette handling and desktop GLB export all currently consume one roads mesh.
Calling the current downloader separately for each class would duplicate work and
would not establish disjoint intersection ownership.

## Reference evidence status

GPXs found in `C:/Users/craig/iCloudDrive/Business_/Teklo Studio/Product Development/Trace/GPX`:

- `BigSur.gpx` and `Chicago Marathon.gpx` exist; a route alone does not identify
  accepted frame, settings, source snapshot, output geometry or application build.
- `Astros_Race_to_the_Pennant_10K.gpx` is a possible Houston reference, unconfirmed.
  Houston Chevron Marathon and UH 5K are different scenarios.
- Root-level historical Houston 3MFs and `test_houston.py` exist, but the test
  only examines GPX location/closure. They are not established Houston 10K baselines.
- `benchmarks/chicago.example.json` is a template with REPLACE placeholders.
  `benchmarks/downloads` is absent in this checkout. Historical Chicago benchmark
  documentation describes three-window building-grouping coverage, explicitly
  not a complete city inventory or recovered earlier experiment.
- Existing LA validation and road-recovery evidence is useful regression context
  but cannot substitute for the requested three references.

Requested from the owner: paths to approved Houston and Chicago projects/exports,
plus the branch/commit containing the described Chicago adaptive street selection.
For each reference, freeze route, frame/config, vector inputs and DEM/raster inputs,
record checksums/build identity, and regenerate an audited baseline. Validate
nonempty requested layers, actual DEM relief, dimensions, bounds/transforms,
closed/wound/support-valid meshes, and model/audit identity before proceeding.
Use exact frozen data for candidate comparisons; do not repeatedly download or
invent settings to approximate the accepted references.

## Proposed control inventory (not implemented)

| Current controls | Proposed disposition | Dependency to preserve |
|---|---|---|
| Profile plus Buildings/Terrain switches | Primary Map Style; conditional geometry sections | Explicit legacy migration must retain mixed old configurations without silently discarding layers |
| Border toggle and trim width | Remove from new UI | Preserve stored legacy border geometry; define new-style borderless default explicitly |
| Route Layer Height | Remove from UI; internal default 0.16 mm for new projects | Preserve saved layer height and derived maximum slope on migration |
| Print optimization/grouping/narrow residential controls | Consolidate into Cityscape processing policy | Barriers, source classification, omission provenance and printer line width remain active internally |
| Building height mode/multiplier/min/max/legacy ceiling | Conditional advanced height section, final inventory after baseline review | Proportional vs illustrative relief and assembly-wide limits are distinct capabilities; not safe to delete blindly |
| Road inclusion/categories | Style defaults and enabled class/width configuration in geometry section | Internal grouping barriers independent from visible road selection |
| Terrain inclusion | Terrain style default | Retain relief, water support, resolution and route draping |
| Surface/ground cover/water settings | Terrain-only controls where applicable | Geographic context in Street Grid/Cityscape may still require water |
| Appearance/detail/landmark emphasis | Cityscape-specific, operator controlled | Existing behavior default; retain prior opt-in City Relief state without automatic switching |
| Finish/route color, markers, route width/height | Route & Appearance | Preserve route treatment; experimental 1.6 mm width must not overwrite old 1.2 mm projects |

## Safe implementation design after the gate

1. Add a versioned style resolver and explicit desktop migration. Cityscape maps
   to established Urban behavior, Terrain to Landscape, Street Grid to a verified
   buildings-off road configuration. Save per-style settings. Keep schema migration
   separate from applying experimental widths.
2. Prove the three style reference outputs before UI removal. Then reorganize the
   UI and connect a complete geometry revision/fingerprint to stale-result checks.
3. Acquire/project road sources once. Keep class footprints before union; apply
   deterministic ownership Route > Motorway/Trunk > Main > Residential/Local >
   Service. Subtract higher-priority footprints from lower classes, preserving the
   route's established corridor/bridge rules. Validate disjoint final polygons
   and solids after topology repair, draping and extrusion, not just before repair.
4. Use one resolved road relief, class-specific widths, independent named bodies,
   and an explicit many-classes-to-one-filament mapping. Do not make color determine
   class identity. Define cycleway/path/pedestrian/unclassified treatment explicitly
   so supported roads do not silently disappear.
5. Extend result/export/preview/audit/support validation together. Add deterministic
   order, intersection, registration, migration, stale-result, uniform relief,
   and component/material tests. Preserve unrelated Existing geometry.
6. Regenerate all three frozen references; compare expected intentional width/body
   changes separately from route/building/terrain invariance. Import in Bambu Studio
   to verify individual assignment and shared-filament assignment. Headless slicing
   alone cannot prove the interactive assignment workflow. Inspect deposited widths,
   continuity, gaps and merging; physical qualification remains owner controlled.

## Regression risks and remaining gates

- Class boundaries can create point contacts, slivers or gaps during topology
  repair; independently repaired meshes can recreate overlaps.
- Terrain smoothing and route bridge support make planar disjointness insufficient
  to prove absence of intersecting solids in 3D.
- Current City Relief uses different road heights and clips local visibility near
  the route; applying that preset to Street Grid could erase neighborhood context.
- Removed border and slope controls currently affect geometry. Migration must not
  silently reinterpret old saved models.
- Terrain fallback must not count as a successful Big Sur terrain baseline.
- A named 3MF body/material does not prove Bambu material assignment or physical
  width survival. Experimental widths remain unqualified.
- The three real reference baselines, reference visual acceptance, Bambu assignment
  and physical-print tests are **pending**, not passed by the 507 automated tests.

Only this audit document was added. No refactor phase, new full LA Balanced run,
release, installation, merge, slicer acceptance claim or printer job was performed.
