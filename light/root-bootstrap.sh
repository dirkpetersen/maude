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
printf '%s\n' \
    'mkdir -p "$HOME/bin" "$HOME/.local/bin"' \
    'case ":$PATH:" in' \
    '    *":$HOME/bin:"*) ;;' \
    '    *) PATH="$HOME/bin:$PATH" ;;' \
    'esac' \
    'PATH="${PATH//:$HOME\/.local\/bin/}:$HOME/.local/bin"' \
    'export PATH' \
    > /etc/profile.d/maude-path.sh
chmod +x /etc/profile.d/maude-path.sh

# Hook into /etc/skel/.bashrc (for future users)
grep -qxF '. /etc/profile.d/maude-path.sh' /etc/skel/.bashrc 2>/dev/null || \
    printf '\n# Maude PATH\n. /etc/profile.d/maude-path.sh\n' >> /etc/skel/.bashrc

# Hook into existing user's .bashrc
if [ -f "/home/$USERNAME/.bashrc" ]; then
    grep -qxF '. /etc/profile.d/maude-path.sh' "/home/$USERNAME/.bashrc" 2>/dev/null || \
        printf '\n# Maude PATH\n. /etc/profile.d/maude-path.sh\n' >> "/home/$USERNAME/.bashrc"
fi

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
    printf "  ${B}Agentic coding sandbox${N}  —  Ubuntu 24.04 LTS\n"
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

# ── Install packages from stdin (fallback — normally baked into template) ─
if [ -n "$PACKAGES" ]; then
    echo "Installing packages..."
    echo "$PACKAGES" | xargs apt-get install -y -q --no-install-recommends
fi

# Remove the no-start policy so services work normally after setup
rm -f /usr/sbin/policy-rc.d

echo "=== Root bootstrap complete ==="
