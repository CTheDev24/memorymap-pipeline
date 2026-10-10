param([string]$Manifest = "benchmarks/outputs/city-relief/packaged-full-manifest.json")
$ErrorActionPreference = "Stop"
$exe = (Resolve-Path "dist/city-relief-reviewed/Trace Studio.exe").Path
$inputPath = (Resolve-Path $Manifest).Path
$definition = Get-Content -LiteralPath $inputPath -Raw | ConvertFrom-Json
$reportPath = $definition.report_path
$clock = [Diagnostics.Stopwatch]::StartNew()
$job = Start-Process -FilePath $exe -ArgumentList @("--validate-map", ('"{0}"' -f $inputPath)) -WindowStyle Hidden -PassThru
$peak = [int64]0
$children = @{}
while (-not $job.HasExited) {
    foreach ($item in (Get-CimInstance Win32_Process -Filter "Name = 'Trace Studio.exe'" | Where-Object { $_.ParentProcessId -eq $job.Id })) {
        $children[$item.ProcessId] = $true
    }
    foreach ($pidToCheck in @($job.Id) + @($children.Keys)) {
        $process = Get-Process -Id $pidToCheck -ErrorAction SilentlyContinue
        if ($process) { $peak = [Math]::Max($peak, $process.PeakWorkingSet64) }
    }
    Start-Sleep -Milliseconds 1000
    $job.Refresh()
}
$clock.Stop()
$metrics = @{ executable = $exe; executable_sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLower(); elapsed_wall_seconds = $clock.Elapsed.TotalSeconds; peak_process_working_set_bytes = $peak; exit_code = $job.ExitCode; report_path = $reportPath; measurement = "Maximum OS peak working set of executable parent or generation child; wall time includes one-file extraction" }
$metrics | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath ([IO.Path]::ChangeExtension($reportPath, ".performance.json")) -Encoding utf8
$report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
if (-not $report.passed -or $job.ExitCode -ne 0) { throw "Packaged generation failed: $reportPath" }
Write-Output "Packaged generation passed: $reportPath"
