@echo off
setlocal

echo [LEGACY] Low-level MAVLink bridge helper. Normal operation should use start-ui.cmd.
echo.

set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python was not found.
  pause
  exit /b 1
)

echo.
echo Serial example:
echo   start-mavlink.cmd COM3 57600
echo.
echo UDP example:
echo   start-mavlink.cmd udpin:0.0.0.0:14550
echo.

set "CONNECTION=%~1"
if "%CONNECTION%"=="" set "CONNECTION=udpin:0.0.0.0:14550"
set "BAUD=%~2"
if "%BAUD%"=="" set "BAUD=57600"

"%PYTHON_EXE%" "%~dp0mavlink_bridge.py" --connection "%CONNECTION%" --baud %BAUD%
