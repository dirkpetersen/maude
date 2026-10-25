#requires -Version 5.1
<#
  add-user.ps1
  Adds a second user to the Maude WSL distro and a Windows Terminal profile
  "Ubuntu (<version>)" that opens the distro as that user. The Maude profile
  and the Maude user are not changed. Run as your NORMAL user (not elevated),
  in an interactive PowerShell window (you set a password for the new user).
#>
[CmdletBinding()]
param(
    [string]$UserName = $env:USERNAME.ToLower(),
    [string]$DistroName = 'Maude'
)
$ErrorActionPreference = 'Stop'
$ProfileGuid = '{5d2f8a9e-7c41-4b6a-9e3d-1a8b0c6f4e27}'

if ($UserName -cnotmatch '^[a-z][a-z0-9_-]{0,31}$') {
    throw "'$UserName' is not a valid Linux user name. Run again with -UserName <name> (lowercase letters, digits, - and _)."
}

# a. Distro must exist.
& wsl.exe -d $DistroName -u root -e true
if ($LASTEXITCODE -ne 0) { throw "WSL distro '$DistroName' is not registered." }

# b. Local fixed drives.
$letters = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
    ForEach-Object { $_.DeviceID.Substring(0, 1).ToLower() })
Write-Host "Drives for '$UserName': $($letters -join ' ')" -ForegroundColor Gray

# c. Run add-user.sh as root. User and letters go in as argv, never into the bash string.
# PS 5.1 strips inner double quotes; args are safe only because UserName is regex-validated and drive letters are single letters.
& wsl.exe -d $DistroName -u root -e bash -c 'd=$(mktemp -d) && curl --max-time 30 -fsSLo $d/add-user.sh https://raw.githubusercontent.com/dirkpetersen/maude/main/light/add-user.sh && bash $d/add-user.sh $@' _ $UserName @letters
if ($LASTEXITCODE -ne 0) { throw "add-user.sh failed (exit $LASTEXITCODE). Nothing else was changed." }

# d. Ubuntu version for the profile name.
$ver = ((& wsl.exe -d $DistroName -u root -e sh -c '. /etc/os-release; echo $VERSION_ID') | Out-String).Trim()
if ($ver -notmatch '^[0-9.]+$') { $ver = '' }
$profileName = if ($ver) { "Ubuntu ($ver)" } else { 'Ubuntu' }

# e. Ubuntu icon shipped in the image (base64: PowerShell 5.1 corrupts binary pipes).
$iconPath = $null
$b64 = ((& wsl.exe -d $DistroName -u root -e sh -c 'find /usr/share/wsl /usr/lib/wsl -name \*.ico 2>/dev/null | head -n 1 | xargs -r base64 -w0') | Out-String).Trim()
if ($b64) {
    $dir = "$env:LOCALAPPDATA\Maude\OS"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $iconPath = Join-Path $dir 'ubuntu.ico'
    [IO.File]::WriteAllBytes($iconPath, [Convert]::FromBase64String($b64))
}

# Ubuntu color scheme shipped in the image (optional).
$scheme = $null
try {
    $tp = ((& wsl.exe -d $DistroName -u root -e cat /usr/share/wsl/terminal-profile.json 2>$null) | Out-String) | ConvertFrom-Json
    $scheme = @($tp.schemes | Where-Object { $_.name -eq 'Ubuntu' }) | Select-Object -First 1
} catch { $scheme = $null }

# f. Windows Terminal profile (fixed guid, so a rerun updates it).
$settingsPath = @(
    "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
    "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json"
    "$env:LOCALAPPDATA\Microsoft\Windows Terminal\settings.json"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $settingsPath) {
    Write-Warning "Windows Terminal settings.json not found. User '$UserName' is ready; open it with: wsl -d $DistroName -u $UserName --cd ~"
    return
}
Copy-Item -LiteralPath $settingsPath -Destination "$settingsPath.bak" -Force
$json = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$new = [PSCustomObject][ordered]@{
    guid        = $ProfileGuid
    name        = $profileName
    commandline = "wsl.exe -d $DistroName -u $UserName --cd ~"
    hidden      = $false
}
if ($iconPath) { $new | Add-Member -NotePropertyName icon -NotePropertyValue ($iconPath -replace '\\', '/') }
if ($scheme) {
    $new | Add-Member -NotePropertyName colorScheme -NotePropertyValue 'Ubuntu' -Force
    $new | Add-Member -NotePropertyName cursorShape -NotePropertyValue 'filledBox' -Force
    $json | Add-Member -NotePropertyName schemes -NotePropertyValue (@($json.schemes | Where-Object { $_ -and $_.name -ne 'Ubuntu' }) + $scheme) -Force
}
$json.profiles.list = @($json.profiles.list | Where-Object { $_.guid -ne $ProfileGuid }) + $new
$json | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $settingsPath -Encoding UTF8

# g. Done.
Write-Host ""
Write-Host "Done. Open the Windows Terminal dropdown and pick '$profileName'," -ForegroundColor Green
Write-Host "or run: wsl -d $DistroName -u $UserName --cd ~" -ForegroundColor Green
Write-Host "Backup of settings: $settingsPath.bak" -ForegroundColor DarkGray
