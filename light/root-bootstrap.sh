#!/bin/bash
# root-bootstrap.sh — Runs as root inside the Maude WSL distro.
# Sets up the system: user, mom, PATH, hushlogin, packages, sandbox mount.
#
# Usage:  root-bootstrap.sh <username>
# Packages are read from stdin (one per line) if provided.
# Host folder path is read from /tmp/maude-hostfolder if present.
set -e
export DEBIAN_FRONTEND=noninteractive
export TERM=dumb

USERNAME="${1:?Usage: root-bootstrap.sh <username>}"

# Read package list from stdin immediately (before any command can consume it)
# Strip \r — PowerShell pipes CRLF even after -replace on the PS side.
PACKAGES=""
if [[ ! -t 0 ]]; then
    PACKAGES=$(cat | tr -d '\r')
fi

echo "=== Maude root bootstrap ==="

# ── Prevent dpkg from trying to start services (no init in WSL) ──────
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod +x /usr/sbin/policy-rc.d

# ── Base packages + enable universe repo ──────────────────────────────
echo "Waiting for network..."
for i in 1 2 3 4 5; do
    apt-get update -q && break
    echo "apt-get update failed (attempt $i/5), retrying in 3s..."
    sleep 3
done
apt-get install -y -q sudo curl git ca-certificates software-properties-common
echo "Enabling universe repository..."
add-apt-repository -y universe
apt-get update -q

# ── Create user ───────────────────────────────────────────────────────
if ! id "$USERNAME" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$USERNAME"
    passwd -d "$USERNAME"
    echo "User '$USERNAME' created."
else
    echo "User '$USERNAME' already exists."
fi

# ── Sudoers: allow reboot/shutdown without password ──────────────────
printf '%s ALL=(ALL) NOPASSWD: /sbin/reboot, /sbin/shutdown, /sbin/poweroff\n' \
    "$USERNAME" > /etc/sudoers.d/maude-reboot
chmod 440 /etc/sudoers.d/maude-reboot

# ── Install mom (setuid package manager) ──────────────────────────────
# The mom-inst .deb package handles all setup: creates group, sets setuid,
# creates config dir, conf file, deny list, and log file.
# Remove old "mom" package if present (conflicts with mom-inst).
if dpkg -s mom >/dev/null 2>&1 && ! dpkg -s mom-inst >/dev/null 2>&1; then
    echo "Removing old 'mom' package before installing mom-inst..."
    dpkg --purge mom
fi
if ! dpkg -s mom-inst >/dev/null 2>&1; then
    _arch=$(dpkg --print-architecture 2>/dev/null || echo "amd64")
    _ver="0.2.19"
    # Detect Ubuntu version to pick the right .deb asset (2204, 2404, 2604).
    # Fall back to 2404 for anything unrecognised.
    _ubuntu_ver=$(. /etc/os-release 2>/dev/null && echo "${VERSION_ID}" | tr -d '.')
    case "$_ubuntu_ver" in
        2204) _os_tag="ubuntu-2204" ;;
        2604) _os_tag="ubuntu-2604" ;;
        *)    _os_tag="ubuntu-2404" ;;
    esac
    _deb="mom-inst_${_ver}_${_os_tag}_${_arch}.deb"
    _base="https://github.com/dirkpetersen/mom/releases/download/v${_ver}"
    echo "Installing mom-inst package v${_ver} (${_os_tag}, ${_arch})..."
    curl -fsSL "${_base}/${_deb}"        -o "/tmp/${_deb}"
    curl -fsSL "${_base}/SHA256SUMS"     -o /tmp/mom-SHA256SUMS
    # Verify checksum before installing — abort if it doesn't match.
    if ! ( cd /tmp && grep " ${_deb}\$" mom-SHA256SUMS | sha256sum -c --status ); then
        echo "ERROR: SHA-256 mismatch for ${_deb} — refusing to install."
        echo "       expected: $(grep " ${_deb}\$" /tmp/mom-SHA256SUMS | awk '{print $1}')"
        echo "       got:      $(sha256sum "/tmp/${_deb}" | awk '{print $1}')"
        rm -f "/tmp/${_deb}" /tmp/mom-SHA256SUMS
        exit 1
    fi
    echo "Checksum OK for ${_deb}."
    dpkg -i "/tmp/${_deb}"
    rm -f "/tmp/${_deb}" /tmp/mom-SHA256SUMS
    unset _ubuntu_ver _os_tag _deb _base
    echo "mom installed via mom-inst package."
fi
usermod -aG mom "$USERNAME" 2>/dev/null || true

# ── Read host folder path (written by setup-wsl-maude.ps1) ──────────
HOST_FOLDER=""
if [[ -f /tmp/maude-hostfolder ]]; then
    HOST_FOLDER=$(cat /tmp/maude-hostfolder)
fi

# ── WSL config (default user + sandbox: disable Windows drive mounts) ─
# Automatic mounting of Windows drives (C:\, D:\, etc.) is disabled for
# sandbox isolation.  Only the shared Maude folder is mounted via fstab.
cat > /etc/wsl.conf << WSLEOF
[boot]
systemd = true

[user]
default=$USERNAME

[automount]
enabled = false
mountFsTab = true

[interop]
enabled = false
appendWindowsPath = false
WSLEOF

# Ensure login starts in the user's home directory (imported distros may default to /)
usermod -d "/home/$USERNAME" "$USERNAME" 2>/dev/null || true

# ── Per-user systemd manager ─────────────────────────────────────────
# wsl.exe runs `systemctl start user@<uid>.service` on every shell launch
# when systemd=true is set in /etc/wsl.conf. If the unit fails for any
# reason (missing dbus-user-session, or the unit is masked), wsl.exe
# prints "Failed to start the systemd user session" on every cold start.
# dbus-user-session is in packages/ubuntu-packages.yaml so the unit
# should start cleanly. Defensively unmask in case a previous bootstrap
# ran with an older version that masked it.
systemctl unmask user@.service >/dev/null 2>&1 || true

# ── Sandbox mount: host folder → /home/<user>/Maude via drvfs ────────
if [[ -n "$HOST_FOLDER" ]]; then
    MOUNT_POINT="/home/$USERNAME/Maude"
    mkdir -p "$MOUNT_POINT"
    chown "$USERNAME:$USERNAME" "$MOUNT_POINT"

    # Escape spaces as \040 for fstab (backslashes are literal for drvfs paths)
    FSTAB_SRC=$(echo "$HOST_FOLDER" | sed 's/ /\\040/g')
    USER_UID=$(id -u "$USERNAME")
    USER_GID=$(id -g "$USERNAME")

    # Add drvfs mount to /etc/fstab (idempotent)
    if ! grep -q "$MOUNT_POINT" /etc/fstab 2>/dev/null; then
        printf '%s %s drvfs defaults,uid=%s,gid=%s 0 0\n' \
            "$FSTAB_SRC" "$MOUNT_POINT" "$USER_UID" "$USER_GID" >> /etc/fstab
    fi
    # Note: the drvfs mount only activates after WSL restart (step 6).
    # The .claude and Projects dirs are pre-created on the Windows side
    # by setup-wsl-maude.ps1 so they exist when the mount activates.
    echo "Sandbox mount configured: $HOST_FOLDER -> $MOUNT_POINT"
else
    echo "WARNING: No host folder path found, skipping sandbox mount."
fi

# ── Hushlogin (suppress Ubuntu MOTD) ─────────────────────────────────
touch "/home/$USERNAME/.hushlogin"
chown "$USERNAME:$USERNAME" "/home/$USERNAME/.hushlogin"

# ── Ensure interactive shells start in home dir ──────────────────────
# WSL imported distros may start in / instead of the user's home.
if [[ -f "/home/$USERNAME/.bashrc" ]]; then
    grep -qxF 'cd ~' "/home/$USERNAME/.bashrc" 2>/dev/null || \
        printf '\n# Start in home directory\ncd ~\n' >> "/home/$USERNAME/.bashrc"
fi

# ── PATH enforcement: ~/bin first, ~/.local/bin at end ────────────────
cat > /etc/profile.d/maude-path.sh << 'PATHEOF'
mkdir -p "$HOME/bin" "$HOME/.local/bin"
# Remove ~/bin and ~/.local/bin from wherever they are in PATH
_clean="$PATH"
_clean="${_clean//$HOME\/bin:/}"
_clean="${_clean//$HOME\/.local\/bin:/}"
_clean="${_clean%:$HOME/bin}"
_clean="${_clean%:$HOME/.local/bin}"
# Re-add: ~/bin first, ~/.local/bin last
PATH="$HOME/bin:$_clean:$HOME/.local/bin"
# Remove empty segments
PATH="${PATH//::/:}"
export PATH
unset _clean
PATHEOF
chmod +x /etc/profile.d/maude-path.sh

# Hook into /etc/skel/.bashrc (for future users)
grep -qxF '. /etc/profile.d/maude-path.sh' /etc/skel/.bashrc 2>/dev/null || \
    printf '\n# Maude PATH\n. /etc/profile.d/maude-path.sh\n' >> /etc/skel/.bashrc

# Hook into existing user's .bashrc
if [[ -f "/home/$USERNAME/.bashrc" ]]; then
    grep -qxF '. /etc/profile.d/maude-path.sh' "/home/$USERNAME/.bashrc" 2>/dev/null || \
        printf '\n# Maude PATH\n. /etc/profile.d/maude-path.sh\n' >> "/home/$USERNAME/.bashrc"
fi

USER_HOME="/home/$USERNAME"
mkdir -p "$USER_HOME/bin" "$USER_HOME/.local/bin" "$USER_HOME/.local/state"
chown -R "$USERNAME:$USERNAME" "$USER_HOME/bin" "$USER_HOME/.local"

# ── ~/.claude symlink is created in maude-bootstrap.sh (step 6) ───────
# The drvfs mount for ~/Maude isn't active until after WSL restart.
# Creating the symlink here would leave it broken, causing Claude Code's
# installer to fail with "mkdir: cannot create directory: File exists".
# The .claude and Projects dirs are pre-created on the Windows side by
# setup-wsl-maude.ps1 so they exist when the mount activates.

# ── Welcome screen ────────────────────────────────────────────────────
# Displayed once per interactive login session.
cat > /etc/profile.d/maude-welcome.sh << 'WELCOME'
# ── Ensure DANGER-ZONE.txt is present on the shared mount ────────────
if [[ -d "$HOME/Maude" ]] && [[ ! -f "$HOME/Maude/DANGER-ZONE.txt" ]]; then
    curl -fsSL "https://raw.githubusercontent.com/dirkpetersen/maude/main/light/DANGER-ZONE.txt" \
        -o "$HOME/Maude/DANGER-ZONE.txt" 2>/dev/null || true
fi

# ── Bootstrap keychain before the TUI launches ───────────────────────
# If the user ran `maude github` and set a passphrase on their SSH key,
# keychain keeps ssh-agent alive across shell logins so the passphrase
# is entered once per WSL session, not every TUI launch. We do this BEFORE
# `maude tui` so the TUI inherits a persistent SSH_AUTH_SOCK.
if [[ -t 1 ]] && [[ -z "$MAUDE_WELCOMED" ]] && command -v keychain >/dev/null 2>&1; then
    _maude_key=""
    for _k in "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa"; do
        [[ -f "$_k" ]] && { _maude_key="$_k"; break; }
    done
    # Step 1: attach to (or start) keychain's persistent ssh-agent without
    # loading any key. Sets SSH_AUTH_SOCK in our env; never prompts.
    eval "$(keychain --quiet --noask --eval --agents ssh)" 2>/dev/null || true
    if [[ -n "$_maude_key" ]]; then
        # Step 2: is our key already loaded? Compare fingerprints.
        _maude_fp=$(ssh-keygen -lf "$_maude_key" 2>/dev/null | awk '{print $2}')
        if [[ -n "$_maude_fp" ]] && ! ssh-add -l 2>/dev/null | grep -qF "$_maude_fp"; then
            # Not loaded → show a friendly banner before keychain prompts.
            G=$'\033[1;32m'; C=$'\033[1;36m'; B=$'\033[1;37m'; D=$'\033[2m'; N=$'\033[0m'
            printf '\n'
            printf '  %s🔑 Unlock your GitHub SSH key%s\n' "$G" "$N"
            printf '  %s%s%s\n' "$D" "────────────────────────────────────────────" "$N"
            printf '  Enter the passphrase you set in %smaude github%s\n' "$C" "$N"
            printf '  to load %s%s%s into ssh-agent for this session.\n' "$B" "$_maude_key" "$N"
            printf '  %s(Press Enter on a blank line to skip — you can unlock later from the TUI.)%s\n' "$D" "$N"
            printf '\n'
            eval "$(keychain --quiet --eval --agents ssh "$_maude_key")" 2>/dev/null || true
        fi
        unset _maude_fp
    fi
    unset _maude_key _k
fi

# Auto-start the TUI by default; users can opt out with ~/.maude-tui-disabled.
# Guard with MAUDE_WELCOMED so we don't re-launch when .bashrc re-sources
# this script (it is already sourced via /etc/profile.d on login shells).
if [[ -t 1 ]] && [[ -z "$MAUDE_WELCOMED" ]] && [[ ! -f "$HOME/.maude-tui-disabled" ]] && command -v maude >/dev/null 2>&1; then
    export MAUDE_WELCOMED=1
    maude tui
    return 0 2>/dev/null || true
fi

# Show welcome only in interactive terminals and only once per session
if [[ -t 1 ]] && [[ -z "$MAUDE_WELCOMED" ]]; then
    export MAUDE_WELCOMED=1
    G='\033[1;32m'   # bright green
    C='\033[1;36m'   # bright cyan
    Y='\033[1;33m'   # bright yellow
    B='\033[1;37m'   # bright white
    N='\033[0m'      # reset
    printf "\n"
    printf "${G}  __  __                 _      ${N}\n"
    printf "${G} |  \/  | __ _ _   _  __| | ___ ${N}\n"
    printf "${G} | |\/| |/ _\` | | | |/ _\` |/ _ \\\\${N}\n"
    printf "${G} | |  | | (_| | |_| | (_| |  __/${N}\n"
    printf "${G} |_|  |_|\__,_|\__,_|\__,_|\___|${N}\n"
    printf "\n"
    printf "  ${B}Agentic coding sandbox${N}  -  Ubuntu LTS\n"
    printf "\n"
    printf "  Always type the command '${B}maude${N}' followed by more words as the instruction:\n"
    printf "\n"
    printf "  ${C}maude project-name${N}   Create or open a coding project\n"
    printf "  ${C}maude tui${N}            Interactive project launcher (Textual UI)\n"
    printf "  ${C}menu${N}                 Shortcut for 'maude tui'\n"
    printf "  ${C}maude web${N}            Launch web UI (kanna)\n"
    printf "  ${C}maude github${N}         Wizard for GitHub identity, SSH/GPG keys, signing\n"
    printf "  ${C}maude update${N}         Update maude CLI, Claude Code, the TUI, and kanna\n"
    printf "  ${C}maude list${N}           Show your projects\n"
    printf "  ${C}maude delete name${N}    Delete a project (moves to .deleted/)\n"
    printf "  ${C}maude help${N}           Full usage info\n"
    printf "\n"
    printf "  ${Y}mom install <pkg>${N}   Install system packages (no sudo needed)\n"
    printf "\n"
    printf "  ${B}Tips:${N}\n"
    printf "    Screen split:    Alt+Shift+Plus (vertical) | Alt+Shift+Minus (horizontal)\n"
    printf "    Share an image:  drop it into your Maude folder on Windows, then ask Claude\n"
    printf "                     to look at it (e.g. ~/Maude/Projects/<name>/screenshot.png)\n"
    printf "    Activate mic:    ${B}Win+H${N}  (Windows voice dictation)\n"
    printf "    TUI auto-launch: untick ${B}Start TUI with Maude${N} in the sidebar to opt out\n"
    printf "\n"
    if [[ ! -f "$HOME/.aws/credentials" ]] && [[ ! -f "$HOME/.azure/clauderc" ]]; then
        printf "  ${Y}LLM service credentials not yet set up.${N}\n"
        printf "  ${Y}Open the TUI ('${B}maude tui${Y}' or '${B}menu${Y}') and click ${B}Set Creds${Y}.${N}\n"
        printf "  ${Y}You can pick: paste exports / AWS Bedrock / Azure Foundry.${N}\n"
        printf "\n"
    fi
fi
WELCOME
chmod +x /etc/profile.d/maude-welcome.sh

# Hook welcome into user's .bashrc (profile.d only runs for login shells)
if [[ -f "/home/$USERNAME/.bashrc" ]]; then
    grep -qxF '. /etc/profile.d/maude-welcome.sh' "/home/$USERNAME/.bashrc" 2>/dev/null || \
        printf '\n# Maude welcome\n. /etc/profile.d/maude-welcome.sh\n' >> "/home/$USERNAME/.bashrc"
fi

# ── Maude shell tweaks: PS1, help/menu functions, tab completion, etc. ─
# Consolidated into a single /etc/profile.d script (replaces six separate
# .bashrc heredoc blocks). The file is downloaded fresh on every bootstrap
# so updates take effect on reinstall.
if [[ -f /tmp/maude-shell.sh ]]; then
    install -m 644 /tmp/maude-shell.sh /etc/profile.d/maude-shell.sh
else
    curl -fsSL "https://raw.githubusercontent.com/dirkpetersen/maude/main/light/maude-shell.sh" \
        -o /etc/profile.d/maude-shell.sh
    chmod 644 /etc/profile.d/maude-shell.sh
fi

# Hook into both /etc/skel/.bashrc and the user's .bashrc
for rc in /etc/skel/.bashrc "/home/$USERNAME/.bashrc"; do
    [[ -f "$rc" ]] || continue
    grep -qxF '. /etc/profile.d/maude-shell.sh' "$rc" 2>/dev/null || \
        printf '\n# Maude shell tweaks (PS1, help/menu, tab completion)\n. /etc/profile.d/maude-shell.sh\n' >> "$rc"
done

# ── Install Claude Code ───────────────────────────────────────────────
echo "Installing Claude Code..."
su - "$USERNAME" -c '
    mkdir -p "$HOME/.local/bin" "$HOME/bin"
    curl -fsSL https://raw.githubusercontent.com/dirkpetersen/dok/main/scripts/claude-wrapper.sh \
        -o "$HOME/.local/bin/claude-wrapper.sh" && chmod +x "$HOME/.local/bin/claude-wrapper.sh" \
        && ln -sfn "$HOME/.local/bin/claude-wrapper.sh" "$HOME/bin/claude" \
        && echo "claude-wrapper installed" || \
    { curl -fsSL https://claude.ai/install.sh | bash -s latest; }
'

# ── Install maude launcher (if copied to /tmp by setup script) ────────
if [[ -f /tmp/maude-launcher ]]; then
    install -m 755 -o "$USERNAME" -g "$USERNAME" /tmp/maude-launcher "/home/$USERNAME/.local/bin/maude"
    echo "'maude' launcher installed to ~/.local/bin/maude"
fi

# ── Install packages from stdin (fallback — normally baked into template) ─
if [[ -n "$PACKAGES" ]]; then
    echo "Installing packages..."
    echo "$PACKAGES" | xargs apt-get install -y -q --no-install-recommends
fi

# Remove the no-start policy so services work normally after setup
rm -f /usr/sbin/policy-rc.d

echo "=== Root bootstrap complete ==="
