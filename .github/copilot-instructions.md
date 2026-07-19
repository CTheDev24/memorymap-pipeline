# GitHub Copilot instructions for MemoryMap

Read `README.md` and `docs/DEVELOPMENT_STATUS.md` before proposing changes. Treat
`docs/DEVELOPMENT_STATUS.md` as the source of truth for what is merged, what is experimental,
the current printer profile, and the required integration order.

## Product goal

MemoryMap converts a GPX activity and nearby geospatial data into a reliable, multicolor,
support-free 3MF display map. Correct slicing and physical printability take priority over
photorealistic detail or maximum triangle count.

## Architecture map

- `memorymap_pipeline/generation.py`: orchestration and layer assembly
- `memorymap_pipeline/config.py`: defaults and JSON configuration merging
- `memorymap_pipeline/gpx_loader.py`, `map_frame.py`, `projection.py`: route and print-space transforms
- `memorymap_pipeline/mesh.py`: route/base meshes and 3MF export
- `memorymap_pipeline/roads.py`: OSM road selection and printable widths
- `memorymap_pipeline/buildings.py`: OSM building parts, height mapping, and roofs
- `memorymap_pipeline/building_classification.py`: city-agnostic fallback classes
- `memorymap_pipeline/landmarks.py`, `data/landmarks.v1.json`, `stadiums.py`: stable-ID curated enhancements
- `memorymap_pipeline/terrain.py`, `terrain_providers.py`: DEM conversion and terrain draping
- `memorymap_pipeline/water.py`: vector water, recess, and terrain partitioning
- `memorymap_pipeline/printability.py`: geometry support and validation rules
- `memorymap_pipeline/desktop/`: PySide6 application and worker boundary
- `tests/fixtures/`: deterministic offline geometry/source fixtures

## Working rules

- Restate the affected pipeline stage and primary risk before a large implementation.
- Keep each change narrowly scoped to one feature or bug and avoid unrelated refactors.
- Inspect the relevant modules and tests before editing; do not infer contracts from filenames.
- Add or update tests for every behavioral change and run the most relevant suite.
- Do not commit, push, merge, publish, or create a release unless the user explicitly requests it.
- Ask before materially changing the architecture or expanding the requested product scope.

## Non-negotiable geometry rules

1. Use millimetres in print space and metres only for source/geographic dimensions.
2. User-facing heights are visible heights above the local base or terrain surface.
3. The default 0.2 mm embed extends a feature downward without reducing its visible height.
4. The base must remain solid underneath features until their intended material begins.
5. Every route, road, building, roof, terrain, and water component needs a support path to the base.
6. Keep route tops smooth and 1.2 mm wide. Terrain stepping may occur along the route, never across its width.
7. Major roads may smooth elevation along travel but must keep a consistent cross-section and terrain-supported underside.
8. Water is a continuous vector solid where the source geometry is connected, remains inside the print margin, and keeps structural white material beneath it.
9. Preserve one multipart 3MF assembly with independently selectable colored parts. Do not merge all materials into one mesh.
10. Empty or invalid geometry may block export. Nonfatal uncertainty should produce an explicit warning/report, not silent layer loss.

## Printer-aware defaults

Assume a 0.4 mm nozzle, 0.16 mm layer height, 0.8 mm minimum structural XY feature,
1.6 mm base, 1.2 mm route width, 2.0 mm visible route height, 0.2 mm feature embed, and
5 mm print margin unless the user explicitly changes a supported setting.

## Buildings and landmarks

- Prefer explicit OSM heights, levels, building parts, and supported roof tags.
- Keep generic behavior city-agnostic. Never add coordinate, city-name, or mutable-name checks
  to shared geometry code.
- Curated exceptions must match stable OSM element IDs or Wikidata IDs through the versioned
  landmark registry and must include tests and documentation.
- Do not globally change roof semantics to repair one skyline. Use a print-safe landmark recipe
  when source data lacks signature spires, stadium roofs, or other architectural details.
- Preserve building holes, inner rings, and part replacement of parent footprints.

## Terrain and external data

- Terrain failures must be reported. Preserve the USGS -> Terrarium -> explicit flat fallback order.
- Network tests must mock HTTP and use offline fixtures; never require live endpoints in CI.
- Cache only validated responses. Use stable request keys, atomic writes, bounded storage, and
  stale data only for transient fetch failures, not for a successful but invalid response.
- Record enough provenance to reproduce or explain a generation without exposing secrets.

## Tests and change discipline

- Add focused regression tests for every geometry or source-data bug.
- Run `pytest -q`, `ruff check .`, and `git diff --check` before publishing.
- Check positive volume, watertightness, winding consistency, support, margins, configured
  feature dimensions, and multipart/color output when relevant.
- Avoid network access, user-specific absolute paths, sleeps, and nondeterministic fixtures.
- Never edit or commit generated `.exe`, `.3mf`, logs, caches, `build/`, or `dist/` content.
- Keep unrelated formatting and refactors out of functional PRs.
- Update `README.md` for user-visible behavior and `docs/DEVELOPMENT_STATUS.md` when feature
  state, defaults, known limitations, PR status, or integration order changes.

## Current branch awareness

PRs #10, #11, and #12 are independent branches from the same `main` baseline. They are not a
combined implementation. Expect conflicts in generation/configuration/desktop files and rebase
each branch after the preceding merge. PR #9 is closed and its TC Energy orientation experiment
must not be revived without new geometry evidence.
