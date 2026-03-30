#!/bin/bash
# root-bootstrap.sh — Runs as root inside the Maude WSL distro.
# Sets up the system: user, mom, PATH, hushlogin, packages, sandbox mount.
#
# Usage:  root-bootstrap.sh <username>
# Packages are read from stdin (one per line) if provided.
# Host folder path is read from /tmp/maude-hostfolder if present.
set -e
export DEBIAN_FRONTEND=noninteractive

USERNAME="${1:?Usage: root-bootstrap.sh <username>}"
MOM_GROUP="users"

# Read package list from stdin immediately (before any command can consume it)
# Strip \r — PowerShell pipes CRLF even after -replace on the PS side.
PACKAGES=""
if [ ! -t 0 ]; then
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
apt-get install -y -q sudo curl git ca-certificates software-properties-common cron lsyncd rsync
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
groupadd --system --gid 100 "$MOM_GROUP" 2>/dev/null || true
usermod -aG "$MOM_GROUP" "$USERNAME" 2>/dev/null || true

if [ ! -x /usr/local/bin/mom ]; then
    _arch=$(uname -m)
    case "$_arch" in
        x86_64)  _arch="amd64" ;;
        aarch64) _arch="arm64" ;;
        armv7l)  _arch="armv7" ;;
    esac
    echo "Downloading mom binary (${_arch})..."
    curl -fsSL \
        "https://github.com/dirkpetersen/mom/releases/latest/download/mom-linux-${_arch}" \
        -o /usr/local/bin/mom
    chmod 4755 /usr/local/bin/mom
    echo "mom installed."
fi

mkdir -p /etc/mom
if [ ! -f /etc/mom/mom.conf ]; then
    printf 'group = %s\ndeny_list = /etc/mom/deny.list\nlog_file = /var/log/mom.log\n' \
        "$MOM_GROUP" > /etc/mom/mom.conf
fi
if [ ! -f /etc/mom/deny.list ]; then
    printf '# mom deny list\nnmap\ntcpdump\nwireshark*\naircrack*\nmetasploit*\n' \
        > /etc/mom/deny.list
fi

# ── Read host folder path (written by setup-wsl-maude.ps1) ──────────
HOST_FOLDER=""
if [ -f /tmp/maude-hostfolder ]; then
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
appendWindowsPath = false
WSLEOF

# Ensure login starts in the user's home directory (imported distros may default to /)
usermod -d "/home/$USERNAME" "$USERNAME" 2>/dev/null || true

# ── Sandbox mount: host folder → /home/<user>/Maude via drvfs ────────
if [ -n "$HOST_FOLDER" ]; then
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
    echo "Sandbox mount configured: $HOST_FOLDER -> $MOUNT_POINT"
else
    echo "WARNING: No host folder path found, skipping sandbox mount."
fi

# ── Hushlogin (suppress Ubuntu MOTD) ─────────────────────────────────
touch "/home/$USERNAME/.hushlogin"
chown "$USERNAME:$USERNAME" "/home/$USERNAME/.hushlogin"

# ── Ensure interactive shells start in home dir ──────────────────────
# WSL imported distros may start in / instead of the user's home.
if [ -f "/home/$USERNAME/.bashrc" ]; then
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
if [ -f "/home/$USERNAME/.bashrc" ]; then
    grep -qxF '. /etc/profile.d/maude-path.sh' "/home/$USERNAME/.bashrc" 2>/dev/null || \
        printf '\n# Maude PATH\n. /etc/profile.d/maude-path.sh\n' >> "/home/$USERNAME/.bashrc"
fi

# ── Real-time sync: ~/Projects and ~/.claude ↔ ~/Maude ────────────────
# On boot: restore from ~/Maude if local dirs are empty (new instance).
# After restore: lsyncd watches local dirs and mirrors changes to ~/Maude.
USER_HOME="/home/$USERNAME"

# Restore script — runs once at boot before lsyncd starts
mkdir -p "$USER_HOME/bin"
chown "$USERNAME:$USERNAME" "$USER_HOME/bin"
cat > "$USER_HOME/bin/maude-restore.sh" << RESTOREEOF
#!/bin/bash
# maude-restore.sh — populate empty dirs from shared Maude folder on boot
MAUDE_DIR="\$HOME/Maude"
[ -d "\$MAUDE_DIR" ] || exit 0

# Restore .claude if local is empty but backup exists
if [ -d "\$MAUDE_DIR/.claude" ] && [ "\$(ls -A "\$MAUDE_DIR/.claude" 2>/dev/null)" ]; then
    if [ ! -d "\$HOME/.claude" ] || [ -z "\$(ls -A "\$HOME/.claude" 2>/dev/null)" ]; then
        echo "maude-restore: restoring ~/.claude from ~/Maude/.claude"
        mkdir -p "\$HOME/.claude"
        rsync -a "\$MAUDE_DIR/.claude/" "\$HOME/.claude/"
    fi
fi

# Restore Projects if local is empty but backup exists
if [ -d "\$MAUDE_DIR/Projects" ] && [ "\$(ls -A "\$MAUDE_DIR/Projects" 2>/dev/null)" ]; then
    if [ ! -d "\$HOME/Projects" ] || [ -z "\$(ls -A "\$HOME/Projects" 2>/dev/null)" ]; then
        echo "maude-restore: restoring ~/Projects from ~/Maude/Projects"
        mkdir -p "\$HOME/Projects"
        rsync -a "\$MAUDE_DIR/Projects/" "\$HOME/Projects/"
    fi
fi
RESTOREEOF
chmod +x "$USER_HOME/bin/maude-restore.sh"
chown "$USERNAME:$USERNAME" "$USER_HOME/bin/maude-restore.sh"

# lsyncd config — watches local dirs, mirrors to ~/Maude in real time
mkdir -p /etc/lsyncd
cat > /etc/lsyncd/maude-sync.conf.lua << LSYNCDEOF
settings {
    logfile    = "$USER_HOME/.local/state/maude-sync.log",
    statusFile = "$USER_HOME/.local/state/maude-sync.status",
    nodaemon   = true,
}

sync {
    default.rsync,
    source = "$USER_HOME/Projects",
    target = "$USER_HOME/Maude/Projects",
    delay  = 3,
    delete = true,
    rsync  = {
        archive = true,
    }
}

sync {
    default.rsync,
    source = "$USER_HOME/.claude",
    target = "$USER_HOME/Maude/.claude",
    delay  = 3,
    delete = true,
    rsync  = {
        archive = true,
        _extra  = { "--exclude=settings.json" },
    }
}
LSYNCDEOF

# systemd service — restore on boot, then real-time sync via lsyncd
cat > /etc/systemd/system/maude-sync.service << SVCEOF
[Unit]
Description=Maude file sync (restore + lsyncd)
After=local-fs.target

[Service]
Type=simple
User=$USERNAME
ExecStartPre=$USER_HOME/bin/maude-restore.sh
ExecStart=/usr/bin/lsyncd /etc/lsyncd/maude-sync.conf.lua
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

# Create log directory
mkdir -p "$USER_HOME/.local/state" "$USER_HOME/.local/bin"
chown -R "$USERNAME:$USERNAME" "$USER_HOME/.local"

systemctl enable maude-sync.service 2>/dev/null || true
echo "Real-time sync service installed (maude-sync)."

# ── Welcome screen ────────────────────────────────────────────────────
# Displayed once per interactive login session.
cat > /etc/profile.d/maude-welcome.sh << 'WELCOME'
# Show welcome only in interactive terminals and only once per session
if [ -t 1 ] && [ -z "$MAUDE_WELCOMED" ]; then
    export MAUDE_WELCOMED=1
    G='\033[0;32m'   # green
    C='\033[1;36m'   # cyan
    Y='\033[1;33m'   # yellow
    B='\033[1m'      # bold
    N='\033[0m'      # reset
    printf "\n"
    printf "${G}  __  __                 _      ${N}\n"
    printf "${G} |  \/  | __ _ _   _  __| | ___ ${N}\n"
    printf "${G} | |\/| |/ _\` | | | |/ _\` |/ _ \\\\${N}\n"
    printf "${G} | |  | | (_| | |_| | (_| |  __/${N}\n"
    printf "${G} |_|  |_|\__,_|\__,_|\__,_|\___|${N}\n"
    printf "\n"
    printf "  ${B}Agentic coding sandbox${N}  -  Ubuntu 24.04 LTS\n"
    printf "\n"
    printf "  ${C}maude project-name${N}   Create or open a coding project\n"
    printf "  ${C}maude list${N}           Show your projects\n"
    printf "  ${C}maude delete name${N}    Delete a project (moves to .deleted/)\n"
    printf "  ${C}maude help${N}           Full usage info\n"
    printf "\n"
    printf "  ${Y}mom install <pkg>${N}   Install system packages (no sudo needed)\n"
    printf "\n"
fi
WELCOME
chmod +x /etc/profile.d/maude-welcome.sh

# Hook welcome into user's .bashrc (profile.d only runs for login shells)
if [ -f "/home/$USERNAME/.bashrc" ]; then
    grep -qxF '. /etc/profile.d/maude-welcome.sh' "/home/$USERNAME/.bashrc" 2>/dev/null || \
        printf '\n# Maude welcome\n. /etc/profile.d/maude-welcome.sh\n' >> "/home/$USERNAME/.bashrc"
fi

# ── PS1: replace hostname with underscore ─────────────────────────────
if [ -f "/home/$USERNAME/.bashrc" ]; then
    grep -q 'MAUDE_PS1' "/home/$USERNAME/.bashrc" 2>/dev/null || \
        cat >> "/home/$USERNAME/.bashrc" << 'PS1EOF'

# Maude PS1: show user@_ instead of user@hostname
MAUDE_PS1=1
PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u\[\033[00m\]@\[\033[01;34m\]_\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
PS1EOF
fi

# ── Tab completion for maude command ─────────────────────────────────
if [ -f "/home/$USERNAME/.bashrc" ]; then
    grep -q '_maude_complete' "/home/$USERNAME/.bashrc" 2>/dev/null || \
        cat >> "/home/$USERNAME/.bashrc" << 'COMPEOF'

# Maude tab completion
_maude_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [ "$COMP_CWORD" -eq 1 ]; then
        local cmds="list ls delete rm help"
        local projects=""
        if [ -d "$HOME/Projects" ]; then
            projects=$(ls -d "$HOME/Projects"/*/ 2>/dev/null | xargs -I{} basename {} 2>/dev/null)
        fi
        COMPREPLY=( $(compgen -W "$cmds $projects" -- "$cur") )
    elif [ "$COMP_CWORD" -eq 2 ] && [[ "$prev" == "delete" || "$prev" == "rm" ]]; then
        local projects=""
        if [ -d "$HOME/Projects" ]; then
            projects=$(ls -d "$HOME/Projects"/*/ 2>/dev/null | xargs -I{} basename {} 2>/dev/null)
        fi
        COMPREPLY=( $(compgen -W "$projects" -- "$cur") )
    fi
}
complete -F _maude_complete maude
COMPEOF
fi

# ── Install Claude Code ───────────────────────────────────────────────
echo "Installing Claude Code..."
su - "$USERNAME" -c '
    curl -fsSL https://raw.githubusercontent.com/dirkpetersen/dok/main/scripts/claude-wrapper.sh | bash || \
    { curl -fsSL https://claude.ai/install.sh | bash -s latest; }
'

# ── Install maude launcher (if copied to /tmp by setup script) ────────
if [ -f /tmp/maude-launcher ]; then
    install -m 755 -o "$USERNAME" -g "$USERNAME" /tmp/maude-launcher "/home/$USERNAME/.local/bin/maude"
    echo "'maude' launcher installed to ~/.local/bin/maude"
fi

# ── Install packages from stdin (fallback — normally baked into template) ─
if [ -n "$PACKAGES" ]; then
    echo "Installing packages..."
    echo "$PACKAGES" | xargs apt-get install -y -q --no-install-recommends
fi

# Remove the no-start policy so services work normally after setup
rm -f /usr/sbin/policy-rc.d

echo "=== Root bootstrap complete ==="
