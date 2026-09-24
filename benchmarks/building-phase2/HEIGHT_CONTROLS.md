# Global building-height controls

Implemented on `codex/building-height-controls`, following performance checkpoint
`07f6b3b`. The shared `dev` branch remains at
`71de412b4da99e61cf4da7ebcaa1feaf2a3dc1aa`; no merge or push.

## User behavior

- Architectural relief remains the default, with multiplier 1 and final limits off.
- Proportional mode uses resolved source height in metres times the horizontal
  map scale, independently of terrain exaggeration and the legacy relief ceiling.
- A positive multiplier applies once, then optional positive minimum/maximum mm.
  Limits include roofs and exclude embedding. Minimum must not exceed maximum.
- Connected parent/part assemblies share an envelope scaling factor and terrain
  datum. A parent wholly replaced by parts does not inflate the visible envelope.
  Dependent assemblies are excluded from manual neighborhood grouping. Existing
  optimized grouping remains aware of linked parts; group records include their
  effective shared top.
- Stadium recipes scale their complete bodies, roofs and supports to the requested
  envelope. Support adjustment with active controls preserves the requested top.
- Height edits invalidate the old preview and Save result. Full regeneration uses
  the same meshes for preview/export. Settings are captured for the worker and
  results from stale height revisions are refused.
- Open/Save project persists controls in schema version 3. Versions 1 and 2 retain
  architectural-relief defaults. Projects reference an existing GPX file.
- Active controls write source-height, assembly, effective-top and floor/cap
  provenance to the model's audit sidecar, which is copied by Save result as.

Individual overrides, new filters, interactive partial regeneration and changes to
appearance defaults remain outside this implementation.

## Validation

- Full suite: **441 passed**, 76 existing warnings; see
  `benchmarks/outputs/pytest-height-final.log`.
- All six stored manual geometry/decision fingerprints match exactly; see
  `benchmarks/outputs/height-default-compatibility.json`.
- Tests cover invalid limits, multiplier order, roof-inclusive tops, embedding,
  parent/part limits, replaced parents, shared terrain datum, stadium envelope,
  manual/optimized groups, legacy project migration, UI settings round-trip and
  stale-result protection.
- Qt controls were rendered offscreen with Segoe UI and checked to fit the panel.
  Browser rendering was stubbed for this widget check; it is not a live map or
  packaged WebEngine interaction test.

## DC Pentagon regression

Recovered local cached OSM relation **89605**, named The Pentagon, with `height=23`.
Its complete courtyard footprint was decoded with OSMnx from the local cache;
no new map-data download was used. Local source snapshot and result models are in
`benchmarks/outputs/dc-height-controls/`.

Auto-fitting the user's DC Half Marathon GPX to 190 x 240 mm with 5.6 mm route
padding gives coverage 5553.381756 x 7014.798008 metres. The test used the production
generation/export pipeline with the Pentagon footprint, route and base, roads off,
terrain/water off, and legacy relief ceiling 26 mm.

| Policy | Roof above map surface |
| --- | ---: |
| Legacy architectural relief | 7.953650 mm |
| Proportional 1x, no limits | 0.786908 mm |
| Proportional 2x, no limits | 1.573816 mm |
| Proportional 2x, maximum 1.2 mm | 1.200000 mm |

All four exports retain identical footprint coordinates. Every generated layer is
watertight and consistently wound, and each passes the production support gate.
Active-policy sidecars match the output model SHA-256. The validation runner is
`run_dc_height_controls.py`; its inputs and results have recorded hashes.

This is a focused actual-Pentagon regression, not a complete DC map regeneration
or exact reproduction of the old model's unavailable settings. There is no slicer
or physical-print validation. Source-height estimates and very small proportional
features still require judgment. Neighborhood grouping intentionally retains its
shared-mass height policy.

## Desktop package

Executable: `dist/height-controls-20260924/Trace Studio.exe` (370,700,273 bytes).

SHA-256: `8bbdc23ca9cc72ad2caece6ac1381611b01f8772a6970599e1928f6cb163163a`.

The executable archive contains the required WebEngine/viewer/landmark assets and
OSMnx metadata. Eight bundled modules, including the height controls and performance
paths, match the tested source bytecode. `build-verification.json` and the SHA-256
file sit beside the executable. A complete generation through the packaged GUI has
not been run; the geometry/export tests use the same verified Python source.
