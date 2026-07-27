# 按 skills.allowlist.yaml 安装 skills.sh 白名单 Skill 到 skills/vendor
# 创建时间: 2026-07-23

param(
    [switch]$SkipOptional
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
    Write-Host "未找到 npx/Node.js。Skill 安装跳过；系统仍可运行（内置 procedural 摘要）。" -ForegroundColor Yellow
    exit 0
}

$allowlist = Join-Path $Root "config\skills.allowlist.yaml"
if (-not (Test-Path $allowlist)) {
    Write-Host "缺少 allowlist: $allowlist" -ForegroundColor Red
    exit 1
}

Write-Host "安装 Skills（项目级目录 skills/vendor）..." -ForegroundColor Cyan
# 安装到项目目录；-y 非交互。失败不阻断主系统。
$repos = @(
    "coreyhaines31/marketingskills",
    "anthropics/skills",
    "vercel-labs/agent-skills",
    "obra/superpowers",
    "vercel-labs/skills"
)

foreach ($repo in $repos) {
    Write-Host "-> npx skills add $repo --agent cursor -y" -ForegroundColor Gray
    npx --yes skills add $repo --agent cursor -y 2>&1 | Out-Host
}

Write-Host "完成。请在控制台「Skills」页查看绑定状态。" -ForegroundColor Green
