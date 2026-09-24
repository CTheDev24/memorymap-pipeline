# Optional building-height controls: assessment and plan

Date: 2026-09-24. Status: proposal only. No height mapper, appearance defaults,
filters, UI, or cache behavior changed in this assessment. Keep implementation in
a separate appearance change after review of the performance fix.

## Recommendation

Expose a small set of predictable controls with saved presets and a building
inspector. Retain automatic source interpretation, dependency handling and geometry
validation. Artistic choices belong to the user; route clearance and valid support
remain enforced. Do not turn every internal heuristic into a slider.

The performance fix has 413 passing tests, six matching manual-mode fingerprints,
and a successful source-backed Peachtree generation/export. That run took 18m12s,
plus 3m22s source acquisition. It validates completion and geometry gates, not an
acceptable interactive latency or physical print quality. Grouping (312s), width
diagnostics (107s), and export/audit (219s) still need attention. See
[performance evidence](PERFORMANCE.md) and [GPX test](PEACHTREE_GPX_TEST.md).

## DC evidence and current behavior

Inspected `C:/Users/craig/Downloads/dc_marathon_v1.3mf` locally, without alteration
or upload. The archive is 13,773,494 bytes and contains the model, relationships
and content-types files, with no source/audit sidecar embedded. Its base object has
local Z bounds -1.6 to 0; the combined Buildings_Verification object has 431,298
vertices and local Z bounds -0.2 to 27.696743457352092. These layer-wide bounds do
not independently identify the Pentagon or recover its source tags/map scale.

The Pentagon roof measurements supplied by the user, approximately 7.94 mm above
the map versus 0.80 mm at horizontal scale, imply approximately 9.93x exaggeration.
Treat those as supplied measurements, not independently georeferenced measurements
from this archive. The active tier mapper provides a plausible explanation; this
is not evidence of a general footprint or meshing defect. Exact attribution to a
particular source height/tier requires the DC source snapshot and generation settings.

Code findings:

- `buildings.py:_tiered_building_height_mapper` makes low-rise subtiers of
  1.2/1.6/2.0 mm, maps mid-rise signals into 3-9 mm, and maps high rises using
  population percentiles. It derives storey signals from explicit heights, levels
  or class fallbacks. This deliberately creates architectural relief.
- Its scale factor is `clip(sqrt(mm_per_m / 0.04), 0.45, 1.0)`. The effective
  ceiling is `max(1.2, configured_max * scale_factor)`. The existing maximum is
  therefore not a direct final-mm control; low/mid tiers are not globally scaled
  by reducing that maximum. Inferred classes can also have visual caps.
- `min_building_height_mm` is accepted/passed through but is not read by the active
  building generation body. Exposing it unchanged would create an ineffective knob.
- Studio currently offers Maximum building height, default 30 mm, range 9-31.75 mm.
  This alone cannot provide true proportional scaling or a sub-mm building floor.
- Roof/eave/min-height fractions share each element's mapped total, but a 0.6 mm
  minimum body and elevated-part support adjustments can change the final result.
  Groups replace member profiles with a shared flat mass. Stadium enhancements
  have their own millimetre parameters and can bypass ordinary height extrusion.

## Proposed controls and exact meaning

| Control | Proposed behavior |
|---|---|
| Height mode | Existing architectural relief remains the legacy default. Proportional is opt-in and uses source metres times horizontal mm/metre. No population percentile or class visual cap in proportional mode. |
| Building height multiplier | Positive scalar applied once after the chosen base mapping, before final limits. 1x leaves that mapping unchanged. Independent of terrain exaggeration; never scale absolute world Z or base thickness. |
| Minimum visible height (mm) | Optional final-height floor; raises short buildings, does not filter them out. Off is allowed in proportional mode. Show how many buildings are raised. |
| Maximum visible height (mm) | Optional final-height cap including roof. Applied after multiplier; does not stretch other buildings to fill the range. Show capped count. Validate min <= max. |
| Building override | Select stable source/assembly ID; choose Inherit, corrected source height in metres, or exact final top height in mm above assembly ground datum. Show provenance and Reset. |
| Missing-height fallback (advanced) | Explicit metres, used only when trustworthy height/levels are missing; label inferred values. Unknown remains unknown and still receives physical printability checks. |

Preserve old projects through an explicit legacy settings/schema path, including
the existing maximum semantics. New optional final-mm limits must have separate
fields; do not reinterpret the old maximum or activate its unused minimum during
migration. For the new proportional mode, 1x with limits off is the unambiguous
true-scale starting point. Existing saved projects still open in legacy mode.

For an ordinary ground-attached building in proportional mode:

`raw_mm = source_total_height_m * horizontal_mm_per_m`

`requested_mm = clamp(raw_mm * multiplier, optional_min, optional_max)`

An exact final-mm override replaces that requested value and bypasses the global
multiplier. If it conflicts with enabled final limits, display the conflict and
require a resolved value or an explicit per-building limit exception. Never silently
clip an exact override. Correcting source metres follows the normal mode/multiplier
pipeline and must not overwrite original OSM tags.

Using the supplied Pentagon true-scale measurement: 1x is 0.80 mm; 2x is 1.60 mm;
with a 1.20 mm minimum, 1x becomes 1.20 mm. A 0.1x multiplier on the current 7.94 mm
relief would request 0.794 mm before limits, but it would also shrink every other
building. An individual override avoids that global consequence. These are
arithmetic illustrations, not regenerated DC models or a claim of printability.

## Roofs, parts, groups, protection, and cache

- Keep footprint XY fixed for height edits. Scale roof/eave/bottom coordinates
  consistently around an assembly's ground datum, retaining holes and part IDs.
  For proportional mode without limits, use one vertical scale across dependent
  parts. Do not independently floor each part, which can create gaps/overlaps.
- Resolve parent/part/support assemblies before overrides or limits. Apply limits
  to the assembly envelope and derive a coherent transform. Independent part edits
  require support revalidation and a displayed adjustment/conflict. Existing support
  repairs cannot secretly invalidate an exact requested top height.
- The current minimum-body rule can consume a short pitched roof. In proportional
  mode preserve proportions first and report thin bodies/roofs; do not silently
  inflate them. Offer an explicit printable-relief adjustment if required.
- Protected status remains independent of height style. Changing height cannot
  silently remove a landmark or dependent part. Route-exclusion rules still apply.
- Height affects grouping eligibility, group height, and height/width diagnostics.
  Recompute affected decisions when it changes. An overridden member must leave a
  merged group or receive a clearly identified whole-group override; never silently
  alter independently selected neighbors. Keep simplification and grouping optional.
- Stadium/landmark recipes need a defined override adapter. Decide whether their
  envelope scales or their dedicated dimensions are edited, validate roof thickness
  and supports, and label unsupported controls. A global building control must not
  appear to apply successfully while a recipe ignores it.
- Cache immutable source/preparation separately from height-dependent groups and
  meshes. Keys include mode/version, multiplier, effective limits, override revision,
  source/assembly IDs and hashes, height provenance, roof/recipe parameters, support
  policy, map scale, terrain and grouping inputs. Exact XY width cache entries can
  survive a height-only edit; height-dependent diagnostics and support audits cannot.
- Debounce/cancel preview work and discard outdated revisions. A provisional vertical
  preview must be marked pending validation. Reuse roads/base and valid prepared
  footprints; do not rerun downloads on each slider movement. No instant-update
  promise until regrouping and export/audit performance is measured.
- Preview and export consume one resolved group manifest. Save must regenerate the
  matching 3MF/audit after edits, not copy the previous artifact. Follow the
  [filter architecture plan](BUILDING_FILTER_PLAN.md) for selection and cache boundaries.

## Other tools: verified primary-source controls

Reviewed public documentation/interfaces on 2026-09-24; no competitor models were
generated or quality/performance claims independently tested.

| Tool | Documented controls | Lesson for Trace |
|---|---|---|
| [PrintPal](https://printpal.io/tools/scale-map-generator) | Vertical scale, separate building-height scale, fallback building height, layer inclusion, road height and water depth. | Give units and distinguish source fallback from artistic scaling. |
| [TerraPrinter](https://terraprinter.com/) | Terrain exaggeration, per-layer height/line-width factors, resolution/smoothing, custom-building heights. Feature-height factors stack on terrain exaggeration. | Per-layer control is useful; Trace should make the combined effect explicit and keep building scaling independent by default. |
| [TouchTerrain](https://chharding.github.io/TouchTerrain_for_CAGEO/) | Z exaggeration relative to horizontal scale, print resolution, tile size and base thickness; advanced nonlinear terrain settings. | Separate physical model scale, vertical exaggeration and output resolution. This is a terrain precedent, not building/part validation. |
| [Blosm](https://github.com/vvoovv/blosm/wiki/Documentation) | Default roof shape, level height, fallback level counts/weights, combined versus separate building objects retaining OSM tags; complex parts stay together. | Expose source assumptions and preserve inspectable identity/assemblies. Avoid randomized fallback heights in Trace's reproducible workflow. |

[Map2Model](https://map2model.com/) currently reports it has shut down. Historical
screenshots or tutorials should not be treated as a verified current interface.

Start the main panel with mode, multiplier and optional limits. Put source fallback,
floor conversion, recipe details and physical thresholds in Advanced. Put individual
height/provenance controls in the building inspector. Show requested and effective
height, true-scale height, inferred/source status, floor/cap reason, protection and
group membership. Presets should store explicit settings and remain editable.

## Comparison gate before any default change

No defaults change is proposed now. The available evidence is asymmetric: Peachtree
has a frozen full source-backed run, DC currently has an exported mesh, and Houston
has a small existing fixture. This is not yet a multi-map visual comparison.

| Map/case | Required comparison |
|---|---|
| DC marathon / Pentagon | Broad low landmark, courtyard and dependent roof parts; obtain exact source/frame/settings, reproduce the supplied height, compare relief, proportional 1x/2x and an individual override. |
| Peachtree | Reuse frozen sources/settings; check dense groups, low-rise visibility, towers, protected buildings and stadium recipes. |
| Houston urban map | Use the compact fixture for deterministic mechanics, then a full saved route/source snapshot with towers and low-rise neighborhoods for visual judgment. The tiny fixture alone is insufficient. |
| Rural or hilly route | Missing height tags, pitched roofs, sparse buildings and terrain anchoring; include elevated/support-dependent parts. |

For each fixed input snapshot, compare existing relief, proportional 1x, proportional
2x, and one explicitly recorded floor/cap preset at identical XY scale/camera. Record
source-height provenance, p10/p50/p90/p99 output height, floor/cap counts, roof/body
thickness flags, group/omission changes, protected retention and support repairs.
Show close-ups and whole-map views. Validate unchanged XY except explicitly enabled
grouping/simplification, all-triangle route clearance, watertightness, support,
preview/export identity, cache invalidation and cold/warm generation latency.
Use slicer checks and representative physical prints before claiming printable
quality, particularly near 0.80 mm and for small pitched roofs.

Deliver in separate reviewable steps: (1) height-policy contract/schema and legacy
fingerprint tests, (2) opt-in proportional mode/multiplier/final limits, (3) assembly
inspector/overrides and recipe compatibility, (4) incremental preview and export
integration. Test no-op legacy settings exactly; test multiplier/clamp precedence,
part/roof consistency, height-dependent regrouping and stale-cache rejection. A
future defaults proposal needs the comparison results and explicit review.

## Performance prerequisite update

The follow-up performance pass has 421 passing tests, six exact manual-mode
fingerprints and a full frozen Peachtree replay with identical exported geometry
and non-runtime audit fields. The paired profiled run improved from 15m13s to
11m15s; see [PERFORMANCE_FOLLOWUP.md](PERFORMANCE_FOLLOWUP.md). This does not
establish interactive edit latency. This plan remains unimplemented; preserve
the shared preview/export manifest contract and the multi-map comparison gate
before any future change to defaults.
