$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$projectRoot = Split-Path $PSScriptRoot -Parent
$statePath = Join-Path $projectRoot 'data\processes.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output 'Not started.'; exit 0 }
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
foreach ($entry in $state.processes) {
    $process = Get-Process -Id $entry.id -ErrorAction SilentlyContinue
    $running = $process -and $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.started
    Write-Output "$($entry.name): running=$running pid=$($entry.id)"
}
$pythonPath = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
$env:VIDEO_STATUS_PORT = "$($state.port)"
try {
    @'
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding='utf-8')
with urllib.request.urlopen('http://127.0.0.1:' + os.environ['VIDEO_STATUS_PORT'] + '/api/health', timeout=3) as response:
    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
'@ | & $pythonPath -
    if ($LASTEXITCODE -ne 0) { throw 'Health request failed. Check data/logs and restart the application.' }
} finally {
    Remove-Item Env:VIDEO_STATUS_PORT
}
