# Building filters: implementation plan (not implemented)

Status: design following the Phase 2 performance fix. This change does not add
filters, alter defaults, split production meshes, or change Save result as.

## Current constraints

`download_and_build_buildings` classifies footprints, transforms them, extrudes
individual structures and concatenates all buildings into one mesh. The GLB viewer
only changes `object.visible`. `MemoryMapWindow.save_result` calls
`copy_generation_result`, which copies the already-generated 3MF and its audit.
Visibility switches therefore neither avoid initial extrusion nor change export.

## Selection model

Keep independent fields on every source building/part record:

- Semantic type: existing `BuildingClass`, source tags and classification evidence.
  Unknown stays unknown; footprint size must never imply residential use.
- Input printability: robust, marginal, below target, or measurement unresolved,
  measured after frame/route clipping at the configured print scale. Record area,
  core width, thresholds and measurement version. Parts/courtyards retain their
  geometry and dependency context. Core width is not a minimum-wall guarantee.
- Protection: landmark, civic/religious/stadium policy, significant courtyard,
  explicit part or dependent parent. Preserve these protections independently of
  semantic/printability filters and expose the reason in the UI and audit.
- Dependency/assembly ID: parent, parts, supports and landmark enhancements.
- Output printability and disposition: report separately after optional cleanup
  and grouping; never silently relabel the input category used for selection.

Ordinary eligibility is `enabled semantic type AND enabled input printability`.
Unknown uses the same physical checks as known types. Protected assemblies remain
included by default as a clearly shown, locked selection with dependent geometry;
coarse filters cannot silently remove them. The enabled set includes these visible
mandatory selections. A future explicit protected-building override is separate
scope, not an accidental effect of a category checkbox.

Using input printability makes early filtering deterministic. If below-target
sources are disabled, grouping will not try to rescue them. If they are enabled,
optional grouping may improve their output printability. Explain that distinction
in the controls and show both input counts and resulting mesh-group counts.

## Pipeline and cache boundaries

1. Load/source-cache footprints and tags; normalize IDs and parent/part relations.
2. Transform to print coordinates, clip frame and route, resolve parent subtraction
   and protection, then classify semantics and measure physical printability.
   Cache this preparation independently of the enabled-category selection.
3. Resolve enabled source assemblies and dependency closure before expensive
   cleanup, grouping, extrusion and roof/enhancement generation. Disabled sources
   still supply obstacle geometry and dependency metadata where needed: skipping
   extrusion must not permit groups to expand into a disabled building or road.
4. Apply simplification and grouping as independent optional settings. Retain a
   compatibility path for existing manual mode. Do not conflate selection with
   geometry transformation or silently enable either option for unknowns.
5. Group only compatible selection buckets/atomic assemblies. A group records all
   source IDs and cannot contain independently toggleable members. If selection
   changes a group's membership or obstacle context, recompute that affected group;
   never export a cached mass containing a disabled member. Dependency assemblies
   spanning types are presented as atomic selections with explained inclusions.
6. Cache immutable production mesh groups: mesh, footprint, member IDs, dependency
   IDs, selection key, effective settings, output metrics and audit records.
   Start with a bounded in-memory cache; add persistent storage only with versioned
   manifests, atomic writes and corruption/mismatch recovery.
7. Build preview and export from the same resolved enabled group-ID set. Keep
   preview decimation on copies. Enabling an uncached category generates its missing
   groups; disabling one avoids future extrusion and removes its export membership.

Keys must include content hashes of source geometry/tags/relationships, effective
frame/scale/margin, GPX/route/marker exclusion, road/water/protected obstacle masks,
line width/physical thresholds, simplification and grouping settings, height/roof/
landmark versions, terrain/support inputs, vertical exaggeration/embed settings and
algorithm/schema versions. Filenames alone are not sufficient. Separate geometry
keys from presentation colors and camera/visibility state. A cache hit still needs
validated provenance and selection membership. Bound memory and discard partial
results after cancellation or failure.

## Export and UI changes

Introduce a generation result containing a building-group manifest plus existing
nonbuilding layer meshes. Preserve current callers through an adapter while the
Studio worker/preview/export paths migrate. Use stable IDs rather than face ranges
alone; regenerate face-range provenance when composing the final export.

Persist semantic selection, input-printability selection, independent simplify/
group toggles and the protection policy in a new project schema, with explicit
legacy defaults. Display selected/protected/dependency counts and whether changing
a setting requires generation. Worker jobs take an immutable selection revision;
late results from an older revision cannot replace the current preview/export.

`Save result as` must export the current resolved groups, regenerate the model hash
and audit sidecar, and atomically publish the matching pair. Copying the last 3MF is
valid only when its manifest proves it matches the current selection and settings.
If enabled groups are missing or stale, generate them or make the required update
explicit before export. Never silently save the previous selection.

## Delivery order and acceptance checks

1. Introduce source classification/assembly and mesh-group manifests behind the
   existing UI. Validate current manual-mode geometry fingerprints and optimized
   grouping, roof, route and protected-building behavior.
2. Add pre-extrusion selection and independent simplify/group options. Verify cold
   runs skip disabled extrusion/roof work; benchmark total stage time with dense
   road/water barriers and realistic source tags. Test unknowns in every physical
   band, clipped multipart sources, significant courtyards and dependencies.
3. Add bounded group caching and invalidation tests for every geometry dependency.
   Test selection-dependent grouping and stale/cancelled worker results. Measure
   cold generation, warm reuse and partial invalidation separately.
4. Migrate preview, Save result as and audit together. Assert exported membership
   equals the resolved enabled set, includes required dependencies exactly once,
   and excludes every disabled group. Test toggling after generation, save/reopen,
   cache eviction, missing meshes and the single-mesh compatibility adapter.
5. Validate all projected building triangles after composition, including roofs
   and landmark overhangs. Run full offline generation, slice and inspect a fresh
   source-backed Peachtree model before claiming production-print improvements.

Open product choice for implementation: confirm the input-printability labels and
protected-selection presentation with the UI brief. The technical selection/export
contract above should remain explicit regardless of those labels.

## Performance prerequisite update

The follow-up performance pass has 421 passing tests, six exact manual-mode
fingerprints and a full frozen Peachtree replay with identical exported geometry
and non-runtime audit fields. The paired profiled run improved from 15m13s to
11m15s; see [PERFORMANCE_FOLLOWUP.md](PERFORMANCE_FOLLOWUP.md). This does not
establish interactive edit latency. This plan remains unimplemented; preserve
the shared preview/export manifest contract and the multi-map comparison gate
before any future change to defaults.
