param(
    [string]$FfmpegDirectory,
    [switch]$WithContent
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'This release supports Windows 10/11 x64. See docs/installation.md.'
}
foreach ($command in @('uv', 'node', 'npm.cmd')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Missing $command. Install the prerequisites in docs/installation.md, then open a new PowerShell."
    }
}
if ($env:UV_PROJECT_ENVIRONMENT -or $env:VIDEO_DATA_DIR) {
    throw 'Unset UV_PROJECT_ENVIRONMENT and VIDEO_DATA_DIR for the standard installation.'
}
@'
const [major, minor] = process.versions.node.split('.').map(Number);
if (major !== 22 || minor < 12 || process.arch !== 'x64') process.exit(1);
'@ | & node
if ($LASTEXITCODE -ne 0) { throw 'Install Node.js 22 x64, version 22.12.0 or newer within 22.x.' }
$npmVersion = & npm.cmd --version
if ($LASTEXITCODE -ne 0) { throw 'Unable to run npm.' }
$npmMajor = [int]($npmVersion.Split('.')[0])
if ($npmMajor -lt 10 -or $npmMajor -gt 12) { throw 'npm 10, 11 or 12 is required.' }
$statePath = Join-Path $projectRoot 'data\processes.json'
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath | ConvertFrom-Json
    foreach ($entry in $state.processes) {
        $process = Get-Process -Id $entry.id -ErrorAction SilentlyContinue
        if ($process -and $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.started) {
            throw 'Stop the application with scripts/stop.ps1 before installing dependencies.'
        }
    }
}
if ($FfmpegDirectory) {
    $mediaDirectory = (Resolve-Path -LiteralPath $FfmpegDirectory).Path
    $settings = @{}
    foreach ($name in @('ffmpeg', 'ffprobe')) {
        $executable = Join-Path $mediaDirectory "$name.exe"
        if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw "Missing $executable" }
        $settings[$name] = $executable
    }
    $configPath = Join-Path $projectRoot 'config\runtime.local.json'
    if (Test-Path -LiteralPath $configPath) {
        $config = Get-Content -Raw -Encoding UTF8 -LiteralPath $configPath | ConvertFrom-Json
    } else { $config = [pscustomobject]@{} }
    foreach ($name in @('ffmpeg', 'ffprobe')) {
        $config | Add-Member -NotePropertyName $name -NotePropertyValue $settings[$name] -Force
    }
    $config | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 -LiteralPath $configPath
}
Push-Location -LiteralPath $projectRoot
try {
    $syncArgs = @('sync', '--project', 'backend', '--locked', '--python', '3.11', '--link-mode', 'copy')
    if ($WithContent) { $syncArgs += @('--extra', 'content') }
    & uv @syncArgs
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed. Resolve the error above and rerun setup.' }
    & npm.cmd --prefix frontend ci --include=dev --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed. Resolve the error above and rerun setup.' }
    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    $checkArgs = @((Join-Path $PSScriptRoot 'check_environment.py'), '--media-test')
    if ($WithContent) { $checkArgs += '--content' }
    & (Join-Path $projectRoot 'backend\.venv\Scripts\python.exe') @checkArgs
    if ($LASTEXITCODE -ne 0) { throw 'Environment verification failed; see the message above.' }
    Write-Output 'Installation and media verification passed. Run scripts/start.ps1 next.'
} finally {
    Pop-Location
}
