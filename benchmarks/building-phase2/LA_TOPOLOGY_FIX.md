# LA Overpass relation assembly fix

The reported exception at -118.28627822947087, 34.014113167559728 reproduces in
OSM relation 6100581. Its outer boundary is split across two open ways. The old
reader independently closed each member into a polygon and unioned the results,
creating crossing edges before it reached downstream polygon repair. This was an
app assembly bug, not evidence that the GPX or the entire source building was bad.

The same cached dataset contains 14 relations that raised topology exceptions.
All 14 now produce valid polygons. The reader joins unordered/reversed fragments
into closed rings first, repairs genuinely invalid rings with make_valid, retains
polygonal pieces, and subtracts assembled courtyard rings. It does not force-close
incomplete chains. Unresolved tiled sources retain their IDs in the building audit.

## Validation

- All 421,196 cached source elements replayed through the corrected reader.
- 5,574 relations; zero returned invalid geometries; no unhandled topology errors.
- Four unresolved records: way 1525629535 (open outline), relation 9583872 (nested
  relation members), relations 6318992 and 6353312 (inner member only, no outer).
- Exact failing source is saved as tests/fixtures/la_relation_6100581.json.
- The reported relation preserves its courtyard and passes the production building
  mesh pipeline with proportional 2x, 0.8 mm floor and print optimization. Its mesh
  is watertight. An incomplete variant is explicitly recorded as unresolved.
- Final full suite: 453 passed, 76 warnings.
- Replacement package: dist/la-topology-20260927/Trace Studio.exe (complete).

This validates cached input assembly and focused production geometry, not a full
LA model generation or physical print. The previous full-map replay was stopped
in road processing; no complete replacement LA 3MF is claimed.

Packaged Qt startup, offline model generation and actual WebEngine/WebGL preview
passed. Six bundled modules match tested source. Executable SHA-256:
`21f8d5397ed08a685ea8a5ba2a84fd410ec2c1b1297e2d09c464d2debf20dc8e`.
