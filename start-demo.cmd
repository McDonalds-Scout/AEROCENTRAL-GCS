@echo off
setlocal

echo [LEGACY] Demo helper. Normal operation should use start-ui.cmd.
echo.

set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "UI_URL=http://127.0.0.1:8080/"

if not exist "%PYTHON_EXE%" (
  echo Python runtime not found:
  echo %PYTHON_EXE%
  pause
  exit /b 1
)

echo Cleaning old UI backend...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup-ui.ps1"

set "AUTO_OPEN=1"
set "PORT=8080"
set "PYTHONUNBUFFERED=1"
start "UAV Ground Station" /min "%PYTHON_EXE%" "%~dp0ground_station_server.py"

echo Waiting for UI backend...
for /L %%i in (1,1,20) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing '%UI_URL%api/system/status' | Out-Null; exit 0 } catch { exit 1 }"
  if not errorlevel 1 goto ready
  timeout /t 1 /nobreak >nul
)

echo UI backend did not become ready. Open %UI_URL% manually after checking the server window.
pause
exit /b 1

:ready
echo UI ready: %UI_URL%
powershell -NoProfile -Command "Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{\"type\":\"demo\",\"listenPort\":14550}' http://127.0.0.1:8080/api/connection/start | Out-Null"
start "" "%UI_URL%"
