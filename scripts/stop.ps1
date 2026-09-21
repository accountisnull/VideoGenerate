$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$statePath = Join-Path $projectRoot 'data\processes.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output 'No managed processes.'; exit 0 }
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
foreach ($entry in $state.processes) {
    $process = Get-Process -Id $entry.id -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.started) {
        Stop-Process -Id $process.Id
        Write-Output "Stopped $($entry.name) ($($entry.id))"
    }
}
Remove-Item -LiteralPath $statePath
