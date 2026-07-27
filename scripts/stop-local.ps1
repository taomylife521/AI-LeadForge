# LeadForge 停止本地 uvicorn（按端口）
# 创建时间: 2026-07-23

param([int]$Port = 8080)

$conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($pid in $conns) {
    if ($pid -and $pid -ne 0) {
        Write-Host "停止 PID $pid (port $Port)" -ForegroundColor Yellow
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
}
Write-Host "完成。" -ForegroundColor Green
