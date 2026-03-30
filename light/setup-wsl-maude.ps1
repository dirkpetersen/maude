#Requires -Version 5.1
<#
.SYNOPSIS
    Sets up a WSL2 Ubuntu 24.04 dev environment named "Maude".

.DESCRIPTION
    Idempotent script that:
    1. Installs WSL2 (if not present)
    2. Installs Windows Terminal (if not present)
    3. Creates a shared host folder (OneDrive or Documents) with custom icon
    4. Imports Ubuntu 24.04 as a WSL distro named "Maude"
    5. Runs root-bootstrap.sh  (user, mom, PATH, packages, sandbox mount)
    6. Runs maude-bootstrap.sh (dev-station, maude launcher, PS1)
    7. Opens Maude in Windows Terminal

    The distro is sandboxed: automatic Windows drive mounting is disabled,
    and only the shared Maude folder is mounted into /home/maude/Maude
    via drvfs + /etc/fstab.

.NOTES
    Run from an elevated PowerShell prompt:
        Set-ExecutionPolicy Bypass -Scope Process -Force
        .\setup-wsl-maude.ps1
#>

param(
    [string]$DistroName  = "Maude",
    [string]$DefaultUser = "maude",
    [string]$InstallDir  = "$env:LOCALAPPDATA\Maude"
)

# ── Locate Windows Terminal settings.json ─────────────────────────────
# Supports Store, Preview, and non-Store (winget/scoop) installs.
function Find-WTSettingsPath {
    $candidates = @(
        Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
        Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json"
        Join-Path $env:LOCALAPPDATA "Microsoft\Windows Terminal\settings.json"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

# ── Resolve script directory and download missing files from GitHub ───
# $PSScriptRoot is empty when run via iex. Even when set, the user may
# have downloaded only setup-wsl-maude.ps1 — companion files may be missing.
$GH_RAW = "https://raw.githubusercontent.com/dirkpetersen/maude/main"
$cacheBust = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

if ($PSScriptRoot -and $PSScriptRoot -ne '') {
    $ScriptDir = $PSScriptRoot
} else {
    $ScriptDir = Join-Path $env:TEMP "maude-setup"
    New-Item -ItemType Directory -Force -Path $ScriptDir | Out-Null
}

# Always download companion files from GitHub (cache-bust to avoid stale CDN copies)
$filesToDownload = @(
    @{ Url = "$GH_RAW/light/root-bootstrap.sh";       Dest = "root-bootstrap.sh" }
    @{ Url = "$GH_RAW/light/maude-bootstrap.sh";      Dest = "maude-bootstrap.sh" }
    @{ Url = "$GH_RAW/light/maude";                   Dest = "maude" }
    @{ Url = "$GH_RAW/maude.png";                     Dest = "maude.png" }
    @{ Url = "$GH_RAW/packages/ubuntu-packages.yaml"; Dest = "..\packages\ubuntu-packages.yaml" }
)
$wc = New-Object Net.WebClient
foreach ($dl in $filesToDownload) {
    $destPath = Join-Path $ScriptDir $dl.Dest
    $destDir = Split-Path $destPath -Parent
    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }
    try {
        $wc.DownloadFile("$($dl.Url)?cache=$cacheBust", $destPath)
    } catch {
        Write-Host "WARNING: Could not download $($dl.Url): $_" -ForegroundColor Yellow
    }
}

# ── Self-elevate to Administrator if needed ──

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Not running as Administrator. Attempting to elevate..." -ForegroundColor Yellow
    try {
        if ($PSCommandPath) {
            Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`""
        } else {
            # Running via iex — re-download and run the script elevated
            $cmd = "Set-ExecutionPolicy Bypass -Scope Process -Force; iex ((New-Object Net.WebClient).DownloadString('$GH_RAW/light/setup-wsl-maude.ps1?cache=$cacheBust'))"
            Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -Command `"$cmd`""
        }
    } catch {
        Write-Host @"

ERROR: This script requires Administrator privileges.
Please right-click PowerShell → "Run as Administrator", then run:

    Set-ExecutionPolicy Bypass -Scope Process -Force
    iex ((New-Object Net.WebClient).DownloadString('$GH_RAW/light/setup-wsl-maude.ps1'))

"@ -ForegroundColor Red
    }
    exit
}

# ── Helper: convert a Windows path to a WSL path ──

function Get-WslPath($winPath) {
    wsl -d $DistroName -u root -- wslpath -u ($winPath -replace '\\', '/')
}

# ── Helper: convert a PNG file to ICO format ──
# Writes a valid ICO container that embeds the PNG data directly.
# Works with any PNG size; Explorer picks the best fit.
function Convert-PngToIco($pngPath, $icoPath) {
    $pngBytes = [System.IO.File]::ReadAllBytes((Resolve-Path $pngPath).Path)

    # Read PNG dimensions from IHDR chunk (bytes 16-23)
    Add-Type -AssemblyName System.Drawing
    $img = [System.Drawing.Image]::FromFile((Resolve-Path $pngPath).Path)
    $w = [Math]::Min($img.Width, 256)
    $h = [Math]::Min($img.Height, 256)
    $img.Dispose()

    # ICO uses 0 to mean 256
    $wb = if ($w -eq 256) { [byte]0 } else { [byte]$w }
    $hb = if ($h -eq 256) { [byte]0 } else { [byte]$h }

    $ms = New-Object System.IO.MemoryStream
    $bw = New-Object System.IO.BinaryWriter($ms)

    # ICO header: reserved(2) + type=1(2) + count=1(2)
    $bw.Write([UInt16]0)      # reserved
    $bw.Write([UInt16]1)      # type: 1 = ICO
    $bw.Write([UInt16]1)      # image count

    # Directory entry: w(1) h(1) colors(1) reserved(1) planes(2) bpp(2) size(4) offset(4)
    $bw.Write($wb)            # width
    $bw.Write($hb)            # height
    $bw.Write([byte]0)        # color palette count (0 = no palette)
    $bw.Write([byte]0)        # reserved
    $bw.Write([UInt16]1)      # color planes
    $bw.Write([UInt16]32)     # bits per pixel
    $bw.Write([UInt32]$pngBytes.Length)  # image data size
    $bw.Write([UInt32]22)     # offset to image data (6 header + 16 entry)

    # PNG data
    $bw.Write($pngBytes)
    $bw.Flush()

    [System.IO.File]::WriteAllBytes($icoPath, $ms.ToArray())
    $bw.Dispose()
    $ms.Dispose()
}

# ── Detect host folder (OneDrive for Business > OneDrive > Documents) ──
# Priority: env vars first (set by OneDrive client), then folder scan, then Documents.
# OneDrive for Business folders are named "OneDrive - <Organization>" (varies by org).

if ($env:OneDriveCommercial) {
    $HostFolder = Join-Path $env:OneDriveCommercial "Maude"
    $HostFolderSource = "OneDrive for Business"
} elseif ($env:OneDriveConsumer) {
    $HostFolder = Join-Path $env:OneDriveConsumer "Maude"
    $HostFolderSource = "OneDrive Personal"
} elseif ($env:OneDrive) {
    $HostFolder = Join-Path $env:OneDrive "Maude"
    $HostFolderSource = "OneDrive"
} else {
    # Env vars not set — scan user profile for OneDrive folders
    $odBusiness = Get-ChildItem -Path $env:USERPROFILE -Directory -Filter "OneDrive - *" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($odBusiness) {
        $HostFolder = Join-Path $odBusiness.FullName "Maude"
        $HostFolderSource = "OneDrive for Business ($($odBusiness.Name))"
    } else {
        $odPersonal = Join-Path $env:USERPROFILE "OneDrive"
        if (Test-Path $odPersonal) {
            $HostFolder = Join-Path $odPersonal "Maude"
            $HostFolderSource = "OneDrive Personal"
        } else {
            $HostFolder = Join-Path ([Environment]::GetFolderPath('MyDocuments')) "Maude"
            $HostFolderSource = "Documents"
        }
    }
}

Write-Host "=== Maude WSL Setup ===" -ForegroundColor Cyan

# ── Step 1: Install WSL2 ──                                       # REQUIRES ADMIN

Write-Host "`n[1/7] Checking WSL..." -ForegroundColor Green
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
    Write-Host "WSL is already installed." -ForegroundColor Gray
} else {
    Write-Host "Installing WSL2 (no distribution)..."
    wsl --install --no-distribution
    Write-Host "WSL2 installed. A reboot may be required before continuing." -ForegroundColor Yellow
    Read-Host "Press Enter to exit, then re-run this script after rebooting"
    exit
}

# ── Step 2: Install Windows Terminal ──                            # does NOT require admin

Write-Host "`n[2/7] Checking Windows Terminal..." -ForegroundColor Green
$wtPresent = (Get-Command wt.exe -ErrorAction SilentlyContinue) -or
             (Get-AppxPackage -Name "Microsoft.WindowsTerminal" -ErrorAction SilentlyContinue)
if ($wtPresent) {
    Write-Host "Windows Terminal is already installed." -ForegroundColor Gray
} else {
    Write-Host "Installing Windows Terminal via winget..."
    winget install --id Microsoft.WindowsTerminal --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "winget failed. Trying AppX fallback..." -ForegroundColor Yellow
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.WindowsTerminal_8wekyb3d8bbwe
    }
    Write-Host "Windows Terminal installed." -ForegroundColor Gray
}

# ── Step 3: Create shared host folder with icon ──                 # does NOT require admin

Write-Host "`n[3/7] Setting up host folder ($HostFolderSource)..." -ForegroundColor Green
New-Item -ItemType Directory -Force -Path $HostFolder | Out-Null
# Pre-create .claude and Projects so they exist when the drvfs mount activates
$claudeDir = Join-Path $HostFolder ".claude"
$projectsDir = Join-Path $HostFolder "Projects"
New-Item -ItemType Directory -Force -Path $claudeDir | Out-Null
New-Item -ItemType Directory -Force -Path $projectsDir | Out-Null
if (Test-Path $claudeDir) {
    Write-Host "Created $claudeDir" -ForegroundColor Gray
} else {
    Write-Host "WARNING: Failed to create $claudeDir" -ForegroundColor Yellow
}
if (Test-Path $projectsDir) {
    Write-Host "Created $projectsDir" -ForegroundColor Gray
} else {
    Write-Host "WARNING: Failed to create $projectsDir" -ForegroundColor Yellow
}
Write-Host "Host folder: $HostFolder" -ForegroundColor Gray

# Set custom folder icon (PNG -> ICO conversion for desktop.ini)
$iconSrc = Join-Path $ScriptDir "maude.png"
if (-not (Test-Path $iconSrc)) { $iconSrc = Join-Path $ScriptDir "..\maude.png" }

if (Test-Path $iconSrc) {
    try {
        $icoPath = Join-Path $HostFolder "maude.ico"
        $desktopIni = Join-Path $HostFolder "desktop.ini"
        # Clear hidden+system attributes from previous run so we can overwrite
        foreach ($f in @($icoPath, $desktopIni)) {
            if (Test-Path $f) { attrib -h -s "$f" }
        }
        Convert-PngToIco $iconSrc $icoPath

        # desktop.ini tells Explorer to use the custom icon
        "[.ShellClassInfo]`r`nIconResource=$icoPath,0" | Set-Content $desktopIni -Encoding Unicode
        attrib +h +s "$desktopIni"
        attrib +h +s "$icoPath"

        # Mark folder as System so Explorer reads desktop.ini
        attrib +s "$HostFolder"
        Write-Host "Folder icon set." -ForegroundColor Gray
    } catch {
        Write-Host "Could not set folder icon: $_" -ForegroundColor Yellow
    }
}

# ── Parse package list (needed for template creation) ────────────────

$packagesYaml = Join-Path $ScriptDir "..\packages\ubuntu-packages.yaml"
$packageList = ""
if (Test-Path $packagesYaml) {
    $packages = @(
        (Get-Content $packagesYaml) |
            Where-Object { $_ -match '^\s+-\s+\S' } |
            ForEach-Object { ($_ -replace '^\s+-\s+', '' -replace '\s*#.*$', '').Trim() } |
            Where-Object { $_ -ne "" }
    )
    $packageList = ($packages -join "`n") -replace "`r", ""
    Write-Host "  $($packages.Count) packages from ubuntu-packages.yaml"
}

# ── Step 4: Import Ubuntu 24.04 as "Maude" ──                     # REQUIRES ADMIN (wsl --install, --import, --unregister)
# WSL -l -q outputs UTF-16 LE with embedded null bytes; strip them before matching.
# Packages are pre-installed into the template so rebuilds are fast (~30s vs ~5min).

Write-Host "`n[4/7] Checking $DistroName WSL distro..." -ForegroundColor Green
$installedDistros = (wsl -l -q 2>&1) -replace "`0", "" | Where-Object { $_.Trim() -ne "" }
$distroExists = $installedDistros | Where-Object { $_.Trim() -eq $DistroName }

if ($distroExists) {
    Write-Host @"

$DistroName is already installed. To reinstall, run teardown first:

    curl.exe -sLo `$env:TEMP\teardown-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/teardown-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File `$env:TEMP\teardown-wsl-maude.ps1

"@ -ForegroundColor Yellow
    exit 0
} else {
    # Use a persistent template distro named "Ubuntu-24.04-Template" with all
    # packages pre-installed.  Avoids re-downloading from the Microsoft Store
    # and re-installing packages on every rebuild.
    # teardown-wsl-maude.ps1 -IncludeTemplate removes it.
    $templateDistro = "Ubuntu-24.04-Template"
    $templateDir    = "$env:LOCALAPPDATA\Maude-Template"
    $rootfsTar      = "$env:TEMP\ubuntu-2404-rootfs.tar"

    $templateExists = (wsl -l -q 2>&1) -replace "`0", "" |
        Where-Object { $_.Trim() -eq $templateDistro }
    if (-not $templateExists) {
        # Download Ubuntu 24.04 from the Microsoft Store, install all packages,
        # then re-import as our named template and remove the store distro.
        $storeDistro = "Ubuntu-24.04"
        $storeExists = $installedDistros | Where-Object { $_.Trim() -eq $storeDistro }
        if ($storeExists) {
            Write-Host @"

ERROR: '$storeDistro' is already installed as a WSL distro.
Maude needs to install a fresh '$storeDistro' to build its template.

To back up and remove it, run these commands:

    wsl --export $storeDistro `$env:TEMP\ubuntu-2404-backup.tar
    wsl --unregister $storeDistro

Then re-run this setup script. To restore your distro later:

    wsl --import $storeDistro `$env:LOCALAPPDATA\$storeDistro `$env:TEMP\ubuntu-2404-backup.tar

"@ -ForegroundColor Red
            exit 1
        }
        Write-Host "Installing '$storeDistro' from Microsoft Store (first time only)..."
        wsl --install -d $storeDistro --no-launch
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: wsl --install -d $storeDistro failed." -ForegroundColor Red
            exit 1
        }

        # Install packages into the store distro before exporting as template.
        # This bakes packages into the template so every rebuild starts with them.
        Write-Host "Installing packages into template (this takes a few minutes)..."
        if ($packageList) {
            $packageList | wsl -d $storeDistro -u root -- bash -c "
                export DEBIAN_FRONTEND=noninteractive
                printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
                chmod +x /usr/sbin/policy-rc.d
                apt-get update -q
                apt-get install -y -q software-properties-common
                add-apt-repository -y universe
                apt-get update -q
                cat | tr -d '\r' | xargs apt-get install -y -q --no-install-recommends
                rm -f /usr/sbin/policy-rc.d
                apt-get clean
            "
            if ($LASTEXITCODE -ne 0) {
                Write-Host "WARNING: Some packages may have failed to install." -ForegroundColor Yellow
            }
        }

        # Export store distro, import as template, keep tar for Maude import below
        Write-Host "Exporting rootfs from '$storeDistro'..."
        wsl --export $storeDistro $rootfsTar
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: wsl --export failed." -ForegroundColor Red
            exit 1
        }
        Write-Host "Importing as '$templateDistro'..."
        New-Item -ItemType Directory -Force -Path $templateDir | Out-Null
        wsl --import $templateDistro $templateDir $rootfsTar --version 2
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: wsl --import template failed." -ForegroundColor Red
            exit 1
        }
        # Remove the store distro and clean up its WT profile
        wsl --unregister $storeDistro
        $wtSettingsPath = Find-WTSettingsPath
        if ($wtSettingsPath -and (Test-Path $wtSettingsPath)) {
            $wtJson = Get-Content $wtSettingsPath -Raw | ConvertFrom-Json
            $countBefore = $wtJson.profiles.list.Count
            $wtJson.profiles.list = @(
                $wtJson.profiles.list | Where-Object {
                    $nm = if ($_.PSObject.Properties['name']) { $_.name } else { '' }
                    $nm.Trim() -ne $storeDistro
                }
            )
            if ($wtJson.profiles.list.Count -lt $countBefore) {
                $wtJson | ConvertTo-Json -Depth 100 | Set-Content $wtSettingsPath -Encoding UTF8
            }
        }
        Write-Host "'$templateDistro' created with packages." -ForegroundColor Gray
    } else {
        Write-Host "Using existing '$templateDistro' (fast path)." -ForegroundColor Gray
    }

    # Export template and import as Maude
    # (on first run, the rootfs tar already exists from the template creation above)
    if (-not (Test-Path $rootfsTar)) {
        Write-Host "Exporting template rootfs..."
        wsl --export $templateDistro $rootfsTar
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: wsl --export failed." -ForegroundColor Red
            exit 1
        }
    }

    Write-Host "Importing as '$DistroName'..."
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    wsl --import $DistroName $InstallDir $rootfsTar --version 2
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: wsl --import failed." -ForegroundColor Red
        exit 1
    }
    Remove-Item -Path $rootfsTar -ErrorAction SilentlyContinue
    Write-Host "$DistroName imported from '$templateDistro'." -ForegroundColor Gray
}

# ── Step 5: Run root-bootstrap.sh ──                              # does NOT require Windows admin (runs inside WSL as root)
# Copies scripts into /tmp, strips CRLF, then runs root-bootstrap.sh.
# Packages are already in the template — root-bootstrap only does user/config setup.
# Host folder path is written to /tmp/maude-hostfolder for sandbox mount config.

Write-Host "`n[5/7] Running root bootstrap..." -ForegroundColor Green

# Copy bootstrap scripts and maude launcher into the distro's /tmp
# Pipe file content via stdin — the distro may have automount disabled from
# a previous template build, so /mnt/c/ paths are not available.
$filesToCopy = @("root-bootstrap.sh", "maude-bootstrap.sh", "maude")
foreach ($f in $filesToCopy) {
    $src = Join-Path $ScriptDir $f
    if (Test-Path $src) {
        Get-Content $src -Raw | wsl -d $DistroName -u root -- bash -c "cat > /tmp/$f && sed -i 's/\r$//' /tmp/$f && chmod +x /tmp/$f"
    }
}

# Copy maude launcher to /tmp/maude-launcher (used by maude-bootstrap.sh)
$maudeLauncher = Join-Path $ScriptDir "maude"
if (Test-Path $maudeLauncher) {
    Get-Content $maudeLauncher -Raw | wsl -d $DistroName -u root -- bash -c "cat > /tmp/maude-launcher && sed -i 's/\r$//' /tmp/maude-launcher && chmod +x /tmp/maude-launcher"
}

# Write host folder path to /tmp so root-bootstrap.sh can configure fstab
$HostFolder | wsl -d $DistroName -u root -- bash -c "cat > /tmp/maude-hostfolder && sed -i 's/\r$//' /tmp/maude-hostfolder"

# Run root-bootstrap.sh (no package piping — packages are baked into the template)
wsl -d $DistroName -u root -- bash /tmp/root-bootstrap.sh $DefaultUser
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Root bootstrap failed." -ForegroundColor Red
    exit 1
}

# Restart so /etc/wsl.conf takes effect (default user + automount disabled)
wsl --terminate $DistroName

# ── Step 6: Run maude-bootstrap.sh ──                             # does NOT require admin
# /tmp is cleared after wsl --terminate, so re-pipe the script.

Write-Host "`n[6/7] Running user bootstrap..." -ForegroundColor Green

$bootstrapSrc = Join-Path $ScriptDir "maude-bootstrap.sh"
if (Test-Path $bootstrapSrc) {
    Get-Content $bootstrapSrc -Raw | wsl -d $DistroName -u root -- bash -c "cat > /tmp/maude-bootstrap.sh && sed -i 's/\r$//' /tmp/maude-bootstrap.sh && chmod +x /tmp/maude-bootstrap.sh"
}
wsl -d $DistroName -u $DefaultUser -- bash /tmp/maude-bootstrap.sh
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: User bootstrap had errors." -ForegroundColor Yellow
}

# ── Configure Windows Terminal profile (name + icon) ──           # does NOT require admin

$iconSrc = Join-Path $ScriptDir "maude.png"
if (-not (Test-Path $iconSrc)) {
    $iconSrc = Join-Path $ScriptDir "..\maude.png"
}
$iconDst = Join-Path $InstallDir "maude.png"

if (Test-Path $iconSrc) {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -Path $iconSrc -Destination $iconDst -Force
}

$wtSettingsPath = Find-WTSettingsPath
if ($wtSettingsPath -and (Test-Path $wtSettingsPath)) {
    $wtJson    = Get-Content $wtSettingsPath -Raw | ConvertFrom-Json

    # Enable copy-on-select: marking text copies it to clipboard automatically
    $wtJson | Add-Member -NotePropertyName 'copyOnSelect' -NotePropertyValue $true -Force

    # Hide auto-generated WSL profiles for Maude and the template.
    # WT regenerates source=Microsoft.WSL profiles — removing them doesn't stick.
    # If the auto-generated profile doesn't exist yet (WT creates them lazily),
    # we insert a pre-hidden stub so WT won't create a visible duplicate later.
    $hasManualProfile   = $false
    $hasAutoProfile     = $false
    $hasTemplateProfile = $false
    for ($i = 0; $i -lt $wtJson.profiles.list.Count; $i++) {
        $p   = $wtJson.profiles.list[$i]
        $nm  = if ($p.PSObject.Properties['name'])   { $p.name }   else { '' }
        $src = if ($p.PSObject.Properties['source']) { $p.source } else { '' }

        # Hide auto-generated Maude profile (has source)
        if ($nm -eq $DistroName -and $src -ne '') {
            $wtJson.profiles.list[$i] | Add-Member -NotePropertyName 'hidden' -NotePropertyValue $true -Force
            $hasAutoProfile = $true
        }

        # Hide template profiles (never need a WT entry)
        if ($nm -eq 'Ubuntu-24.04-Template') {
            $wtJson.profiles.list[$i] | Add-Member -NotePropertyName 'hidden' -NotePropertyValue $true -Force
            $hasTemplateProfile = $true
        }

        # Track if our manual profile already exists
        if ($nm -eq $DistroName -and $src -eq '') {
            $hasManualProfile = $true
            # Update icon in case it changed
            if (Test-Path $iconDst) {
                $wtJson.profiles.list[$i] | Add-Member -NotePropertyName 'icon' -NotePropertyValue $iconDst -Force
            }
            $wtJson.profiles.list[$i] | Add-Member -NotePropertyName 'hidden' -NotePropertyValue $false -Force
        }
    }

    # Insert pre-hidden stubs for auto-generated profiles so WT doesn't
    # create visible ones later when it discovers the new WSL distros.
    if (-not $hasAutoProfile) {
        $autoStub = [PSCustomObject]@{
            guid   = "{$([guid]::NewGuid().ToString())}"
            name   = $DistroName
            source = "Windows.Terminal.Wsl"
            hidden = $true
        }
        $wtJson.profiles.list += $autoStub
    }
    if (-not $hasTemplateProfile) {
        $templateStub = [PSCustomObject]@{
            guid   = "{$([guid]::NewGuid().ToString())}"
            name   = "Ubuntu-24.04-Template"
            source = "Windows.Terminal.Wsl"
            hidden = $true
        }
        $wtJson.profiles.list += $templateStub
    }

    # Create our manual profile if it doesn't exist yet
    if (-not $hasManualProfile) {
        $newProfile = [PSCustomObject]@{
            guid        = "{$([guid]::NewGuid().ToString())}"
            name        = $DistroName
            commandline = "wsl.exe -d $DistroName"
            hidden      = $false
        }
        if (Test-Path $iconDst) {
            $newProfile | Add-Member -NotePropertyName 'icon' -NotePropertyValue $iconDst -Force
        }
        $wtJson.profiles.list += $newProfile
    }

    $wtJson | ConvertTo-Json -Depth 100 | Set-Content $wtSettingsPath -Encoding UTF8
    Write-Host "Windows Terminal profile created for $DistroName." -ForegroundColor Gray
} else {
    Write-Host "Windows Terminal settings not found, skipping profile config." -ForegroundColor Gray
}

# ── Create desktop shortcut (Maude icon → Windows Terminal) ──    # does NOT require admin

$desktopPath = [Environment]::GetFolderPath('Desktop')
$shortcutFile = Join-Path $desktopPath "$DistroName.lnk"
$icoFile = Join-Path $InstallDir "maude.ico"

# Convert maude.png → maude.ico for the shortcut (lnk files require ico)
$iconSrc = Join-Path $ScriptDir "maude.png"
if (-not (Test-Path $iconSrc)) { $iconSrc = Join-Path $ScriptDir "..\maude.png" }

if (Test-Path $iconSrc) {
    try {
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        Convert-PngToIco $iconSrc $icoFile
    } catch {
        Write-Host "Could not convert icon: $_" -ForegroundColor Yellow
    }
}

# Find wt.exe path for the shortcut target
$wtExe = (Get-Command wt.exe -ErrorAction SilentlyContinue).Source
if ($wtExe) {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($shortcutFile)
    $sc.TargetPath = $wtExe
    $sc.Arguments = "-p `"$DistroName`""
    $sc.Description = "Open $DistroName in Windows Terminal"
    if (Test-Path $icoFile) { $sc.IconLocation = "$icoFile,0" }
    $sc.Save()
    Write-Host "Desktop shortcut created: $shortcutFile" -ForegroundColor Gray
} else {
    Write-Host "wt.exe not found, skipping desktop shortcut." -ForegroundColor Yellow
}

# ── Step 7: Open Maude in Windows Terminal ──                     # does NOT require admin

Write-Host "`n[7/7] Opening $DistroName..." -ForegroundColor Green
if (Get-Command wt.exe -ErrorAction SilentlyContinue) {
    wt new-tab -- wsl -d $DistroName
} else {
    wsl -d $DistroName
}

Write-Host "`nMaude setup complete!" -ForegroundColor Cyan
