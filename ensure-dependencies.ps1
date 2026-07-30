param(
    [string]$PythonPath = ""
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
            & $candidate -c "import sys; print(sys.version)" > $null 2>&1
            if ($LASTEXITCODE -eq 0) {
                return (Resolve-Path -LiteralPath $candidate).Path
            }
        } catch {
            continue
        }
    }

    throw "Python runtime not found. Install Python 3.10+ or create .venv."
}

$PythonPath = Resolve-PythonRuntime -RequestedPath $PythonPath

$checker = Join-Path $PSScriptRoot "ensure_dependencies.py"
if (-not (Test-Path -LiteralPath $checker)) {
    Write-Host "Dependency checker not found:" -ForegroundColor Red
    Write-Host $checker -ForegroundColor Red
    exit 1
}

& $PythonPath $checker
exit $LASTEXITCODE
