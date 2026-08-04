param(
    [string]$PythonPath = "",
    [int]$Port = 8080,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

function Resolve-PythonRuntime {
    param([string]$RequestedPath)

    $candidates = New-Object System.Collections.Generic.List[string]

    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        $candidates.Add($RequestedPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHON_EXE)) {
        $candidates.Add($env:PYTHON_EXE)
    }

    $candidates.Add((Join-Path $PSScriptRoot ".venv\Scripts\python.exe"))
    $candidates.Add((Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"))

    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($systemPython) {
        $candidates.Add($systemPython.Source)
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $candidates.Add($pyLauncher.Source)
    }

    foreach ($candidate in ($candidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        try {
            $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($version)) {
                return (Resolve-Path -LiteralPath $candidate).Path
            }
        } catch {
            continue
        }
    }

    throw "Python runtime not found. Install Python 3.10+ or create .venv, then run .\start-ui.cmd again."
}

$pythonPath = Resolve-PythonRuntime -RequestedPath $PythonPath
$uiUrl = "http://127.0.0.1:$Port/"

Write-Host ""
Write-Host "Using Python runtime:" -ForegroundColor Cyan
Write-Host $pythonPath

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

$env:AUTO_OPEN = if ($NoOpen) { "0" } else { "1" }
$env:PORT = [string]$Port
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

    Write-Host "Cleaning before restart..." -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "cleanup-ui.ps1")
}
