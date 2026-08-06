param(
    [string]$PythonPath = "",
    [string]$Version = "0.1.0",
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"

$DesktopRoot = $PSScriptRoot
$Root = (Resolve-Path -LiteralPath (Join-Path $DesktopRoot "..\..")).Path
$BackendDist = Join-Path $Root "dist\AEROCENTRAL"

function Invoke-Step {
    param([string]$Title, [scriptblock]$Block)
    Write-Host ""
    Write-Host $Title -ForegroundColor Cyan
    & $Block
}

function Resolve-NodeRuntime {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($env:NODE_EXE)) { $candidates.Add($env:NODE_EXE) }
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCommand) { $candidates.Add($nodeCommand.Source) }
    $codexNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    $candidates.Add($codexNode)
    foreach ($candidate in ($candidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate) { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    throw "Node.js runtime not found. Install Node.js before building the Electron desktop package."
}

function Invoke-Npm {
    param([string[]]$Arguments)
    $node = Resolve-NodeRuntime
    $nodeDir = Split-Path -Parent $node
    if (($env:PATH -split ";") -notcontains $nodeDir) {
        $env:PATH = "$nodeDir;$env:PATH"
    }
    $npmCli = Join-Path (Split-Path -Parent $node) "node_modules\npm\bin\npm-cli.js"
    if (-not (Test-Path -LiteralPath $npmCli)) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
        if ($npm) {
            & $npm.Source @Arguments
        } else {
            $pnpmCandidates = @(
                (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"),
                (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\override\pnpm.cmd")
            )
            $pnpm = $pnpmCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if (-not $pnpm) {
                $pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
                if ($pnpmCommand) { $pnpm = $pnpmCommand.Source }
            }
            if (-not $pnpm) { throw "npm/pnpm not found. Install Node.js or use the bundled Codex runtime." }
            & $pnpm @Arguments
        }
    } else {
        & $node $npmCli @Arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "npm command failed: npm $($Arguments -join ' ')"
    }
}

Invoke-Step "[1/5] Build Python backend executables" {
    if ($SkipBackend) {
        Write-Host "Backend build skipped."
        if (-not (Test-Path -LiteralPath (Join-Path $BackendDist "ground_station_server.exe"))) {
            throw "Backend executable not found. Run without -SkipBackend first."
        }
        return
    }
    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "packaging\windows\build_windows_package.ps1"), "-Version", $Version, "-SkipInstaller")
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        $args += @("-PythonPath", $PythonPath)
    }
    powershell @args
    if ($LASTEXITCODE -ne 0) { throw "Backend PyInstaller package failed." }
}

Invoke-Step "[2/5] Prepare Electron icons" {
    New-Item -ItemType Directory -Force -Path (Join-Path $DesktopRoot "icons") | Out-Null
    Copy-Item -LiteralPath (Join-Path $Root "assets\aerocentral-logo.png") -Destination (Join-Path $DesktopRoot "icons\aerocentral.png") -Force
    $python = $PythonPath
    if ([string]::IsNullOrWhiteSpace($python)) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand) { $python = $pythonCommand.Source }
    }
    try {
        if ([string]::IsNullOrWhiteSpace($python)) { throw "Python not found for icon generation." }
        $iconScript = Join-Path $env:TEMP "aerocentral_make_icon.py"
        @'
from pathlib import Path
from PIL import Image

root = Path("desktop/electron/icons")
png = root / "aerocentral.png"
ico = root / "aerocentral.ico"
img = Image.open(png).convert("RGBA")
img.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
'@ | Set-Content -LiteralPath $iconScript -Encoding UTF8
        Set-Location -LiteralPath $Root
        & $python $iconScript
        Remove-Item -LiteralPath $iconScript -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Host "ICO generation skipped; install Pillow if electron-builder requires an .ico file." -ForegroundColor Yellow
    }
}

Invoke-Step "[3/5] Install Electron dependencies" {
    Set-Location -LiteralPath $DesktopRoot
    Invoke-Npm @("install")
    Invoke-Npm @("rebuild", "electron")
}

Invoke-Step "[4/5] Build Electron desktop package" {
    Set-Location -LiteralPath $DesktopRoot
    Invoke-Npm @("run", "dist:win")
}

Invoke-Step "[5/5] Desktop package ready" {
    Write-Host "Electron output:" -ForegroundColor Green
    Write-Host (Join-Path $Root "dist\desktop")
    Write-Host "Backend bundled from:" -ForegroundColor Green
    Write-Host $BackendDist
}
