@echo off
setlocal

REM Universal Windows launcher. The PowerShell script will find a usable Python
REM runtime from .venv, Codex runtime, system python, or py launcher.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-ui.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo UAV ground station failed to start. Keep this window open and check the error above.
  pause
)

exit /b %EXIT_CODE%
