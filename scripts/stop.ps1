# LeadForge 一键停止
# 创建时间: 2026-07-23

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== LeadForge 停止 ===" -ForegroundColor Cyan
docker compose down
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "已停止全部容器。" -ForegroundColor Green
