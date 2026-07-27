# LeadForge 一键启动
# 创建时间: 2026-07-23
# 作用: 检查 Docker、准备 .env、拉起 compose、打开可视化控制台

param(
    [switch]$NoBrowser,
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== LeadForge 启动 ===" -ForegroundColor Cyan

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "未检测到 Docker。请先安装并启动 Docker Desktop。" -ForegroundColor Red
    exit 1
}

try {
    docker info 2>$null | Out-Null
} catch {
    Write-Host "Docker 未运行。请启动 Docker Desktop 后重试。" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path "$Root\.env")) {
    Copy-Item "$Root\.env.example" "$Root\.env"
    Write-Host "已生成 .env（来自 .env.example），请填写 API Key 后可在控制台切换模型。" -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force -Path "$Root\data" | Out-Null

$composeArgs = @("compose", "up", "-d")
if ($Build) { $composeArgs += "--build" }

Write-Host "启动容器: docker $($composeArgs -join ' ')" -ForegroundColor Green
& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "启动失败，尝试重建: docker compose up -d --build" -ForegroundColor Yellow
    & docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$port = "8080"
if (Test-Path "$Root\.env") {
    $m = Select-String -Path "$Root\.env" -Pattern '^LEADFORGE_WEB_PORT=(.+)$' | Select-Object -First 1
    if ($m) { $port = $m.Matches.Groups[1].Value.Trim() }
}

$url = "http://localhost:$port"
Write-Host ""
Write-Host "可视化控制台: $url" -ForegroundColor Green
Write-Host "n8n 审批台:     http://localhost:5678" -ForegroundColor Green
Write-Host "LiteLLM:        http://localhost:4000" -ForegroundColor Green
Write-Host "停止请运行:     stop.bat" -ForegroundColor Cyan

if (-not $NoBrowser) {
    Start-Sleep -Seconds 3
    Start-Process $url
}
