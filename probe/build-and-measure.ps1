# PIC FINDER stack validation script.
#
# Usage, from the pic-finder directory:
#   powershell -ExecutionPolicy Bypass -File probe\build-and-measure.ps1
#
# This file is ASCII-only on purpose. Windows PowerShell 5.1 reads a script
# without a BOM as ANSI, which corrupts non-ASCII text and breaks parsing.
# All display text lives in probe\messages.json and is loaded as UTF-8.
#
# What it does:
#   1. Check Python and PySide6.
#   2. Build the probe program with PyInstaller in directory mode.
#   3. Measure the size of the built program directory.
#   4. Launch the built program five times and record time to first window.
#
# Output: probe\probe-result.json plus a summary on screen.

[CmdletBinding()]
param(
    [string]$Python = "python",
    [int]$Runs = 5
)

$ErrorActionPreference = "Stop"

$probeDir = $PSScriptRoot
$entry = Join-Path $probeDir "picfinder_probe.py"
$distDir = Join-Path $probeDir "dist"
$buildDir = Join-Path $probeDir "build"
$specDir = Join-Path $probeDir "spec"
$resultPath = Join-Path $probeDir "probe-result.json"
$messagesPath = Join-Path $probeDir "messages.json"

$messages = Get-Content -Path $messagesPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host ("== " + $Text) -ForegroundColor Cyan
}

function Format-Message([string]$Key, [object[]]$Values) {
    return ($messages.$Key -f $Values)
}

Write-Step $messages.checkEnv

$pythonVersion = (& $Python --version 2>&1 | Out-String).Trim()
if (-not $pythonVersion) {
    throw $messages.pythonFailed
}
Write-Host (Format-Message "interpreter" @($pythonVersion))

& $Python -c "import PySide6; print(PySide6.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw $messages.pysideMissing
}

& $Python -c "import PyInstaller" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host $messages.pyinstallerMissing
    & $Python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw $messages.pyinstallerFailed }
}

Write-Step $messages.buildStep

if (Test-Path $distDir) { Remove-Item $distDir -Recurse -Force }
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $specDir | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --windowed `
    --name picfinder-probe `
    --distpath $distDir `
    --workpath $buildDir `
    --specpath $specDir `
    $entry

if ($LASTEXITCODE -ne 0) { throw $messages.buildFailed }

$appDir = Join-Path $distDir "picfinder-probe"
$exePath = Join-Path $appDir "picfinder-probe.exe"
if (-not (Test-Path $exePath)) { throw (Format-Message "exeMissing" @($exePath)) }

Write-Step $messages.sizeStep

$files = Get-ChildItem -Path $appDir -Recurse -File
$totalBytes = ($files | Measure-Object -Property Length -Sum).Sum
$sizeMb = [math]::Round($totalBytes / 1MB, 1)
Write-Host (Format-Message "dir" @($appDir))
Write-Host (Format-Message "fileCount" @($files.Count))
Write-Host (Format-Message "sizeMb" @($sizeMb))

Write-Step $messages.startupStep

$samples = @()
$windowSamples = @()
$readySamples = @()
for ($i = 1; $i -le $Runs; $i++) {
    $runResult = Join-Path $probeDir ("run-" + $i + ".json")
    if (Test-Path $runResult) { Remove-Item $runResult -Force }

    # Cold start is defined as what the user perceives: process creation to window visible.
    # The clock must therefore start before Start-Process, not inside the program under test.
    $wallStart = Get-Date
    $process = Start-Process -FilePath $exePath `
        -ArgumentList @("--measure", $runResult) `
        -PassThru

    # Poll the main window handle to learn when the window actually appeared.
    $deadline = $wallStart.AddSeconds(60)
    $windowMs = $null
    while ((Get-Date) -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) { break }
        if ($process.MainWindowHandle -ne 0) {
            $windowMs = ((Get-Date) - $wallStart).TotalMilliseconds
            break
        }
        Start-Sleep -Milliseconds 20
    }

    # Wait for the program under test to write its internal timing.
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $runResult) { break }
        if ($process.HasExited -and -not (Test-Path $runResult)) { break }
        Start-Sleep -Milliseconds 20
    }
    $null = $process.WaitForExit(20000)

    $frameMs = $null
    $source = "-"
    if (Test-Path $runResult) {
        $payload = Get-Content $runResult -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -ne $payload.elapsed_to_first_frame_ms) {
            $frameMs = [math]::Round([double]$payload.elapsed_to_first_frame_ms, 1)
        }
        if ($payload.first_frame_source) { $source = $payload.first_frame_source }
    }

    if ($null -ne $windowMs) {
        $windowSamples += [math]::Round($windowMs, 1)
        Write-Host (Format-Message "runN" @($i, [math]::Round($windowMs, 1)))
    }
    else {
        Write-Host (Format-Message "runMissing" @($i)) -ForegroundColor Yellow
    }
    if ($null -ne $frameMs) { $samples += $frameMs }
    Write-Host (Format-Message "runDetail" @($frameMs, $source))

    Start-Sleep -Milliseconds 800
}

function Get-Stats([double[]]$Values) {
    if ($Values.Count -eq 0) { return @{ Average = $null; Worst = $null } }
    return @{
        Average = [math]::Round((($Values | Measure-Object -Average).Average), 1)
        Worst   = [math]::Round((($Values | Measure-Object -Maximum).Maximum), 1)
    }
}

$windowStats = Get-Stats $windowSamples
$frameStats = Get-Stats $samples

if ($windowSamples.Count -gt 0) {
    Write-Host (Format-Message "summaryWindow" @($windowStats.Average, $windowStats.Worst))
}
if ($samples.Count -gt 0) {
    Write-Host (Format-Message "summaryFrame" @($frameStats.Average, $frameStats.Worst))
}

if ($windowSamples.Count -eq 0) {
    Write-Host $messages.windowFailed -ForegroundColor Yellow
}

$report = [ordered]@{
    measured_at            = (Get-Date).ToString("s")
    python                 = $pythonVersion
    app_directory          = $appDir
    file_count             = $files.Count
    size_mb                = $sizeMb
    startup_window_samples = $windowSamples
    startup_window_average = $windowStats.Average
    startup_window_worst   = $windowStats.Worst
    startup_frame_samples  = $samples
    startup_frame_average  = $frameStats.Average
    startup_frame_worst    = $frameStats.Worst
    runs                   = $Runs
}

$report | ConvertTo-Json -Depth 4 | Set-Content -Path $resultPath -Encoding UTF8

Write-Step $messages.done
Write-Host (Format-Message "resultWritten" @($resultPath))
Write-Host ""
Write-Host $messages.reportHint
