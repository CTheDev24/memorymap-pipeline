param(
    [string]$SourceRoot = "benchmarks/outputs/map-style-review-fixes/representative",
    [string]$OutputRoot = "benchmarks/outputs/map-style-review-fixes/slicer"
)
$ErrorActionPreference = "Stop"
$profiles = (Resolve-Path "benchmarks/outputs/city-relief/slicer").Path
$sources = (Resolve-Path $SourceRoot).Path
foreach ($style in @("cityscape", "terrain", "street_grid")) {
    $folder = [IO.Path]::GetFullPath((Join-Path $OutputRoot $style))
    New-Item -ItemType Directory -Force $folder | Out-Null
    $source = Join-Path $sources "$style/candidate.3mf"
    $audit = Get-Content ([IO.Path]::ChangeExtension($source, ".audit.json")) -Raw | ConvertFrom-Json
    $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $audit.model_sha256) { throw "Audit identity mismatch: $style" }
    $output = Join-Path $folder "sliced.3mf"
    $arguments = @("--load-settings", ('"{0};{1}"' -f (Join-Path $profiles "machine.json"), (Join-Path $profiles "process.json")), "--load-filaments", ('"{0}"' -f (Join-Path $profiles "filament.json")), "--slice", "0", "--export-3mf", ('"{0}"' -f $output), ('"{0}"' -f $source))
    $job = Start-Process -FilePath "C:/Program Files/Bambu Studio/bambu-studio.exe" -ArgumentList $arguments -WorkingDirectory $folder -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput (Join-Path $folder "stdout.log") -RedirectStandardError (Join-Path $folder "stderr.log")
    if ($job.ExitCode -ne 0) { throw "Slicing failed: $style, exit $($job.ExitCode)" }
    $result = Get-Content (Join-Path $folder "result.json") -Raw | ConvertFrom-Json
    if ($result.return_code -ne 0) { throw "Slicer returned failure: $style" }
    $result | Add-Member -NotePropertyName source_sha256 -NotePropertyValue $hash
    $result | ConvertTo-Json -Depth 30 | Set-Content (Join-Path $folder "verified-result.json") -Encoding utf8
    Write-Output "CLI sliced ${style}. GUI assignment, toolpath inspection and physical acceptance remain pending."
}
