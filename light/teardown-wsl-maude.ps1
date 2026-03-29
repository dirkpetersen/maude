#Requires -Version 5.1
<#
.SYNOPSIS
    Completely removes the Maude WSL distro and all associated files.

.DESCRIPTION
    Idempotent teardown that:
    1. Unregisters the Maude WSL distro (deletes the virtual disk)
    2. Removes the install directory (%LOCALAPPDATA%\Maude)

    By default the Ubuntu-24.04 template distro is kept so the next
    setup-wsl-maude.ps1 run is fast (no Microsoft Store download).
    Pass -IncludeTemplate to remove it too.

.NOTES
    Run from an elevated PowerShell prompt:
        Set-ExecutionPolicy Bypass -Scope Process -Force
        .\teardown-wsl-maude.ps1                  # keep template
        .\teardown-wsl-maude.ps1 -IncludeTemplate # remove everything
#>

param(
    [string]$DistroName      = "Maude",
    [string]$InstallDir      = "$env:LOCALAPPDATA\Maude",
    [switch]$IncludeTemplate
)

# ── Self-elevate to Administrator if needed ──

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Not running as Administrator. Attempting to elevate..." -ForegroundColor Yellow
    try {
        $args = "-ExecutionPolicy Bypass -File `"$PSCommandPath`""
        if ($IncludeTemplate) { $args += " -IncludeTemplate" }
        Start-Process powershell.exe -Verb RunAs -ArgumentList $args
    } catch {
        Write-Host "ERROR: This script requires Administrator privileges." -ForegroundColor Red
    }
    exit
}

Write-Host "=== Maude WSL Teardown ===" -ForegroundColor Cyan

# ── Step 1: Unregister the Maude WSL distro ──                    # REQUIRES ADMIN

Write-Host "`n[1/3] Checking for $DistroName WSL distro..." -ForegroundColor Green
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

# ── Step 2: Remove the install directory ──                        # does NOT require admin

Write-Host "`n[2/3] Removing install directory..." -ForegroundColor Green
if (Test-Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force
    Write-Host "Removed $InstallDir" -ForegroundColor Gray
} else {
    Write-Host "$InstallDir does not exist. Nothing to remove." -ForegroundColor Gray
}

# ── Step 3: Optionally remove the Ubuntu-24.04 template ──        # REQUIRES ADMIN

$templateDistro = "Ubuntu-24.04"
$templateExists = $installedDistros | Where-Object { $_.Trim() -eq $templateDistro }

if ($IncludeTemplate) {
    Write-Host "`n[3/3] Removing '$templateDistro' template..." -ForegroundColor Green
    if ($templateExists) {
        wsl --unregister $templateDistro
        Write-Host "'$templateDistro' template removed." -ForegroundColor Gray
    } else {
        Write-Host "'$templateDistro' template not found." -ForegroundColor Gray
    }
} else {
    Write-Host "`n[3/3] Keeping '$templateDistro' template for fast rebuilds." -ForegroundColor Green
    if ($templateExists) {
        Write-Host "  (pass -IncludeTemplate to remove it too)" -ForegroundColor Gray
    }
}

Write-Host "`nMaude teardown complete." -ForegroundColor Cyan
