#Requires -Version 5.1
<#
.SYNOPSIS
    Sets up a WSL2 Ubuntu dev environment named "Maude".

.DESCRIPTION
    Two-phase installer where the admin phase is only needed when WSL
    itself (a Windows feature) needs installing or upgrading. On any
    machine where WSL is already present, the user phase can do the
    entire install — including building the Ubuntu template — without
    elevation.

      Admin phase (-Admin):
        - Install WSL2 (and the VM Platform Windows feature) if missing
        - Install Windows Terminal if missing
        - Build the Ubuntu template distro (skipped if it already exists)
        - (Optional, default on) Add a permanent Windows Defender
          exclusion for the Maude install path so user-phase imports
          aren't blocked by AV scanning. Disable with -NoDefenderExclusion.
        Run this on a fresh machine, or any time WSL/WT is missing.

      User phase (no flag, no admin):
        - Verify WSL is present (else: "run -Admin once")
        - If the Ubuntu template is missing, build it here (per-user
          operation — Store install or Canonical download)
        - wsl --export the template, wsl --import it as Maude
        - Run root-bootstrap.sh inside Maude (user creation, /etc/wsl.conf,
          fstab mount, mom, PATH, sandbox isolation)
        - Create the Maude shared folder (OneDrive or %LOCALAPPDATA%\Maude\Data\Maude),
          set its custom icon, pin to Quick Access
        - Run maude-bootstrap.sh inside Maude (dev-station, kanna, skills,
          Claude Code config, maude launcher)
        - Configure the Windows Terminal profile and desktop shortcut

    Why split this way? Building the template via Store install or
    Canonical-download `wsl --import` is per-user state (HKCU\...\Lxss),
    so it doesn't strictly need admin. The only hard admin step is
    enabling the WSL Windows feature itself. By moving the template
    build into the user phase, machines that already have WSL installed
    can do the entire Maude setup with zero UAC prompts.

    Security note: with root-bootstrap.sh running unelevated in the user
    phase, the WSL-interop privilege-escalation TOCTOU vector that the
    cybersec review flagged (an elevated `wsl -u root` calling
    /mnt/c/Windows/System32/net.exe via interop) doesn't apply — the
    parent wsl.exe is unprivileged, so spawned Windows binaries are too.

    The distro is sandboxed: automatic Windows drive mounting is
    disabled, and only the shared Maude folder is mounted into
    /home/maude/Maude via drvfs + /etc/fstab.

.NOTES
    Machine WITHOUT WSL installed yet (rare, requires admin once):

        # 1. From an elevated PowerShell (one-time, ~5 min):
        Set-ExecutionPolicy Bypass -Scope Process -Force
        .\setup-wsl-maude.ps1 -Admin

        # 2. From a NON-elevated PowerShell:
        .\setup-wsl-maude.ps1                # Ubuntu 26.04, local data folder

    Machine WITH WSL already installed (most users):

        # Just run the user phase — it'll build the template if missing:
        .\setup-wsl-maude.ps1                # Ubuntu 26.04, local data folder
        .\setup-wsl-maude.ps1 -OneDrive      # Shared folder in OneDrive
        .\setup-wsl-maude.ps1 -NoOneDrive    # Force local %LOCALAPPDATA%\Maude\Data\Maude

    Subsequent reinstalls (template already built): just the user-phase
    command. Completes in ~30 seconds, no admin.

    Refresh template (e.g., new Ubuntu version):
        .\teardown-wsl-maude.ps1 -IncludeTemplate
        .\setup-wsl-maude.ps1 -Noble         # rebuilds template, no admin

    Verified install (recommended in production):
        .\setup-wsl-maude.ps1 -Admin -Release v0.4.0
        .\setup-wsl-maude.ps1        -Release v0.4.0

    -Release pins downloads to a specific git tag and verifies every
    downloaded file's SHA-256 against light/checksums.txt at that tag.
    -Release main (default) is for development and skips verification.
#>

param(
    [string]$DistroName          = "Maude",
    [string]$DefaultUser         = "maude",
    [string]$InstallDir          = "$env:LOCALAPPDATA\Maude\OS",
    [switch]$OneDrive,
    [switch]$NoOneDrive,
    [switch]$Noble,
    [switch]$Admin,
    [switch]$NoDefenderExclusion,
    [string]$Release             = "main"
)

# ── Ubuntu version: default 26.04 (Resolute Raccoon), -Noble for 24.04 ──
if ($Noble) {
    $ubuntuVersion  = "24.04"
    $ubuntuCodename = "noble"
    $ubuntuLabel    = "Ubuntu 24.04 (Noble Numbat)"
} else {
    $ubuntuVersion  = "26.04"
    $ubuntuCodename = "resolute"
    $ubuntuLabel    = "Ubuntu 26.04 (Resolute Raccoon)"
}
$templateDistro = "Ubuntu-${ubuntuVersion}-Template"

$GH_RAW    = "https://raw.githubusercontent.com/dirkpetersen/maude/$Release"
$cacheBust = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

# ── Helper: locate Windows Terminal settings.json ─────────────────────
function Find-WTSettingsPath {
    $candidates = @(
        Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
        Join-Path $env:LOCALAPPDATA "Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json"
        Join-Path $env:LOCALAPPDATA "Microsoft\Windows Terminal\settings.json"
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { return $c }
    }
    return $null
}

# ── Helper: reliably test if a WSL distro is registered ──
function Test-WslDistro([string]$name) {
    $lines = (wsl --list --verbose 2>&1) -replace "`0", ""
    foreach ($line in $lines) {
        $fields = ($line -replace '^\*?\s+', '').Trim() -split '\s+'
        if ($fields[0] -ieq $name) { return $true }
    }
    return $false
}

# ── Helper: PNG → ICO ──
function Convert-PngToIco($pngPath, $icoPath) {
    $pngBytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $pngPath).Path)

    Add-Type -AssemblyName System.Drawing
    $img = [System.Drawing.Image]::FromFile((Resolve-Path -LiteralPath $pngPath).Path)
    $w = [Math]::Min($img.Width, 256)
    $h = [Math]::Min($img.Height, 256)
    $img.Dispose()

    $wb = if ($w -eq 256) { [byte]0 } else { [byte]$w }
    $hb = if ($h -eq 256) { [byte]0 } else { [byte]$h }

    $ms = New-Object System.IO.MemoryStream
    $bw = New-Object System.IO.BinaryWriter($ms)
    $bw.Write([UInt16]0); $bw.Write([UInt16]1); $bw.Write([UInt16]1)
    $bw.Write($wb); $bw.Write($hb); $bw.Write([byte]0); $bw.Write([byte]0)
    $bw.Write([UInt16]1); $bw.Write([UInt16]32)
    $bw.Write([UInt32]$pngBytes.Length); $bw.Write([UInt32]22)
    $bw.Write($pngBytes); $bw.Flush()
    [System.IO.File]::WriteAllBytes($icoPath, $ms.ToArray())
    $bw.Dispose(); $ms.Dispose()
}

# ── Helper: discover existing OneDrive Maude folders ──
function Find-OneDriveMaudeFolder {
    $candidates = @()
    if ($env:OneDriveCommercial) {
        $candidates += [PSCustomObject]@{ Path = Join-Path $env:OneDriveCommercial "Maude"; Source = "OneDrive for Business" }
    }
    if ($env:OneDriveConsumer) {
        $candidates += [PSCustomObject]@{ Path = Join-Path $env:OneDriveConsumer "Maude"; Source = "OneDrive Personal" }
    }
    if ($env:OneDrive -and -not $env:OneDriveCommercial -and -not $env:OneDriveConsumer) {
        $candidates += [PSCustomObject]@{ Path = Join-Path $env:OneDrive "Maude"; Source = "OneDrive" }
    }
    $odBusiness = Get-ChildItem -Path $env:USERPROFILE -Directory -Filter "OneDrive - *" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($odBusiness) {
        $p = Join-Path $odBusiness.FullName "Maude"
        if (-not ($candidates | Where-Object { $_.Path -eq $p })) {
            $candidates += [PSCustomObject]@{ Path = $p; Source = "OneDrive for Business ($($odBusiness.Name))" }
        }
    }
    $odPersonal = Join-Path $env:USERPROFILE "OneDrive"
    if ((Test-Path -LiteralPath $odPersonal) -and -not ($candidates | Where-Object { $_.Path -eq (Join-Path $odPersonal "Maude") })) {
        $candidates += [PSCustomObject]@{ Path = Join-Path $odPersonal "Maude"; Source = "OneDrive Personal" }
    }
    return $candidates
}

# ── Helper: warn the user about OneDrive sharing risk ──
function Show-OneDriveSharingWarning {
    param([string]$Path)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host "  WARNING: OneDrive Sharing Risk" -ForegroundColor Yellow
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  The Maude folder will live inside OneDrive:"
    Write-Host "    $Path"
    Write-Host ""
    Write-Host "  If this folder OR ANY ANCESTOR folder is shared via" -ForegroundColor Yellow
    Write-Host "  OneDrive's sharing feature, anyone with edit access can" -ForegroundColor Yellow
    Write-Host "  drop files into Maude that the AI will execute as" -ForegroundColor Yellow
    Write-Host "  instructions (prompt-injection vector)." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Verify NOTHING in the Maude path is shared:"
    Write-Host "    * Right-click each folder -> 'Manage access' in Explorer"
    Write-Host "    * Or visit https://onedrive.com -> 'Shared by me'"
    Write-Host ""
    Write-Host "  Press Ctrl+C in 8 seconds to abort, then re-run with" -ForegroundColor Yellow
    Write-Host "  -NoOneDrive to use the local %LOCALAPPDATA%\Maude\Data folder instead." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Yellow
    Start-Sleep -Seconds 8
}

# ── Helper: load checksums.txt into a hashtable ──
function Get-ChecksumsTable([string]$path) {
    $tbl = @{}
    if (-not (Test-Path -LiteralPath $path)) { return $tbl }
    foreach ($line in Get-Content -LiteralPath $path) {
        if ($line -match '^([0-9a-fA-F]{64})\s+\*?(.+)$') {
            $tbl[$Matches[2].Trim()] = $Matches[1].ToLower()
        }
    }
    return $tbl
}

# ── Helper: detect which Ubuntu template (if any) is installed ──
# User phase auto-detects so the user doesn't have to remember whether
# they built the Noble or Resolute template. Prefers a match for the
# current -Noble flag, but accepts any present template.
function Find-InstalledTemplate {
    param([bool]$preferNoble)
    $preferred = if ($preferNoble) { "Ubuntu-24.04-Template" } else { "Ubuntu-26.04-Template" }
    $alt       = if ($preferNoble) { "Ubuntu-26.04-Template" } else { "Ubuntu-24.04-Template" }
    if (Test-WslDistro $preferred) { return $preferred }
    if (Test-WslDistro $alt)       { return $alt }
    return $null
}

# ── Build-Template: download Ubuntu, install packages, leave it stopped ──
# Used by both the admin phase (first-time machine setup) and the user
# phase (when WSL+WT are already present and the template is missing).
# Add-MpPreference and Stop-Service LxssManager are admin-only fallbacks;
# they are skipped silently when $IsElevated is $false. The Path A Store
# install and Path B Canonical-download paths are both per-user and work
# unelevated.
function Build-Template {
    param(
        [Parameter(Mandatory)][string]$TemplateName,
        [Parameter(Mandatory)][bool]$IsNoble,
        [Parameter(Mandatory)][string]$UbuntuVersion,
        [Parameter(Mandatory)][string]$UbuntuLabel,
        [Parameter(Mandatory)][string]$ScriptDir,
        [Parameter(Mandatory)][bool]$IsElevated
    )

    if (Test-WslDistro $TemplateName) {
        Write-Host "'$TemplateName' already exists; nothing to build." -ForegroundColor Gray
        return $true
    }

    # Parse package list from packages/ubuntu-packages.yaml
    $packagesYaml = Join-Path $ScriptDir "..\packages\ubuntu-packages.yaml"
    $packageList = ""
    if (Test-Path -LiteralPath $packagesYaml) {
        $packages = @(
            (Get-Content -LiteralPath $packagesYaml) |
                Where-Object { $_ -match '^\s+-\s+\S' } |
                ForEach-Object { ($_ -replace '^\s+-\s+', '' -replace '\s*#.*$', '').Trim() } |
                Where-Object { $_ -ne "" }
        )
        $packageList = ($packages -join "`n") -replace "`r", ""
        Write-Host "  $($packages.Count) packages from ubuntu-packages.yaml"
    }

    # Detect --name support by parsing wsl --help output.
    $wslHelp = (wsl --help 2>&1) -replace "`0", "" -join "`n"
    $hasNameFlag = $wslHelp -match '--name'
    Write-Host "WSL --name flag: $(if ($hasNameFlag) {'supported'} else {'not supported'})" -ForegroundColor Gray

    $installed = $false

    if ($hasNameFlag) {
        # ── Path A: Modern WSL with --name (Store install) ──
        $onlineList = (wsl --list --online 2>&1) -join "`n"
        $candidates = @()
        if ($onlineList -match "Ubuntu-$UbuntuVersion") { $candidates += "Ubuntu-$UbuntuVersion" }
        if ($IsNoble) {
            if ($onlineList -match 'Ubuntu\b') { $candidates += "Ubuntu" }
            if ($candidates.Count -eq 0) { $candidates = @("Ubuntu-$UbuntuVersion", "Ubuntu") }
        }
        if ($candidates.Count -eq 0) { $candidates = @("Ubuntu-$UbuntuVersion") }

        foreach ($distro in $candidates) {
            Write-Host "Trying Store install: '$distro' as '$TemplateName'..." -ForegroundColor Gray
            $out = (wsl --install -d $distro --name $TemplateName --no-launch 2>&1) -replace "`0","" -join "`n"
            if ($LASTEXITCODE -eq 0) { $installed = $true; break }
            if ($out -match 'HCS_E_HYPERV_NOT_INSTALLED|WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED') {
                Write-Host "Hyper-V/VM Platform not available, skipping Store install." -ForegroundColor Yellow
                wsl --terminate $TemplateName 2>&1 | Out-Null
                wsl --unregister $TemplateName 2>&1 | Out-Null
                break
            }
            wsl --unregister $TemplateName 2>&1 | Out-Null
            wsl --install -d $distro --name $TemplateName --no-launch 2>$null
            if ($LASTEXITCODE -eq 0) { $installed = $true; break }
            Write-Host "'$distro' not available via Store, trying next..." -ForegroundColor Yellow
        }
    }

    # ── Path B: Download from Canonical + wsl --import ──
    if (-not $installed) {
        if ($hasNameFlag) {
            Write-Host "Store install failed. Downloading from Canonical..." -ForegroundColor Yellow
        } else {
            Write-Host "Downloading $UbuntuLabel WSL image from Canonical..." -ForegroundColor Yellow
        }
        if ($IsNoble) {
            $rootfsUrl = "https://releases.ubuntu.com/noble/ubuntu-24.04.4-wsl-amd64.wsl"
        } else {
            $rootfsUrl = "https://releases.ubuntu.com/resolute/ubuntu-26.04-wsl-amd64.wsl"
        }
        $rootfsFile = Join-Path $env:TEMP "ubuntu-$UbuntuVersion-wsl-amd64.wsl"
        Write-Host "Downloading ~375 MB (this may take a few minutes)..."
        curl.exe -L -o $rootfsFile "$rootfsUrl"
        if (-not (Test-Path -LiteralPath $rootfsFile) -or (Get-Item -LiteralPath $rootfsFile).Length -lt 100MB) {
            Write-Host "ERROR: Failed to download Ubuntu WSL image." -ForegroundColor Red
            return $false
        }

        # Defender exclusions during the import (admin-only; best-effort).
        # When unelevated, skip — the import usually succeeds anyway because
        # Defender real-time scanning only sometimes locks the rootfs file.
        $defenderExclusions = @($rootfsFile, (Join-Path $env:LOCALAPPDATA "Maude\Template"))
        if ($IsElevated) {
            foreach ($excl in $defenderExclusions) {
                Add-MpPreference -ExclusionPath $excl -ErrorAction SilentlyContinue
            }
        }

        wsl --terminate $TemplateName 2>&1 | Out-Null
        wsl --unregister $TemplateName 2>&1 | Out-Null
        $tplDir = Join-Path $env:LOCALAPPDATA "Maude\Template"
        if (Test-Path -LiteralPath $tplDir) {
            Start-Sleep -Seconds 2
            Remove-Item -LiteralPath $tplDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        # File-lock fallback: stopping LxssManager requires admin. When
        # unelevated, just skip the service-restart and let Remove-Item fail
        # gracefully if files are still locked.
        if ((Test-Path -LiteralPath $tplDir) -and $IsElevated) {
            Write-Host "Files locked. Restarting WSL service to release locks..." -ForegroundColor Yellow
            Stop-Service LxssManager -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
            Remove-Item -LiteralPath $tplDir -Recurse -Force -ErrorAction SilentlyContinue
            Start-Service LxssManager -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
        } elseif (Test-Path -LiteralPath $tplDir) {
            Write-Host "Some template files are locked. If the import fails, re-run with -Admin." -ForegroundColor Yellow
        }
        New-Item -ItemType Directory -Force -Path $tplDir | Out-Null

        $wslVersion = 2
        Write-Host "Importing as '$TemplateName' (WSL $wslVersion)..."
        wsl --import $TemplateName $tplDir $rootfsFile --version $wslVersion
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WSL2 import failed. Trying WSL1 (no virtualization needed)..." -ForegroundColor Yellow
            wsl --unregister $TemplateName 2>&1 | Out-Null
            if (Test-Path -LiteralPath $tplDir) {
                Remove-Item -LiteralPath $tplDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            New-Item -ItemType Directory -Force -Path $tplDir | Out-Null
            $wslVersion = 1
            wsl --import $TemplateName $tplDir $rootfsFile --version $wslVersion
        }
        Remove-Item -LiteralPath $rootfsFile -ErrorAction SilentlyContinue
        if ($IsElevated) {
            foreach ($excl in $defenderExclusions) {
                Remove-MpPreference -ExclusionPath $excl -ErrorAction SilentlyContinue
            }
        }
        if (-not (Test-WslDistro $TemplateName)) {
            Write-Host "ERROR: wsl --import failed." -ForegroundColor Red
            if (-not $IsElevated) {
                Write-Host "If Defender locked the import, re-run with -Admin once to add a permanent exclusion." -ForegroundColor Yellow
            }
            Write-Host "If on a VM, ensure nested virtualization is enabled for WSL2," -ForegroundColor Yellow
            Write-Host "or check that WSL1 is supported on this system." -ForegroundColor Yellow
            return $false
        }
        Write-Host "'$TemplateName' imported as WSL$wslVersion." -ForegroundColor Gray
    }

    # Verify WSL is operational (catches post-upgrade reboot needed)
    wsl -d $TemplateName -- echo ok 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`nWSL was just installed/upgraded and needs a reboot before continuing." -ForegroundColor Yellow
        Write-Host "After rebooting, re-run this script." -ForegroundColor Yellow
        return $false
    }

    # Bake packages into the template.
    Write-Host "Installing packages into template (this takes a few minutes)..."
    if ($packageList) {
        $installLines = @(
            'export DEBIAN_FRONTEND=noninteractive',
            'export TERM=dumb',
            "printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d",
            'chmod +x /usr/sbin/policy-rc.d',
            'apt-get update -q',
            'apt-get install -y unzip software-properties-common',
            'add-apt-repository -y universe',
            'apt-get update -q',
            "PKGS=`$(cat | tr -d '\r'); apt-get install -y --no-install-recommends `$PKGS || for p in `$PKGS; do apt-get install -y --no-install-recommends `$p 2>/dev/null; done",
            'rm -f /usr/sbin/policy-rc.d',
            'apt-get clean'
        )
        $installScriptLF = $installLines -join "`n"
        $installScriptB64 = [Convert]::ToBase64String(
            [System.Text.Encoding]::UTF8.GetBytes($installScriptLF)
        )
        wsl -d $TemplateName -u root -- bash -c "echo $installScriptB64 | base64 -d > /tmp/install-pkgs.sh && chmod +x /tmp/install-pkgs.sh"
        $packageList | wsl -d $TemplateName -u root -- bash /tmp/install-pkgs.sh
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: Some packages may have failed to install." -ForegroundColor Yellow
        }
    }
    # Stop the template so its ext4.vhdx is consistent for export later.
    wsl --terminate $TemplateName 2>&1 | Out-Null
    Write-Host "'$TemplateName' built with packages." -ForegroundColor Gray
    return $true
}

# ── Elevation gating ────────────────────────────────────────────────
$isElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if ($Admin -and -not $isElevated) {
    Write-Host "-Admin specified but PowerShell is not elevated. Self-elevating..." -ForegroundColor Yellow
    try {
        $extraArgs = " -Admin"
        if ($OneDrive)             { $extraArgs += " -OneDrive" }
        if ($NoOneDrive)           { $extraArgs += " -NoOneDrive" }
        if ($Noble)                { $extraArgs += " -Noble" }
        if ($NoDefenderExclusion)  { $extraArgs += " -NoDefenderExclusion" }
        if ($Release -ne 'main')   { $extraArgs += " -Release $Release" }
        if ($PSCommandPath) {
            Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`"$extraArgs"
        } else {
            $cmd = "Set-ExecutionPolicy Bypass -Scope Process -Force; & { `$f = `$env:TEMP + '\setup-wsl-maude.ps1'; (New-Object Net.WebClient).DownloadFile('$GH_RAW/light/setup-wsl-maude.ps1?cache=$cacheBust', `$f); & `$f$extraArgs }"
            Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -Command `"$cmd`""
        }
    } catch {
        Write-Host @"

ERROR: Self-elevation failed.

Open PowerShell as Administrator (right-click -> 'Run as Administrator')
and run:

    Set-ExecutionPolicy Bypass -Scope Process -Force
    iex ((New-Object Net.WebClient).DownloadString('$GH_RAW/light/setup-wsl-maude.ps1')) -Admin

"@ -ForegroundColor Red
    }
    exit
}

if (-not $Admin -and $isElevated) {
    Write-Host @"

ERROR: PowerShell is running as Administrator but -Admin was NOT supplied.

The user phase configures Windows Terminal, the desktop shortcut, and the
Maude folder for the *current user*. Running it elevated will write those
into the admin's profile instead of yours.

Do one of:
  * Run from a non-elevated PowerShell to perform user-phase setup, or
  * Re-run with -Admin to perform admin-phase setup (WSL install, WT
    install, build Ubuntu template).

"@ -ForegroundColor Red
    exit 1
}

# ── ScriptDir: per-user TEMP, never $PSScriptRoot ─────────────────────
$ScriptDir = Join-Path $env:TEMP "maude-setup"
New-Item -ItemType Directory -Force -Path $ScriptDir | Out-Null

# ── Download bootstrap files (with checksum verification when pinned) ──
# Both phases use the same download list. Each phase only USES the files
# it needs; downloading the full set in either phase keeps the structure
# simple and means the user phase has fresh, verified copies.
$filesToDownload = @(
    @{ Url = "$GH_RAW/light/root-bootstrap.sh";       Dest = "root-bootstrap.sh";                  Key = "light/root-bootstrap.sh" }
    @{ Url = "$GH_RAW/light/maude-bootstrap.sh";      Dest = "maude-bootstrap.sh";                 Key = "light/maude-bootstrap.sh" }
    @{ Url = "$GH_RAW/light/maude";                   Dest = "maude";                              Key = "light/maude" }
    @{ Url = "$GH_RAW/maude.png";                     Dest = "maude.png";                          Key = "maude.png" }
    @{ Url = "$GH_RAW/packages/ubuntu-packages.yaml"; Dest = "..\packages\ubuntu-packages.yaml";   Key = "packages/ubuntu-packages.yaml" }
)

$verifyChecksums = ($Release -ne 'main')
$checksums = @{}

if ($verifyChecksums) {
    Write-Host "Fetching checksums.txt for release '$Release'..." -ForegroundColor Cyan
    $checksumPath = Join-Path $ScriptDir "checksums.txt"
    try {
        (New-Object Net.WebClient).DownloadFile("$GH_RAW/light/checksums.txt?cache=$cacheBust", $checksumPath)
    } catch {
        Write-Host "ERROR: Could not download checksums.txt from release '$Release'." -ForegroundColor Red
        Write-Host "$_" -ForegroundColor Red
        Write-Host "If this release pre-dates the checksum process, use -Release main (development mode)." -ForegroundColor Yellow
        exit 1
    }
    $checksums = Get-ChecksumsTable $checksumPath
    if ($checksums.Count -eq 0) {
        Write-Host "ERROR: checksums.txt is empty or unparseable." -ForegroundColor Red
        exit 1
    }
}

$wc = New-Object Net.WebClient
foreach ($dl in $filesToDownload) {
    $destPath = Join-Path $ScriptDir $dl.Dest
    $destDir  = Split-Path $destPath -Parent
    if (-not (Test-Path -LiteralPath $destDir)) {
        New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    }
    try {
        $wc.DownloadFile("$($dl.Url)?cache=$cacheBust", $destPath)
    } catch {
        if ($verifyChecksums) {
            Write-Host "ERROR: Could not download $($dl.Url): $_" -ForegroundColor Red
            exit 1
        }
        Write-Host "WARNING: Could not download $($dl.Url): $_" -ForegroundColor Yellow
        continue
    }
    if ($verifyChecksums) {
        $expected = $checksums[$dl.Key]
        if (-not $expected) {
            Write-Host "ERROR: No checksum entry for '$($dl.Key)' in checksums.txt." -ForegroundColor Red
            Remove-Item -LiteralPath $destPath -Force -ErrorAction SilentlyContinue
            exit 1
        }
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $destPath).Hash.ToLower()
        if ($actual -ne $expected) {
            Write-Host "ERROR: Checksum mismatch for $($dl.Url)" -ForegroundColor Red
            Write-Host "  Expected: $expected" -ForegroundColor Red
            Write-Host "  Actual:   $actual" -ForegroundColor Red
            Remove-Item -LiteralPath $destPath -Force -ErrorAction SilentlyContinue
            exit 1
        }
    }
}

if ($verifyChecksums) {
    Write-Host "All files verified against release '$Release' checksums.txt." -ForegroundColor Green
} else {
    Write-Host "Note: -Release main; checksums NOT verified (development mode)." -ForegroundColor Yellow
}

# =====================================================================
# ADMIN PHASE — first-time machine setup (rarely run)
# =====================================================================
if ($Admin) {

    Write-Host ""
    Write-Host "=== Maude Setup: Admin Phase ===" -ForegroundColor Cyan
    Write-Host "(WSL2, Windows Terminal, Ubuntu template)" -ForegroundColor DarkGray

    # ── Free disk space check (template build is the heavy part) ──
    $cDrive = Get-PSDrive -Name C
    $freeGB = [math]::Round($cDrive.Free / 1GB, 1)
    Write-Host "Free disk space on C: drive: ${freeGB} GB" -ForegroundColor Cyan
    if ($freeGB -lt 5) {
        Write-Host "`nWARNING: Very low disk space (${freeGB} GB free)!" -ForegroundColor Red
        Write-Host "Maude may not function properly with less than 5 GB free." -ForegroundColor Red
        Write-Host "Press Ctrl+C within 10 seconds to cancel installation..." -ForegroundColor Yellow
        Start-Sleep -Seconds 10
    } elseif ($freeGB -lt 10) {
        Write-Host "NOTE: Less than 10 GB free. Template build may run tight." -ForegroundColor Yellow
    }

    # ── Step 1 (admin): Install WSL2 + VM Platform ──
    Write-Host "`n[1/4] Checking WSL..." -ForegroundColor Green
    $needsReboot = $false
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        $wslStatus = (wsl --status 2>&1) -join "`n"
        if ($wslStatus -match 'HCS_E_HYPERV_NOT_INSTALLED|WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED') {
            Write-Host "WSL needs setup/upgrade..."
            wsl --install --no-distribution
            $vmPlatform = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue
            if ($vmPlatform -and $vmPlatform.State -ne 'Enabled') {
                Write-Host "Enabling Virtual Machine Platform..."
                dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
            }
            $needsReboot = $true
        } else {
            Write-Host "WSL is already installed." -ForegroundColor Gray
        }
    } else {
        Write-Host "Installing WSL2..."
        wsl --install --no-distribution
        $needsReboot = $true
    }
    if ($needsReboot) {
        Write-Host "`nA reboot is required before continuing." -ForegroundColor Yellow
        Write-Host "After rebooting, re-run this script with -Admin." -ForegroundColor Yellow
        Read-Host "Press Enter to exit"
        exit
    }

    # ── Step 2 (admin): Install Windows Terminal ──
    Write-Host "`n[2/4] Checking Windows Terminal..." -ForegroundColor Green
    $wtPresent = (Get-Command wt.exe -ErrorAction SilentlyContinue) -or
                 (Get-AppxPackage -Name "Microsoft.WindowsTerminal" -ErrorAction SilentlyContinue)
    if ($wtPresent) {
        Write-Host "Windows Terminal is already installed." -ForegroundColor Gray
    } else {
        $wtInstalled = $false
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "Installing Windows Terminal via winget..."
            winget install --id Microsoft.WindowsTerminal --accept-source-agreements --accept-package-agreements
            if ($LASTEXITCODE -eq 0) { $wtInstalled = $true }
        }
        if (-not $wtInstalled) {
            Write-Host "Trying AppX store registration..." -ForegroundColor Yellow
            try {
                Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.WindowsTerminal_8wekyb3d8bbwe -ErrorAction Stop
                $wtInstalled = $true
            } catch {
                Write-Host "AppX registration not available." -ForegroundColor Yellow
            }
        }
        if (-not $wtInstalled) {
            Write-Host "Downloading Windows Terminal from GitHub..." -ForegroundColor Yellow
            $wtTmp = Join-Path $env:TEMP "wt-install"
            New-Item -ItemType Directory -Force -Path $wtTmp | Out-Null
            try {
                $wtRelease = curl.exe -s "https://api.github.com/repos/microsoft/terminal/releases/latest?cache=$cacheBust" | ConvertFrom-Json
                $msixUrl = ($wtRelease.assets | Where-Object { $_.name -match '\.msixbundle$' } | Select-Object -First 1).browser_download_url
                if ($msixUrl) {
                    $vclibsUrl = "https://aka.ms/Microsoft.VCLibs.x64.14.00.Desktop.appx"
                    $xamlUrl   = "https://github.com/microsoft/microsoft-ui-xaml/releases/download/v2.8.6/Microsoft.UI.Xaml.2.8.x64.appx"
                    curl.exe -sL -o "$wtTmp\vclibs.appx" $vclibsUrl
                    curl.exe -sL -o "$wtTmp\uixaml.appx" $xamlUrl
                    curl.exe -sL -o "$wtTmp\terminal.msixbundle" $msixUrl
                    Add-AppxPackage -Path "$wtTmp\vclibs.appx" -ErrorAction SilentlyContinue
                    Add-AppxPackage -Path "$wtTmp\uixaml.appx" -ErrorAction SilentlyContinue
                    Add-AppxPackage -Path "$wtTmp\terminal.msixbundle" -ErrorAction Stop
                    $wtInstalled = $true
                }
            } catch {
                Write-Host "GitHub download install failed: $_" -ForegroundColor Yellow
            } finally {
                Remove-Item -Recurse -Force -LiteralPath $wtTmp -ErrorAction SilentlyContinue
            }
        }
        if ($wtInstalled) {
            Write-Host "Windows Terminal installed." -ForegroundColor Gray
        } else {
            Write-Host "Windows Terminal could not be installed." -ForegroundColor Yellow
            Write-Host "Maude will still work -- launch via: wsl -d $DistroName" -ForegroundColor Yellow
        }
    }

    # ── Step 3 (admin): Build the Ubuntu template ──
    Write-Host "`n[3/4] Building '$templateDistro'..." -ForegroundColor Green
    $ok = Build-Template -TemplateName $templateDistro -IsNoble:$Noble `
        -UbuntuVersion $ubuntuVersion -UbuntuLabel $ubuntuLabel `
        -ScriptDir $ScriptDir -IsElevated:$true
    if (-not $ok) { exit 1 }

    # ── Step 4 (admin): Defender exclusion (best-effort) ──
    # During first-time install, the Path B fallback (Canonical rootfs
    # download) can hit Defender real-time scanning lock-ups; an exclusion
    # avoids that. The user phase uses `wsl --import` from a local tarball,
    # which AV scanning rarely blocks — so a missing exclusion here is NOT
    # fatal to reinstalls. We probe Defender's status first and tailor the
    # message accordingly:
    #
    #   * Defender active (AMRunningMode=Normal) → add exclusion
    #   * Defender passive / disabled (third-party AV is the active engine)
    #     → skip cleanly with an informational note (the user can't change
    #       this from a per-machine PowerShell)
    #   * Defender service unavailable (0x800106ba etc.) → skip with a
    #     clear note, not a scary warning
    Write-Host "`n[4/4] Configuring Windows Defender exclusion..." -ForegroundColor Green

    function Get-DefenderState {
        # Returns one of: 'active', 'passive', 'unavailable', 'tamper-protected', 'unknown'
        # Plus an optional detail message.
        try {
            $status = Get-MpComputerStatus -ErrorAction Stop
        } catch {
            return [PSCustomObject]@{ State = 'unavailable'; Detail = "Defender service not reachable ($($_.Exception.Message))" }
        }
        $mode = if ($status.PSObject.Properties['AMRunningMode']) { $status.AMRunningMode } else { '' }
        $av   = if ($status.PSObject.Properties['AntivirusEnabled']) { $status.AntivirusEnabled } else { $true }
        $rt   = if ($status.PSObject.Properties['RealTimeProtectionEnabled']) { $status.RealTimeProtectionEnabled } else { $true }
        $tp   = if ($status.PSObject.Properties['IsTamperProtected']) { $status.IsTamperProtected } else { $false }
        if ($mode -and $mode -ne 'Normal') {
            return [PSCustomObject]@{ State = 'passive'; Detail = "Defender is in '$mode' mode (third-party AV is the active engine)" }
        }
        if (-not $av -or -not $rt) {
            return [PSCustomObject]@{ State = 'passive'; Detail = "Defender is disabled (third-party AV likely active)" }
        }
        if ($tp) {
            # Tamper-protected machines may still accept exclusions if the policy
            # allows them; we'll let Add-MpPreference attempt and fall back.
            return [PSCustomObject]@{ State = 'active'; Detail = 'Tamper Protection is enabled; exclusion may be policy-blocked' }
        }
        return [PSCustomObject]@{ State = 'active'; Detail = '' }
    }

    if ($NoDefenderExclusion) {
        Write-Host "Skipped (-NoDefenderExclusion)." -ForegroundColor Yellow
    } else {
        # Exclude only the OS distro and template paths — never Data\, which
        # holds user-created project files that AV should still scan.
        $exclusionPaths = @(
            (Join-Path $env:LOCALAPPDATA "Maude\OS")
            (Join-Path $env:LOCALAPPDATA "Maude\Template")
        )
        $defender = Get-DefenderState
        switch ($defender.State) {
            'active' {
                if ($defender.Detail) { Write-Host "Note: $($defender.Detail)" -ForegroundColor Gray }
                $allOk = $true
                $errMsg = $null
                foreach ($excl in $exclusionPaths) {
                    try {
                        Add-MpPreference -ExclusionPath $excl -ErrorAction Stop
                    } catch {
                        $allOk = $false
                        $errMsg = $_.Exception.Message
                        break
                    }
                }
                if ($allOk) {
                    Write-Host "Added Defender exclusions:" -ForegroundColor Gray
                    foreach ($excl in $exclusionPaths) { Write-Host "  $excl" -ForegroundColor Gray }
                    Write-Host "(remove with: Remove-MpPreference -ExclusionPath <path>)" -ForegroundColor Gray
                } else {
                    # Common on managed machines: Tamper Protection or MDM policy
                    # blocks the call (e.g., 0x800106ba RPC failure).
                    Write-Host "Could not add Defender exclusion: $errMsg" -ForegroundColor Yellow
                    Write-Host "This is expected on managed machines with Tamper Protection or MDM-controlled" -ForegroundColor Gray
                    Write-Host "AV policy. The user phase doesn't typically need the exclusion (it imports" -ForegroundColor Gray
                    Write-Host "from a local tarball, which AV rarely blocks). If reinstalls do fail, ask" -ForegroundColor Gray
                    Write-Host "your IT/AV admin to add an exclusion for these paths:" -ForegroundColor Gray
                    foreach ($excl in $exclusionPaths) { Write-Host "  $excl" -ForegroundColor Gray }
                }
            }
            'passive' {
                Write-Host "Skipped: $($defender.Detail)." -ForegroundColor Gray
                Write-Host "Defender exclusions don't apply when Defender is in passive mode. Your" -ForegroundColor Gray
                Write-Host "active AV (CrowdStrike, SentinelOne, Defender ATP, etc.) controls scanning." -ForegroundColor Gray
                Write-Host "If reinstalls fail with file-lock errors, ask your IT/AV admin to add an" -ForegroundColor Gray
                Write-Host "exclusion for these paths:" -ForegroundColor Gray
                foreach ($excl in $exclusionPaths) { Write-Host "  $excl" -ForegroundColor Gray }
            }
            'unavailable' {
                Write-Host "Skipped: $($defender.Detail)." -ForegroundColor Gray
                Write-Host "(Common on Windows Server SKUs or when Defender service is stopped.)" -ForegroundColor Gray
            }
            default {
                Write-Host "Skipped: could not determine Defender state." -ForegroundColor Gray
            }
        }
    }

    Write-Host ""
    Write-Host "=============================================================" -ForegroundColor Cyan
    Write-Host "  Admin phase complete!" -ForegroundColor Cyan
    Write-Host "=============================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Next: close this elevated window and run from a NON-elevated" -ForegroundColor White
    Write-Host "PowerShell. The user phase imports the template as Maude and" -ForegroundColor White
    Write-Host "is the same command you'll use for every future reinstall:" -ForegroundColor White
    Write-Host ""
    Write-Host "    curl.exe -sLo `$env:TEMP\setup-wsl-maude.ps1 $GH_RAW/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File `$env:TEMP\setup-wsl-maude.ps1$(if($OneDrive){' -OneDrive'})$(if($NoOneDrive){' -NoOneDrive'})$(if($Noble){' -Noble'})$(if($Release -ne 'main'){" -Release $Release"})" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to close this window"
    exit
}

# =====================================================================
# USER PHASE — runs every install and reinstall (no admin needed)
# =====================================================================
Write-Host ""
Write-Host "=== Maude Setup: User Phase ===" -ForegroundColor Cyan
Write-Host "(Distro import, host folder, bootstrap, WT profile, shortcut)" -ForegroundColor DarkGray

# ── Verify WSL is operational ──
# WSL itself requires admin to install (it enables Windows features), so
# this is the one hard prerequisite that user-phase setup cannot satisfy
# on its own. If WSL is already present, the rest of the install runs
# fine without admin.
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Host @"

ERROR: WSL is not installed.

WSL itself needs admin to install (it enables a Windows feature). Run the
admin phase once from an ELEVATED PowerShell:

    curl.exe -sLo `$env:TEMP\setup-wsl-maude.ps1 $GH_RAW/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File `$env:TEMP\setup-wsl-maude.ps1 -Admin$(if($Noble){' -Noble'})$(if($Release -ne 'main'){" -Release $Release"})

After that, all future installs and reinstalls work without admin.

"@ -ForegroundColor Red
    exit 1
}

# ── Find or build the Ubuntu template ──
# WSL itself needed admin to install (Windows feature), but everything that
# follows — building the template via Store install or Canonical download,
# importing it as a distro, running root-bootstrap inside it — is per-user
# state. So if WSL is already present, we can build the template here in
# the user phase without elevation. The Defender-exclusion and
# LxssManager-restart fallbacks inside Build-Template are skipped silently
# when unelevated; on managed machines they're often unavailable anyway.
$installedTemplate = Find-InstalledTemplate -preferNoble:$Noble
if (-not $installedTemplate) {
    Write-Host ""
    Write-Host "No Ubuntu template found. Building '$templateDistro' now..." -ForegroundColor Cyan
    Write-Host "(This is a one-time setup that takes a few minutes; future reinstalls" -ForegroundColor Gray
    Write-Host "will reuse the template and complete in seconds.)" -ForegroundColor Gray
    $ok = Build-Template -TemplateName $templateDistro -IsNoble:$Noble `
        -UbuntuVersion $ubuntuVersion -UbuntuLabel $ubuntuLabel `
        -ScriptDir $ScriptDir -IsElevated:$isElevated
    if (-not $ok) {
        Write-Host @"

If the failure looks AV-related, run the admin phase once to add a
permanent Defender exclusion and (if needed) install Windows Terminal:

    curl.exe -sLo `$env:TEMP\setup-wsl-maude.ps1 $GH_RAW/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File `$env:TEMP\setup-wsl-maude.ps1 -Admin$(if($Noble){' -Noble'})$(if($Release -ne 'main'){" -Release $Release"})

"@ -ForegroundColor Yellow
        exit 1
    }
    $installedTemplate = $templateDistro
}
Write-Host "Using template: $installedTemplate" -ForegroundColor Gray
$templateDistro = $installedTemplate

# ── Verify Maude isn't already installed ──
if (Test-WslDistro $DistroName) {
    Write-Host @"

$DistroName is already installed. To reinstall, run teardown first:

    curl.exe -sLo `$env:TEMP\teardown-wsl-maude.ps1 $GH_RAW/light/teardown-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File `$env:TEMP\teardown-wsl-maude.ps1

"@ -ForegroundColor Yellow
    exit 0
}

# ── Detect legacy install layout and refuse safely ──
# Old layout: %LOCALAPPDATA%\Maude was directly the WSL distro install dir
# (containing ext4.vhdx). New layout uses that path as a parent for OS\,
# Template\, and Data\. If we see an ext4.vhdx at the old top-level path,
# the user has a pre-restructure install and we must not write into it.
$legacyVhdx = Join-Path $env:LOCALAPPDATA "Maude\ext4.vhdx"
if (Test-Path -LiteralPath $legacyVhdx) {
    Write-Host @"

ERROR: Detected a pre-restructure Maude install at:
    $env:LOCALAPPDATA\Maude\ext4.vhdx

Maude now expects this layout under %LOCALAPPDATA%\Maude\:
    OS\        - WSL Maude distro
    Template\  - Ubuntu template distro
    Data\Maude - your project files (icon-bearing)

Run teardown first (it'll remove the old distro dir but won't touch any
Data\ folder you may have already created):

    curl.exe -sLo `$env:TEMP\teardown-wsl-maude.ps1 $GH_RAW/light/teardown-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File `$env:TEMP\teardown-wsl-maude.ps1

Then re-run this script.

"@ -ForegroundColor Red
    exit 1
}

# ── Step 1 (user): Export template + Import as Maude ──
Write-Host "`n[1/5] Importing $DistroName from '$templateDistro'..." -ForegroundColor Green
$rootfsTar = "$env:TEMP\ubuntu-$($ubuntuVersion -replace '\.','')_rootfs.tar"

Write-Host "Exporting template..."
wsl --export $templateDistro $rootfsTar
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: wsl --export failed." -ForegroundColor Red
    Write-Host "If Defender is interfering, re-run setup with -Admin to add an exclusion." -ForegroundColor Yellow
    exit 1
}

Write-Host "Importing as '$DistroName'..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
wsl --import $DistroName $InstallDir $rootfsTar --version 2
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: wsl --import failed." -ForegroundColor Red
    Write-Host "If Defender is interfering, re-run setup with -Admin to add an exclusion." -ForegroundColor Yellow
    exit 1
}
Remove-Item -LiteralPath $rootfsTar -ErrorAction SilentlyContinue
Write-Host "$DistroName imported." -ForegroundColor Gray

# ── Determine host folder location ──
# New layout (default): %LOCALAPPDATA%\Maude\Data\Maude
#   - Sits under medium-IL %LOCALAPPDATA%, so the desktop.ini icon mechanism
#     actually works (LocalLow's low-IL ACLs broke +S on the parent folder).
#   - Co-located with the OS\ and Template\ siblings under %LOCALAPPDATA%\Maude\.
# OneDrive: <OneDrive>\Maude (flat, OneDrive only ever holds user data).
$localFolder = Join-Path $env:LOCALAPPDATA "Maude\Data\Maude"

# Detect the legacy LocalLow layout. We don't migrate or delete — the user
# may have moved their data manually. Just inform.
$legacyLocalLow = Join-Path $env:USERPROFILE "AppData\LocalLow\Maude"
if (Test-Path -LiteralPath (Join-Path $legacyLocalLow "Projects")) {
    Write-Host ""
    Write-Host "Note: a legacy Maude folder was detected at:" -ForegroundColor Yellow
    Write-Host "  $legacyLocalLow" -ForegroundColor Yellow
    Write-Host "Maude no longer uses LocalLow. The new location is:" -ForegroundColor Yellow
    Write-Host "  $localFolder" -ForegroundColor Yellow
    Write-Host "Move any data you still need from the old folder; this script" -ForegroundColor Yellow
    Write-Host "will NOT touch the old location." -ForegroundColor Yellow
    Write-Host ""
}

if ($NoOneDrive) {
    $HostFolder = $localFolder
    $HostFolderSource = "Local (-NoOneDrive)"
} elseif ($OneDrive) {
    $odCandidates = Find-OneDriveMaudeFolder
    if ($odCandidates.Count -gt 0) {
        $HostFolder = $odCandidates[0].Path
        $HostFolderSource = "$($odCandidates[0].Source) (-OneDrive)"
    } else {
        Write-Host "WARNING: -OneDrive specified but no OneDrive folder found. Falling back to Local." -ForegroundColor Yellow
        $HostFolder = $localFolder
        $HostFolderSource = "Local (OneDrive not found)"
    }
} else {
    $localExists = Test-Path -LiteralPath (Join-Path $localFolder "Projects")
    $odCandidates = Find-OneDriveMaudeFolder
    $odExisting = $odCandidates | Where-Object { Test-Path -LiteralPath (Join-Path $_.Path "Projects") } | Select-Object -First 1

    if ($localExists) {
        $HostFolder = $localFolder
        $HostFolderSource = "Local (existing)"
    } elseif ($odExisting) {
        $HostFolder = $odExisting.Path
        $HostFolderSource = "$($odExisting.Source) (existing)"
    } else {
        $HostFolder = $localFolder
        $HostFolderSource = "Local"
    }
}

if ($HostFolderSource -match 'OneDrive') {
    Show-OneDriveSharingWarning -Path $HostFolder
}

# ── Step 2 (user): Create shared host folder + icon + Quick Access ──
Write-Host "`n[2/5] Setting up host folder ($HostFolderSource)..." -ForegroundColor Green
New-Item -ItemType Directory -Force -Path $HostFolder | Out-Null
$claudeDir   = Join-Path $HostFolder ".claude"
$kannaDir    = Join-Path $HostFolder ".kanna"
$projectsDir = Join-Path $HostFolder "Projects"
New-Item -ItemType Directory -Force -Path $claudeDir   | Out-Null
New-Item -ItemType Directory -Force -Path $kannaDir    | Out-Null
New-Item -ItemType Directory -Force -Path $projectsDir | Out-Null
Write-Host "Host folder: $HostFolder" -ForegroundColor Gray

# One-time scrub of any inherited Low Mandatory Integrity Level from the
# host folder (and parents under %LOCALAPPDATA%\Maude\). Necessary when a
# user migrates data from AppData\LocalLow — the low-IL label rides along
# with the moved files, and Explorer then refuses to honour desktop.ini,
# so the custom folder icon never renders.
#
# Gated by a marker file so this runs only once per machine. Delete the
# marker to re-trigger (e.g., after migrating more data from a low-IL
# location). The marker lives outside Data\ so teardown -IncludeTemplate
# preserves it.
$ilMarker = Join-Path $env:LOCALAPPDATA "Maude\.il-fixed"
if (($HostFolder -like "$env:LOCALAPPDATA\Maude\*") -and (-not (Test-Path -LiteralPath $ilMarker))) {
    $localData = Join-Path $env:LOCALAPPDATA "Maude\Data"
    foreach ($p in @($localData, $HostFolder)) {
        if (Test-Path -LiteralPath $p) {
            $il = (& icacls.exe "$p" 2>&1) -join "`n"
            if ($il -match 'Low Mandatory Level') {
                Write-Host "Stripping inherited low-integrity label from $p..." -ForegroundColor Gray
                & icacls.exe "$p" /setintegritylevel "(OI)(CI)Medium" /T 2>&1 | Out-Null
            }
        }
    }
    # Mark done so future reinstalls skip the icacls probe entirely.
    New-Item -ItemType File -Path $ilMarker -Force | Out-Null
}

$iconSrc = Join-Path $ScriptDir "maude.png"
if (Test-Path -LiteralPath $iconSrc) {
    try {
        $icoPath    = Join-Path $HostFolder "maude.ico"
        $desktopIni = Join-Path $HostFolder "desktop.ini"
        foreach ($f in @($icoPath, $desktopIni)) {
            if (Test-Path -LiteralPath $f) { attrib -h -s "$f" }
        }
        Convert-PngToIco $iconSrc $icoPath
        "[.ShellClassInfo]`r`nIconResource=$icoPath,0" | Set-Content -LiteralPath $desktopIni -Encoding Unicode
        attrib +h +s "$desktopIni"
        attrib +h +s "$icoPath"
        # Set ReadOnly + System on the parent folder so Explorer reads desktop.ini.
        # +R is the Microsoft-documented method and survives non-default ACLs
        # better than +S alone (the previous LocalLow location only had +S and
        # the attribute never stuck on a low-integrity folder).
        attrib +r +s "$HostFolder"
        # Verify the attribute landed; warn if not.
        $hostAttr = (Get-Item -LiteralPath $HostFolder -Force).Attributes.ToString()
        if ($hostAttr -notmatch 'ReadOnly') {
            Write-Host "WARNING: ReadOnly attribute did not stick on $HostFolder; icon may not appear." -ForegroundColor Yellow
            Write-Host "(folder attributes: $hostAttr)" -ForegroundColor Yellow
        } else {
            Write-Host "Folder icon set." -ForegroundColor Gray
        }
    } catch {
        Write-Host "Could not set folder icon: $_" -ForegroundColor Yellow
    }
}

try {
    $Shell = New-Object -ComObject Shell.Application
    $QuickAccess = $Shell.Namespace("shell:::{679f85cb-0220-4080-b29b-5540cc05aab6}")
    $isPinned = $false
    foreach ($item in $QuickAccess.Items()) {
        if ($item.Path -eq $HostFolder) { $isPinned = $true; break }
    }
    if (-not $isPinned) {
        $FolderToPin = $Shell.Namespace($HostFolder)
        if ($FolderToPin) {
            $FolderToPin.Self.InvokeVerb("pintohome")
            Write-Host "Pinned Maude folder to Quick Access." -ForegroundColor Gray
        }
    } else {
        Write-Host "Maude folder already pinned to Quick Access." -ForegroundColor Gray
    }
} catch {
    Write-Host "Could not pin to Quick Access: $_" -ForegroundColor Yellow
}

# ── Step 3 (user): Run root-bootstrap.sh inside Maude ──
# Runs unelevated, so the WSL-interop privilege escalation vector doesn't
# apply (parent wsl.exe is unprivileged → spawned Windows binaries are
# unprivileged too).
Write-Host "`n[3/5] Running root bootstrap..." -ForegroundColor Green

$filesToPipe = @(
    @{ Src = "root-bootstrap.sh";  Dst = "root-bootstrap.sh" }
    @{ Src = "maude";              Dst = "maude-launcher" }
)
foreach ($f in $filesToPipe) {
    $src = Join-Path $ScriptDir $f.Src
    if (Test-Path -LiteralPath $src) {
        Get-Content -LiteralPath $src -Raw | wsl -d $DistroName -u root -- bash -c "cat > /tmp/$($f.Dst) && sed -i 's/\r$//' /tmp/$($f.Dst) && chmod +x /tmp/$($f.Dst)"
    } else {
        Write-Host "ERROR: Required file '$($f.Src)' not found in $ScriptDir" -ForegroundColor Red
        exit 1
    }
}

# Push host folder path so root-bootstrap.sh can configure the fstab mount.
$HostFolder | wsl -d $DistroName -u root -- bash -c "cat > /tmp/maude-hostfolder && sed -i 's/\r$//' /tmp/maude-hostfolder"

wsl -d $DistroName -u root -- bash /tmp/root-bootstrap.sh $DefaultUser
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Root bootstrap failed." -ForegroundColor Red
    exit 1
}

# Restart so /etc/wsl.conf takes effect (default user, automount=false, interop=false).
wsl --terminate $DistroName

# ── Step 4 (user): Run maude-bootstrap.sh ──
# /tmp is cleared after wsl --terminate, so re-pipe.
Write-Host "`n[4/5] Running user bootstrap..." -ForegroundColor Green
$bootstrapSrc = Join-Path $ScriptDir "maude-bootstrap.sh"
if (-not (Test-Path -LiteralPath $bootstrapSrc)) {
    Write-Host "ERROR: Required file 'maude-bootstrap.sh' not found in $ScriptDir" -ForegroundColor Red
    exit 1
}
Get-Content -LiteralPath $bootstrapSrc -Raw | wsl -d $DistroName -u root -- bash -c "cat > /tmp/maude-bootstrap.sh && sed -i 's/\r$//' /tmp/maude-bootstrap.sh && chmod +x /tmp/maude-bootstrap.sh"
wsl -d $DistroName -u $DefaultUser -- bash /tmp/maude-bootstrap.sh
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: User bootstrap had errors." -ForegroundColor Yellow
}

# ── Step 5 (user): Windows Terminal profile + desktop shortcut ──
Write-Host "`n[5/5] Configuring Windows Terminal and desktop shortcut..." -ForegroundColor Green

$iconDst = Join-Path $InstallDir "maude.png"
if (Test-Path -LiteralPath $iconSrc) {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -LiteralPath $iconSrc -Destination $iconDst -Force
}

$wtSettingsPath = Find-WTSettingsPath
if ($wtSettingsPath -and (Test-Path -LiteralPath $wtSettingsPath)) {
    $wtJson = Get-Content -LiteralPath $wtSettingsPath -Raw | ConvertFrom-Json

    $wtJson | Add-Member -NotePropertyName 'copyOnSelect' -NotePropertyValue $true -Force

    # Keep one Maude profile (customized), hide all template profiles, drop dupes.
    $wtIconPath = if (Test-Path -LiteralPath $iconDst) { $iconDst -replace '\\', '/' } else { $null }
    $hasAutoProfile     = $false
    $hasTemplateProfile = $false
    $keepProfiles = @()
    for ($i = 0; $i -lt $wtJson.profiles.list.Count; $i++) {
        $p   = $wtJson.profiles.list[$i]
        $nm  = if ($p.PSObject.Properties['name'])   { $p.name }   else { '' }
        $src = if ($p.PSObject.Properties['source']) { $p.source } else { '' }

        if ($nm -eq $DistroName) {
            if ($src -ne '' -and -not $hasAutoProfile) {
                if ($wtIconPath) {
                    $wtJson.profiles.list[$i] | Add-Member -NotePropertyName 'icon' -NotePropertyValue $wtIconPath -Force
                }
                $wtJson.profiles.list[$i] | Add-Member -NotePropertyName 'hidden' -NotePropertyValue $false -Force
                $hasAutoProfile = $true
            } else {
                continue
            }
        }

        if ($nm -eq $templateDistro) {
            $wtJson.profiles.list[$i] | Add-Member -NotePropertyName 'hidden' -NotePropertyValue $true -Force
            $hasTemplateProfile = $true
        }

        $keepProfiles += $wtJson.profiles.list[$i]
    }
    $wtJson.profiles.list = $keepProfiles

    if (-not $hasAutoProfile) {
        $autoProfile = [PSCustomObject]@{
            name   = $DistroName
            source = "Windows.Terminal.Wsl"
            hidden = $false
        }
        if ($wtIconPath) {
            $autoProfile | Add-Member -NotePropertyName 'icon' -NotePropertyValue $wtIconPath -Force
        }
        $wtJson.profiles.list += $autoProfile
    }
    if (-not $hasTemplateProfile) {
        $templateStub = [PSCustomObject]@{
            name   = $templateDistro
            source = "Windows.Terminal.Wsl"
            hidden = $true
        }
        $wtJson.profiles.list += $templateStub
    }

    $wtJson | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $wtSettingsPath -Encoding UTF8
    Write-Host "Windows Terminal profile created for $DistroName." -ForegroundColor Gray
} else {
    Write-Host "Windows Terminal settings not found, skipping profile config." -ForegroundColor Gray
}

# Desktop shortcut
$desktopPath  = [Environment]::GetFolderPath('Desktop')
$shortcutFile = Join-Path $desktopPath "$DistroName.lnk"
$icoFile      = Join-Path $InstallDir "maude.ico"

if (Test-Path -LiteralPath $iconSrc) {
    try {
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        Convert-PngToIco $iconSrc $icoFile
        $shortcutIco = Join-Path $InstallDir "shortcut.ico"
        if (Test-Path -LiteralPath $shortcutIco) {
            Copy-Item -LiteralPath $icoFile -Destination $shortcutIco -Force
            Write-Host "Replaced shortcut.ico with Maude icon." -ForegroundColor Gray
        }
    } catch {
        Write-Host "Could not convert icon: $_" -ForegroundColor Yellow
    }
}

$distroGuid = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss\*" |
    Where-Object { $_.DistributionName -eq $DistroName }).PSChildName

$wtExe = (Get-Command wt.exe -ErrorAction SilentlyContinue).Source
if ($wtExe) {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($shortcutFile)
    $sc.TargetPath = $wtExe
    if ($distroGuid) {
        $sc.Arguments = "new-tab -- C:\Windows\System32\wsl.exe --distribution-id $distroGuid"
    } else {
        $sc.Arguments = "new-tab -- wsl -d $DistroName"
    }
    $sc.Description = "Open $DistroName in Windows Terminal"
    if (Test-Path -LiteralPath $icoFile) { $sc.IconLocation = "$icoFile,0" }
    $sc.Save()
    Write-Host "Desktop shortcut created: $shortcutFile" -ForegroundColor Gray
} else {
    Write-Host "wt.exe not found, skipping desktop shortcut." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Maude setup complete!" -ForegroundColor Cyan
Write-Host "Launch Maude from the desktop shortcut or Windows Terminal." -ForegroundColor Green
