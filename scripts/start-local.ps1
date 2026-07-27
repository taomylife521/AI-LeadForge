# LeadForge local start (no Docker)
# Created: 2026-07-23

param(
    [switch]$NoBrowser,
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Api = Join-Path $Root "apps\api"
Set-Location $Api

if (-not (Test-Path "$Api\.venv\Scripts\python.exe")) {
    Write-Host "Creating venv and installing deps..." -ForegroundColor Cyan
    python -m venv .venv
    & "$Api\.venv\Scripts\python.exe" -m pip install -r requirements.txt
}

if (-not (Test-Path "$Root\.env")) {
    Copy-Item "$Root\.env.example" "$Root\.env"
}

$env:LEADFORGE_DATA_DIR = Join-Path $Root "data"
$env:LITELLM_BASE_URL = "http://127.0.0.1:4000"
$env:MOCK_LLM = "false"

# Load root .env into process env (AGNES_API_KEY etc.)
Get-Content "$Root\.env" | ForEach-Object {
    $line = $_.Trim()
    if (($line -eq "") -or ($line.StartsWith("#")) -or (-not $line.Contains("="))) {
        return
    }
    $idx = $line.IndexOf("=")
    $k = $line.Substring(0, $idx).Trim()
    $v = $line.Substring($idx + 1).Trim()
    if ($k) {
        [Environment]::SetEnvironmentVariable($k, $v, "Process")
    }
}

# Force live LLM even if .env had MOCK_LLM=true
$env:MOCK_LLM = "false"

Write-Host "Starting http://localhost:$Port (MOCK_LLM=false)" -ForegroundColor Green
if (-not $NoBrowser) {
    Start-Process "http://localhost:$Port"
}

& "$Api\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port $Port
