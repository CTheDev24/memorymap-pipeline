# LA building download recovery

The supplied LA GPX has 868 track points and a calculated length of 42.2206 km.
The largest adjacent-point gap is 160.9 m. The selected 240 x 190 mm frame covers
22.908 x 18.135 km. These checks found no obvious coordinate outlier; they do not
verify the route against an official course.

The earlier building fetch used a 20-second HTTP timeout and could export a map
without buildings after all endpoints failed. The LA audit records those failures.

## Change

Frames requiring more than 16 roughly 4 km query cells now use tiled Overpass
requests directly against the frame bounds, including when a query radius is also
supplied. The supplied LA frame requires 30 cells. Smaller frames keep the existing
fetch behavior. Local building files also keep their existing behavior.

Each cell tries the configured endpoints with a 60-second server query limit and
10-second connect / 75-second read timeout. Auth-denied and rate-limited endpoints
are skipped for subsequent cells in that run. Requests are sequential. Completed
responses (including valid empty results) are cached under LocalAppData/Trace Studio/
building-cache-v1. Invalid JSON, missing elements, or Overpass runtime remarks are
not cached. Retrying the same frame reuses completed cells. OSM type and ID deduplicate
buildings crossing cell boundaries. Source tags and full returned geometry are retained.

A failed cell raises an error before building mesh generation or export. The message
identifies the cell and explains that cached work can be reused. This prevents the
large-area path from silently exporting a partial building layer. It does not change
legacy small-frame failure handling.

Successful generation includes download mode, tile counts, cache counts, and source
counts in building_generalization.download in its audit.

## Verification

- 447 tests passed, 76 warnings (full suite, September 27).
- New tests cover frame tiling, boundary deduplication, cache resume after failure,
  rejected partial responses, valid empty-result caching, and routing large frames
  to the tiled downloader even when the caller also supplies a radius.
- User explicitly approved the live test after automatic approval review initially
  blocked geographic-bounds transmission. All 30 LA cells downloaded in 233.2 seconds,
  yielding 421,196 unique source elements. Server 504 responses recovered through
  fallback; a rate-limited endpoint was skipped for subsequent cells.
- Windows executable built at dist/la-recovery-20260927/Trace Studio.exe.
  Actual packaged Qt startup, offline generation and WebEngine/WebGL preview passed.
  Five bundled modules match the tested source, including the new downloader.
- Full LA mesh validation was not completed. An extra full-generation replay was
  explicitly stopped during long road processing after the isolated fetch succeeded.
  No finished LA model or printability pass is claimed. This fix addresses downloading;
  geometry processing of this much larger frame remains a separate performance concern.

Replay script: benchmarks/building-phase2/validate_la_download.py. It uses the supplied
LA GPX, audit settings and frame, and writes a separate model in benchmarks/outputs/
la-download-recovery. It does not overwrite user exports.

Cache replay with HTTP disabled passed: 30 cached cells, zero downloads, identical
421,196 source count, 23.4 seconds. The cache is ready for the new executable on
this computer.
