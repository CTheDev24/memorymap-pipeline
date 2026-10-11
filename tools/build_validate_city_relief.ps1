param([switch]$BalancedValidationOnly)
# Windows PowerShell treats native stderr (including PyInstaller INFO) as
# error records. Native success is determined by explicit exit-code checks.
$ErrorActionPreference = 'Continue'
$py = '.venv\Scripts\python.exe'
& $py -m PyInstaller --clean --noconfirm --distpath dist/city-relief-reviewed --workpath build/city-relief-reviewed memorymap-desktop.spec *> benchmarks/outputs/city-relief/package-final.log
if ($LASTEXITCODE -ne 0) { throw 'Package build failed' }
& $py tools/verify_city_relief_package.py 'dist/city-relief-reviewed/Trace Studio.exe' benchmarks/outputs/city-relief/package-provenance.json
if ($LASTEXITCODE -ne 0) { throw 'Package source provenance failed' }
$reviewExe = (Resolve-Path 'dist/city-relief-reviewed/Trace Studio.exe' -ErrorAction Stop).Path
$smokeReport = [IO.Path]::GetFullPath('benchmarks/outputs/city-relief/package-startup-smoke.json')
$smokeJob = Start-Process -FilePath $reviewExe -ArgumentList @('--smoke-test', ('"{0}"' -f $smokeReport)) -WindowStyle Hidden -PassThru -Wait -ErrorAction Stop
if ($smokeJob.ExitCode -ne 0) { throw 'Package smoke failed' }
if ($BalancedValidationOnly) {
    foreach ($mode in @('existing', 'balanced')) {
        & $py tools/compare_la_city_relief.py --mode $mode --line-width .42 *>> benchmarks/outputs/city-relief/prototypes-final.log
        if ($LASTEXITCODE -ne 0) { throw "Source prototypes failed: $mode" }
    }
} else {
    & $py tools/compare_la_city_relief.py --line-width .42 *> benchmarks/outputs/city-relief/prototypes-final.log
    if ($LASTEXITCODE -ne 0) { throw 'Source prototypes failed' }
}
foreach ($area in @('downtown', 'neighborhood', 'landmark')) {
    powershell -NoProfile -File tools/validate_city_relief_package.ps1 -Manifest "benchmarks/outputs/city-relief/packaged-$area-manifest.json"
    if ($LASTEXITCODE -ne 0) { throw "Package prototype failed: $area" }
}
& $py tools/check_city_relief_package_outputs.py
if ($LASTEXITCODE -ne 0) { throw 'Geometry verification failed' }
& $py tools/finalize_la_city_relief.py --baseline-pid 0 *> benchmarks/outputs/city-relief/final-validation.log
if ($LASTEXITCODE -ne 0) { throw 'Full validation failed' }
