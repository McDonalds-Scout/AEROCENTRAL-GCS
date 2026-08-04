@echo off
setlocal

echo [LEGACY] Direct PX6C connector helper. Normal operation should use start-ui.cmd.
echo.

set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python was not found.
  pause
  exit /b 1
)

echo.
echo PX6C / Pixhawk 6C PX4 Connector
echo 1. Auto-detect USB telemetry radio
echo 2. UDP 14550
echo.
set /p MODE=Select connection [1]:
if "%MODE%"=="" set "MODE=1"

if "%MODE%"=="2" (
  "%PYTHON_EXE%" "%~dp0px6c_connector.py" --connection "udpin:0.0.0.0:14550"
) else (
  "%PYTHON_EXE%" "%~dp0px6c_connector.py" --connection auto
)
