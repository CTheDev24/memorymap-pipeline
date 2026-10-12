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

```powershell
.venv/Scripts/python.exe -m memorymap_pipeline.desktop --automation job.json
```

The normal `memorymap-desktop` console entry point also accepts `--automation`.
The installed executable is unchanged. A future reviewed package build will expose:

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

Local Windows suite: **533 passed**, 106 existing dependency warnings, 36.06 seconds.
Scoped Ruff and `git diff --check` pass. Both desktop entry points dispatch the
explicit request; wrong argument counts are rejected without normal app launch.

Regression coverage exercises fresh-directory isolation, relative paths, frozen
source allowlisting, atomic status replacement, mismatched/failed audit rejection,
and actual Qt worker success/failure. The worker success test generates a small
route/base model with no network calls; only the WebEngine view is replaced by a
test widget. Legacy hidden border and route-layer-height values remain preserved.

Live Computer Use remains blocked by its runtime initialization error. The installed
EXE does not support this new option until a reviewed build is produced. Packaging,
actual Windows WebEngine preview, and Chicago physical acceptance are separate gates.
No full LA generation, printer job, installed-app replacement, or release is part
of this change.
