#requires -Version 5.1
<#
  Repair-MaudeWTProfile.ps1
  Restores a working "Maude" entry in the Windows Terminal dropdown and clears
  the orphaned "profile no longer detected" entry left by the installer.
  Run as your NORMAL user (not elevated). Fully close Windows Terminal after.
#>
[CmdletBinding()]
param([string]$DistroName = 'Maude')

$ErrorActionPreference = 'Stop'

# 1. Confirm the WSL distro is actually registered.
$distros = (& wsl.exe -l -q) -replace "`0", '' -split "`r?`n" |
    ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
if ($distros -notcontains $DistroName) {
    Write-Warning "WSL distro '$DistroName' is not registered (found: $($distros -join ', '))."
    Write-Warning "Fix/register the distro first, then re-run."
    return
}
Write-Host "Found WSL distro '$DistroName'." -ForegroundColor Green

# 2. Locate Windows Terminal settings.json.
$settingsPath = @(
    "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
    "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json"
    "$env:LOCALAPPDATA\Microsoft\Windows Terminal\settings.json"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $settingsPath) { throw "Windows Terminal settings.json not found. Is Windows Terminal installed?" }
Write-Host "Settings: $settingsPath" -ForegroundColor Gray

# 3. Back it up.
$backup = "$settingsPath.maude-bak"
Copy-Item -LiteralPath $settingsPath -Destination $backup -Force
Write-Host "Backup:   $backup" -ForegroundColor Gray

# 4. Parse (tolerate // line comments if present).
$raw = Get-Content -LiteralPath $settingsPath -Raw
try { $json = $raw | ConvertFrom-Json }
catch {
    $stripped = ($raw -split "`n" | Where-Object { $_.TrimStart() -notlike '//*' }) -join "`n"
    $json = $stripped | ConvertFrom-Json
}
if (-not $json.profiles) { throw "Unexpected settings.json shape (no 'profiles')." }
if (-not $json.profiles.PSObject.Properties['list']) {
    $json.profiles | Add-Member -NotePropertyName list -NotePropertyValue @() -Force
}

# 5. Reuse a Maude icon if an install left one behind.
$icon = @(
    "$env:LOCALAPPDATA\OSU\Maude\maude.png"
    "$env:LOCALAPPDATA\Maude\maude.png"
    "$env:LOCALAPPDATA\Maude\Data\Maude\maude.png"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

# 6. Drop generator/stub/stale "Maude" entries (the ones that orphan). A
#    generator profile has a 'source' or no 'commandline'; keep everything else.
$fixedGuid = '{c0ffee01-1111-2222-3333-444455556666}'
$kept = @(); $removed = 0
foreach ($p in $json.profiles.list) {
    $nm  = if ($p.PSObject.Properties['name'])        { $p.name }        else { '' }
    $cmd = if ($p.PSObject.Properties['commandline']) { $p.commandline } else { '' }
    $gid = if ($p.PSObject.Properties['guid'])        { $p.guid }        else { '' }
    if ($nm -eq $DistroName -and ($cmd -eq '' -or $gid -eq $fixedGuid)) { $removed++; continue }
    $kept += $p
}

# 7. Build a static, self-contained Maude profile.
$maudeProfile = [PSCustomObject][ordered]@{
    guid        = $fixedGuid
    name        = $DistroName
    commandline = "wsl.exe -d $DistroName --cd ~"
    hidden      = $false
}
if ($icon) { $maudeProfile | Add-Member -NotePropertyName icon -NotePropertyValue ($icon -replace '\\','/') -Force }
$kept += $maudeProfile
$json.profiles.list = $kept

# 8. Write back.
$json | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $settingsPath -Encoding UTF8

$suffix = if ($removed -eq 1) { 'y' } else { 'ies' }
Write-Host ""
Write-Host "Done. Removed $removed stale/orphan entr$suffix; added 1 working '$DistroName' profile." -ForegroundColor Green
Write-Host "Fully close Windows Terminal (every window) and reopen it - '$DistroName' will be in the dropdown." -ForegroundColor Yellow
Write-Host "Undo if needed:  Copy-Item '$backup' '$settingsPath' -Force" -ForegroundColor DarkGray
