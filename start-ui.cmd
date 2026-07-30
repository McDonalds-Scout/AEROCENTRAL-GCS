@echo off
setlocal
set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "AUTO_OPEN=1"
set "PORT=8080"
set "PYTHONUNBUFFERED=1"
set "UI_URL=http://127.0.0.1:8080/"

if not exist "%PYTHON_EXE%" (
  echo Python runtime not found:
  echo %PYTHON_EXE%
  pause
  exit /b 1
)

echo.
echo [1/4] Checking Python dependencies...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure-dependencies.ps1" -PythonPath "%PYTHON_EXE%"
if errorlevel 1 (
  echo.
  echo Dependency check failed. Please keep this window open and check the error above.
  pause
  exit /b 1
)

echo.
echo [2/4] Cleaning old UAV UI background processes...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup-ui.ps1"

echo.
echo [3/4] Starting UAV ground station.
echo URL: %UI_URL%
echo Keep this terminal open while using the UI.
echo.

:run
"%PYTHON_EXE%" "%~dp0ground_station_server.py"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo UAV ground station stopped. Exit code: %EXIT_CODE%
echo If the browser says connection refused, press R here to restart the backend.
choice /C RN /N /M "Press R to restart, or N to exit: "
if errorlevel 2 exit /b %EXIT_CODE%
echo.
echo Checking dependencies before restart...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure-dependencies.ps1" -PythonPath "%PYTHON_EXE%"
if errorlevel 1 exit /b 1
echo.
echo Cleaning before restart...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup-ui.ps1"
goto run
