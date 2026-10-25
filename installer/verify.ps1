# PIC FINDER packaged program verification.
#
# Usage, from the pic-finder directory:
#   powershell -ExecutionPolicy Bypass -File installer\verify.ps1
#   powershell -ExecutionPolicy Bypass -File installer\verify.ps1 -Probe
#
# This file is ASCII-only on purpose, same reason as build.ps1: Windows
# PowerShell 5.1 reads a BOM-less script as ANSI, which corrupts non-ASCII
# text and breaks parsing. Even the patterns this script searches for in the
# program's log are Chinese, so they live in messages.json too.
#
# Why a separate script instead of trusting the build
#   A build that produced files is not a program that runs. Two failures in
#   this project were only visible after packaging, and both looked like
#   "double click does nothing":
#     - the entry script was executed as a top level module, so the package
#       relative imports failed at startup;
#     - the bundled dictionary was not where the program looked for it.
#   This script runs the packaged executable and checks what it wrote.
#
# Levels
#   default: headless self check plus a window smoke run. No network needed.
#   -Probe : additionally search and download real images. Needs network.

[CmdletBinding()]
param(
    [string]$DataRoot = "work\_verify",
    [string]$AppDirectory = "",
    [int]$SmokeMs = 4000,
    [int]$ProbeCount = 2,
    [string]$ProbeQuery = "",
    [switch]$Probe,
    [switch]$KeepData
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "path-safety.ps1")

$installerDir = $PSScriptRoot
$project = Split-Path $installerDir -Parent
$messages = Get-Content -Path (Join-Path $installerDir "messages.json") -Raw -Encoding UTF8 | ConvertFrom-Json

$appName = "PIC FINDER"
$appDir = Get-BuiltAppDirectory $installerDir $AppDirectory
$exePath = Join-Path $appDir ($appName + ".exe")
$requestedRoot = if ([IO.Path]::IsPathRooted($DataRoot)) { $DataRoot } else { Join-Path $project $DataRoot }
$verifyBase = Get-ContainedDirectory $project $requestedRoot

if (-not $ProbeQuery) { $ProbeQuery = $messages.probeDefaultQuery }

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host ("== " + $Text) -ForegroundColor Cyan
}

function Format-Message([string]$Key, [object[]]$Values) {
    return ($messages.$Key -f $Values)
}

if (-not (Test-Path $exePath)) { throw (Format-Message "verifyNoExe" @($exePath)) }
Write-Host (Format-Message "verifyExe" @($exePath))

# Never reuse or delete the supplied base directory. Own only this fresh child.
$ownedRun = New-OwnedDirectory $verifyBase 'verify'
$dataPath = $ownedRun.Path
$reportPath = Join-Path $dataPath 'self-check.txt'
Write-Host ("Verification data: " + $dataPath)

# Start-Process returns the real exit code for a windowed program.
# The call operator does not wait for one, which is how a crash got mistaken
# for a successful launch the first time this was tested.
#
# Start-Process also does NOT quote arguments for us. It joins the array with
# spaces into one command line, so a value containing a space arrives at the
# program as two arguments. That is how a Chinese probe query made argparse
# exit with code 2 and the program produce no log at all, which looked like a
# packaging failure. Quote anything with whitespace in it.
function Quote-Argument([string]$Value) {
    if ($Value -match '\s') { return '"' + $Value + '"' }
    return $Value
}

function Invoke-App([string[]]$Arguments) {
    $quoted = @()
    foreach ($argument in $Arguments) { $quoted += (Quote-Argument $argument) }
    $process = Start-Process -FilePath $exePath `
        -ArgumentList $quoted `
        -WorkingDirectory $project `
        -WindowStyle Hidden `
        -Wait -PassThru
    return $process.ExitCode
}

Write-Step $messages.verifyCheckStep
$code = Invoke-App @("--check", "--report", $reportPath, "--data-root", $dataPath)
if ($code -ne 0) { throw (Format-Message "verifyCheckFailed" @($code)) }
if (-not (Test-Path $reportPath)) { throw (Format-Message "verifyReportMissing" @($reportPath)) }
Write-Host (Format-Message "verifyCheckOk" @($reportPath))

# The self check writes what it read. A packaged program that cannot find its
# bundled dictionary still starts, so the row count is the real evidence.
$reportText = Get-Content -Path $reportPath -Raw -Encoding UTF8
$dictionaryPattern = $messages.reportDictionaryRows + "\s+(\d+)"
if ($reportText -match $dictionaryPattern) {
    $rows = [int]$Matches[1]
    if ($rows -lt 1) { throw (Format-Message "verifyDictionaryEmpty" @($reportPath)) }
    Write-Host (Format-Message "verifyDictionaryOk" @($rows))
}
else {
    Write-Host $messages.verifyDictionaryUnknown -ForegroundColor Yellow
}

# The program loads the ten-megabyte global Chinese tag table in the background.
# A build that lost it still starts and still passes the smoke run, and the only
# visible symptom is that Chinese input finds almost nothing.
#
# The pattern lives in messages.json, not here: this file must stay ASCII-only,
# and the line being matched is Chinese.
if ($reportText -match $messages.reportGlobalIndexPattern) {
    $tags = [int]$Matches[1]
    if ($tags -lt 1000) { throw (Format-Message "verifyGlobalIndexSmall" @($tags, $reportPath)) }
    Write-Host (Format-Message "verifyGlobalIndexOk" @($tags))
}
else {
    throw (Format-Message "verifyGlobalIndexMissing" @($reportPath))
}

Write-Step $messages.verifySmokeStep
$code = Invoke-App @("--smoke", "$SmokeMs", "--data-root", $dataPath)
if ($code -ne 0) { throw (Format-Message "verifySmokeFailed" @($code)) }
Write-Host (Format-Message "verifySmokeOk" @($SmokeMs))

$logPath = Join-Path $dataPath "data\logs\picfinder.log"
if (Test-Path $logPath) {
    $logText = Get-Content -Path $logPath -Raw -Encoding UTF8
    if ($logText -notmatch $messages.logWindowBuilt) {
        throw (Format-Message "verifyWindowMissing" @($logPath))
    }
    Write-Host (Format-Message "verifyWindowOk" @($logPath))
}

if ($Probe) {
    Write-Step $messages.verifyProbeStep
    $code = Invoke-App @(
        "--probe-search", $ProbeQuery,
        "--probe-download", "$ProbeCount",
        "--data-root", $dataPath
    )
    if ($code -ne 0) { throw (Format-Message "verifyProbeFailed" @($code)) }

    # Four lines must be present. The last one is the only proof that the
    # background download threads and the interface are wired together.
    $logText = Get-Content -Path $logPath -Raw -Encoding UTF8
    $checks = @(
        @{ Pattern = $messages.probeSearchMark },
        @{ Pattern = $messages.probeFilesMark },
        @{ Pattern = $messages.probeLibraryMark },
        @{ Pattern = $messages.probeCardsMark }
    )
    foreach ($check in $checks) {
        if ($logText -match [regex]::Escape($check.Pattern)) {
            Write-Host ("  OK  " + $check.Pattern)
        }
        else {
            throw (Format-Message "verifyProbeMissing" @($check.Pattern, $logPath))
        }
    }

    $libraryPath = Join-Path $dataPath "library"
    $images = @(Get-ChildItem -Path $libraryPath -Recurse -File -ErrorAction SilentlyContinue)
    Write-Host (Format-Message "verifyProbeFiles" @($images.Count))
    foreach ($image in $images) {
        $relative = $image.FullName.Substring($libraryPath.Length + 1)
        Write-Host ("  " + $relative + "  " + $image.Length + " bytes")
    }
}

if (-not $KeepData) {
    Remove-OwnedDirectory $verifyBase $dataPath $ownedRun.Token
    Write-Host ""
    Write-Host $messages.verifyCleaned
}

Write-Host ""
Write-Host $messages.verifyDone -ForegroundColor Green
