# memorymap-pipeline

This project converts a GPX track into a Bambu-ready 3MF memory map.

## Phase 1 features

## Installation

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Phase II — Road Network (OSM)

This release adds road-network generation from OpenStreetMap as a separate mesh layer.

Installation (optional dev deps for roads)

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
```

Usage

```powershell

## Usage


Notes

- Roads require `osmnx` (installed via `requirements-dev.txt`) and internet access to download OSM data.
- The exporter now writes three independent objects into the 3MF: `Base_White`, `Route_Accent`, and `Roads_Black`.
- Road widths, available highway types, and road height are configurable in `memorymap_pipeline/config.py` or via a JSON config passed with `--config`.
- Debug plots for roads can be enabled by setting `roads_debug` to `true` in the config.
```bash
python -m memorymap_pipeline.cli sample.gpx output.3mf
```

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
