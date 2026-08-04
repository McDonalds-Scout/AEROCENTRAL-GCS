$ErrorActionPreference = "SilentlyContinue"

$Root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$Ports = @(8080, 8094, 8095, 8096, 8097, 8100)
$ProjectMarkers = @(
    "ground_station_server.py",
    "px6c_connector.py",
    "mavlink_simulator.py",
    "server.js",
    "run-ai-report-server.cmd",
    "start-ai-report-ui.cmd"
)

function Is-ProjectProcess {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    $normalizedCommand = $CommandLine.ToLowerInvariant()
    $normalizedRoot = $Root.ToLowerInvariant()
    if (-not $normalizedCommand.Contains($normalizedRoot)) {
        return $false
    }
    foreach ($marker in $ProjectMarkers) {
        if ($normalizedCommand.Contains($marker.ToLowerInvariant())) {
            return $true
        }
    }
    return $false
}

function Stop-ProjectPid {
    param([int]$ProcessId, [string]$Reason)
    if ($ProcessId -le 0 -or $ProcessId -eq $PID) {
        return
    }
    $basicProcess = Get-Process -Id $ProcessId
    if (-not $basicProcess) {
        return
    }
    $allowedByCommand = $false
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if ($processInfo) {
        $allowedByCommand = Is-ProjectProcess $processInfo.CommandLine
    }
    $allowedByPortOwner = $basicProcess.ProcessName -match "^(python|pythonw|node|cmd)$"
    if (-not $allowedByCommand -and -not $allowedByPortOwner) {
        return
    }
    Write-Host "Stopping old UI backend PID $ProcessId ($Reason)"
    Stop-Process -Id $ProcessId -Force
}

foreach ($port in $Ports) {
    $matches = netstat -ano | Select-String ":$port\s"
    foreach ($line in $matches) {
        $parts = ($line.Line -split "\s+") | Where-Object { $_ }
        if ($parts.Count -lt 5) {
            continue
        }
        $state = $parts[-2]
        $ownerPid = 0
        [void][int]::TryParse($parts[-1], [ref]$ownerPid)
        if ($state -eq "LISTENING") {
            Stop-ProjectPid -ProcessId $ownerPid -Reason "port $port"
        }
    }
}

$PidFiles = @(
    Join-Path $Root "connection.pids"
)
foreach ($pidFile in $PidFiles) {
    if (-not (Test-Path -LiteralPath $pidFile)) {
        continue
    }
    Get-Content -LiteralPath $pidFile | ForEach-Object {
        $savedPid = 0
        [void][int]::TryParse(($_ -as [string]).Trim(), [ref]$savedPid)
        if ($savedPid -gt 0) {
            Stop-ProjectPid -ProcessId $savedPid -Reason "saved connector pid"
        }
    }
    Remove-Item -LiteralPath $pidFile -Force
}

$projectProcesses = Get-CimInstance Win32_Process
if ($projectProcesses) {
    $projectProcesses = $projectProcesses |
        Where-Object { $_.Name -match "^(python|pythonw|node|cmd)\.exe$" -and (Is-ProjectProcess $_.CommandLine) }

    foreach ($processInfo in $projectProcesses) {
        Stop-ProjectPid -ProcessId ([int]$processInfo.ProcessId) -Reason "stale project process"
    }
}

Start-Sleep -Milliseconds 500
Write-Host "UI backend cleanup complete."
