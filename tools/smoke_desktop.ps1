param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [string]$ReportPath
)
$ErrorActionPreference = "Stop"
$executablePath = (Resolve-Path -LiteralPath $Executable).Path
if (-not $ReportPath) {
    $ReportPath = Join-Path (Split-Path -Parent $executablePath) "startup-smoke.json"
}
if (-not [IO.Path]::IsPathRooted($ReportPath)) {
    $ReportPath = Join-Path (Get-Location).Path $ReportPath
}
if ([IO.Path]::GetFullPath($ReportPath) -ieq $executablePath) {
    throw "Smoke report path must differ from the executable path."
}
[IO.Directory]::CreateDirectory((Split-Path -Parent $ReportPath)) | Out-Null
# A previous passing report must never mask an executable that exits too early.
Set-Content -LiteralPath $ReportPath -Encoding utf8 -Value '{"passed":false,"stage":"waiting for executable"}'
$smokeProcess = Start-Process -FilePath $executablePath -WorkingDirectory (Split-Path -Parent $executablePath) -WindowStyle Hidden -PassThru -ArgumentList @(
    "--smoke-test", ('"{0}"' -f $ReportPath)
)
if (-not $smokeProcess.WaitForExit(120000)) {
    Write-Output (Get-Content -LiteralPath $ReportPath -Raw)
    # Windows PowerShell 5 uses .NET Framework, whose Process.Kill has no
    # tree-kill overload. Keep the same failure gate on both supported shells.
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        $smokeProcess.Kill($true)
    } else {
        $smokeProcess.Kill()
    }
    throw "Packaged startup test exceeded 120 seconds. See $ReportPath"
}
if (-not (Test-Path -LiteralPath $ReportPath)) {
    throw "Packaged startup test produced no report (exit $($smokeProcess.ExitCode))."
}
$report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
if ($smokeProcess.ExitCode -ne 0 -or -not $report.passed -or -not $report.frozen -or
    -not $report.window_created -or -not $report.preview.ready -or -not $report.preview.canvas) {
    Write-Output (Get-Content -LiteralPath $ReportPath -Raw)
    throw "Packaged startup/generation/preview check failed (exit $($smokeProcess.ExitCode)). See $ReportPath"
}
$systemIcu = Join-Path $env:SystemRoot "System32/icuuc.dll"
if ([IO.Path]::GetFullPath($report.loaded_icuuc) -ine [IO.Path]::GetFullPath($systemIcu)) {
    throw "Qt loaded an unexpected ICU DLL: $($report.loaded_icuuc)"
}
Write-Output "Packaged Qt startup, offline 3MF generation and WebEngine preview passed: $ReportPath"
