param([string]$Executable = "dist/city-relief-supported-reviewed/Trace Studio.exe")
$ErrorActionPreference = "Stop"
$py = ".venv/Scripts/python.exe"
$root = "benchmarks/outputs/city-relief"
& $py tools/verify_city_relief_package.py $Executable "$root/supported-package-provenance.json"
if ($LASTEXITCODE -ne 0) { throw "Package provenance failed" }
$exe = (Resolve-Path $Executable).Path
$smoke = [IO.Path]::GetFullPath("$root/supported-package-startup-smoke.json")
$job = Start-Process -FilePath $exe -ArgumentList @("--smoke-test", ('"{0}"' -f $smoke)) -WindowStyle Hidden -PassThru -Wait
if ($job.ExitCode -ne 0) { throw "Package startup smoke failed" }
foreach ($area in @("full-roads", "downtown", "neighborhood", "landmark")) {
    $source = Get-Content "$root/indexed-$area-manifest.json" -Raw | ConvertFrom-Json
    $source.report_path = [IO.Path]::GetFullPath("$root/supported-$area-report.json")
    $source.request.output_path = $source.request.output_path.Replace("indexed-", "supported-")
    $manifest = "$root/supported-$area-manifest.json"
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($manifest), ($source | ConvertTo-Json -Depth 50), [Text.UTF8Encoding]::new($false))
    & powershell -NoProfile -File tools/validate_city_relief_package.ps1 -Manifest $manifest -Executable $Executable
    if ($LASTEXITCODE -ne 0) { throw "Native generation failed: $area" }
    $verifyArgs = @("tools/verify_city_relief_candidate.py", $source.request.output_path, "$root/supported-$area-verification.json")
    if ($area -ne "full-roads") {
        $verifyArgs += @("--previous", "$root/packaged-prototypes/$area/sample.3mf")
    }
    & $py @verifyArgs
    if ($LASTEXITCODE -ne 0) { throw "Serialized geometry failed: $area" }
    if ($area -ne "full-roads") {
        & $py tools/render_city_relief_preview.py ([IO.Path]::ChangeExtension($source.request.output_path, ".glb")) 24 24
        if ($LASTEXITCODE -ne 0) { throw "Coupon render failed: $area" }
    }
}
& powershell -NoProfile -File tools/slice_city_relief_prototypes.ps1 -SourceRoot "$root/supported-prototypes" -OutputRoot "$root/supported-slicer"
if ($LASTEXITCODE -ne 0) { throw "Current coupon slicing failed" }
& $py tools/check_city_relief_toolpaths.py --root "$root/supported-slicer"
if ($LASTEXITCODE -ne 0) { throw "Slicer diagnostics failed" }
Write-Output "Bounded packaged recovery validation completed. Full building generation and physical acceptance remain pending."
