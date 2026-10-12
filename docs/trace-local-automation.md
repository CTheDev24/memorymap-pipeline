# Local Trace automation

Branch: `codex/trace-local-automation`, based on reviewed Map Style head `9885dc5`.

The Windows Computer Use runtime currently fails before app discovery with
`failed to write kernel assets: The system cannot find the path specified. (os error 3)`.
No connection to the already-running installed application has been established.

The fallback is an explicit one-job desktop session. It loads the existing Trace
project schema, restores the same UI controls, derives the desktop configuration,
and runs the same `GenerationWorker` as the Regenerate button. There is no HTTP
listener, remote access, printer action, or separate geometry engine.

## Run from the repository

Save a **Trace project** (`*.trace.json`) from Trace Studio. A Bambu `.3mf` is not
a Trace project: it does not contain the original GPX and editable generator frame.
Make a copy of the Trace project for each experiment, changing its existing fields
(for example `road_class_widths_mm.local`) rather than the original owner project.

Create a request JSON:

```json
{
  "version": 1,
  "action": "generate",
  "project": "chicago.trace.json",
  "job_directory": "chicago-local-width-06",
  "sources": {
    "roads_file": "frozen-roads.geojson"
  }
}
```

`sources` is optional. Without it, the existing engine uses its usual acquisition
and caches. Supported frozen inputs are `roads_file`, `buildings_file`,
`water_file`, and `landcover_file`. Relative request paths resolve beside the
request; a relative GPX path resolves beside the Trace project. The output
directory must not exist. Reusing a job is rejected rather than overwriting files.

Use `"action": "load"` to open an interactive instance with the project restored
without starting generation. Its status becomes `ready`; the window remains open
until closed by the operator, after which status becomes `closed`. The default
action is `generate`. Load-mode status records the initial restored settings only;
it is not a live settings mirror or an external command listener.

```powershell
.venv/Scripts/python.exe -m memorymap_pipeline.desktop --automation job.json
```

The normal `memorymap-desktop` console entry point also accepts `--automation`.
The installed executable is unchanged. A separate review package supports:

```powershell
& 'Trace Studio.exe' --automation job.json
```

## Results and progress

The separate Trace window displays progress and is locked during the job, preventing
settings changes and accidental close while its worker runs. It closes after the
worker thread finishes. Existing Trace windows are not controlled or modified.

Read `job_directory/status.json` while it runs. Status snapshots are replaced
atomically, with states `starting`, `running`, `complete`, or `failed`, progress,
warnings, and the effective generation settings. Successful jobs contain:

- `map.3mf` and matching `map.audit.json`
- `effective.trace.json`, recording the restored desktop settings
- `preview.glb`, when preview export succeeds
- `staging/`, retaining the worker output for diagnosis

Only `state=complete` qualifies a result for consumption. Completion requires a
matching audit hash, no failed layer state, and desktop stale-result acceptance.
Generation failures return exit code 1 and a diagnostic status without reporting
an earlier result as successful. Invalid requests fail before starting generation;
wrong argument count returns exit code 2. There is no automatic retry, cancellation,
resume, or recovery of an externally terminated process in this first version.

The output may be inspected in the live job window during generation; WebGL preview
availability remains dependent on the Windows graphics environment. Preview failure
does not discard a valid model, matching the existing desktop behavior.

## Validation and remaining gates

Local Windows suite after load-mode support: **534 passed**, 106 existing dependency
warnings, 33.58 seconds.
Scoped Ruff and `git diff --check` pass. Both desktop entry points dispatch the
explicit request; wrong argument counts are rejected without normal app launch.

Regression coverage exercises fresh-directory isolation, relative paths, frozen
source allowlisting, atomic status replacement, mismatched/failed audit rejection,
and actual Qt worker success/failure. The worker success test generates a small
route/base model with no network calls; only the WebEngine view is replaced by a
test widget. Legacy hidden border and route-layer-height values remain preserved.

Live Computer Use remains blocked by its runtime initialization error. The installed
EXE remains unchanged. Full generation smoke, actual Windows WebEngine preview,
and Chicago physical acceptance are separate gates.
No full LA generation, printer job, installed-app replacement, or release is part
of this change.

## Packaged Chicago load verification

A separate EXE was built from code commit `11539a7` at
`dist/trace-automation-review/Trace Studio.exe`. Its SHA256 is
`fd036880f3bc4391281681211af9e2856e83c0b43a26da179ca72ffb6ae14119`.
The package verifier confirms every checked module matches source, including
`desktop.automation`, the desktop worker, and the existing geometry engines.

The Downloads Chicago Trace project was copied to
`benchmarks/outputs/trace-automation/chicago.trace.json`. The copy restores explicit
Street Grid, audit-matching major-road widths, and the owner's requested **0.6 mm
local width**. The original Downloads project was not changed.

The packaged load-only session reports `ready`, with 163 route points, buildings
disabled, the original 190 × 240 mm frame, and widths 1.2/0.8/0.6/0.4 mm for
motorways/main/local/service. The visible native window title is
`Trace Studio — Chicago Marathon`; the process is responding. This validates
packaged project loading, not full map generation or rendered 3D preview.

Artifacts: `benchmarks/outputs/trace-automation/package-provenance.json`,
`session-inputs.json`, and `chicago-visible-session/status.json` plus its
`effective.trace.json`. The initial hidden launch restored the project but exposed
no visible native window. Only those verified, owned idle review processes were
stopped and replaced by the visible interactive launch. The installed session was
not stopped or modified. The visible review instance is left open for the owner.
