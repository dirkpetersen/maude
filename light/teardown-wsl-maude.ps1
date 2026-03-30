#Requires -Version 5.1
<#
.SYNOPSIS
    Completely removes the Maude WSL distro and all associated files.

.DESCRIPTION
    Idempotent teardown that:
    1. Removes the Windows Terminal profile and desktop shortcut (no admin needed)
    2. Unregisters the Maude WSL distro (requires admin — self-elevates)
    3. Removes the install directory
    4. Optionally removes the Ubuntu-24.04-Template distro

    By default the Ubuntu-24.04 template distro is kept so the next
    setup-wsl-maude.ps1 run is fast (no Microsoft Store download).
    Pass -IncludeTemplate to remove it too.

.NOTES
    Run from a PowerShell prompt (admin not required — script self-elevates):
        Set-ExecutionPolicy Bypass -Scope Process -Force
        .\teardown-wsl-maude.ps1                  # keep template
        .\teardown-wsl-maude.ps1 -IncludeTemplate # remove everything
#>

param(
    [string]$DistroName      = "Maude",
    [string]$InstallDir      = "$env:LOCALAPPDATA\Maude",
    [switch]$IncludeTemplate
)

Write-Host "=== Maude WSL Teardown ===" -ForegroundColor Cyan

# ── Step 1: Remove Windows Terminal profile + desktop shortcut ───  # runs as current user

Write-Host "`n[1/4] Cleaning up Windows Terminal profile..." -ForegroundColor Green
$wtSettingsPath = Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
if (Test-Path $wtSettingsPath) {
    $wtJson   = Get-Content $wtSettingsPath -Raw | ConvertFrom-Json
    $before   = $wtJson.profiles.list.Count
    $wtJson.profiles.list = @(
        $wtJson.profiles.list | Where-Object {
            $nm = if ($_.PSObject.Properties['name']) { $_.name } else { '' }
            $nm -ne $DistroName
        }
    )
    if ($wtJson.profiles.list.Count -lt $before) {
        $wtJson | ConvertTo-Json -Depth 100 | Set-Content $wtSettingsPath -Encoding UTF8
        Write-Host "$DistroName profile removed from Windows Terminal." -ForegroundColor Gray
    } else {
        Write-Host "No $DistroName profile found in Windows Terminal." -ForegroundColor Gray
    }
} else {
    Write-Host "Windows Terminal settings not found, skipping." -ForegroundColor Gray
}

# Check both local and OneDrive desktops for shortcut
$desktopPaths = @([Environment]::GetFolderPath('Desktop'))
$userDesktop = Join-Path $env:USERPROFILE "Desktop"
if ($userDesktop -ne $desktopPaths[0]) { $desktopPaths += $userDesktop }
# Also check OneDrive desktops
foreach ($od in @($env:OneDriveCommercial, $env:OneDriveConsumer, $env:OneDrive)) {
    if ($od) {
        $odDesktop = Join-Path $od "Desktop"
        if ((Test-Path $odDesktop) -and ($desktopPaths -notcontains $odDesktop)) {
            $desktopPaths += $odDesktop
        }
    }
}
foreach ($dp in $desktopPaths) {
    $shortcutFile = Join-Path $dp "$DistroName.lnk"
    if (Test-Path $shortcutFile) {
        Remove-Item -Path $shortcutFile -Force
        Write-Host "$DistroName desktop shortcut removed from $dp." -ForegroundColor Gray
    }
}

# ── Self-elevate to Administrator for WSL operations ──

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "`nElevating to Administrator for WSL operations..." -ForegroundColor Yellow
    try {
        $elevateArgs = "-ExecutionPolicy Bypass -File `"$PSCommandPath`" -DistroName `"$DistroName`" -InstallDir `"$InstallDir`""
        if ($IncludeTemplate) { $elevateArgs += " -IncludeTemplate" }
        Start-Process powershell.exe -Verb RunAs -ArgumentList $elevateArgs -Wait
    } catch {
        Write-Host "ERROR: Administrator privileges required for WSL operations." -ForegroundColor Red
    }
    exit
}

# ── Below here runs elevated ──

# ── Step 2: Unregister the Maude WSL distro ──                    # REQUIRES ADMIN

Write-Host "`n[2/4] Checking for $DistroName WSL distro..." -ForegroundColor Green
$installedDistros = (wsl -l -q 2>&1) -replace "`0", "" | Where-Object { $_.Trim() -ne "" }
$distroExists = $installedDistros | Where-Object { $_.Trim() -eq $DistroName }

if ($distroExists) {
    Write-Host "Unregistering $DistroName..."
    wsl --unregister $DistroName
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: wsl --unregister failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "$DistroName unregistered." -ForegroundColor Gray
} else {
    Write-Host "$DistroName is not installed. Nothing to unregister." -ForegroundColor Gray
}

# ── Step 3: Remove the install directory ──

Write-Host "`n[3/4] Removing install directory..." -ForegroundColor Green
if (Test-Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force
    Write-Host "Removed $InstallDir" -ForegroundColor Gray
} else {
    Write-Host "$InstallDir does not exist. Nothing to remove." -ForegroundColor Gray
}

# ── Step 4: Optionally remove the Ubuntu-24.04-Template ──        # REQUIRES ADMIN

$templateDistro = "Ubuntu-24.04-Template"
$templateDir    = "$env:LOCALAPPDATA\Maude-Template"
$templateExists = $installedDistros | Where-Object { $_.Trim() -eq $templateDistro }

if ($IncludeTemplate) {
    Write-Host "`n[4/4] Removing '$templateDistro'..." -ForegroundColor Green
    if ($templateExists) {
        wsl --unregister $templateDistro
        Write-Host "'$templateDistro' unregistered." -ForegroundColor Gray
    } else {
        Write-Host "'$templateDistro' not found." -ForegroundColor Gray
    }
    if (Test-Path $templateDir) {
        Remove-Item -Path $templateDir -Recurse -Force
        Write-Host "Removed $templateDir" -ForegroundColor Gray
    }
} else {
    Write-Host "`n[4/4] Keeping '$templateDistro' for fast rebuilds." -ForegroundColor Green
    if ($templateExists) {
        Write-Host "  (pass -IncludeTemplate to remove it too)" -ForegroundColor Gray
    }
}

Write-Host "`nMaude teardown complete." -ForegroundColor Cyan
