param([ValidateRange(1, 65535)][int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
$statePath = Join-Path $projectRoot 'data\processes.json'
$logPath = Join-Path $projectRoot 'data\logs'
if ($env:VIDEO_DATA_DIR) { throw 'Unset VIDEO_DATA_DIR when using the standard lifecycle scripts.' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first.' }
& $pythonPath (Join-Path $PSScriptRoot 'check_environment.py')
if ($LASTEXITCODE -ne 0) { throw 'Environment check failed. See docs/installation.md.' }
if (Test-Path -LiteralPath $statePath) {
    $previous = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath | ConvertFrom-Json
    foreach ($entry in $previous.processes) {
        $existing = Get-Process -Id $entry.id -ErrorAction SilentlyContinue
        if ($existing -and $existing.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.started) {
            throw 'A managed process is still running. Use scripts/status.ps1 or scripts/stop.ps1.'
        }
    }
}
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $listener.Start() } catch { throw "Port $Port is unavailable. Use scripts/start.ps1 -Port <unused port>." } finally { $listener.Stop() }
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
$startedProcesses = @()
try {
    $api = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port") -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logPath 'api.out.log') -RedirectStandardError (Join-Path $logPath 'api.err.log') -PassThru
    $startedProcesses += $api
    $worker = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'app.worker') -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logPath 'worker.out.log') -RedirectStandardError (Join-Path $logPath 'worker.err.log') -PassThru
    $startedProcesses += $worker
    $records = @(
        @{ name = 'api'; id = $api.Id; started = $api.StartTime.ToUniversalTime().Ticks.ToString() },
        @{ name = 'worker'; id = $worker.Id; started = $worker.StartTime.ToUniversalTime().Ticks.ToString() }
    )
    @{ port = $Port; processes = $records } | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 -LiteralPath $statePath
    $healthy = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($api.HasExited -or $worker.HasExited) { throw 'Startup process exited; see data/logs.' }
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
            if ($health.application -eq 'video-generate-local' -and $health.worker -eq 'online') { $healthy = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (-not $healthy) { throw 'Startup health check failed; see data/logs.' }
    Write-Output "Started: http://127.0.0.1:$Port"
} catch {
    foreach ($process in $startedProcesses) {
        if (-not $process.HasExited) { $process.Kill() }
    }
    throw
}
