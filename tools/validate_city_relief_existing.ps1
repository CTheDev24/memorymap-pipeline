param(
    [string]$Executable = "dist/city-relief-supported-reviewed/Trace Studio.exe",
    [ValidateSet("all", "downtown", "neighborhood", "landmark")][string]$Area = "all"
)
$ErrorActionPreference = "Stop"
$root = "benchmarks/outputs/city-relief"
$py = ".venv/Scripts/python.exe"
$areas = if ($Area -eq "all") { @("downtown", "neighborhood", "landmark") } else { @($Area) }
foreach ($area in $areas) {
    $definition = Get-Content "$root/supported-$area-manifest.json" -Raw | ConvertFrom-Json
    $baseline = Get-Content "$root/prototypes/$area/existing/sample.audit.json" -Raw | ConvertFrom-Json
    $definition.request.config = $baseline.settings
    $definition.report_path = [IO.Path]::GetFullPath("$root/supported-existing-$area-report.json")
    $definition.request.output_path = [IO.Path]::GetFullPath("$root/supported-existing/$area/sample.3mf")
    $manifest = "$root/supported-existing-$area-manifest.json"
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($manifest), ($definition | ConvertTo-Json -Depth 50), [Text.UTF8Encoding]::new($false))
    & powershell -NoProfile -File tools/validate_city_relief_package.ps1 -Manifest $manifest -Executable $Executable
    if ($LASTEXITCODE -ne 0) { throw "Existing native generation failed: $area" }
    $verification = "$root/supported-existing-$area-verification.json"
    & $py tools/verify_city_relief_candidate.py $definition.request.output_path $verification --previous "$root/prototypes/$area/existing/sample.3mf"
    if ($LASTEXITCODE -ne 0) { throw "Existing serialized validation failed: $area" }
    $check = Get-Content $verification -Raw | ConvertFrom-Json
    if (@($check.previous_geometry_identical.PSObject.Properties | Where-Object { $_.Name -ne "roads" -and -not $_.Value }).Count) {
        throw "Unrelated Existing geometry differs from the frozen baseline: $area"
    }
    if (-not $check.previous_geometry_identical.roads) {
        & $py tools/check_existing_road_recovery.py $area
        if ($LASTEXITCODE -ne 0) { throw "Changed Existing roads do not preserve the exact frozen source footprint: $area" }
    }
}
Write-Output "Existing fixtures preserve unrelated geometry; any repaired road mesh matches its exact frozen extrusion input."
