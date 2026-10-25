# PIC FINDER packaging script.
#
# Usage, from the pic-finder directory:
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Zip
#
# This file is ASCII-only on purpose. Windows PowerShell 5.1 reads a script
# without a BOM as ANSI, which corrupts non-ASCII text and breaks parsing.
# All display text lives in installer\messages.json and is loaded as UTF-8.
#
# This script never pipes a native program's output. Pipes into PowerShell
# cmdlets fail in the restricted command environment this project is built in,
# and the version number is read from the source file with a regex instead.
#
# What it does:
#   1. Check Python, PySide6 and PyInstaller.
#   2. Draw the application icon if it is missing.
#   3. Build the program directory with PyInstaller from installer\picfinder.spec.
#   4. Verify the executable, the bundled dictionary and the icon are in place.
#   5. Measure the directory and write installer\build-report.json.
#   6. Optionally pack the directory into installer\dist\PIC-FINDER-<version>.zip.
#
# The program directory is the deliverable. The installer script
# installer\picfinder.iss turns it into a setup executable.

[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$Clean,
    [switch]$Zip
)

$ErrorActionPreference = "Stop"
if ($Clean) { throw '-Clean is disabled. Each build now uses a new output directory.' }
. (Join-Path $PSScriptRoot 'path-safety.ps1')

$installerDir = $PSScriptRoot
$project = Split-Path $installerDir -Parent
$specPath = Join-Path $installerDir "picfinder.spec"
$distBase = Join-Path $installerDir "dist"
$distBase = Get-ContainedDirectory $project $distBase
$ownedBuild = New-OwnedDirectory $distBase 'build'
$distDir = $ownedBuild.Path
$buildDir = Join-Path $distDir '.work'
$reportPath = Join-Path $installerDir "build-report.json"
$messagesPath = Join-Path $installerDir "messages.json"

$messages = Get-Content -Path $messagesPath -Raw -Encoding UTF8 | ConvertFrom-Json

$appName = "PIC FINDER"
$appDir = Join-Path $distDir $appName
$exePath = Join-Path $appDir ($appName + ".exe")

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host ("== " + $Text) -ForegroundColor Cyan
}

function Format-Message([string]$Key, [object[]]$Values) {
    return ($messages.$Key -f $Values)
}

# The version lives in one place: the package. Read it with a regex so the
# script does not depend on running Python just to learn a string.
$version = "unknown"
$initPath = Join-Path $project "src\picfinder\__init__.py"
if (Test-Path $initPath) {
    $initText = Get-Content -Path $initPath -Raw -Encoding UTF8
    if ($initText -match '__version__\s*=\s*"([^"]+)"') { $version = $Matches[1] }
}
if ($version -eq "unknown") { throw $messages.versionFailed }

Write-Step $messages.checkEnv

& $Python --version
if ($LASTEXITCODE -ne 0) { throw $messages.pythonFailed }
Write-Host (Format-Message "interpreter" @($Python, $version))

& $Python -c "import PySide6"
if ($LASTEXITCODE -ne 0) { throw $messages.pysideMissing }

& $Python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) { throw $messages.pyinstallerMissing }

Write-Step $messages.iconStep

$iconPath = Join-Path $project "assets\picfinder.ico"
if (-not (Test-Path $iconPath)) {
    & $Python (Join-Path $project "work\make_icon.py")
    if ($LASTEXITCODE -ne 0) { throw $messages.iconFailed }
}
Write-Host (Format-Message "iconReady" @($iconPath))

Write-Step $messages.buildStep

& $Python -m PyInstaller `
    --distpath $distDir `
    --workpath $buildDir `
    $specPath

if ($LASTEXITCODE -ne 0) { throw $messages.buildFailed }

Write-Step $messages.verifyStep

if (-not (Test-Path $exePath)) { throw (Format-Message "exeMissing" @($exePath)) }
Write-Host (Format-Message "exeReady" @($exePath))

# The bundled dictionary is what the translation layer reads on first run.
# Missing files here would not crash the program, which is exactly why this
# check must be explicit.
$bundledDictionary = Join-Path $appDir "_internal\dictionary"
if (-not (Test-Path (Join-Path $bundledDictionary "manifest.json"))) {
    throw (Format-Message "dictionaryMissing" @($bundledDictionary))
}
$dictionaryFiles = Get-ChildItem -Path $bundledDictionary -File
Write-Host (Format-Message "dictionaryReady" @($dictionaryFiles.Count))

# The global Chinese tag table is what makes the translator usable. It lives in
# a subdirectory and is about ten megabytes, so a datas entry can go missing
# without anything else noticing. Check it explicitly.
$bundledZh = Join-Path $bundledDictionary "zh"
if (-not (Test-Path (Join-Path $bundledZh "general.csv"))) {
    throw (Format-Message "zhTableMissing" @($bundledZh))
}
$zhFiles = Get-ChildItem -Path $bundledZh -File
$zhBytes = ($zhFiles | Measure-Object -Property Length -Sum).Sum
Write-Host (Format-Message "zhTableReady" @($zhFiles.Count, [math]::Round($zhBytes / 1MB, 1)))

Write-Step $messages.sizeStep

$files = Get-ChildItem -Path $appDir -Recurse -File
$totalBytes = ($files | Measure-Object -Property Length -Sum).Sum
$sizeMb = [math]::Round($totalBytes / 1MB, 1)
Write-Host (Format-Message "dir" @($appDir))
Write-Host (Format-Message "fileCount" @($files.Count))
Write-Host (Format-Message "sizeMb" @($sizeMb))

$zipPath = $null
if ($Zip) {
    Write-Step $messages.zipStep
    $zipPath = Join-Path $distDir ("PIC-FINDER-" + $version + ".zip")
    if (Test-Path -LiteralPath $zipPath) { throw "Archive already exists: $zipPath" }
    Compress-Archive -Path $appDir -DestinationPath $zipPath -CompressionLevel Optimal
    $zipMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host (Format-Message "zipReady" @($zipPath, $zipMb))
}

$report = [ordered]@{
    built_at      = (Get-Date).ToString("s")
    version       = $version
    app_directory = $appDir
    executable    = $exePath
    file_count    = $files.Count
    size_bytes    = $totalBytes
    size_mb       = $sizeMb
    dictionary    = $bundledDictionary
    zip           = $zipPath
}

$report | ConvertTo-Json -Depth 4 | Set-Content -Path $reportPath -Encoding UTF8

Write-Step $messages.done
Write-Host (Format-Message "resultWritten" @($reportPath))
Write-Host ""
Write-Host $messages.nextHint
