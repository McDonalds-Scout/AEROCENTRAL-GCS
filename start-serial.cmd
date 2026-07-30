@echo off
setlocal

set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python was not found.
  pause
  exit /b 1
)

"%PYTHON_EXE%" "%~dp0serial_launcher.py"
