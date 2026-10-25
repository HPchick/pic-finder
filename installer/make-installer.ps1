# PIC FINDER installer builder.
#
# Usage, from the pic-finder directory:
#   powershell -ExecutionPolicy Bypass -File installer\make-installer.ps1
#
# This file is ASCII-only on purpose, same reason as build.ps1.
#
# What it does:
#   1. Locate ISCC.exe (the Inno Setup compiler).
#   2. Locate Languages\ChineseSimplified.isl if the user supplied one.
#   3. Compile installer\picfinder.iss with the right defines.
#   4. Report the setup executable path and size.
#
# The program directory must exist first: run build.ps1 before this script.

[CmdletBinding()]
param(
    [string]$Iscc = "",
    [string]$ChineseIsl = "",
    [string]$AppDirectory = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "path-safety.ps1")

$installerDir = $PSScriptRoot
$project = Split-Path $installerDir -Parent
$messages = Get-Content -Path (Join-Path $installerDir "messages.json") -Raw -Encoding UTF8 | ConvertFrom-Json

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host ("== " + $Text) -ForegroundColor Cyan
}

function Format-Message([string]$Key, [object[]]$Values) {
    return ($messages.$Key -f $values)
}

Write-Step $messages.installerFindStep

if (-not $Iscc) {
    # Build the candidate list defensively: Join-Path throws on an empty parent,
    # and either ProgramFiles variable can be empty in a 32 bit shell.
    #
    # The per user location comes first. winget installs Inno Setup there when
    # it runs without elevation, which is the normal case on a development
    # machine, and that is exactly where it ended up here.
    $candidates = @()
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    }
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 5\ISCC.exe")
    }
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { $Iscc = $candidate; break }
    }
}
if (-not $Iscc -or -not (Test-Path $Iscc)) { throw $messages.installerNoIscc }
Write-Host (Format-Message "installerIscc" @($Iscc))

$isccDir = Split-Path $Iscc -Parent

# Chinese is not shipped with Inno Setup. The developer supplies
# ChineseSimplified.isl; without it the compiler falls back to English so the
# build never fails just because a translation is missing.
if (-not $ChineseIsl) {
    $candidate = Join-Path $isccDir "Languages\ChineseSimplified.isl"
    if (Test-Path $candidate) { $ChineseIsl = $candidate }
}

$defines = @()
if ($ChineseIsl -and (Test-Path $ChineseIsl)) {
    Write-Host (Format-Message "installerChinese" @($ChineseIsl))
    $defines += ('/DChineseIsl=' + $ChineseIsl)
}
else {
    Write-Host $messages.installerNoChinese -ForegroundColor Yellow
    Write-Host (Format-Message "installerChineseHint" @((Join-Path $isccDir "Languages")))
}

# The version lives in one place: the package.
$version = "unknown"
$initPath = Join-Path $project "src\picfinder\__init__.py"
if (Test-Path $initPath) {
    $initText = Get-Content -Path $initPath -Raw -Encoding UTF8
    if ($initText -match '__version__\s*=\s*"([^"]+)"') { $version = $Matches[1] }
}
if ($version -eq "unknown") { throw $messages.versionFailed }
$defines += ('/DAppVersion=' + $version)

$sourceDir = Get-BuiltAppDirectory $installerDir $AppDirectory
$outputBase = Get-ContainedDirectory $project (Join-Path $installerDir 'dist')
$ownedSetup = New-OwnedDirectory $outputBase 'setup'
$defines += ('/DAppSourceDir=' + $sourceDir)
$defines += ('/DAppOutputDir=' + $ownedSetup.Path)
if (-not (Test-Path (Join-Path $sourceDir "PIC FINDER.exe"))) {
    throw (Format-Message "installerNoProgram" @($sourceDir))
}

Write-Step $messages.installerCompileStep
Write-Host (Format-Message "installerVersion" @($version))

$issPath = Join-Path $installerDir "picfinder.iss"
& $Iscc @defines $issPath
if ($LASTEXITCODE -ne 0) { throw (Format-Message "installerCompileFailed" @($LASTEXITCODE)) }

$setupPath = Join-Path $ownedSetup.Path ("PIC-FINDER-Setup-" + $version + ".exe")
if (-not (Test-Path $setupPath)) { throw (Format-Message "installerSetupMissing" @($setupPath)) }

$setupMb = [math]::Round((Get-Item $setupPath).Length / 1MB, 1)
Write-Step $messages.installerDone
Write-Host (Format-Message "installerSetupReady" @($setupPath, $setupMb))
Write-Host ""
Write-Host $messages.installerNextHint
