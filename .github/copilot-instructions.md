# Memory Map Engine Workspace Instructions

Apply these instructions to the Memory Map Engine repository.

## Project Scope
This repository is a Python desktop application that converts GPX routes and OpenStreetMap data into Bambu Lab-ready 3MF files. It includes route generation, roads, buildings, a desktop GUI, an API, and automated tests.

## Desktop 3D Preview
- The desktop application includes an embedded, locally bundled 3D geometry preview for
  generated component meshes. It must work without a localhost server or CDN.
- The preview reflects the generated geometry, layer separation, and assigned colors, but
  it is not a slicer and must not be described as a layer, toolpath, support, purge, or
  print-time preview.
- Preview-only simplification may improve interactivity, but must never modify the meshes
  passed to 3MF export.
- Keep viewer assets under the Python package and preserve their setuptools package-data
  and PyInstaller collection whenever asset files are added, moved, or renamed.

## Current Map Styles
- `urban` is the established production profile. It retains a white base, gray
  buildings/water, black roads, and an orange route.
- `landscape` is the terrain-first profile. It uses a complete bone structural terrain,
  a supported green conformal land skin, blue water, black roads, and an orange route.
- Landscape green and blue surface skins have a 0.4 mm user-facing visible thickness by
  default and overlap their supporting terrain; do not generate floating color shells.
- Beach, sand, bare rock, scree, shingle, mud, gravel, rock, and quarry polygons are
  excluded from the green skin so the bone substrate remains exposed.
- Preserve the profile distinction. Landscape additions must not silently change Urban
  colors, component names, or established road/building geometry.

## Terrain and Landscape Resolution
- The default terrain grid is rectangular and print-resolution aware, targeting 0.55 mm
  cells in finished print space rather than a fixed square raster.
- Preserve the print aspect ratio and enforce `terrain_grid_max_samples` (180,000 by
  default) and `terrain_grid_max_dimension` (512 by default) before mesh construction.
- Keep integer and `(rows, columns)` terrain grid overrides working for fixtures,
  diagnostics, and reproducible tests.
- The structural trim is optional and defaults off in the desktop UI. When enabled,
  preserve a coplanar trim across Urban and Landscape maps: the configured margin is its
  inner boundary, terrain must not contour the annulus, and no feature or surface skin
  may cross it. When disabled, terrain and map layers may use the full plate extents.
- Preserve monotonic elevation detail outside the robust 5th-to-95th-percentile range.
  Compress DEM tails instead of clipping them into flat minimum/maximum shelves.
- Treat both positive and negative out-of-range DEM values as no-data and fill holes from
  nearby valid samples. Do not let ArcGIS float sentinels propagate into draped layers.
- Preserve the configured lowland detail curve (`terrain_detail_gamma`, 0.75 by default)
  so subtle coastal relief remains visible without moving the terrain endpoints.
- Landscape surface boundaries follow the resolved DEM triangles. Avoid adding detail
  finer than either the terrain grid or the 0.4 mm nozzle can reproduce.

## Water and Land-Cover Status
- Water currently combines OSM water polygons, oriented coastline-derived ocean fill,
  and mapped `river`, `stream`, `canal`, `drain`, and `ditch` lines.
- Linear waterways are buffered to class-specific print widths and must be at least
  `minimum_waterway_width_mm` (0.8 mm by default).
- Current exposed-land behavior is based on OSM polygon tags. It is not a global
  vegetation/land-cover classifier.
- DEM flow accumulation and DEM-derived drainage channels are future work. Do not
  describe or test them as implemented until the generation pipeline actually uses them.
- A future global land-cover source may refine green versus exposed terrain, but must
  retain deterministic offline fixtures and supported surface construction.

## Landscape Validation Matrix
- Big Sur: rugged terrain, coastline, printable linear waterways, and exposed-ground
  masking.
- San Juan: ocean-side coastline classification, island/land preservation, and dense
  road generation.
- Houston: flatness relief scaling and regression protection for mature Urban road,
  building, route, and roof behavior.

## Working Rules
- Read `README.md`, `pyproject.toml`, `tests/`, and the relevant modules before editing code.
- Restate the affected pipeline stage before proposing or making changes.
- Identify the main risk and a short implementation plan before large changes.
- Keep changes narrowly scoped to one focused feature or bug fix at a time.
- Add or update tests for every behavioral change.
- Run the most relevant tests before declaring work complete.
- Avoid modifying unrelated files.
- Do not commit or push unless explicitly instructed.

## Geometry and Output Constraints
- Preserve the shared XY coordinate transformation and the global Z-coordinate system.
- Keep separate 3MF bodies for base, route, roads, buildings, and other printable layers.
- Treat OSM `natural=coastline` as an oriented boundary: land is on the left and ocean
  is on the right. Classify each frame region by comparing directional evidence from
  both sides; do not accept a region merely because one coastline fragment touches it.
  Ocean fill must be clipped to the printable frame before water recess/support and
  separate-body export are generated.
- Prioritize printability, geometry validity, and Bambu Studio compatibility.
- Preserve recognizable landmark geometry and avoid unnecessary polygon simplification.
- Keep the standard map footprint, raised route height, and modular layer structure intact unless the task explicitly requires otherwise.
- Small controlled overlaps between touching printable bodies are acceptable when they improve slicing reliability.

## Safety and Repository Hygiene
- Never modify generated artifacts such as `.exe`, `.3mf`, logs, caches, `build/`, or `dist/`.
- Do not silently skip requested layers or incomplete outputs.
- If the request implies a large architectural change, ask before proceeding.

## Delivery Expectations
When finishing work, summarize:
- changed files
- tests run
- known limitations
- recommended next steps
