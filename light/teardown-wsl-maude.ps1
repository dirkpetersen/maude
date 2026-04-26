#Requires -Version 5.1
<#
.SYNOPSIS
    Completely removes the Maude WSL distro and all associated files.

.DESCRIPTION
    Idempotent teardown that:
    1. Removes the Windows Terminal profile and desktop shortcut (no admin needed)
    2. Unregisters the Maude WSL distro (requires admin — self-elevates)
    3. Removes the install directory
    4. Optionally removes the Ubuntu template distro

    By default the Ubuntu template distro is kept so the next
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

# ── Helper: reliably test if a WSL distro is registered ───────────────
function Test-WslDistro([string]$name) {
    $lines = (wsl --list --verbose 2>&1) -replace "`0", ""
    foreach ($line in $lines) {
        $fields = ($line -replace '^\*?\s+', '').Trim() -split '\s+'
        if ($fields[0] -ieq $name) { return $true }
    }
    return $false
}

# ── Step 1: Remove Windows Terminal profile + desktop shortcut ───  # runs as current user

Write-Host "`n[1/4] Cleaning up Windows Terminal profile..." -ForegroundColor Green

# WT cleanup helper — removes Maude + template stubs from settings.json.
# Called both before and after elevation (elevated process may have a
# different $env:LOCALAPPDATA, so we need both passes).
function Remove-WTMaudeProfiles {
    $wtCandidates = @(
        Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
        Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json"
        Join-Path $env:LOCALAPPDATA "Microsoft\Windows Terminal\settings.json"
    )
    $wtSettingsPath = $wtCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $wtSettingsPath) {
        Write-Host "Windows Terminal settings not found, skipping." -ForegroundColor Gray
        return
    }
    $wtJson  = Get-Content $wtSettingsPath -Raw | ConvertFrom-Json
    $before  = $wtJson.profiles.list.Count
    $removeNames = @($DistroName)
    if ($IncludeTemplate) { $removeNames += @("Ubuntu-24.04-Template", "Ubuntu-26.04-Template") }
    $wtJson.profiles.list = @(
        $wtJson.profiles.list | Where-Object {
            $nm = if ($_.PSObject.Properties['name']) { $_.name } else { '' }
            $nm -notin $removeNames
        }
    )
    if ($wtJson.profiles.list.Count -lt $before) {
        $wtJson | ConvertTo-Json -Depth 100 | Set-Content $wtSettingsPath -Encoding UTF8
        $removed = $before - $wtJson.profiles.list.Count
        Write-Host "Removed $removed profile(s) from Windows Terminal." -ForegroundColor Gray
    }
}

Remove-WTMaudeProfiles

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

# Unpin Maude folder from Quick Access if pinned
try {
    $Shell = New-Object -ComObject Shell.Application
    $QuickAccess = $Shell.Namespace("shell:::{679f85cb-0220-4080-b29b-5540cc05aab6}")
    foreach ($item in $QuickAccess.Items()) {
        if ($item.Name -eq 'Maude') {
            $item.InvokeVerb("unpinfromhome")
            Write-Host "Unpinned Maude from Quick Access." -ForegroundColor Gray
            break
        }
    }
} catch {
    # Not critical — ignore
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
# Re-run WT cleanup in the elevated context (catches leftover profiles
# if a previous teardown was interrupted after elevation).
Remove-WTMaudeProfiles

# ── Step 2: Unregister the Maude WSL distro ──                    # REQUIRES ADMIN

Write-Host "`n[2/4] Checking for $DistroName WSL distro..." -ForegroundColor Green
Write-Host "Unregistering $DistroName..."
wsl --unregister $DistroName 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "$DistroName unregistered." -ForegroundColor Gray
} else {
    Write-Host "$DistroName is not installed. Nothing to unregister." -ForegroundColor Gray
}

# Terminate only the Maude distro to release its file locks (ext4.vhdx).
# Avoids wsl --shutdown which would kill all running WSL distros.
wsl --terminate $DistroName 2>$null

# ── Step 3: Remove the install directory ──

Write-Host "`n[3/4] Removing install directory..." -ForegroundColor Green
if (Test-Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) {
        # Retry after a brief pause (WSL may need a moment to release locks)
        Start-Sleep -Seconds 2
        Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $InstallDir) {
        # Last resort: shut down all of WSL to release stubborn locks
        Write-Host "Files locked. Stopping WSL to release locks..." -ForegroundColor Yellow
        wsl --shutdown 2>$null
        Start-Sleep -Seconds 2
        Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $InstallDir) {
        Write-Host "WARNING: Could not fully remove $InstallDir (files may be locked)." -ForegroundColor Yellow
    } else {
        Write-Host "Removed $InstallDir" -ForegroundColor Gray
    }
} else {
    Write-Host "$InstallDir does not exist. Nothing to remove." -ForegroundColor Gray
}

# ── Step 4: Optionally remove Ubuntu template distros ──          # REQUIRES ADMIN

$templateDistros = @("Ubuntu-24.04-Template", "Ubuntu-26.04-Template")

if ($IncludeTemplate) {
    Write-Host "`n[4/4] Removing Ubuntu templates..." -ForegroundColor Green
    foreach ($tpl in $templateDistros) {
        wsl --unregister $tpl 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "'$tpl' unregistered." -ForegroundColor Gray
        }
    }
} else {
    $kept = $templateDistros | Where-Object { Test-WslDistro $_ }
    if ($kept) {
        Write-Host "`n[4/4] Keeping template(s) for fast rebuilds: $($kept -join ', ')" -ForegroundColor Green
        Write-Host "  (pass -IncludeTemplate to remove them too)" -ForegroundColor Gray
    } else {
        Write-Host "`n[4/4] No templates found." -ForegroundColor Green
    }
}

Write-Host "`nMaude teardown complete." -ForegroundColor Cyan
