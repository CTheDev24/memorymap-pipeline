param(
    [ValidateSet("all", "downtown", "neighborhood", "landmark")][string]$Area = "all",
    [string]$SourceRoot = "benchmarks/outputs/city-relief/prototypes",
    [string]$OutputRoot = "benchmarks/outputs/city-relief/slicer"
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "benchmarks/outputs/city-relief").Path
$profiles = Join-Path $root "slicer"
$sourceDirectory = (Resolve-Path $SourceRoot).Path
$slicer = [IO.Path]::GetFullPath($OutputRoot)
New-Item -ItemType Directory -Path $slicer -Force | Out-Null
$areas = if ($Area -eq "all") { @("downtown", "neighborhood", "landmark") } else { @($Area) }
foreach ($name in $areas) {
    $source = Join-Path $sourceDirectory "$name/balanced/sample.3mf"
    $audit = [IO.Path]::ChangeExtension($source, ".audit.json")
    $output = Join-Path $slicer "$name-final-sliced.3mf"
    $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLower()
    $sourceAudit = Get-Content -LiteralPath $audit -Raw | ConvertFrom-Json
    if ($sourceAudit.model_sha256 -ne $hash) { throw "Source audit hash mismatch: $name" }
    Copy-Item -LiteralPath $audit -Destination (Join-Path $slicer "$name-final-source.audit.json")
    Copy-Item -LiteralPath $source -Destination (Join-Path $slicer "$name-final-source.3mf")
    $arguments = @("--load-settings", ('"{0};{1}"' -f (Join-Path $profiles "machine.json"), (Join-Path $profiles "process.json")), "--load-filaments", ('"{0}"' -f (Join-Path $profiles "filament.json")), "--slice", "0", "--export-3mf", ('"{0}"' -f $output), ('"{0}"' -f $source))
    $job = Start-Process -FilePath "C:/Program Files/Bambu Studio/bambu-studio.exe" -ArgumentList $arguments -WorkingDirectory $slicer -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput (Join-Path $slicer "$name-final.stdout.log") -RedirectStandardError (Join-Path $slicer "$name-final.stderr.log")
    if ($job.ExitCode -ne 0) { throw "Slicing failed: $name, exit $($job.ExitCode)" }
    $result = Get-Content -LiteralPath (Join-Path $slicer "result.json") -Raw | ConvertFrom-Json
    if ($result.return_code -ne 0) { throw "Slicer reported failure: $name" }
    $result | Add-Member -NotePropertyName source_sha256 -NotePropertyValue $hash
    $result | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $slicer "$name-final-result.json") -Encoding utf8
    Write-Output "Sliced ${name}: $output"
}
