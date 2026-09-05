param(
    [string]$PythonPath = "",
    [string]$Version = "0.1.0",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $Root "build\windows"
$DistRoot = Join-Path $Root "dist\AEROCENTRAL"
$PortableZip = Join-Path $Root "dist\AEROCENTRAL-portable.zip"
$SpecRoot = Join-Path $BuildRoot "spec"

function Resolve-PythonRuntime {
    param([string]$RequestedPath)
    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) { $candidates.Add($RequestedPath) }
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHON_EXE)) { $candidates.Add($env:PYTHON_EXE) }
    $candidates.Add((Join-Path $Root ".venv\Scripts\python.exe"))
    $candidates.Add((Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"))
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($systemPython) { $candidates.Add($systemPython.Source) }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) { $candidates.Add($pyLauncher.Source) }

    foreach ($candidate in ($candidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            & $candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return (Resolve-Path -LiteralPath $candidate).Path }
        } catch {
            continue
        }
    }
    throw "Python 3.10+ runtime not found."
}

function Invoke-Step {
    param([string]$Title, [scriptblock]$Block)
    Write-Host ""
    Write-Host $Title -ForegroundColor Cyan
    & $Block
}

function Invoke-PyInstaller {
    param([object[]]$Arguments)
    $flatArgs = New-Object System.Collections.Generic.List[string]
    foreach ($argument in $Arguments) {
        if ($null -eq $argument) { continue }
        if ($argument -is [System.Array]) {
            foreach ($inner in $argument) {
                if ($null -ne $inner) { $flatArgs.Add([string]$inner) }
            }
        } else {
            $flatArgs.Add([string]$argument)
        }
    }
    & $PyInstaller @flatArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed: $($flatArgs -join ' ')"
    }
}

$Python = Resolve-PythonRuntime -RequestedPath $PythonPath
Set-Location -LiteralPath $Root

Invoke-Step "[1/6] Installing build dependencies" {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r (Join-Path $Root "requirements.txt") pyinstaller
}

$PyInstaller = Join-Path (Split-Path -Parent $Python) "Scripts\pyinstaller.exe"
if (-not (Test-Path -LiteralPath $PyInstaller)) {
    throw "PyInstaller executable not found at $PyInstaller. Try: $Python -m pip install pyinstaller"
}

function Get-ProjectPath {
    param([string]$RelativePath)
    return (Resolve-Path -LiteralPath (Join-Path $Root $RelativePath)).Path
}

function Get-AddDataSpec {
    param(
        [string]$RelativeSource,
        [string]$Destination
    )
    $source = Get-ProjectPath -RelativePath $RelativeSource
    return "${source}:$Destination"
}

Invoke-Step "[2/6] Cleaning previous package outputs" {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $DistRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PortableZip -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $BuildRoot, $DistRoot, $SpecRoot | Out-Null
}

$CommonData = @(
    "--add-data", (Get-AddDataSpec "index.html" "."),
    "--add-data", (Get-AddDataSpec "app.js" "."),
    "--add-data", (Get-AddDataSpec "styles.css" "."),
    "--add-data", (Get-AddDataSpec "assets" "assets"),
    "--add-data", (Get-AddDataSpec "vendor" "vendor"),
    "--add-data", (Get-AddDataSpec "config" "config"),
    "--add-data", (Get-AddDataSpec "mock" "mock"),
    "--add-data", (Get-AddDataSpec ".env.example" ".")
)

$MavlinkHiddenImports = @(
    "--hidden-import=pymavlink",
    "--hidden-import=pymavlink.mavutil",
    "--hidden-import=pymavlink.dialects.v10.ardupilotmega",
    "--hidden-import=pymavlink.dialects.v10.common",
    "--hidden-import=pymavlink.dialects.v20.common",
    "--hidden-import=pymavlink.dialects.v20.ardupilotmega",
    "--hidden-import=serial.tools.list_ports"
)

$BackendHiddenImports = @()
$BackendHiddenImports += $MavlinkHiddenImports
$BackendHiddenImports += @(
    "--hidden-import=pyulog",
    "--hidden-import=pyulog.core",
    "--hidden-import=docx",
    "--hidden-import=reportlab",
    "--hidden-import=matplotlib.backends.backend_agg"
)

Invoke-Step "[3/6] Building backend executable" {
    Invoke-PyInstaller @(
        "--noconfirm", "--clean", "--onedir", "--console",
        "--name", "ground_station_server",
        "--distpath", $DistRoot,
        "--workpath", (Join-Path $BuildRoot "server"),
        "--specpath", $SpecRoot,
        $CommonData,
        $BackendHiddenImports,
        (Get-ProjectPath "ground_station_server.py")
    )
}

Invoke-Step "[4/6] Building MAVLink connector executables" {
    Invoke-PyInstaller @(
        "--noconfirm", "--clean", "--onedir", "--console",
        "--name", "px6c_connector",
        "--distpath", $DistRoot,
        "--workpath", (Join-Path $BuildRoot "connector"),
        "--specpath", $SpecRoot,
        $MavlinkHiddenImports,
        (Get-ProjectPath "px6c_connector.py")
    )
    Invoke-PyInstaller @(
        "--noconfirm", "--clean", "--onedir", "--console",
        "--name", "mavlink_simulator",
        "--distpath", $DistRoot,
        "--workpath", (Join-Path $BuildRoot "simulator"),
        "--specpath", $SpecRoot,
        $MavlinkHiddenImports,
        (Get-ProjectPath "mavlink_simulator.py")
    )
}

Invoke-Step "[5/6] Building desktop launcher" {
    Invoke-PyInstaller @(
        "--noconfirm", "--clean", "--onefile", "--noconsole",
        "--name", "AEROCENTRAL",
        "--distpath", $DistRoot,
        "--workpath", (Join-Path $BuildRoot "launcher"),
        "--specpath", $SpecRoot,
        (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "aerocentral_launcher.py")).Path
    )
    Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $DistRoot -Force
    Copy-Item -LiteralPath (Join-Path $Root ".env.example") -Destination $DistRoot -Force
    Compress-Archive -Path (Join-Path $DistRoot "*") -DestinationPath $PortableZip -CompressionLevel Optimal -Force
}

Invoke-Step "[6/6] Optional installer build" {
    if ($SkipInstaller) {
        Write-Host "Installer build skipped."
        return
    }
    $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if (-not $iscc) {
        Write-Host "Inno Setup ISCC.exe not found. Portable package is ready at: $DistRoot" -ForegroundColor Yellow
        Write-Host "Install Inno Setup and rerun this script to create a Setup.exe installer." -ForegroundColor Yellow
        return
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "dist\installer") | Out-Null
    & $iscc.Source "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot "aerocentral_ground_station.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup installer build failed."
    }
}

Write-Host ""
Write-Host "Package ready:" -ForegroundColor Green
Write-Host $DistRoot
Write-Host "Run:" -ForegroundColor Green
Write-Host (Join-Path $DistRoot "AEROCENTRAL.exe")
Write-Host "Portable zip:" -ForegroundColor Green
Write-Host $PortableZip
