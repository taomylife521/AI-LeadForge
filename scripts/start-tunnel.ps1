# LeadForge local tunnel for public demo (China-friendly via cpolar)
# Created: 2026-07-27
#
# Prefer cpolar (domestic nodes). Fallback: cloudflared.
#
# Usage:
#   1. Start local API: start-local.bat
#   2. Run: start-tunnel.bat  (share the https URL shown)

param(
    [int]$Port = 8080,
    [ValidateSet("auto", "cpolar", "cloudflared")]
    [string]$Provider = "auto"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Test-LocalApi {
    param([int]$P)
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$P/api/health" -UseBasicParsing -TimeoutSec 3
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Find-Cpolar {
    $candidates = @(
        (Get-Command cpolar -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
        "E:\Program Files\cpolar\cpolar.exe",
        "$env:LOCALAPPDATA\cpolar\cpolar.exe",
        "$env:USERPROFILE\cpolar\cpolar.exe",
        "$env:ProgramFiles\cpolar\cpolar.exe",
        "${env:ProgramFiles(x86)}\cpolar\cpolar.exe",
        (Join-Path $Root "tools\cpolar\cpolar.exe")
    ) | Where-Object { $_ }
    foreach ($p in $candidates) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return $null
}

function Find-Cloudflared {
    $candidates = @(
        (Get-Command cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
        "$env:LOCALAPPDATA\cloudflared\cloudflared.exe",
        "$env:ProgramFiles\cloudflared\cloudflared.exe",
        (Join-Path $Root "tools\cloudflared\cloudflared.exe")
    ) | Where-Object { $_ }
    foreach ($p in $candidates) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return $null
}

function Ensure-Cloudflared {
    $dir = Join-Path $Root "tools\cloudflared"
    $exe = Join-Path $dir "cloudflared.exe"
    if (Test-Path $exe) { return $exe }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    Write-Host "Downloading cloudflared portable..." -ForegroundColor Cyan
    Write-Host "  $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
    } catch {
        Write-Host "Download failed (proxy may be required): $($_.Exception.Message)" -ForegroundColor Yellow
        return $null
    }
    if (Test-Path $exe) { return $exe }
    return $null
}

Write-Host ""
Write-Host "=== LeadForge tunnel ===" -ForegroundColor Green
Write-Host "Local port: $Port"
Write-Host ""

if (-not (Test-LocalApi -P $Port)) {
    Write-Host "Local API not detected: http://127.0.0.1:$Port" -ForegroundColor Yellow
    Write-Host "Start start-local.bat first, then run this script again." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Or use start-public.bat (API + tunnel together)." -ForegroundColor Cyan
    exit 1
}

Write-Host "Local API is ready." -ForegroundColor Green

$cpolar = Find-Cpolar
$cloudflared = Find-Cloudflared
$use = $Provider

if ($use -eq "auto") {
    if ($cpolar) { $use = "cpolar" }
    elseif ($cloudflared) { $use = "cloudflared" }
    else { $use = "cpolar" }
}

if ($use -eq "cpolar") {
    if (-not $cpolar) {
        Write-Host ""
        Write-Host "cpolar not installed (recommended for China visitors)." -ForegroundColor Yellow
        Write-Host "Free setup:" -ForegroundColor Cyan
        Write-Host "  1. Register/download: https://www.cpolar.com/download"
        Write-Host "  2. Install Windows client"
        Write-Host "  3. Copy Authtoken from dashboard, run:"
        Write-Host "       cpolar authtoken YOUR_TOKEN"
        Write-Host "  4. Run start-tunnel.bat again"
        Write-Host ""
        Write-Host "Fallback (may still need VPN for visitors):" -ForegroundColor DarkYellow
        Write-Host "  powershell -File scripts\start-tunnel.ps1 -Provider cloudflared"
        Write-Host ""
        Start-Process "https://www.cpolar.com/download"
        exit 2
    }
    Write-Host "Using cpolar: $cpolar" -ForegroundColor Cyan
    Write-Host "Share the https://xxxx.cpolar.cn (or .top) URL shown below." -ForegroundColor Green
    Write-Host "Keep PC on; do not close this window. Free URL may change each run." -ForegroundColor DarkYellow
    Write-Host ""
    & $cpolar "http" "$Port"
    exit $LASTEXITCODE
}

if ($use -eq "cloudflared") {
    if (-not $cloudflared) {
        $cloudflared = Ensure-Cloudflared
    }
    if (-not $cloudflared) {
        Write-Host "cloudflared unavailable. Please install cpolar." -ForegroundColor Red
        Start-Process "https://www.cpolar.com/download"
        exit 2
    }
    Write-Host "Using cloudflared: $cloudflared" -ForegroundColor Cyan
    Write-Host "Share the https://xxxx.trycloudflare.com URL shown below." -ForegroundColor Green
    Write-Host "Note: trycloudflare.com is often blocked in China; prefer cpolar." -ForegroundColor Yellow
    Write-Host ""
    & $cloudflared tunnel --url "http://127.0.0.1:$Port"
    exit $LASTEXITCODE
}
