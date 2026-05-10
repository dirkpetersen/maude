#Requires -Version 5.1
<#
.SYNOPSIS
    Removes the Maude WSL distro and all associated files.

.DESCRIPTION
    Idempotent teardown. Mirrors the setup script's two-phase model:

      User mode (default, no admin required):
        - Remove the Windows Terminal profile and desktop shortcut
        - Unpin the Maude folder from Quick Access
        - wsl --unregister the Maude distro (per-user HKCU operation)
        - Remove the install directory
        - Optionally wsl --unregister the Ubuntu template(s) too

      Admin mode (-Admin, only useful with -IncludeTemplate):
        - All of the above, PLUS
        - Remove the Windows Defender exclusion that the setup admin
          phase added for $env:LOCALAPPDATA\Maude*

    Most uninstalls are user-mode — the WSL distro registry, install
    directory, Windows Terminal profile, and desktop shortcut are all
    per-user state. The only genuinely admin-only step is removing
    the Defender exclusion. If you don't pass -IncludeTemplate (or you
    don't care about lingering Defender exclusion entries), no admin
    is needed.

.NOTES
    Typical uninstall (user-mode, no admin needed):
        Set-ExecutionPolicy Bypass -Scope Process -Force
        .\teardown-wsl-maude.ps1                     # keep template
        .\teardown-wsl-maude.ps1 -IncludeTemplate    # remove template too

    Full cleanup including Defender exclusion (one-time, on machine
    decommission). Admin is needed because Defender preferences are
    machine-wide:
        .\teardown-wsl-maude.ps1 -Admin -IncludeTemplate
#>

param(
    [string]$DistroName      = "Maude",
    [string]$InstallDir      = "$env:LOCALAPPDATA\Maude",
    [switch]$IncludeTemplate,
    [switch]$Admin
)

Write-Host "=== Maude WSL Teardown ===" -ForegroundColor Cyan

# ── Helper: reliably test if a WSL distro is registered ──
function Test-WslDistro([string]$name) {
    $lines = (wsl --list --verbose 2>&1) -replace "`0", ""
    foreach ($line in $lines) {
        $fields = ($line -replace '^\*?\s+', '').Trim() -split '\s+'
        if ($fields[0] -ieq $name) { return $true }
    }
    return $false
}

# ── Elevation gating (mirrors setup-wsl-maude.ps1) ──
# -Admin runs the Defender-exclusion cleanup; everything else runs as user.
# Default flow (no -Admin) MUST be unelevated so WT/shortcut/distro state
# lands in the actual end user's profile.
$isElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if ($Admin -and -not $isElevated) {
    Write-Host "-Admin specified but PowerShell is not elevated. Self-elevating..." -ForegroundColor Yellow
    try {
        $extraArgs = " -Admin"
        if ($IncludeTemplate) { $extraArgs += " -IncludeTemplate" }
        if ($PSCommandPath) {
            Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`"$extraArgs" -Wait
        } else {
            $cmd = "Set-ExecutionPolicy Bypass -Scope Process -Force; & { `$f = `$env:TEMP + '\teardown-wsl-maude.ps1'; (New-Object Net.WebClient).DownloadFile('https://raw.githubusercontent.com/dirkpetersen/maude/main/light/teardown-wsl-maude.ps1', `$f); & `$f$extraArgs }"
            Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -Command `"$cmd`"" -Wait
        }
    } catch {
        Write-Host "ERROR: Self-elevation failed." -ForegroundColor Red
    }
    exit
}

if (-not $Admin -and $isElevated) {
    Write-Host @"

ERROR: PowerShell is running as Administrator but -Admin was NOT supplied.

The default teardown removes per-user state (Windows Terminal profile,
desktop shortcut, WSL distro registration). Running it elevated would
operate on the admin's profile, not yours.

Do one of:
  * Run from a non-elevated PowerShell for the default teardown, or
  * Re-run with -Admin (only needed if you also want to remove the
    Windows Defender exclusion, which is machine-wide).

"@ -ForegroundColor Red
    exit 1
}

# ── Step 1 (user): Remove Windows Terminal profile + desktop shortcut ──
Write-Host "`n[1/4] Cleaning up Windows Terminal profile..." -ForegroundColor Green

function Remove-WTMaudeProfiles {
    $wtCandidates = @(
        Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
        Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json"
        Join-Path $env:LOCALAPPDATA "Microsoft\Windows Terminal\settings.json"
    )
    $wtSettingsPath = $wtCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $wtSettingsPath) {
        Write-Host "Windows Terminal settings not found, skipping." -ForegroundColor Gray
        return
    }
    $wtJson = Get-Content -LiteralPath $wtSettingsPath -Raw | ConvertFrom-Json
    $before = $wtJson.profiles.list.Count
    $removeNames = @($DistroName)
    if ($IncludeTemplate) { $removeNames += @("Ubuntu-24.04-Template", "Ubuntu-26.04-Template") }
    $wtJson.profiles.list = @(
        $wtJson.profiles.list | Where-Object {
            $nm = if ($_.PSObject.Properties['name']) { $_.name } else { '' }
            $nm -notin $removeNames
        }
    )
    if ($wtJson.profiles.list.Count -lt $before) {
        $wtJson | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $wtSettingsPath -Encoding UTF8
        $removed = $before - $wtJson.profiles.list.Count
        Write-Host "Removed $removed profile(s) from Windows Terminal." -ForegroundColor Gray
    } else {
        Write-Host "No matching Windows Terminal profiles to remove." -ForegroundColor Gray
    }
}

Remove-WTMaudeProfiles

# Check both local and OneDrive desktops for shortcut
$desktopPaths = @([Environment]::GetFolderPath('Desktop'))
$userDesktop = Join-Path $env:USERPROFILE "Desktop"
if ($userDesktop -ne $desktopPaths[0]) { $desktopPaths += $userDesktop }
foreach ($od in @($env:OneDriveCommercial, $env:OneDriveConsumer, $env:OneDrive)) {
    if ($od) {
        $odDesktop = Join-Path $od "Desktop"
        if ((Test-Path -LiteralPath $odDesktop) -and ($desktopPaths -notcontains $odDesktop)) {
            $desktopPaths += $odDesktop
        }
    }
}
foreach ($dp in $desktopPaths) {
    $shortcutFile = Join-Path $dp "$DistroName.lnk"
    if (Test-Path -LiteralPath $shortcutFile) {
        Remove-Item -LiteralPath $shortcutFile -Force
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

# ── Step 2 (user): Unregister the Maude WSL distro ──
# wsl --unregister writes to HKCU\...\Lxss — per-user, no admin needed.
Write-Host "`n[2/4] Checking for $DistroName WSL distro..." -ForegroundColor Green
if (Test-WslDistro $DistroName) {
    Write-Host "Unregistering $DistroName..."
    wsl --terminate $DistroName 2>$null
    wsl --unregister $DistroName 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "$DistroName unregistered." -ForegroundColor Gray
    } else {
        Write-Host "WARNING: wsl --unregister returned non-zero." -ForegroundColor Yellow
    }
} else {
    Write-Host "$DistroName is not installed. Nothing to unregister." -ForegroundColor Gray
}

# ── Step 3 (user): Remove the install directory ──
Write-Host "`n[3/4] Removing install directory..." -ForegroundColor Green
if (Test-Path -LiteralPath $InstallDir) {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $InstallDir) {
        # Retry after a brief pause (WSL may need a moment to release locks)
        Start-Sleep -Seconds 2
        Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $InstallDir) {
        # Last resort: shut down all of WSL to release stubborn locks
        Write-Host "Files locked. Stopping WSL to release locks..." -ForegroundColor Yellow
        wsl --shutdown 2>$null
        Start-Sleep -Seconds 2
        Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $InstallDir) {
        Write-Host "WARNING: Could not fully remove $InstallDir (files may be locked)." -ForegroundColor Yellow
    } else {
        Write-Host "Removed $InstallDir" -ForegroundColor Gray
    }
} else {
    Write-Host "$InstallDir does not exist. Nothing to remove." -ForegroundColor Gray
}

# ── Step 4 (user): Optionally remove Ubuntu template distros ──
$templateDistros = @("Ubuntu-24.04-Template", "Ubuntu-26.04-Template")

if ($IncludeTemplate) {
    Write-Host "`n[4/4] Removing Ubuntu templates..." -ForegroundColor Green
    foreach ($tpl in $templateDistros) {
        if (Test-WslDistro $tpl) {
            wsl --unregister $tpl 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "'$tpl' unregistered." -ForegroundColor Gray
            }
        }
    }
    # Also remove the template install directory
    $tplDir = Join-Path $env:LOCALAPPDATA "Maude-Template"
    if (Test-Path -LiteralPath $tplDir) {
        Remove-Item -LiteralPath $tplDir -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $tplDir) {
            Start-Sleep -Seconds 2
            Remove-Item -LiteralPath $tplDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (-not (Test-Path -LiteralPath $tplDir)) {
            Write-Host "Removed $tplDir" -ForegroundColor Gray
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

# ── Admin-only: remove Defender exclusion ──
# Only relevant when -Admin is supplied. Setup added these in the admin
# phase; remove them on full uninstall.
if ($Admin) {
    Write-Host "`n[admin] Removing Defender exclusion..." -ForegroundColor Green
    $exclusionPaths = @(
        (Join-Path $env:LOCALAPPDATA "Maude")
        (Join-Path $env:LOCALAPPDATA "Maude-Template")
    )
    foreach ($excl in $exclusionPaths) {
        try {
            Remove-MpPreference -ExclusionPath $excl -ErrorAction Stop
            Write-Host "  Removed exclusion: $excl" -ForegroundColor Gray
        } catch {
            Write-Host "  Could not remove $($excl): $($_.Exception.Message)" -ForegroundColor Gray
        }
    }
} else {
    # Inform the user that the Defender exclusion is still present (it's
    # harmless on its own, but full cleanup needs -Admin).
    $hasMaudeExclusion = $false
    try {
        $prefs = Get-MpPreference -ErrorAction Stop
        $maudePath = Join-Path $env:LOCALAPPDATA "Maude"
        if ($prefs.ExclusionPath -and ($prefs.ExclusionPath -contains $maudePath)) {
            $hasMaudeExclusion = $true
        }
    } catch {
        # Defender not reachable (third-party AV, server SKU, etc.)
    }
    if ($hasMaudeExclusion) {
        Write-Host "`nNote: a Windows Defender exclusion for the Maude install path is still in place." -ForegroundColor Gray
        Write-Host "      Re-run with -Admin -IncludeTemplate to remove it as part of full cleanup." -ForegroundColor Gray
    }
}

Write-Host "`nMaude teardown complete." -ForegroundColor Cyan
