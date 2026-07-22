# MemoryMap development status

Last updated: 2026-07-22

This document is the living engineering handoff for MemoryMap Studio. It distinguishes the
production baseline on `main` from work that exists only in an open pull request. Update it
whenever a feature branch is merged, rejected, or materially redesigned.

## Production baseline

The package version and latest permanent release are `v2.0.0`. The `main` branch also contains
post-release improvements through PR #8, including city-agnostic building classification,
landmark/stadium scaffolding, continuous terrain-draped routes, and directionally smoothed
major roads. Pull-request artifacts are commit-specific test builds and are not releases.

The current pipeline can:

- load a GPX route and fit a landscape or portrait print frame with a 6 mm route inset;
- let the desktop user move and zoom the frame over a MapLibre map;
- download or load offline roads, buildings, water, and elevation data;
- generate a structural base, continuous route, roads, Simple 3D Building parts/roofs,
  terrain, and recessed water;
- export one 3MF assembly containing separately selectable, pre-colored component parts;
- fall back from USGS 3DEP to the global Terrarium DEM and finally to a reported flat base;
- build and publish a standalone Windows executable through GitHub Actions.

## Physical print profile

The current design target is a 0.4 mm nozzle with 0.16 mm layers. Unless a tested feature
explicitly requires otherwise, use these product-level defaults:

| Setting | Current value | Meaning |
| --- | ---: | --- |
| Base thickness | 1.6 mm | Structural white base below terrain/features |
| Feature embed depth | 0.2 mm | Hidden overlap anchoring raised features |
| Minimum structural XY feature | 0.8 mm | Two nozzle widths |
| Route width | 1.2 mm | Three nozzle widths |
| Route visible height | 2.0 mm | Height above local base/terrain surface |
| Print margin | 5.0 mm | Geometry inset from the base edge |
| Route-to-frame fit | 6.0 mm | Target frame offset from GPX extents |
| Water thickness | 0.6 mm | 0.4 mm supported/embedded plus 0.2 mm visible |
| Water recess | 0.4 mm | Water surface below surrounding terrain |
| Maximum building height | 25.0 mm default | GUI-adjustable, hard limit 31.75 mm |

User-facing feature heights are always visible heights above the supporting surface. Embedding
must extend geometry downward; it must never reduce the requested visible height. Terrain,
roads, route, buildings, and water must retain a geometric support path to the base.

## 3MF layer contract

The exporter writes one multipart `MemoryMap` assembly. Compatible slicers should import it as
one object with multiple parts.

| Part | Default color | Role |
| --- | --- | --- |
| `Base_White` | White | Base and terrain support |
| `Route_Accent` | Orange | GPX route |
| `Roads_Black` | Black | OSM road network |
| `Buildings_Verification` | Gray | Buildings and monochrome water |

Do not flatten these into one colored mesh. Intersections are intentional embedding overlaps;
large unsupported overlaps, floating components, and negative space are not.

## Active parallel development

These branches were created independently from `main`; none of their features are present in
the other branches' executable artifact.

| PR | Branch | Purpose | Local validation |
| --- | --- | --- | ---: |
| [#10](https://github.com/CTheDev24/memorymap-pipeline/pull/10) | `codex/printability-preflight` | Green/yellow/red mesh preflight and desktop report | 134 passed |
| [#11](https://github.com/CTheDev24/memorymap-pipeline/pull/11) | `codex/terrain-validation-presets` | Flat Urban, Rolling Terrain, and Mountain/Coast presets with offline fixtures | 140 passed |
| [#12](https://github.com/CTheDev24/memorymap-pipeline/pull/12) | `codex/source-cache-provenance` | Bounded source cache, stale fallback, provenance sidecar, and GUI summary | 133 passed |

Recommended integration order is PR #12, then PR #11, then PR #10. All three touch
`generation.py` and parts of the desktop GUI, so rebase and conflict resolution are expected.
After each merge, rebase the next branch onto current `main`, rerun its full suite, and build a
combined executable. Do not merge all three based only on their independent green checks.

Current branch status (`codex/review-development-status`): PR #12, then #11, then #10 have
been merged in that order with conflict resolution in `generation.py` and desktop test wiring.
Local combined validation on this branch completed with `python -m pytest -q` (149 passed,
4 skipped). Repository-wide `ruff check .` still reports pre-existing lint debt in debug/helper
scripts that are outside this integration scope.

## Known limitations and deferred work

- Terrain presets have deterministic geometry tests but still require representative slicer
  review and physical prints in flat urban, rolling, and mountain/coastal subjects.
- TC Energy Center's three mapped crowns are separate gabled building parts. OSM does not
  provide the small end spires that create the real building's pointed silhouette. PR #9's
  relative-orientation experiment was closed because it rotated the upper ridge away from the
  lower pair. A future fix should be a printable landmark recipe, not a global roof rule.
- `roof:direction` and specialized shapes such as domes, cones, and mansards are not yet
  modeled by the generic roof builder.
- Landmark fidelity is limited by OSM building-part quality unless a stable OSM/Wikidata keyed
  correction or procedural recipe exists.
- The source-cache branch directly manages DEM and raw building Overpass responses. Ordinary
  road/water feature calls still use OSMnx's cache and are not individually represented in its
  provenance sidecar.
- Automated printability checks are conservative geometry heuristics, not a Bambu Studio
  slicing simulation. Final production qualification still requires slicer review.

## Development priorities

1. Qualify the integrated #12+#11+#10 branch in slicer and physical print workflows, then merge.
2. Perform a three-subject terrain test matrix: Houston/Buffalo Bayou, Nashville, and a
   Big Sur-style mountain/coast route.
3. Save and reload a complete MemoryMap project containing GPX, frame, settings, and source
   provenance.
4. Expand the stable-ID landmark recipe library for signature roofs, stadiums, bridges, and
   monuments without adding city-coordinate conditionals to generic geometry.
5. Add optional slicer-level validation in CI when a supported headless slicer is available.

## Validation expectations

Before publishing a branch:

```powershell
pytest -q
ruff check .
git diff --check
```

New network behavior must use mocked responses and deterministic offline fixtures in tests.
Tests must not depend on live OSM, USGS, Terrarium, map tiles, or a logged-in GUI session.

For geometry changes, verify at least:

- positive volume, watertightness, and consistent winding;
- base support and absence of floating regions;
- visible-height and embed-depth semantics;
- route continuity and configured width;
- water support and bottom skin;
- frame margins and 3MF multipart/color assignments.

GitHub Actions artifacts are the supported way to test a branch executable. After physical and
slicer validation, merge deliberately and create a semantic version tag for a permanent release.
