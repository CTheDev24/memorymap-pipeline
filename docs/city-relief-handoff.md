# City relief review handoff — 2026-10-10

Work is paused at the user's request on `codex/city-relief`. Do not merge or replace the installed application automatically. The implementation and reproduction tools are ready for code review; full-map and physical acceptance remain unfinished.

## Verified work

- Full regression suite: **489 passed**, 85 warnings, 26.20 seconds.
- Clean packaged executable passed bundled module/asset provenance and offline startup, export, and WebGL smoke checks.
- Nine source comparisons (three LA areas × Existing/Balanced/Dense) and three packaged Balanced coupons completed. Source/package geometry, audit hashes, Existing compatibility, and previously sliced geometry equivalence checks passed.
- Three coupons sliced with the actual A1 0.4 mm nozzle PLA profile (outer line width 0.42 mm). Toolpath presence is diagnostic; it does not establish strength, complete silhouettes, or physical acceptance.
- Full Existing LA baseline completed with buildings: 5,328.82 seconds, peak RSS 6,357,286,912 bytes. It represents 11,557 sources as 9,758 outputs from 421,240 decoded polygon records. Its 3MF SHA-256 is `d54d77f0dd623f9589e445db4fd7db10f8c22e683d0c729957597a61e2167557`.

The baseline predates final export-cleanup accounting fixes: four unsupported shells (28 triangles) were removed during export, so the historical audit's face ranges refer to the earlier mesh. Its original audit is preserved. Baseline review renders were reconstructed from the actual 3MF; the matching export hash is verified.

## Paused checkpoint and unresolved acceptance

The task-owned validation process tree was stopped. The packaged full City run reached **100,800 / 400,061** neighborhood candidates at **3,144.78 seconds**. No full City 3MF/audit was produced. The checkpoint is `benchmarks/outputs/city-relief/full-validation-state.json`; it records `paused_by_user`, `passed: false`, and `release_accepted: false`.

A 10-second profiler sample found polygon intersections and spatial queries dominating typification. This is an investigation sample, not a completed performance comparison. Controlled full-map stage timings and memory gates remain pending. Restarting generation reuses frozen inputs/cache but restarts computation; there is no in-memory optimizer checkpoint resume.

City Hall and Disney Hall could not safely achieve the requested 1.5× footprint emphasis in the landmark coupon. They retained feasible scale, with aspect-ratio height reductions and unresolved thin geometry recorded. Landmark recognition is not approved. GPX edition remains unconfirmed (user estimates 2025 or 2026); use verified route proximity rather than claiming a particular edition.

Physical coupons have not been printed. Full-map visual review, toolpath gap/fragility review, physical acceptance, and release approval remain required.

## Build identity and local artifacts

Production source was frozen at `1fefa5c`; later commits add review/validation helpers and documentation. Normalized source fingerprint: `112a410183075d9f1dc2db62201d021167585dba55795ec92b7f4918a8506e65`.

Candidate: `dist/city-relief-reviewed/Trace Studio.exe`, SHA-256 `f4598f73a01c9040609c2cc799fa7c36ebe619c675d713747c930959a4c749d8`.

Generated artifacts are ignored and **not included in the Git push**. On Craig's machine, inspect `benchmarks/outputs/city-relief/`: `review.html`, `tests-final.log`, `package-provenance.json`, `package-startup-smoke.json`, `package-output-verification.json`, `performance-investigation.json`, `existing/`, `prototypes/`, `packaged-prototypes/`, and `slicer/`. The review ZIP predates the latest handoff and should not be treated as current. Large source caches and executables are also local only.

## Review and restart

Read `docs/city-relief.md` for behavior, parameters, diagnostics, and acceptance details. Review acquisition failure handling, bounded typification, route/park/water exclusions, shared assembly heights, export cleanup mappings, persistence/stale-result behavior, and Existing compatibility.

To repeat the complete serial build/validation workflow on the configured machine, run `powershell -NoProfile -File tools/build_validate_city_relief.ps1`. For the existing verified package, the full-map entry point is `powershell -NoProfile -File tools/validate_city_relief_package.ps1`; inspect its manifest and source/cache paths first. `tools/finalize_la_city_relief.py` performs post-generation hash, rendering, loss-breakdown, and performance diagnostics. These runs can take substantial time and memory; resume only when requested.
