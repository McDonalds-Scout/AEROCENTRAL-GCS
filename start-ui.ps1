$ErrorActionPreference = "Stop"

$pythonPath = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$uiUrl = "http://127.0.0.1:8080/"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host "Python runtime not found:" -ForegroundColor Red
    Write-Host $pythonPath -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[1/4] Checking Python dependencies..." -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ensure-dependencies.ps1") -PythonPath $pythonPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dependency check failed. Please check the error above." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "[2/4] Cleaning old UAV UI background processes..." -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "cleanup-ui.ps1")

$env:AUTO_OPEN = "1"
$env:PORT = "8080"
$env:PYTHONUNBUFFERED = "1"

Write-Host ""
Write-Host "[3/4] Starting UAV ground station." -ForegroundColor Cyan
Write-Host "URL: $uiUrl" -ForegroundColor Green
Write-Host "Keep this terminal open while using the UI." -ForegroundColor Yellow
Write-Host ""

while ($true) {
    & $pythonPath (Join-Path $PSScriptRoot "ground_station_server.py")
    $exitCode = $LASTEXITCODE
    Write-Host ""
    Write-Host "UAV ground station stopped. Exit code: $exitCode" -ForegroundColor Yellow
    $answer = Read-Host "Type R to restart, or N to exit"
    if ($answer -notmatch "^[Rr]$") {
        exit $exitCode
    }
    Write-Host "Checking dependencies before restart..." -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ensure-dependencies.ps1") -PythonPath $pythonPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "cleanup-ui.ps1")
}
