# memorymap-pipeline

This project converts a GPX track into a Bambu-ready 3MF memory map.

The generated model can contain separate base, route, road, building, water, and
landscape-surface objects.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Road and building data

Roads and buildings are loaded from OpenStreetMap by default. Internet access is required,
or local GeoJSON/GeoPackage files can be supplied with `--roads-file` and `--buildings-file`.

## Usage

Notes:

- The exporter writes one `MemoryMap` assembly containing independent, pre-colored
  component parts: `Base_White` (white), `Route_Accent` (orange), `Roads_Black`
  (black), and `Buildings_Verification` (gray). Compatible slicers import the model as
  one multipart object and read the assignments from standard 3MF base materials.
- Road widths, available highway types, and road height are configurable in `memorymap_pipeline/config.py` or via a JSON config passed with `--config`. When terrain is enabled, motorways and trunks retain one elevation across each cross-section and use classification-specific smoothing only along travel. Their undersides remain embedded in the original relief; minor streets continue following terrain directly.
- Major-road face refinement is bounded to five conforming passes so dense city networks
  remain watertight without allowing refinement complexity to abort map generation.
- Route, road, and building heights are visible heights measured above the base plate.
- On relief maps, route tops use one centerline-derived elevation across the full configured
  width and smooth only along the direction of travel. The underside remains terrain-draped,
  keeping the orange route continuous, supported, and 1.2 mm wide by default.
- Route faces are locally refined to a 2.4 mm maximum edge before terrain draping, preventing
  long triangulation diagonals from becoming thin fins near bends or converging segments.
- Building heights follow the physical map scale by default; only unusually tall outliers are adaptively compressed to the GUI maximum (25 mm by default, 31.75 mm hard limit).
- Urban water solids are 0.6 mm thick: 0.4 mm is embedded into structural support and
  0.2 mm remains exclusively visible. A minimum 0.4 mm base-material bottom skin prevents
  water from appearing on the underside. Landscape surface dimensions are described below.
- Water is clipped to the same margin-inset printable bounds as route, road, and building layers.
- Terrain sampling is print-resolution aware by default. The adaptive rectangular grid targets
  0.55 mm cells while preserving the print aspect ratio, then applies sample-count and
  per-dimension caps before mesh construction.
- USGS terrain requests are cached and retried, then fall back to the public global AWS Terrarium DEM; a clearly reported flat base is used only if both elevation services fail.
- Raised features overlap the base by `feature_embed_depth` (0.2 mm by default) to keep short geometry printable without changing its visible height.
- Export now stops when a component is non-manifold, has inconsistent face winding or
  non-positive volume, or has no geometric support path to the base. Roofs may be supported
  through their building body; mutually touching floating shells do not satisfy the check.
- The production printability profile assumes a 0.4 mm nozzle, 0.16 mm layer height,
  0.8 mm minimum structural XY feature, and a 1.6 mm structural base.
- Debug plots for roads can be enabled by setting `roads_debug` to `true` in the config.

```bash
python -m memorymap_pipeline.cli sample.gpx output.3mf
```

Use `--no-roads`, `--no-buildings`, or `--no-base` to omit optional layers. An unexpected
failure in a requested layer stops the command instead of silently exporting an incomplete map.

## Configuration

Create a JSON file such as `memorymap-pipeline/config.json`:

```json
{
  "portrait": {
    "map_width": 190.0,
    "map_height": 240.0
  },
  "landscape": {
    "map_width": 240.0,
    "map_height": 190.0
  },
  "route_height": 2.0,
  "route_width": 1.2,
  "base_thickness": 1.6,
  "feature_embed_depth": 0.2,
  "margin": 5.0
}
```

The base top is the model's Z=0 plane. With a 1.6 mm base, 0.2 mm embed depth,
and 0.6 mm road height, the road mesh runs from Z=-0.2 mm to Z=0.6 mm. Thus
0.2 mm is anchored inside the base and the full requested 0.6 mm remains visible.
The embed depth is automatically clamped to the base thickness and becomes zero
when generating without a base.

Run with the config file and let the orientation be chosen automatically by the route shape:

```bash
python -m memorymap_pipeline.cli sample.gpx output.3mf --config config.json
```

If you want to override orientation explicitly:

```bash
python -m memorymap_pipeline.cli sample.gpx output.3mf --config config.json --orientation landscape
```

The pipeline will maximize the printed route area within the selected map dimensions while reserving a consistent border on all sides.

### Terrain, water, and map styles

Terrain uses bare-earth DEM samples through a provider interface. The first provider is
the official USGS 3DEP ImageServer for United States frames; downloaded TIFF samples are
cached under the user's local MemoryMap data directory. AWS Terrarium supplies the global
fallback when USGS data is unavailable. The provider boundary also accepts offline fixtures
for deterministic testing.

Provider rasters are geographic, axis-aligned bounding rectangles. Before relief
normalization, MemoryMap resamples them into the exact print frame, including its aspect
ratio and rotation. Do not stretch the provider rectangle directly over the plate: on
coastal routes, its corner and ocean cells otherwise become large false sea-level shelves
inside the land surface.

Terrain is disabled by default. MemoryMap Studio exposes Terrain and Water layer toggles,
maximum relief, water recess, and an Urban/Landscape style selector. Its initial terrain and
landscape configuration is:

```json
{
  "terrain_enabled": false,
  "terrain_provider": "usgs-3dep",
  "terrain_grid_size": null,
  "terrain_target_cell_size_mm": 0.55,
  "terrain_grid_max_samples": 180000,
  "terrain_grid_max_dimension": 512,
  "flat_border_enabled": false,
  "terrain_max_relief_mm": 3.0,
  "terrain_min_relief_mm": 1.5,
  "terrain_detail_gamma": 0.75,
  "water_enabled": false,
  "water_recess_mm": 0.4,
  "water_mesh_thickness_mm": 0.6,
  "water_support_overlap_mm": 0.4,
  "water_base_skin_mm": 0.4,
  "water_shoreline_tolerance_mm": 0.1,
  "style_profile": "urban",
  "surface_skin_thickness_mm": 0.4,
  "landscape_water_visible_thickness_mm": 0.4,
  "minimum_waterway_width_mm": 0.8
}
```

With `terrain_grid_size` set to `null`, grid rows and columns are derived from the finished
print dimensions rather than using one fixed square raster. The default 240 x 190 mm plate
resolves to approximately 0.55 mm cells. The 180,000-sample and 512-cell dimension limits
bound terrain mesh complexity. An integer still requests an explicit square grid, while a
two-value `(rows, columns)` override is available to fixtures and programmatic clients.

Each frame receives a flatness rating from 0 (rugged) to 5 (very flat), based on its
robust elevation range relative to the frame diagonal. Very flat areas receive the full
3 mm printed relief; rugged areas taper toward 1.5 mm so terrain does not overpower map
features. Routes and roads are draped over the resulting height field while retaining
their visible heights. Elevations outside the robust 5th-to-95th-percentile range are
softly compressed into the relief tails rather than hard-clipped, preserving contours
through unusually low and high areas without allowing one DEM outlier to dominate the
entire print.
USGS TIFF values outside the physically plausible -12,000 to 12,000 metre range are
treated as undeclared no-data sentinels and locally interpolated from valid neighbours.
This prevents isolated terrain needles from propagating into routes, water, and landscape
skins. The default `terrain_detail_gamma` gently expands lowland elevation differences
while preserving both the minimum and the selected maximum relief.

The flat border is optional and unchecked when MemoryMap Studio launches. With
`flat_border_enabled` set to `false`, terrain and map layers use the full plate extents.
When enabled, the configured margin becomes the trim width: terrain grid lines are inserted
at its exact inner boundary, the outer annulus remains coplanar, and route, road, building,
water, and landscape bodies remain inside it. This behavior is shared by Urban and
Landscape profiles.

#### Urban and Landscape profiles

The **Urban** profile preserves the production palette and behavior: a white structural
base, gray buildings and water, black roads, and an orange route.

The **Landscape** profile uses a complete bone-colored structural terrain substrate and
adds independently colored, terrain-following surface bodies:

- `Terrain_Green`: a supported 0.4 mm visible conformal skin over ordinary land.
- `Water_Blue`: a blue water body for oceans, mapped water areas, and mapped linear
  waterways.
- Exposed substrate: OSM polygons tagged as beach, sand, bare rock, scree, shingle, mud,
  gravel, rock, or quarry are omitted from the green skin so the bone terrain shows through.
- Existing black roads and the orange route remain separate printable parts.

Surface skins follow the DEM triangles and overlap their structural support by the normal
feature embed depth. This keeps the colored layer supported instead of creating a floating
shell. The user-facing skin thickness defaults to 0.4 mm.

Water polygons are part of the same multipart model and use the same gray material as
buildings in Urban mode and the blue material in Landscape mode. Water starts 0.4 mm below
the local terrain surface and embeds into supported terrain, while the structural base
remains underneath. When Water is enabled, desktop generation downloads OSM areas tagged
`natural=water`, marine `water=*`/`place=*` values such as `ocean` and `sea`,
`waterway=riverbank`, or reservoir/basin land use. It also retrieves
oriented `natural=coastline` ways in the same request and fills the ocean side within
the print frame, because OpenStreetMap coastlines normally imply the ocean rather than
storing it as a closed water polygon. Coastline regions are classified using the
dominant right-side (ocean) versus left-side (land) directional evidence, preventing
secondary island or fragmented shoreline ways from selecting both sides of the coast.
Local water files or pre-transformed polygons remain available for offline tests.

Mapped `waterway=river|stream|canal|drain|ditch` lines are buffered into printable water
areas and merged with polygonal water. Class-specific widths are used where available,
and every linear waterway is widened to at least 0.8 mm by default. This minimum is a
print-space requirement; it does not claim the mapped channel is that wide in the real
world.

Connected water polygons are merged before meshing, so a river such as Buffalo Bayou is
one continuous vector solid rather than a collection of terrain-grid rectangles. The
terrain is partitioned along the same shoreline and cut down to the water level. A 0.1 mm
print-space simplification removes insignificant OSM noise while preserving islands and
inner openings.

Landscape classification currently depends on the completeness and geometry of OSM tags.
Unmapped streams cannot be inferred from the DEM yet, and the green surface is a default
land treatment with OSM exposed-ground exclusions rather than a global vegetation or
land-cover classification product. DEM-derived drainage and global land-cover data are
future refinements, not current generation behavior.

Landscape regression subjects are:

- Big Sur: rugged relief, coastline, linear waterways, and exposed ground.
- San Juan: ocean/land classification and dense urban roads.
- Houston: flatness scaling and protection of established urban road/building output.

To omit the base plate:

```bash
python -m memorymap_pipeline.cli sample.gpx route-only.3mf --config config.json --no-base
```

## Project structure

- memorymap_pipeline/gpx_loader.py: GPX parsing
- memorymap_pipeline/projection.py: coordinate projection and normalization
- memorymap_pipeline/mesh.py: mesh generation and 3MF export
- memorymap_pipeline/cli.py: command-line interface

## Development

```bash
pytest
ruff check .
```

## Local GUI

The repository includes a React and MapLibre editor for uploading a GPX route, viewing it
over a map, dragging the print frame, changing orientation and print settings, previewing
clipping warnings, and generating a 3MF.

Start the API:

```bash
python -m memorymap_pipeline.api
```

In a second terminal, start the frontend:

```bash
cd frontend
pnpm install
pnpm dev
```

Open `http://localhost:5173`. The Vite development server proxies `/api` to the local API
at `http://localhost:8000`.

This first GUI slice previews the route and editable frame immediately. API generation
uses the selected frame for the base and route; detailed road/building preview and framed
generation are exposed as layer options but currently return an explicit warning while
that integration is completed.

## Windows desktop application

Install the desktop dependencies and launch MemoryMap Studio as a native window:

```powershell
python -m pip install -e ".[desktop]"
python -m memorymap_pipeline.desktop
```

The desktop application uses PySide6 and Qt WebEngine. GPX parsing and 3MF generation
run directly inside the application; no localhost server or external browser is required.
The map itself uses online MapLibre/OpenStreetMap tiles, so map imagery and OSM layer
downloads still require an internet connection. A newly loaded route and orientation change
reserve 6 mm between the route extents and the displayed print frame. **Zoom in** and
**Zoom out** adjust geographic coverage around the current frame center; **Reset frame to
route** restores the centered 6 mm fit. In Terrain mode, enabling Water exports the gray
water as its own mesh body, and the Layers panel shows this as a dedicated indicator.

### Generated 3D preview

After a successful generation, **3D Preview** displays the same generated component
meshes used to create the 3MF. The preview can be rotated, panned, zoomed, and fitted to
the window. Route, roads, buildings, terrain/landscape, water, and base retain their
assigned print colors, and each available layer can be shown or hidden independently.
This makes missing layers, unexpected water coverage, discontinuities, and obvious
geometry problems visible before opening the file in Bambu Studio.

The viewer and its JavaScript assets are bundled locally with both the Python package and
the standalone Windows executable. It does not need an external browser, localhost server,
or internet connection after the meshes have been generated. Large maps may use simplified
display geometry to keep interaction responsive; simplification affects only the preview,
not the exported 3MF.

The 3D preview is a geometry inspection tool, **not a slicer or toolpath preview**. Bambu
Studio is still required to inspect layers, bridges and overhangs, filament changes, purge
behavior, supports, and printer-specific toolpaths.

### Building parts and roofs

Building generation follows the core OpenStreetMap Simple 3D Buildings tags. Both
`building=*` outlines and `building:part=*` footprints are downloaded. Where parts overlap
a parent outline, the part geometry replaces that area so the parent is not extruded through
the detailed volumes.

Supported vertical tags are `height`, `min_height`, `building:levels`,
`building:min_level`, `roof:height`, and `roof:levels`. Explicit `height` includes the roof;
when height is derived from `building:levels`, the tagged roof height is added above those
levels. Supported `roof:shape` values are `flat`, `gabled`, `hipped`, `pyramidal`, and
`skillion`. Unknown shapes remain flat. A non-flat shape without a roof height receives a
conservative one-storey roof.

All building bodies and roofs remain in the gray `Buildings_Verification` component of the
colored multipart 3MF. Bodies are anchored at the lowest sampled terrain elevation across
their footprint instead of a single centroid. In the default support-free printability mode,
otherwise unsupported `min_height` volumes extend to terrain; this can be disabled with
`extend_elevated_building_parts_to_ground=false`. Roof bottoms overlap their body by the
configured embed depth, without lowering the visible eave or peak. `roof:orientation=along|across`
is honored for supported roof shapes. `roof:direction` and more specialized roof shapes are not
yet modeled; supported roofs otherwise align to the footprint's minimum rotated rectangle.

### Building classification and landmark enhancements

When explicit `height`, `building:levels`, and roof tags are absent, buildings use a
city-agnostic classification preset instead of one universal fallback. Residential,
commercial/office, industrial/warehouse, retail, parking, civic/institutional,
stadium/arena, religious, landmark, and unknown classes provide realistic fallback floor
heights plus print-aware minimum and maximum visual heights. Explicit source dimensions
remain authoritative.

Recognizable landmarks can opt into versioned offline corrections and procedural geometry
through `memorymap_pipeline/data/landmarks.v1.json`. Entries match stable Wikidata or OSM
element identifiers; mutable names and city-specific coordinate checks are deliberately not
used. The priority order is curated landmark data, explicit OSM dimensions and building
parts, classification estimates, then the generic fallback.

Daikin Park is the first bundled landmark recipe, matched by Wikidata `Q1193671`. Its default
print representation is a fully supported closed-roof mass following the mapped stadium
footprint, with broad shallow roof bands that remain printable with a 0.4 mm nozzle. The generic
stadium builder also retains open-bowl and supported retractable-roof options for future detail
modes and other venues. Recipe dimensions in the landmark registry are applied directly rather
than serving as descriptive metadata. If a footprint cannot satisfy the selected recipe's
minimum feature sizes, generation falls back to ordinary building massing. Landmark JSON is
bundled in both Python distributions and the standalone Windows executable.

To build a distributable Windows executable, install the packaging extra and run:

```powershell
python -m pip install -e ".[desktop,package]"
pyinstaller --noconfirm --clean memorymap-desktop.spec
```

The finished executable is written to `dist\MemoryMap.exe`. The `build/`, `dist/`, and
`*.exe` paths remain ignored by Git: source code, dependencies, and the PyInstaller spec
are versioned, while generated binaries are distributed through Actions and Releases.

Because development builds are not code-signed, Windows SmartScreen may display a warning.
If you trust the commit that produced the build, select **More info** and **Run anyway**.

### Download a branch or pull-request build

1. Open the repository's **Actions** tab on GitHub.
2. Select a successful **Build Windows desktop app** run for the desired commit.
3. Under **Artifacts**, download `MemoryMap-Windows-<commit SHA>`.
4. Extract the ZIP and run `MemoryMap.exe`.

Workflow artifacts are temporary, commit-specific test builds. The accompanying
`MemoryMap.exe.sha256` file can be used to verify the download.

### Publish a tagged release

Create and push a semantic version tag after the target commit is tested:

```powershell
git tag v2.0.0
git push origin v2.0.0
```

The tag triggers the release workflow, which tests and packages the exact tagged source,
then attaches a versioned Windows executable and checksum to a permanent GitHub Release.
Use patch tags such as `v0.1.1` for fixes, minor tags such as `v0.2.0` for compatible
features, and major tags such as `v1.0.0` for the first stable release or breaking changes.
