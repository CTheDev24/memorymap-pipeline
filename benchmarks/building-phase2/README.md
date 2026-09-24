# Print-optimized buildings (Phase 2)

This opt-in mode runs in Trace Studio and the frame-aware generation service.
Manual mode remains the default. Six stored pre-Phase-1 mesh/disposition fingerprints
match exactly on this branch, including legacy grouping and residential filtering.

## Using the test build

1. Open the GPX and set the frame as usual.
2. Enable **Print-optimized buildings** in Building filter.
3. Set **Printer line width (mm)** to the slicer's outer/default extrusion width
   (0.42 mm for the supplied Peachtree profile).
4. Keep Roads enabled so grouping has street barriers. Road and water barriers must
   both be available; otherwise grouping is skipped with a warning. Simplification,
   route exclusion and isolated-feature omission can still run.
5. Generate, then Save result as. The model is accompanied by `name.audit.json`.
6. Slice with the same printer profile and inspect the right-side neighborhood and
   the previously identified route crossing before a production print.

The mode supersedes the legacy grouping checkbox and residential-only width filter.
Those controls are disabled while optimized mode is selected and their previous
values remain available when returning to manual mode. The older route-fitting CLI
rejects optimized mode explicitly; it cannot silently produce a manual result.

## Unknown buildings

The source class remains `unknown`. The physical policy is recorded separately:

- Ordinary low-rise: eligible for grouping, cleanup and selective omission.
- Tall: individual, including explicit height/levels above 15 m, or a rendered
  height above the group-height cap. Thin/tall residuals are flagged unresolved.
- Protected: registered landmarks, landmark/civic/religious/stadium classes,
  substantial courtyards, explicit parts and contacting parent remnants.

No land-use class is inferred from footprint size or neighborhood location. Records
include source class, height evidence, policy class, decision, reason, input/output
areas, output core width, route clipping, group membership and output mesh ranges.
Above-base structures keep the existing support-mapping behavior; this feature is
not a replacement for a full unsupported-geometry audit.

## Physical policy

With 0.42 mm configured line width, the existing scale ratios produce:

| Rule | Value |
|---|---:|
| Preferred core width | 0.84 mm |
| Minimum ordinary fragment area | 0.7056 mm² |
| Boundary simplification tolerance | 0.1575 mm |
| Neighbor merge gap | 0.42 mm |
| Group span | 4.2 mm |
| Route clearance beyond route/marker silhouette | 0.84 mm |
| Neighborhood height cap | 3.0 mm (existing configurable cap) |

1. Clip all footprints to the print frame and route exclusion corridor.
2. For unprotected footprints, simplify outlines and open narrow appendages, with
   a maximum 20% footprint-area loss. Insignificant holes can be filled with at
   most 20% added area, only without invading neighbors or barriers.
3. Reuse deterministic bounded grouping, with a maximum 128 members and the
   existing maximum eightfold source-area expansion guard. Groups respect street,
   water, route, frame and protected-building barriers.
4. Clean groups again while retaining contact with every member and a robust core.
   Use median member height capped by the neighborhood-height limit.
5. Omit ordinary standalone fragments that fail either the core-width or area test.
   Significant courtyard/part geometry is exempt and flagged if still too fine.
6. Small protected standalone footprints may expand by half a line width only if
   area growth is at most 50%, the width target is met, and no barrier or neighbor
   is crossed. Otherwise they remain unresolved, never silently deleted.
7. Residual narrow appendages exceeding the cleanup budget and protected/tall
   height-to-core-width ratios above six are explicitly unresolved.

Core width measures the largest surviving interior core; it is not a minimum-neck
measurement or slicer certification. Roofs follow the final outline. If a generated
pitched roof fails closure/winding checks, optimized mode substitutes a closed flat
cap at the same top height and records the fallback. A final projection check covers
all building triangles, including roofs and landmark enhancements, and prevents
export if they enter the route corridor.

## Audit and compatibility

Optimized exports include the output model hash, route-point hash, algorithm version,
frame, effective settings, physical thresholds, summary statistics and source records.
Save result as copies the report too. Regenerating a filename removes a stale report.
The sidecar contains source geometry and can be large for dense city maps.

No new package dependencies. `dev` is not changed or merged. The existing Windows
build workflow tests the branch and packages the preview executable on push.

## Validation and limits

- Full local suite: 408 passed (74 existing/dependency warnings).
- Six manual-mode mesh/source-record fingerprint comparisons: exact match.
- New tests cover unknown small/tall cases, road barriers, ordering, courtyards,
  contacting parts, clipped roofs, the actual Peachtree crossing, sidecar lifecycle,
  configuration, and legacy UI control precedence.
- The Peachtree collision fixture is extracted from exported shell 90, translated
  to local printed coordinates. It has no inferred OSM identity or class.
- A separate 5,651-shell export-only stress test completed in 34.3 seconds, had zero
  remaining projected route overlap, and preserved the 22.466 mm highest structure.
  Grouping was disabled because original road/water/source data was unavailable.
  Its 4,202 omissions are NOT a forecast for a source-classified fresh generation.
- Full Peachtree regeneration, a fresh slice, and a physical right-side test remain
  required. The new executable does not establish that stringing is eliminated.
