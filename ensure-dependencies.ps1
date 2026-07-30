param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Host "Python runtime not found:" -ForegroundColor Red
    Write-Host $PythonPath -ForegroundColor Red
    exit 1
}

$checker = Join-Path $PSScriptRoot "ensure_dependencies.py"
if (-not (Test-Path -LiteralPath $checker)) {
    Write-Host "Dependency checker not found:" -ForegroundColor Red
    Write-Host $checker -ForegroundColor Red
    exit 1
}

& $PythonPath $checker
exit $LASTEXITCODE
