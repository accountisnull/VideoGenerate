param(
    [ValidateRange(1, 65535)][int]$Port = 8761,
    [string]$AssetRoot,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$backendRoot = Join-Path $projectRoot 'backend'
$pythonPath = Join-Path $backendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '缺少 backend/.venv，请先按安装说明准备依赖。' }
$launchArguments = @('-m', 'app.content', '--port', "$Port")
if ($AssetRoot) {
    if (-not [System.IO.Path]::IsPathRooted($AssetRoot)) { $AssetRoot = Join-Path $projectRoot $AssetRoot }
    $launchArguments += @('--asset-root', [System.IO.Path]::GetFullPath($AssetRoot))
}
if ($CheckOnly) { $launchArguments += '--check' }
Push-Location $backendRoot
try {
    # 前台运行，使用 Ctrl+C 停止；不管理或终止其他服务。
    & $pythonPath @launchArguments
    if ($LASTEXITCODE -ne 0) { throw '内容服务退出失败，请检查配置、数据库占用及启动日志。' }
} finally {
    Pop-Location
}
