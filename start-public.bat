@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === LeadForge 公网演示（本机服务 + 穿透）===
echo.
echo [1/2] 启动本机 API（新窗口）...
start "LeadForge-Local" cmd /k "cd /d ""%~dp0"" && start-local.bat"

echo 等待本机服务就绪...
set PORT=8080
set /a tries=0
:waitloop
set /a tries+=1
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0}else{exit 1} } catch { exit 1 }"
if %ERRORLEVEL%==0 goto ready
if %tries% GEQ 40 goto fail
timeout /t 2 /nobreak >nul
goto waitloop

:fail
echo 等待超时：本机服务未在 http://127.0.0.1:%PORT% 起来。
echo 请查看 LeadForge-Local 窗口报错。
pause
exit /b 1

:ready
echo [2/2] 启动穿透（本窗口）...
echo 把下面出现的 https 地址发给别人即可。电脑需保持开机。
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-tunnel.ps1" -Port %PORT%
pause
