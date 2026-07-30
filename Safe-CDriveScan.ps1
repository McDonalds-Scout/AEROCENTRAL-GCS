[CmdletBinding()]
param(
    [ValidateSet('Scan','Execute')]
    [string]$Mode = 'Scan',
    [string]$OutputDirectory = '',
    [int]$LargeFileThresholdMB = 1024,
    [int]$TopCount = 30,
    [switch]$WhatIf
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $baseDirectory = if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) { (Get-Location).Path } else { $PSScriptRoot }
    $OutputDirectory = Join-Path $baseDirectory 'C_Drive_Scan_Reports'
}

# 安全护栏：当前版本只实现只读扫描。即使指定 Execute，也绝不删除文件。
if ($Mode -eq 'Execute') {
    throw '安全停止：本脚本当前仅实现 Scan 模式。清理必须在用户确认具体编号后使用另一份经审核的脚本。'
}

$script:Errors = New-Object System.Collections.Generic.List[object]
$script:Items = New-Object System.Collections.Generic.List[object]
$script:LargeFiles = New-Object System.Collections.Generic.List[object]
$script:CandidateFiles = New-Object System.Collections.Generic.List[object]
$script:StartTime = Get-Date
$stamp = $script:StartTime.ToString('yyyyMMdd_HHmmss')

function Convert-Size {
    param([long]$Bytes)
    if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N2} MB' -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ('{0:N2} KB' -f ($Bytes / 1KB)) }
    return "$Bytes B"
}

function Add-ScanError {
    param([string]$Path,[string]$Operation,[string]$Message)
    $script:Errors.Add([pscustomobject]@{Time=(Get-Date);Path=$Path;Operation=$Operation;Message=$Message})
}

function Get-NormalizedPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    try { return [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($Path)) }
    catch { Add-ScanError $Path 'Normalize' $_.Exception.Message; return $null }
}

function Test-ReparsePoint {
    param([IO.FileSystemInfo]$Item)
    return (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Test-SafeContainer {
    param([string]$Path)
    try { return [bool](Test-Path -LiteralPath $Path -PathType Container -ErrorAction Stop) }
    catch { Add-ScanError $Path 'TestPath' $_.Exception.Message; return $false }
}

function Get-SafeDirectoryStats {
    param([string]$Path,[int]$MaxDepth=20,[int]$MaxSeconds=12,[int]$MaxEntries=100000)
    $full = Get-NormalizedPath $Path
    if (-not $full -or -not (Test-SafeContainer $full)) {
        return [pscustomobject]@{Bytes=0;Files=0;Exists=$false;Complete=$true}
    }
    [long]$bytes=0; [long]$files=0; [long]$entries=0; $complete=$true
    $timer=[Diagnostics.Stopwatch]::StartNew()
    $queue = New-Object 'System.Collections.Generic.Queue[object]'
    $queue.Enqueue([pscustomobject]@{Path=$full;Depth=0})
    while ($queue.Count -gt 0) {
        if($timer.Elapsed.TotalSeconds -ge $MaxSeconds -or $entries -ge $MaxEntries) {
            $complete=$false
            Add-ScanError $full 'ScanBudget' "达到扫描预算（${MaxSeconds}秒或 $MaxEntries 项），结果为已扫描下限"
            break
        }
        $entry=$queue.Dequeue()
        try {
            $children = Get-ChildItem -LiteralPath $entry.Path -Force -ErrorAction Stop
            foreach ($child in $children) {
                $entries++
                if (Test-ReparsePoint $child) { continue }
                if ($child.PSIsContainer) {
                    if ($entry.Depth -lt $MaxDepth) {
                        $queue.Enqueue([pscustomobject]@{Path=$child.FullName;Depth=$entry.Depth+1})
                    } else { $complete=$false; Add-ScanError $child.FullName 'DepthLimit' "超过最大深度 $MaxDepth，已跳过" }
                } else { $bytes += [long]$child.Length; $files++ }
            }
        } catch { $complete=$false; Add-ScanError $entry.Path 'Enumerate' $_.Exception.Message }
    }
    [pscustomobject]@{Bytes=$bytes;Files=$files;Exists=$true;Complete=$complete}
}

function Add-ScanItem {
    param([string]$Name,[string]$Path,[string]$Type,[string]$Risk,[string]$Recoverable,[string]$Suggestion,[int]$MaxDepth=20)
    Write-Progress -Activity '安全扫描 C 盘' -Status $Name -PercentComplete ([Math]::Min(90,5+$script:Items.Count*3))
    $stats=Get-SafeDirectoryStats -Path $Path -MaxDepth $MaxDepth
    $script:Items.Add([pscustomobject]@{
        Name=$Name;Path=(Get-NormalizedPath $Path);Type=$Type;Bytes=[long]$stats.Bytes;
        Size=(Convert-Size $stats.Bytes);Files=$stats.Files;Risk=$Risk;Recoverable=$Recoverable;
        Suggestion=$Suggestion;Exists=$stats.Exists;Complete=$stats.Complete
    })
}

function Find-ListedFiles {
    param([string[]]$Roots)
    $extensions=@('.iso','.zip','.rar','.7z','.exe','.msi','.tmp','.dmp')
    foreach($root in $Roots) {
        $full=Get-NormalizedPath $root
        if(-not $full -or -not (Test-SafeContainer $full)){continue}
        try {
            Get-ChildItem -LiteralPath $full -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
                if($_.Length -ge ($LargeFileThresholdMB*1MB)) {
                    $script:LargeFiles.Add([pscustomobject]@{Path=$_.FullName;Bytes=$_.Length;Size=(Convert-Size $_.Length);LastWriteTime=$_.LastWriteTime;Risk='D-仅报告'})
                }
                if($extensions -contains $_.Extension.ToLowerInvariant()) {
                    $script:CandidateFiles.Add([pscustomobject]@{Path=$_.FullName;Extension=$_.Extension;Bytes=$_.Length;Size=(Convert-Size $_.Length);LastWriteTime=$_.LastWriteTime;Risk='C-需人工确认'})
                }
            }
        } catch { Add-ScanError $full 'ListFiles' $_.Exception.Message }
    }
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$logPath=Join-Path $OutputDirectory "C_Drive_Scan_Log_$stamp.txt"
$reportPath=Join-Path $OutputDirectory "C_Drive_Scan_Report_$stamp.md"
$jsonPath=Join-Path $OutputDirectory "C_Drive_Scan_Data_$stamp.json"

try {
    $drive=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'" -ErrorAction Stop
    [long]$total=$drive.Size; [long]$free=$drive.FreeSpace
} catch {
    Add-ScanError 'C:' 'DriveInfo/CIM' $_.Exception.Message
    $driveInfo=New-Object IO.DriveInfo('C')
    [long]$total=$driveInfo.TotalSize; [long]$free=$driveInfo.AvailableFreeSpace
}
[long]$used=$total-$free
$usage=if($total){[Math]::Round(($used/$total)*100,1)}else{0}

$user=$env:USERPROFILE
$local=$env:LOCALAPPDATA
$roaming=$env:APPDATA
$temp=[IO.Path]::GetTempPath()

# 个人目录只统计，不建议清理。
Add-ScanItem '桌面' (Join-Path $user 'Desktop') '个人文件' 'D-禁止自动清理' '取决于备份' '仅报告'
Add-ScanItem '文档' (Join-Path $user 'Documents') '个人文件/项目' 'D-禁止自动清理' '取决于备份' '仅报告'
Add-ScanItem '下载' (Join-Path $user 'Downloads') '个人文件/安装包' 'D-禁止自动清理' '取决于备份' '仅报告'
Add-ScanItem '图片' (Join-Path $user 'Pictures') '个人文件' 'D-禁止自动清理' '取决于备份' '仅报告'
Add-ScanItem '视频' (Join-Path $user 'Videos') '个人文件' 'D-禁止自动清理' '取决于备份' '仅报告'
Add-ScanItem '音乐' (Join-Path $user 'Music') '个人文件' 'D-禁止自动清理' '取决于备份' '仅报告'
Add-ScanItem 'AppData Local' $local '应用数据' 'C-高风险' '通常不可直接恢复' '仅报告'
Add-ScanItem 'AppData Roaming' $roaming '应用配置' 'D-禁止自动清理' '通常不可直接恢复' '仅报告'

# 明确的缓存/临时位置。
Add-ScanItem '用户临时文件' $temp '临时文件' 'A-低风险' '通常不可恢复，可重建' '可选清理'
Add-ScanItem 'Windows Temp' 'C:\Windows\Temp' '系统临时文件' 'A-低风险' '通常不可恢复，可重建' '仅清理未占用文件'
Add-ScanItem 'Windows Update 下载缓存' 'C:\Windows\SoftwareDistribution\Download' '更新缓存' 'B-中风险' '可重新下载' '需单独确认'
Add-ScanItem 'Windows 错误报告' (Join-Path $local 'Microsoft\Windows\WER') '错误报告' 'A-低风险' '不可恢复' '可选清理'
Add-ScanItem '缩略图缓存' (Join-Path $local 'Microsoft\Windows\Explorer') '缩略图/资源管理器缓存' 'A-低风险' '可重建' '仅筛选 thumbcache 后清理'
Add-ScanItem 'DirectX Shader Cache' (Join-Path $local 'D3DSCache') '图形缓存' 'A-低风险' '可重建' '可选清理'
Add-ScanItem 'Delivery Optimization' 'C:\Windows\SoftwareDistribution\DeliveryOptimization' '传递优化缓存' 'B-中风险' '可重新下载' '需单独确认'
Add-ScanItem '崩溃转储' (Join-Path $local 'CrashDumps') '崩溃诊断文件' 'B-中风险' '不可恢复' '确认不需调试后清理'
Add-ScanItem 'VS Code Cache' (Join-Path $roaming 'Code\Cache') '编辑器缓存' 'B-中风险' '可重建' '不涉及设置和扩展'
Add-ScanItem 'VS Code CachedData' (Join-Path $roaming 'Code\CachedData') '编辑器缓存' 'B-中风险' '可重建' '不涉及设置和扩展'
Add-ScanItem 'pip Cache' (Join-Path $local 'pip\Cache') '包下载缓存' 'A-低风险' '可重新下载' '不卸载包'
Add-ScanItem 'Conda Package Cache' (Join-Path $user '.conda\pkgs') 'Conda 包缓存' 'A-低风险' '可重新下载' '只按 conda dry-run 结果确认'
Add-ScanItem 'npm Cache' (Join-Path $local 'npm-cache') '包缓存' 'A-低风险' '可重新下载' '不影响项目依赖声明'
Add-ScanItem 'NuGet Cache' (Join-Path $user '.nuget\packages') '包缓存' 'B-中风险' '可重新下载' '需单独确认'
Add-ScanItem 'MATLAB 临时目录' (Join-Path $temp '') '可能含 MATLAB 临时文件' 'B-中风险' '通常不可恢复' '只清理已确认且未占用项'

# 只检查用户目录顶层文件，避免递归进入 OneDrive、代码仓库和受保护区域。
Find-ListedFiles -Roots @($user,(Join-Path $user 'Downloads'),$temp)

# 开发工具只运行官方只读查询。
$toolInfo=[ordered]@{}
foreach($probe in @(
    @{Name='pip cache dir';Exe='pip';Args=@('cache','dir')},
    @{Name='pip cache info';Exe='pip';Args=@('cache','info')},
    @{Name='conda clean dry-run';Exe='conda';Args=@('clean','--dry-run','--all')},
    @{Name='docker system df';Exe='docker';Args=@('system','df')},
    @{Name='wsl list';Exe='wsl';Args=@('--list','--verbose')}
)) {
    try {
        if(Get-Command $probe.Exe -ErrorAction SilentlyContinue) {
            $out=& $probe.Exe @($probe.Args) 2>&1 | Out-String
            $toolInfo[$probe.Name]=$out.Trim()
        } else {$toolInfo[$probe.Name]='命令未找到，未运行'}
    } catch {$toolInfo[$probe.Name]="查询失败：$($_.Exception.Message)";Add-ScanError $probe.Exe 'ToolProbe' $_.Exception.Message}
}

$osText='未知（无权限）'
try {
    $cv=Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $osText="$($cv.ProductName) / build $($cv.CurrentBuild).$($cv.UBR)"
} catch {Add-ScanError 'HKLM CurrentVersion' 'OSVersion' $_.Exception.Message}
$isAdmin=(New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

$orderedItems=$script:Items | Sort-Object Bytes -Descending
$topFiles=$script:LargeFiles | Sort-Object Bytes -Descending | Select-Object -First $TopCount
$data=[ordered]@{
    ScanTime=$script:StartTime;Mode='Scan/read-only';OS=$osText;PowerShell=$PSVersionTable.PSVersion.ToString();IsAdmin=$isAdmin
    Drive=[ordered]@{TotalBytes=$total;UsedBytes=$used;FreeBytes=$free;UsagePercent=$usage}
    Items=$orderedItems;LargeFiles=$topFiles;ListedCandidates=($script:CandidateFiles|Sort-Object Bytes -Descending)
    ToolQueries=$toolInfo;Errors=$script:Errors
}
$data | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

$lines=New-Object System.Collections.Generic.List[string]
$lines.Add('# C 盘安全只读扫描报告')
$lines.Add('');$lines.Add("生成时间：$($script:StartTime.ToString('yyyy-MM-dd HH:mm:ss'))；模式：只读扫描；管理员：$isAdmin")
$lines.Add('');$lines.Add('## 1. C 盘概况');$lines.Add('')
$lines.Add('| 项目 | 数值 |');$lines.Add('|---|---:|')
$lines.Add("| 总容量 | $(Convert-Size $total) |");$lines.Add("| 已使用 | $(Convert-Size $used) |")
$lines.Add("| 剩余空间 | $(Convert-Size $free) | ");$lines.Add("| 使用率 | $usage% |")
$lines.Add('');$lines.Add('## 2. 主要目录与建议清理项目');$lines.Add('')
$lines.Add('| 编号 | 项目 | 路径 | 大小 | 风险 | 可恢复性 | 建议 | 扫描完整 |')
$lines.Add('|---:|---|---|---:|---|---|---|---|')
$n=0
foreach($i in $orderedItems){$n++;$lines.Add("| $n | $($i.Name) | ``$($i.Path)`` | $($i.Size) | $($i.Risk) | $($i.Recoverable) | $($i.Suggestion) | $($i.Complete) |")}
$lines.Add('');$lines.Add('## 3. 大文件（受限安全范围）');$lines.Add('')
$lines.Add('| 排名 | 文件路径 | 大小 | 最后修改 | 风险 |');$lines.Add('|---:|---|---:|---|---|')
$n=0;foreach($f in $topFiles){$n++;$lines.Add("| $n | ``$($f.Path)`` | $($f.Size) | $($f.LastWriteTime) | $($f.Risk) |")}
$lines.Add('');$lines.Add('> 为保护 OneDrive、代码仓库、WSL/Docker 磁盘、个人文档及系统核心目录，本次没有递归遍历整个 C 盘；因此“大文件/最大目录”不是全盘穷举结果。')
$lines.Add('');$lines.Add('## 4. 旧安装包与候选文件（仅列出）');$lines.Add('')
$lines.Add('| 文件 | 类型 | 大小 | 最后修改 | 风险 |');$lines.Add('|---|---|---:|---|---|')
foreach($f in ($script:CandidateFiles|Sort-Object Bytes -Descending|Select-Object -First 100)){$lines.Add("| ``$($f.Path)`` | $($f.Extension) | $($f.Size) | $($f.LastWriteTime) | $($f.Risk) |")}
$lines.Add('');$lines.Add('## 5. 开发环境只读查询');$lines.Add('')
foreach($k in $toolInfo.Keys){$lines.Add("### $k");$lines.Add('');$lines.Add('```text');$lines.Add([string]$toolInfo[$k]);$lines.Add('```');$lines.Add('')}
$lines.Add('## 6. 扫描限制与错误');$lines.Add('')
if($script:Errors.Count -eq 0){$lines.Add('无。')}else{foreach($e in $script:Errors){$lines.Add("- [$($e.Time)] $($e.Operation)：``$($e.Path)`` — $($e.Message)")}}
$lines.Add('');$lines.Add('## 7. 安全说明');$lines.Add('')
$lines.Add('- 本次未删除、移动或修改任何被扫描文件，也未修改权限、注册表、服务或系统长期设置。')
$lines.Add('- 报告中的大小是“候选上限”；被占用、权限不足、重解析点和不符合细筛条件的文件不会在未来清理中删除。')
$lines.Add('- 必须由用户确认具体项目编号后，才能另行生成带日志、路径白名单和预览机制的清理脚本。')
$lines | Set-Content -LiteralPath $reportPath -Encoding UTF8

@(
    "时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    '操作: 只读扫描（无删除）',
    "报告: $reportPath",
    "数据: $jsonPath",
    "错误/跳过: $($script:Errors.Count)",
    "结果: 完成"
) | Set-Content -LiteralPath $logPath -Encoding UTF8

Write-Progress -Activity '安全扫描 C 盘' -Completed
Write-Host "只读扫描完成。报告：$reportPath"
Write-Host "结构化数据：$jsonPath"
Write-Host "日志：$logPath"
