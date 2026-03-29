#Requires -Version 5.1
<#
.SYNOPSIS
    Sets up a WSL2 Ubuntu 24.04 dev environment named "Maude".

.DESCRIPTION
    Idempotent script that:
    1. Installs WSL2 (if not present)
    2. Installs Windows Terminal (if not present)
    3. Imports Ubuntu 24.04 as a WSL distro named "Maude"
    4. Runs root-bootstrap.sh  (user, mom, PATH, packages, welcome screen)
    5. Runs maude-bootstrap.sh (dev-station, maude launcher, PS1)
    6. Opens Maude in Windows Terminal

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

# ── Self-elevate to Administrator if needed ──

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Not running as Administrator. Attempting to elevate..." -ForegroundColor Yellow
    try {
        Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`""
    } catch {
        Write-Host @"

ERROR: This script requires Administrator privileges.
Please run PowerShell as Administrator and execute:

    Set-ExecutionPolicy Bypass -Scope Process -Force
    .\setup-wsl-maude.ps1

"@ -ForegroundColor Red
    }
    exit
}

# ── Helper: convert a Windows path to a WSL path ──

function Get-WslPath($winPath) {
    wsl -d $DistroName -u root -- wslpath -u ($winPath -replace '\\', '/')
}

Write-Host "=== Maude WSL Setup ===" -ForegroundColor Cyan

# ── Step 1: Install WSL2 ──                                       # REQUIRES ADMIN

Write-Host "`n[1/6] Checking WSL..." -ForegroundColor Green
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

Write-Host "`n[2/6] Checking Windows Terminal..." -ForegroundColor Green
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

# ── Step 3: Import Ubuntu 24.04 as "Maude" ──                     # REQUIRES ADMIN (wsl --install, --import, --unregister)
# WSL -l -q outputs UTF-16 LE with embedded null bytes; strip them before matching.

Write-Host "`n[3/6] Checking $DistroName WSL distro..." -ForegroundColor Green
$installedDistros = (wsl -l -q 2>&1) -replace "`0", "" | Where-Object { $_.Trim() -ne "" }
$distroExists = $installedDistros | Where-Object { $_.Trim() -eq $DistroName }

if ($distroExists) {
    Write-Host "$DistroName is already installed. Skipping import." -ForegroundColor Gray
} else {
    # Use a persistent Ubuntu-24.04 template distro to avoid re-downloading
    # from the Microsoft Store on every rebuild.  The template is kept around
    # after the first run; teardown-wsl-maude.ps1 -IncludeTemplate removes it.
    $templateDistro = "Ubuntu-24.04"
    $rootfsTar      = "$env:TEMP\ubuntu-2404-rootfs.tar"

    $templateExists = (wsl -l -q 2>&1) -replace "`0", "" |
        Where-Object { $_.Trim() -eq $templateDistro }
    if ($templateExists) {
        Write-Host "Using existing '$templateDistro' template (fast path)." -ForegroundColor Gray
    } else {
        Write-Host "Installing '$templateDistro' template from Microsoft Store (first time only)..."
        wsl --install -d $templateDistro --no-launch
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: wsl --install -d $templateDistro failed." -ForegroundColor Red
            exit 1
        }
    }

    Write-Host "Exporting template rootfs..."
    wsl --export $templateDistro $rootfsTar
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: wsl --export failed." -ForegroundColor Red
        exit 1
    }

    Write-Host "Importing as '$DistroName'..."
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    wsl --import $DistroName $InstallDir $rootfsTar --version 2
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: wsl --import failed." -ForegroundColor Red
        exit 1
    }

    # Keep the template — only remove the temporary tarball
    Remove-Item -Path $rootfsTar -ErrorAction SilentlyContinue
    Write-Host "$DistroName imported from '$templateDistro' template." -ForegroundColor Gray
}

# ── Step 4: Run root-bootstrap.sh ──                              # does NOT require Windows admin (runs inside WSL as root)
# Copies the script into /tmp, strips CRLF, then runs it.
# Package names from ubuntu-packages.yaml are piped via stdin.

Write-Host "`n[4/6] Running root bootstrap..." -ForegroundColor Green

# Copy bootstrap scripts and maude launcher into the distro's /tmp
$filesToCopy = @("root-bootstrap.sh", "maude-bootstrap.sh", "maude")
foreach ($f in $filesToCopy) {
    $src = Join-Path $PSScriptRoot $f
    if (Test-Path $src) {
        $wslSrc = Get-WslPath $src
        wsl -d $DistroName -u root -- bash -c "cp '$wslSrc' /tmp/$f && sed -i 's/\r$//' /tmp/$f && chmod +x /tmp/$f"
    }
}

# Parse packages from YAML (PowerShell side — no python3-yaml needed in the rootfs)
$packagesYaml = Join-Path $PSScriptRoot "..\packages\ubuntu-packages.yaml"
$packageList = ""
if (Test-Path $packagesYaml) {
    $packages = @(
        (Get-Content $packagesYaml) |
            Where-Object { $_ -match '^\s+-\s+\S' } |
            ForEach-Object { ($_ -replace '^\s+-\s+', '' -replace '\s*#.*$', '').Trim() } |
            Where-Object { $_ -ne "" }
    )
    $packageList = ($packages -join "`n") -replace "`r", ""
    Write-Host "  $($packages.Count) packages queued from ubuntu-packages.yaml"
}

# Run root-bootstrap.sh with package list piped via stdin
if ($packageList) {
    $packageList | wsl -d $DistroName -u root -- bash /tmp/root-bootstrap.sh $DefaultUser
} else {
    wsl -d $DistroName -u root -- bash /tmp/root-bootstrap.sh $DefaultUser
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Root bootstrap failed." -ForegroundColor Red
    exit 1
}

# Restart so /etc/wsl.conf default user takes effect
wsl --terminate $DistroName

# ── Step 5: Run maude-bootstrap.sh ──                             # does NOT require admin

Write-Host "`n[5/6] Running user bootstrap..." -ForegroundColor Green

# Copy the maude launcher to /tmp for the user script to pick up
$maudeLauncher = Join-Path $PSScriptRoot "maude"
if (Test-Path $maudeLauncher) {
    $wslLauncher = Get-WslPath $maudeLauncher
    wsl -d $DistroName -u root -- bash -c "cp '$wslLauncher' /tmp/maude-launcher && sed -i 's/\r$//' /tmp/maude-launcher && chmod +x /tmp/maude-launcher"
}

wsl -d $DistroName -u $DefaultUser -- bash /tmp/maude-bootstrap.sh
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: User bootstrap had errors." -ForegroundColor Yellow
}

# ── Configure Windows Terminal profile (name + icon) ──           # does NOT require admin

$iconSrc = Join-Path $PSScriptRoot "maude.png"
if (-not (Test-Path $iconSrc)) {
    $iconSrc = Join-Path $PSScriptRoot "..\maude.png"
}
$iconDst = Join-Path $InstallDir "maude.png"

if (Test-Path $iconSrc) {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -Path $iconSrc -Destination $iconDst -Force
}

$wtSettingsPath = Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
if (Test-Path $wtSettingsPath) {
    $wtJson    = Get-Content $wtSettingsPath -Raw | ConvertFrom-Json
    $profiles  = $wtJson.profiles.list
    $modified  = $false

    for ($i = 0; $i -lt $profiles.Count; $i++) {
        $p = $profiles[$i]
        $src = if ($p.PSObject.Properties['source']) { $p.source } else { '' }
        $nm  = if ($p.PSObject.Properties['name'])   { $p.name   } else { '' }
        if (($src -match 'WSL|Wsl') -and ($nm -match "(?i)^$DistroName$")) {
            $profiles[$i].name = $DistroName
            if (Test-Path $iconDst) {
                $profiles[$i] | Add-Member -NotePropertyName 'icon' -NotePropertyValue $iconDst -Force
            }
            $modified = $true
        }
    }

    if ($modified) {
        $wtJson | ConvertTo-Json -Depth 100 | Set-Content $wtSettingsPath -Encoding UTF8
        Write-Host "Windows Terminal profile updated (name + icon)." -ForegroundColor Gray
    }
} else {
    Write-Host "Windows Terminal settings not found, skipping profile config." -ForegroundColor Gray
}

# ── Step 6: Open Maude in Windows Terminal ──                     # does NOT require admin

Write-Host "`n[6/6] Opening $DistroName..." -ForegroundColor Green
if (Get-Command wt.exe -ErrorAction SilentlyContinue) {
    wt new-tab -- wsl -d $DistroName
} else {
    wsl -d $DistroName
}

Write-Host "`nMaude setup complete!" -ForegroundColor Cyan
