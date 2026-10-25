# Shared path guards. This file deliberately contains ASCII only.
function Get-ContainedDirectory([string]$Base, [string]$Candidate) {
    $basePath = [IO.Path]::GetFullPath($Base).TrimEnd('\', '/')
    $candidatePath = [IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
    if (-not $candidatePath.StartsWith($basePath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Directory must be a strict child of the approved base.'
    }
    $current = $candidatePath
    while ($current.Length -ge $basePath.Length) {
        if (Test-Path -LiteralPath $current) {
            if (((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Reparse points are not allowed in managed temporary paths.'
            }
        }
        if ($current -eq $basePath) { break }
        $current = Split-Path -Path $current -Parent
    }
    return $candidatePath
}

function New-OwnedDirectory([string]$Base, [string]$Prefix) {
    $token = [Guid]::NewGuid().ToString('N')
    $path = Get-ContainedDirectory $Base (Join-Path $Base ($Prefix + '-' + $token))
    if (Test-Path -LiteralPath $path) { throw 'Temporary directory already exists.' }
    New-Item -ItemType Directory -Path $path -ErrorAction Stop | Out-Null
    Set-Content -LiteralPath (Join-Path $path '.picfinder-owned') -Value $token -Encoding ASCII
    return [PSCustomObject]@{ Path = $path; Token = $token }
}

function Remove-OwnedDirectory([string]$Base, [string]$Path, [string]$Token) {
    $checked = Get-ContainedDirectory $Base $Path
    $marker = Join-Path $checked '.picfinder-owned'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { throw 'Ownership marker is missing.' }
    if ((Get-Content -LiteralPath $marker -Raw).Trim() -ne $Token) { throw 'Ownership token does not match.' }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($checked)
    while ($pending.Count -gt 0) {
        foreach ($entry in Get-ChildItem -LiteralPath $pending.Pop() -Force) {
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Refusing to clean a directory containing reparse points.'
            }
            if ($entry.PSIsContainer) { $pending.Push($entry.FullName) }
        }
    }
    Remove-Item -LiteralPath $checked -Recurse -Force
}

function Get-BuiltAppDirectory([string]$InstallerDirectory, [string]$ExplicitDirectory) {
    if ($ExplicitDirectory) { return [IO.Path]::GetFullPath($ExplicitDirectory) }
    $report = Join-Path $InstallerDirectory 'build-report.json'
    if (-not (Test-Path -LiteralPath $report -PathType Leaf)) { throw 'Run build.ps1 first, or specify -AppDirectory.' }
    $build = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $build.app_directory) { throw 'Build report has no application directory.' }
    return [IO.Path]::GetFullPath($build.app_directory)
}
