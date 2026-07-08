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
    _ver="0.2.21"
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
    # Verify checksum before installing. Look up the entry first — if the
    # asset isn't listed in SHA256SUMS, sha256sum -c with empty stdin would
    # spuriously succeed.
    _expected=$(grep " ${_deb}\$" /tmp/mom-SHA256SUMS || true)
    if [[ -z "$_expected" ]]; then
        echo "ERROR: no checksum entry for ${_deb} in SHA256SUMS — refusing to install."
        rm -f "/tmp/${_deb}" /tmp/mom-SHA256SUMS
        exit 1
    fi
    if ! ( cd /tmp && printf '%s\n' "$_expected" | sha256sum -c --status ); then
        echo "ERROR: SHA-256 mismatch for ${_deb} — refusing to install."
        echo "       expected: $(printf '%s' "$_expected" | awk '{print $1}')"
        echo "       got:      $(sha256sum "/tmp/${_deb}" | awk '{print $1}')"
        rm -f "/tmp/${_deb}" /tmp/mom-SHA256SUMS
        exit 1
    fi
    echo "Checksum OK for ${_deb}."
    dpkg -i "/tmp/${_deb}"
    rm -f "/tmp/${_deb}" /tmp/mom-SHA256SUMS
    unset _ubuntu_ver _os_tag _deb _base _expected
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

# ── Profile.d scripts (welcome + shell tweaks) ────────────────────────
# Both files live in the repo; they're piped to /tmp by the PowerShell
# setup script (preferred) or fetched directly from GitHub as a fallback.
GH_RAW="https://raw.githubusercontent.com/dirkpetersen/maude/main/light"

install_profile_script() {
    local local_src="$1" url="$2" dest="$3"
    if [[ -f "$local_src" ]]; then
        install -m 644 "$local_src" "$dest"
    else
        curl -fsSL "$url" -o "$dest"
        chmod 644 "$dest"
    fi
    # /etc/profile.d/*.sh need to be sourceable; chmod +x is a Debian convention.
    chmod +x "$dest"
}

install_profile_script /tmp/maude-welcome.sh "$GH_RAW/welcome.sh"     /etc/profile.d/maude-welcome.sh
install_profile_script /tmp/maude-shell.sh   "$GH_RAW/maude-shell.sh" /etc/profile.d/maude-shell.sh

# Hook the profile.d scripts into both /etc/skel/.bashrc (future users)
# and the active user's .bashrc (profile.d only auto-runs for login shells).
hook_into_bashrc() {
    local rc="$1" path="$2" header="$3"
    [[ -f "$rc" ]] || return 0
    grep -qxF ". $path" "$rc" 2>/dev/null || \
        printf '\n# %s\n. %s\n' "$header" "$path" >> "$rc"
}
for rc in /etc/skel/.bashrc "/home/$USERNAME/.bashrc"; do
    hook_into_bashrc "$rc" /etc/profile.d/maude-welcome.sh "Maude welcome"
    hook_into_bashrc "$rc" /etc/profile.d/maude-shell.sh   "Maude shell tweaks (PS1, help/menu, tab completion)"
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
