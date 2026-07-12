# memorymap-pipeline

This project converts a GPX track into a Bambu-ready 3MF memory map.

The generated model can contain separate base, route, road, and building objects.

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

- The exporter writes independent `Base_White`, `Route_Accent`, `Roads_Black`, and `Buildings_Verification` objects.
- Road widths, available highway types, and road height are configurable in `memorymap_pipeline/config.py` or via a JSON config passed with `--config`.
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
  "base_thickness": 1.0,
  "margin": 8.0
}
```

Run with the config file and let the orientation be chosen automatically by the route shape:

```bash
python -m memorymap_pipeline.cli sample.gpx output.3mf --config config.json
```

If you want to override orientation explicitly:

```bash
python -m memorymap_pipeline.cli sample.gpx output.3mf --config config.json --orientation landscape
```

The pipeline will maximize the printed route area within the selected map dimensions while reserving a consistent border on all sides.

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
downloads still require an internet connection.

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
git tag v0.1.0
git push origin v0.1.0
```

The tag triggers the release workflow, which tests and packages the exact tagged source,
then attaches a versioned Windows executable and checksum to a permanent GitHub Release.
Use patch tags such as `v0.1.1` for fixes, minor tags such as `v0.2.0` for compatible
features, and major tags such as `v1.0.0` for the first stable release or breaking changes.
