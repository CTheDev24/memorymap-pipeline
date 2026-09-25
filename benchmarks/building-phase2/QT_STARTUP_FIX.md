# Windows Qt startup packaging fix

The first height-controls executable failed before opening its window with
`DLL load failed while importing QtCore: The specified procedure could not be found.`
The earlier archive checks proved code/assets were present but did not validate
Windows DLL loading. That gap is now covered by a packaged runtime smoke test.

## Root cause and fix

PyInstaller resolved Qt6Core's `icuuc.dll` dependency to an unrelated ICU 78 DLL
from the build host's Poppler tool directory on PATH. Qt expects unversioned Windows
ICU exports; this DLL lacked all 20 imported conversion functions. The Windows
System32 DLL provides every required function. Loading the Poppler DLL in an
isolated Python process reproduced the user's exact QtCore import error.

Only Qt6Core imports `icuuc.dll` in the collected application binaries. The spec
now excludes the root-level `icuuc.dll`, allowing Windows' system component to
resolve normally. Application height/generation behavior is unchanged.

The diagnostic hashes and missing-symbol comparison are recorded in
`benchmarks/outputs/qt-startup/dll-diagnosis.json`.

## Additional issue caught by the packaged test

The first repaired candidate opened Qt and loaded Windows System32 ICU, but the
synthetic building read exposed another packaging omission: `pyogrio._geometry.pyd`.
Pyogrio catches the resulting import error and reports the misleading message
"GDAL DLL could not be found". Hiding that helper during a source import reproduced
both the reported error and its underlying missing-module exception. GDAL itself
and all of the helper's non-system DLL dependencies were already in the archive.

The spec now explicitly collects Pyogrio's native `.pyd` helpers, which Cython can
import without a discoverable Python import statement. The candidate's failure
report and audit are retained as `missing-geometry-smoke.json` and
`missing-geometry-audit.json` in the diagnostic output directory.

## Regression coverage

`Trace Studio.exe --smoke-test REPORT.json` creates a hidden, non-activating native
Qt window and blocks HTTP/HTTPS requests. It generates a small synthetic map with
proportional buildings, verifies the roof height, exports the 3MF through production
validation, and loads its GLB in the real bundled WebEngine viewer. Success requires
a loaded model and canvas. The report records the actual ICU DLL loaded.

`tools/smoke_desktop.ps1` runs this mode from the executable's own directory,
checks exit status and the report, enforces a 120-second timeout, and verifies the
Windows System32 ICU path. CI runs it before hashing/uploading the build.

The smoke test uses the native Windows graphics backend with the window hidden.
The initial offscreen backend could not render WebGL; the command sandbox also
prevented renderer startup. The final source check ran outside that sandbox,
without disabling Chromium's own sandbox, and passed native Qt startup, generation,
export and actual WebGL/GLB loading. No user GPX or network source was used.

- Full test suite: **442 passed**, 76 warnings.
- Source runtime report: `benchmarks/outputs/qt-startup/source-native-smoke.json`.
- Replacement executable: `dist/height-controls-20260924-fixed/Trace Studio.exe`.
- Actual packaged runtime test: **passed**, exit 0. Native Qt window, proportional
  3MF generation, GLB loading and WebGL canvas all verified. Loaded ICU:
  `C:/Windows/System32/icuuc.dll`.
- `startup-smoke.json`, `build-verification.json` and `.exe.sha256` accompany the
  replacement. Ten bundled modules match the tested source bytecode; required
  assets, OSMnx metadata and Pyogrio's geometry helper are present.
- Size: **369,809,751 bytes**.
- SHA-256: `9c3a0936a863d178e6aeb47ee01ab2709632c3b1c862eac6b6e1b7bad53300f6`.
- This is an offline synthetic runtime check, not a new full DC regeneration or
  physical-print validation.

The old executable remains in `dist/height-controls-20260924/` for diagnosis, with
its README marked superseded. Use the replacement directory
`dist/height-controls-20260924-fixed/`, which passed runtime verification.
