#Requires -Version 5.1
<#
.SYNOPSIS
    Sets up a WSL2 Ubuntu dev environment named "Maude".

.DESCRIPTION
    Two-phase installer:

      Admin phase (-Admin, runs elevated):
        - Install WSL2 (and VM Platform feature) if missing
        - Import Ubuntu (24.04 or 26.04) as a WSL distro named "Maude"
        - Run root-bootstrap.sh inside WSL (user, mom, /etc/wsl.conf, fstab,
          PATH, sandbox mount)
        - Terminate WSL so wsl.conf takes effect (interop disabled)

      User phase (no flag, runs unelevated):
        - Install Windows Terminal (if missing)
        - Create the Maude shared folder (OneDrive or AppData\LocalLow),
          set its custom icon, pin to Quick Access
        - Run maude-bootstrap.sh inside WSL (dev-station, kanna, skills,
          Claude Code config, maude launcher)
        - Configure the Windows Terminal profile and desktop shortcut

    The two phases are deliberately separated. The user phase configures
    HKCU and the user's profile (Windows Terminal settings, desktop
    shortcut, OneDrive folder), so it MUST run as the actual end user.
    The admin phase performs the privileged operations and the trusted
    parts of the WSL bootstrap.

    The distro is sandboxed: automatic Windows drive mounting is
    disabled, and only the shared Maude folder is mounted into
    /home/maude/Maude via drvfs + /etc/fstab.

.NOTES
    Typical install (one machine, single admin user):

        # 1. From an elevated PowerShell:
        Set-ExecutionPolicy Bypass -Scope Process -Force
        .\setup-wsl-maude.ps1 -Admin

        # 2. From a NON-elevated PowerShell (after admin phase exits):
        .\setup-wsl-maude.ps1                # Ubuntu 26.04, AppData\LocalLow
        .\setup-wsl-maude.ps1 -OneDrive      # Shared folder in OneDrive
        .\setup-wsl-maude.ps1 -NoOneDrive    # Force AppData\LocalLow
        .\setup-wsl-maude.ps1 -Noble         # Ubuntu 24.04 instead of 26.04

    Verified install (recommended in production):

        .\setup-wsl-maude.ps1 -Admin -Release v0.4.0

        # Then user phase:
        .\setup-wsl-maude.ps1 -Release v0.4.0

    -Release pins downloads to a specific git tag and verifies every
    downloaded file's SHA-256 against light/checksums.txt at that tag.
    The default ("main") is for development and skips checksum verification.
#>

param(
    [string]$DistroName  = "Maude",
    [string]$DefaultUser = "maude",
    [string]$InstallDir  = "$env:LOCALAPPDATA\Maude",
    [switch]$OneDrive,
    [switch]$NoOneDrive,
    [switch]$Noble,
    [switch]$Admin,
    [string]$Release     = "main"
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
# Supports Store, Preview, and non-Store (winget/scoop) installs.
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
# wsl -l -q has UTF-16/null-byte encoding issues.
# wsl --list --verbose is more robust: parse the NAME column directly.
function Test-WslDistro([string]$name) {
    $lines = (wsl --list --verbose 2>&1) -replace "`0", ""
    foreach ($line in $lines) {
        $fields = ($line -replace '^\*?\s+', '').Trim() -split '\s+'
        if ($fields[0] -ieq $name) { return $true }
    }
    return $false
}

# ── Helper: convert a PNG file to ICO format ──
# Writes a valid ICO container that embeds the PNG data directly.
# Works with any PNG size; Explorer picks the best fit.
function Convert-PngToIco($pngPath, $icoPath) {
    $pngBytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $pngPath).Path)

    Add-Type -AssemblyName System.Drawing
    $img = [System.Drawing.Image]::FromFile((Resolve-Path -LiteralPath $pngPath).Path)
    $w = [Math]::Min($img.Width, 256)
    $h = [Math]::Min($img.Height, 256)
    $img.Dispose()

    # ICO uses 0 to mean 256
    $wb = if ($w -eq 256) { [byte]0 } else { [byte]$w }
    $hb = if ($h -eq 256) { [byte]0 } else { [byte]$h }

    $ms = New-Object System.IO.MemoryStream
    $bw = New-Object System.IO.BinaryWriter($ms)

    # ICO header: reserved(2) + type=1(2) + count=1(2)
    $bw.Write([UInt16]0)
    $bw.Write([UInt16]1)
    $bw.Write([UInt16]1)

    # Directory entry: w(1) h(1) colors(1) reserved(1) planes(2) bpp(2) size(4) offset(4)
    $bw.Write($wb)
    $bw.Write($hb)
    $bw.Write([byte]0)
    $bw.Write([byte]0)
    $bw.Write([UInt16]1)
    $bw.Write([UInt16]32)
    $bw.Write([UInt32]$pngBytes.Length)
    $bw.Write([UInt32]22)

    $bw.Write($pngBytes)
    $bw.Flush()

    [System.IO.File]::WriteAllBytes($icoPath, $ms.ToArray())
    $bw.Dispose()
    $ms.Dispose()
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
# Triggered when the chosen host folder is inside OneDrive. The cybersec
# review flagged OneDrive sharing as a prompt-injection amplifier: anyone
# with edit access to the Maude folder (or any ancestor) can inject files
# the AI will treat as instructions.
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
    Write-Host "  -NoOneDrive to use AppData\LocalLow instead." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Yellow
    Start-Sleep -Seconds 8
}

# ── Helper: load checksums.txt into a hashtable ──
# Parses standard `sha256sum` output: one line per file, "<hash> <filename>"
# (with optional binary-mode "*" prefix on the filename).
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

# ── Elevation gating ────────────────────────────────────────────────
# -Admin   ⇒ MUST run elevated; self-elevate if not already
# no flag  ⇒ MUST NOT run elevated (would write user state into admin's
#            profile — Windows Terminal settings, desktop shortcut,
#            OneDrive detection)

$isElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if ($Admin -and -not $isElevated) {
    Write-Host "-Admin specified but PowerShell is not elevated. Self-elevating..." -ForegroundColor Yellow
    try {
        $extraArgs = " -Admin"
        if ($OneDrive)             { $extraArgs += " -OneDrive" }
        if ($NoOneDrive)           { $extraArgs += " -NoOneDrive" }
        if ($Noble)                { $extraArgs += " -Noble" }
        if ($Release -ne 'main')   { $extraArgs += " -Release $Release" }
        if ($PSCommandPath) {
            Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`"$extraArgs"
        } else {
            # Running via iex — re-download the script into the elevated TEMP and run it.
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
  * Re-run with -Admin to perform admin-phase setup (WSL install, distro
    import, root bootstrap).

"@ -ForegroundColor Red
    exit 1
}

# ── ScriptDir: ALWAYS the current process's own TEMP ──────────────────
# Cybersec mitigation (TOCTOU): the previous version used $PSScriptRoot
# when the script was launched from disk, which could be a user-writable
# directory like Downloads. A non-admin user could swap root-bootstrap.sh
# during the install window to escalate privileges via WSL interop.
#
# Resolving unconditionally to $env:TEMP\maude-setup ensures the bootstrap
# files only ever come from the elevated identity's TEMP folder (in admin
# phase) or the unelevated user's own TEMP (in user phase) — never from a
# location another local user can write to.
$ScriptDir = Join-Path $env:TEMP "maude-setup"
New-Item -ItemType Directory -Force -Path $ScriptDir | Out-Null

# ── Download bootstrap files (with checksum verification when pinned) ──
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
# ADMIN PHASE — runs elevated only
# =====================================================================
if ($Admin) {

    Write-Host ""
    Write-Host "=== Maude Setup: Admin Phase ===" -ForegroundColor Cyan
    Write-Host "(WSL install, distro import, root bootstrap)" -ForegroundColor DarkGray

    # ── Free disk space check ──
    $cDrive = Get-PSDrive -Name C
    $freeGB = [math]::Round($cDrive.Free / 1GB, 1)
    Write-Host "Free disk space on C: drive: ${freeGB} GB" -ForegroundColor Cyan

    $removeTplAfterInstall = $false
    if ($freeGB -lt 5) {
        Write-Host "`nWARNING: Very low disk space (${freeGB} GB free)!" -ForegroundColor Red
        Write-Host "Maude may not function properly with less than 5 GB free." -ForegroundColor Red
        Write-Host "Press Ctrl+C within 10 seconds to cancel installation..." -ForegroundColor Yellow
        Start-Sleep -Seconds 10
        $removeTplAfterInstall = $true
    } elseif ($freeGB -lt 10) {
        Write-Host "NOTE: Less than 10 GB free. The Ubuntu template will be removed after" -ForegroundColor Yellow
        Write-Host "install to free disk space (reinstalls will take longer)." -ForegroundColor Yellow
        $removeTplAfterInstall = $true
    }

    # ── Step 1 (admin): Install WSL2 ──
    Write-Host "`n[1/3] Checking WSL..." -ForegroundColor Green
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

    # ── Parse the package list (used by template install below) ──
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

    # ── Step 2 (admin): Import $ubuntuLabel as "Maude" ──
    Write-Host "`n[2/3] Checking $DistroName WSL distro..." -ForegroundColor Green
    if (Test-WslDistro $DistroName) {
        Write-Host @"

$DistroName is already installed. To reinstall, run teardown first:

    curl.exe -sLo `$env:TEMP\teardown-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/$Release/light/teardown-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File `$env:TEMP\teardown-wsl-maude.ps1

"@ -ForegroundColor Yellow
        exit 0
    }

    # Persistent template distro with all packages pre-installed.
    # Avoids re-downloading from the Microsoft Store and re-installing
    # packages on every rebuild. teardown-wsl-maude.ps1 -IncludeTemplate removes it.
    $rootfsTar = "$env:TEMP\ubuntu-$($ubuntuVersion -replace '\.','')_rootfs.tar"

    if (-not (Test-WslDistro $templateDistro)) {
        Write-Host "Installing '$templateDistro' (first time only)..."

        # Detect --name support by parsing wsl --help output.
        # wsl.exe outputs UTF-16 with spaces between chars; strip nulls before matching.
        $wslHelp = (wsl --help 2>&1) -replace "`0", "" -join "`n"
        $hasNameFlag = $wslHelp -match '--name'
        Write-Host "WSL --name flag: $(if ($hasNameFlag) {'supported'} else {'not supported'})" -ForegroundColor Gray

        $installed = $false

        if ($hasNameFlag) {
            # ── Path A: Modern WSL with --name ──
            # Install from Store directly as the template name.
            # No risk of overwriting existing distros.
            $onlineList = (wsl --list --online 2>&1) -join "`n"
            $candidates = @()
            if ($onlineList -match "Ubuntu-$ubuntuVersion") { $candidates += "Ubuntu-$ubuntuVersion" }
            # Only fall back to plain "Ubuntu" for 24.04 (-Noble).
            # For 26.04 (default), plain "Ubuntu" would install the wrong version.
            if ($Noble) {
                if ($onlineList -match 'Ubuntu\b') { $candidates += "Ubuntu" }
                if ($candidates.Count -eq 0) { $candidates = @("Ubuntu-$ubuntuVersion", "Ubuntu") }
            }
            if ($candidates.Count -eq 0) { $candidates = @("Ubuntu-$ubuntuVersion") }

            foreach ($distro in $candidates) {
                Write-Host "Trying Store install: '$distro' as '$templateDistro'..." -ForegroundColor Gray
                $out = (wsl --install -d $distro --name $templateDistro --no-launch 2>&1) -replace "`0","" -join "`n"
                if ($LASTEXITCODE -eq 0) { $installed = $true; break }
                # Bail immediately on system-level errors (don't retry other distros)
                if ($out -match 'HCS_E_HYPERV_NOT_INSTALLED|WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED') {
                    Write-Host "Hyper-V/VM Platform not available, skipping Store install." -ForegroundColor Yellow
                    # Clean up partial install — only terminate the specific distro,
                    # not all of WSL (other user distros may be running).
                    wsl --terminate $templateDistro 2>&1 | Out-Null
                    wsl --unregister $templateDistro 2>&1 | Out-Null
                    break
                }
                # Ghost entry? Clear and retry once
                wsl --unregister $templateDistro 2>&1 | Out-Null
                wsl --install -d $distro --name $templateDistro --no-launch 2>$null
                if ($LASTEXITCODE -eq 0) { $installed = $true; break }
                Write-Host "'$distro' not available via Store, trying next..." -ForegroundColor Yellow
            }
        }

        # ── Path B: Download from Canonical + wsl --import ──
        # Used when: --name not supported (older WSL), or Store install failed.
        # Safe: wsl --import always accepts a custom name, never overwrites existing distros.
        if (-not $installed) {
            if ($hasNameFlag) {
                Write-Host "Store install failed. Downloading from Canonical..." -ForegroundColor Yellow
            } else {
                Write-Host "Downloading $ubuntuLabel WSL image from Canonical..." -ForegroundColor Yellow
            }
            if ($Noble) {
                $rootfsUrl = "https://releases.ubuntu.com/noble/ubuntu-24.04.4-wsl-amd64.wsl"
            } else {
                $rootfsUrl = "https://releases.ubuntu.com/resolute/ubuntu-26.04-wsl-amd64.wsl"
            }
            $rootfsFile = Join-Path $env:TEMP "ubuntu-$ubuntuVersion-wsl-amd64.wsl"
            Write-Host "Downloading ~375 MB (this may take a few minutes)..."
            curl.exe -L -o $rootfsFile "$rootfsUrl"
            if (-not (Test-Path -LiteralPath $rootfsFile) -or (Get-Item -LiteralPath $rootfsFile).Length -lt 100MB) {
                Write-Host "ERROR: Failed to download Ubuntu WSL image." -ForegroundColor Red
                exit 1
            }
            # Temporarily exclude the downloaded file and import directory from
            # Windows Defender real-time scanning to prevent file locking during import.
            $defenderExclusions = @($rootfsFile, (Join-Path $env:LOCALAPPDATA "Maude-Template"))
            foreach ($excl in $defenderExclusions) {
                Add-MpPreference -ExclusionPath $excl -ErrorAction SilentlyContinue
            }
            # Clean up any ghost registration from failed Store installs.
            # Only terminate the specific distro — don't kill other running WSL instances.
            wsl --terminate $templateDistro 2>&1 | Out-Null
            wsl --unregister $templateDistro 2>&1 | Out-Null
            # Remove stale directory from failed Store installs (ext4.vhdx)
            $tplDir = Join-Path $env:LOCALAPPDATA "Maude-Template"
            if (Test-Path -LiteralPath $tplDir) {
                Start-Sleep -Seconds 2
                Remove-Item -LiteralPath $tplDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            # If directory is still locked, escalate: stop LxssManager to release all handles
            if (Test-Path -LiteralPath $tplDir) {
                Write-Host "Files locked. Restarting WSL service to release locks..." -ForegroundColor Yellow
                Stop-Service LxssManager -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 3
                Remove-Item -LiteralPath $tplDir -Recurse -Force -ErrorAction SilentlyContinue
                Start-Service LxssManager -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 3
            }
            New-Item -ItemType Directory -Force -Path $tplDir | Out-Null

            # Try WSL2 first; fall back to WSL1 if Hyper-V/VM Platform unavailable.
            # WSL1 runs without virtualization (works on VMs without nested virt).
            $wslVersion = 2
            Write-Host "Importing as '$templateDistro' (WSL $wslVersion)..."
            wsl --import $templateDistro $tplDir $rootfsFile --version $wslVersion
            if ($LASTEXITCODE -ne 0) {
                Write-Host "WSL2 import failed. Trying WSL1 (no virtualization needed)..." -ForegroundColor Yellow
                wsl --unregister $templateDistro 2>&1 | Out-Null
                if (Test-Path -LiteralPath $tplDir) {
                    Remove-Item -LiteralPath $tplDir -Recurse -Force -ErrorAction SilentlyContinue
                }
                New-Item -ItemType Directory -Force -Path $tplDir | Out-Null
                $wslVersion = 1
                wsl --import $templateDistro $tplDir $rootfsFile --version $wslVersion
            }
            Remove-Item -LiteralPath $rootfsFile -ErrorAction SilentlyContinue
            # Remove the temporary Defender exclusions
            foreach ($excl in $defenderExclusions) {
                Remove-MpPreference -ExclusionPath $excl -ErrorAction SilentlyContinue
            }
            if (-not (Test-WslDistro $templateDistro)) {
                Write-Host "ERROR: wsl --import failed." -ForegroundColor Red
                Write-Host "If on a VM, ensure nested virtualization is enabled for WSL2," -ForegroundColor Yellow
                Write-Host "or check that WSL1 is supported on this system." -ForegroundColor Yellow
                exit 1
            }
            Write-Host "'$templateDistro' imported as WSL$wslVersion." -ForegroundColor Gray
            $installed = $true
        }

        # Verify WSL is actually operational (catches post-upgrade reboot needed)
        wsl -d $templateDistro -- echo ok 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "`nWSL was just installed/upgraded and needs a reboot before continuing." -ForegroundColor Yellow
            Write-Host "After rebooting, re-run this script with -Admin." -ForegroundColor Yellow
            Read-Host "Press Enter to exit"
            exit
        }

        # Install packages into the template.
        Write-Host "Installing packages into template (this takes a few minutes)..."
        if ($packageList) {
            # Build the install script with LF line endings and write via base64 to avoid
            # PowerShell's pipe re-adding CRLF when writing to WSL stdin.
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
            wsl -d $templateDistro -u root -- bash -c "echo $installScriptB64 | base64 -d > /tmp/install-pkgs.sh && chmod +x /tmp/install-pkgs.sh"
            $packageList | wsl -d $templateDistro -u root -- bash /tmp/install-pkgs.sh
            if ($LASTEXITCODE -ne 0) {
                Write-Host "WARNING: Some packages may have failed to install." -ForegroundColor Yellow
            }
        }
        Write-Host "'$templateDistro' created with packages." -ForegroundColor Gray
    } else {
        Write-Host "Using existing '$templateDistro' (fast path)." -ForegroundColor Gray
    }

    # Export template and import as Maude
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
    Remove-Item -LiteralPath $rootfsTar -ErrorAction SilentlyContinue
    Write-Host "$DistroName imported from '$templateDistro'." -ForegroundColor Gray

    # ── Step 3 (admin): Run root-bootstrap.sh ──
    # The host-folder path is decided in the user phase, so we bootstrap WSL
    # without it and let the user phase write the fstab entry on its first run.
    # root-bootstrap.sh tolerates missing /tmp/maude-hostfolder.
    Write-Host "`n[3/3] Running root bootstrap..." -ForegroundColor Green

    # Pipe files into the distro's /tmp via stdin — automount is disabled so
    # /mnt/c/ paths are not available.
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

    # Run root-bootstrap.sh (no host-folder argument — user phase writes the fstab entry).
    wsl -d $DistroName -u root -- bash /tmp/root-bootstrap.sh $DefaultUser
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Root bootstrap failed." -ForegroundColor Red
        exit 1
    }

    # Restart so /etc/wsl.conf takes effect (default user + automount disabled + interop off).
    # After this point, WSL interop into Windows is disabled — closing the
    # admin-elevated TOCTOU window for any subsequent invocations.
    wsl --terminate $DistroName

    # Cleanup: drop the template if we're tight on disk
    if ($removeTplAfterInstall -and (Test-WslDistro $templateDistro)) {
        Write-Host "`nRemoving Ubuntu template to free disk space (low disk: ${freeGB} GB)..." -ForegroundColor Yellow
        wsl --unregister $templateDistro 2>&1 | Out-Null
        Write-Host "Template removed. Note: future reinstalls will take longer." -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "=============================================================" -ForegroundColor Cyan
    Write-Host "  Admin phase complete!" -ForegroundColor Cyan
    Write-Host "=============================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Next: close this elevated window and run the user phase from" -ForegroundColor White
    Write-Host "a NON-elevated PowerShell:" -ForegroundColor White
    Write-Host ""
    Write-Host "    curl.exe -sLo `$env:TEMP\setup-wsl-maude.ps1 $GH_RAW/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File `$env:TEMP\setup-wsl-maude.ps1$(if($OneDrive){' -OneDrive'})$(if($NoOneDrive){' -NoOneDrive'})$(if($Noble){' -Noble'})$(if($Release -ne 'main'){" -Release $Release"})" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "The user phase configures the Maude folder, Windows Terminal" -ForegroundColor White
    Write-Host "profile, desktop shortcut, and runs the user-level bootstrap." -ForegroundColor White
    Write-Host ""
    Read-Host "Press Enter to close this window"
    exit
}

# =====================================================================
# USER PHASE — runs unelevated
# =====================================================================
Write-Host ""
Write-Host "=== Maude Setup: User Phase ===" -ForegroundColor Cyan
Write-Host "(Host folder, Windows Terminal, desktop shortcut, user bootstrap)" -ForegroundColor DarkGray

# ── Verify the Maude distro exists (admin phase must have run first) ──
if (-not (Test-WslDistro $DistroName)) {
    Write-Host @"

ERROR: $DistroName WSL distro is not registered.

The admin phase has not been run yet. From an ELEVATED PowerShell:

    curl.exe -sLo `$env:TEMP\setup-wsl-maude.ps1 $GH_RAW/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File `$env:TEMP\setup-wsl-maude.ps1 -Admin$(if($Noble){' -Noble'})$(if($Release -ne 'main'){" -Release $Release"})

Then re-run this user-phase command.

"@ -ForegroundColor Red
    exit 1
}

# ── Step 1 (user): Install Windows Terminal ──
Write-Host "`n[1/4] Checking Windows Terminal..." -ForegroundColor Green
$wtPresent = (Get-Command wt.exe -ErrorAction SilentlyContinue) -or
             (Get-AppxPackage -Name "Microsoft.WindowsTerminal" -ErrorAction SilentlyContinue)
if ($wtPresent) {
    Write-Host "Windows Terminal is already installed." -ForegroundColor Gray
} else {
    $wtInstalled = $false
    # Method 1: winget
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing Windows Terminal via winget..."
        winget install --id Microsoft.WindowsTerminal --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 0) { $wtInstalled = $true }
    }
    # Method 2: AppX store registration
    if (-not $wtInstalled) {
        Write-Host "Trying AppX store registration..." -ForegroundColor Yellow
        try {
            Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.WindowsTerminal_8wekyb3d8bbwe -ErrorAction Stop
            $wtInstalled = $true
        } catch {
            Write-Host "AppX registration not available." -ForegroundColor Yellow
        }
    }
    # Method 3: Direct download from GitHub (Windows Server, no Store)
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

# ── Determine host folder location ──
# -NoOneDrive → always AppData\LocalLow\Maude
# -OneDrive   → always OneDrive (Business > Personal > generic)
# Neither     → scan for existing Maude folders on disk:
#   - Found in both OneDrive and LocalLow → pick LocalLow
#   - Found only in OneDrive → pick OneDrive
#   - Found only in LocalLow → pick LocalLow
#   - Found nowhere → pick LocalLow (new install default)
$localLowFolder = Join-Path $env:USERPROFILE "AppData\LocalLow\Maude"

if ($NoOneDrive) {
    $HostFolder = $localLowFolder
    $HostFolderSource = "LocalLow (-NoOneDrive)"
} elseif ($OneDrive) {
    $odCandidates = Find-OneDriveMaudeFolder
    if ($odCandidates.Count -gt 0) {
        $HostFolder = $odCandidates[0].Path
        $HostFolderSource = "$($odCandidates[0].Source) (-OneDrive)"
    } else {
        Write-Host "WARNING: -OneDrive specified but no OneDrive folder found. Using LocalLow." -ForegroundColor Yellow
        $HostFolder = $localLowFolder
        $HostFolderSource = "LocalLow (OneDrive not found)"
    }
} else {
    $localLowExists = Test-Path -LiteralPath (Join-Path $localLowFolder "Projects")
    $odCandidates = Find-OneDriveMaudeFolder
    $odExisting = $odCandidates | Where-Object { Test-Path -LiteralPath (Join-Path $_.Path "Projects") } | Select-Object -First 1

    if ($localLowExists) {
        $HostFolder = $localLowFolder
        $HostFolderSource = "LocalLow (existing)"
    } elseif ($odExisting) {
        $HostFolder = $odExisting.Path
        $HostFolderSource = "$($odExisting.Source) (existing)"
    } else {
        $HostFolder = $localLowFolder
        $HostFolderSource = "LocalLow"
    }
}

# OneDrive sharing risk warning (cybersec finding)
if ($HostFolderSource -match 'OneDrive') {
    Show-OneDriveSharingWarning -Path $HostFolder
}

# ── Step 2 (user): Create shared host folder + icon + Quick Access ──
Write-Host "`n[2/4] Setting up host folder ($HostFolderSource)..." -ForegroundColor Green
New-Item -ItemType Directory -Force -Path $HostFolder | Out-Null
$claudeDir   = Join-Path $HostFolder ".claude"
$kannaDir    = Join-Path $HostFolder ".kanna"
$projectsDir = Join-Path $HostFolder "Projects"
New-Item -ItemType Directory -Force -Path $claudeDir   | Out-Null
New-Item -ItemType Directory -Force -Path $kannaDir    | Out-Null
New-Item -ItemType Directory -Force -Path $projectsDir | Out-Null
Write-Host "Host folder: $HostFolder" -ForegroundColor Gray

# Custom folder icon (PNG → ICO conversion for desktop.ini)
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
        attrib +s "$HostFolder"
        Write-Host "Folder icon set." -ForegroundColor Gray
    } catch {
        Write-Host "Could not set folder icon: $_" -ForegroundColor Yellow
    }
}

# Pin Maude folder to Quick Access in File Explorer
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

# Push the host folder path into WSL so root-bootstrap can write the fstab entry.
# We re-run a small fragment of root-bootstrap from /tmp; root-bootstrap.sh is
# idempotent for the fstab section.
$HostFolder | wsl -d $DistroName -u root -- bash -c "cat > /tmp/maude-hostfolder && sed -i 's/\r$//' /tmp/maude-hostfolder"

# Re-pipe root-bootstrap.sh (admin phase already ran it, but /tmp was cleared by
# wsl --terminate). We invoke it again with the host folder argument; the script
# is idempotent and will only update the fstab/mount.
$rootBootstrapSrc = Join-Path $ScriptDir "root-bootstrap.sh"
if (Test-Path -LiteralPath $rootBootstrapSrc) {
    Get-Content -LiteralPath $rootBootstrapSrc -Raw | wsl -d $DistroName -u root -- bash -c "cat > /tmp/root-bootstrap.sh && sed -i 's/\r$//' /tmp/root-bootstrap.sh && chmod +x /tmp/root-bootstrap.sh"
    wsl -d $DistroName -u root -- bash /tmp/root-bootstrap.sh $DefaultUser 2>&1 | Out-Null
}

# ── Step 3 (user): Run maude-bootstrap.sh ──
Write-Host "`n[3/4] Running user bootstrap..." -ForegroundColor Green
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

# ── Step 4 (user): WT profile + desktop shortcut ──
Write-Host "`n[4/4] Configuring Windows Terminal and desktop shortcut..." -ForegroundColor Green

$iconDst = Join-Path $InstallDir "maude.png"
if (Test-Path -LiteralPath $iconSrc) {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -LiteralPath $iconSrc -Destination $iconDst -Force
}

$wtSettingsPath = Find-WTSettingsPath
if ($wtSettingsPath -and (Test-Path -LiteralPath $wtSettingsPath)) {
    $wtJson = Get-Content -LiteralPath $wtSettingsPath -Raw | ConvertFrom-Json

    # Enable copy-on-select: marking text copies it to clipboard automatically
    $wtJson | Add-Member -NotePropertyName 'copyOnSelect' -NotePropertyValue $true -Force

    # WT auto-generates profiles for WSL distros using two different source
    # strings: "Windows.Terminal.Wsl" (older WT) and "Microsoft.WSL" (newer WT).
    # This can produce duplicate entries.  We keep exactly one Maude profile
    # (customized with our icon), hide all template profiles, and remove
    # everything else with a matching name (stale manual profiles, duplicates).
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
        # Overwrite the WSL-generated shortcut icon with ours
        $shortcutIco = Join-Path $InstallDir "shortcut.ico"
        if (Test-Path -LiteralPath $shortcutIco) {
            Copy-Item -LiteralPath $icoFile -Destination $shortcutIco -Force
            Write-Host "Replaced shortcut.ico with Maude icon." -ForegroundColor Gray
        }
    } catch {
        Write-Host "Could not convert icon: $_" -ForegroundColor Yellow
    }
}

# Read the distro's distribution-id from the WSL registry — what WT uses internally.
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
