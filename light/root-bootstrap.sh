#!/bin/bash
# root-bootstrap.sh — Runs as root inside the Maude WSL distro.
# Sets up the system: user, mom, PATH, hushlogin, packages.
#
# Usage:  root-bootstrap.sh <username>
# Packages are read from stdin (one per line) if provided.
set -e

USERNAME="${1:?Usage: root-bootstrap.sh <username>}"
MOM_GROUP="users"

echo "=== Maude root bootstrap ==="

# ── Base packages ─────────────────────────────────────────────────────
apt-get update -qq
apt-get install -y -qq sudo curl git ca-certificates software-properties-common

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
    printf 'group = "%s"\ndeny_list = "/etc/mom/deny.list"\nlog_file = "/var/log/mom.log"\n' \
        "$MOM_GROUP" > /etc/mom/mom.conf
fi
if [ ! -f /etc/mom/deny.list ]; then
    printf '# mom deny list\nnmap\ntcpdump\nwireshark*\naircrack*\nmetasploit*\n' \
        > /etc/mom/deny.list
fi

# ── WSL config (default user + start in home) ────────────────────────
printf '[user]\ndefault=%s\n' "$USERNAME" > /etc/wsl.conf

# ── Hushlogin (suppress Ubuntu MOTD) ─────────────────────────────────
touch "/home/$USERNAME/.hushlogin"
chown "$USERNAME:$USERNAME" "/home/$USERNAME/.hushlogin"

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
    printf "  ${C}maude <name>${N}        Create or open a coding project\n"
    printf "  ${C}maude list${N}          Show your projects\n"
    printf "  ${C}maude help${N}          Full usage info\n"
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

# ── Install packages from stdin (if any) ─────────────────────────────
if [ ! -t 0 ]; then
    # stdin is not a terminal → read package list
    PACKAGES=$(cat)
    if [ -n "$PACKAGES" ]; then
        echo "Enabling universe repository..."
        add-apt-repository -y universe >/dev/null 2>&1 || true
        apt-get update -qq
        echo "Installing packages..."
        echo "$PACKAGES" | xargs apt-get install -y -q --no-install-recommends
    fi
fi

echo "=== Root bootstrap complete ==="
