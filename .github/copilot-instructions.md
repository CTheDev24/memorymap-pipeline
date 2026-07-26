# Memory Map Engine Workspace Instructions

Apply these instructions to the Memory Map Engine repository.

## Project Scope
This repository is a Python desktop application that converts GPX routes and OpenStreetMap data into Bambu Lab-ready 3MF files. It includes route generation, roads, buildings, a desktop GUI, an API, and automated tests.

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
  is on the right. Ocean fill must be clipped to the printable frame before water
  recess/support and separate-body export are generated.
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
