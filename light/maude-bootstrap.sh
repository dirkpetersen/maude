#!/bin/bash
# maude-bootstrap.sh — Runs as the maude user inside the Maude WSL distro.
# Installs dev-station, the maude launcher, and user-level configuration.
#
# Usage:  maude-bootstrap.sh
set -e

echo "=== Maude user bootstrap ==="

# ── Ensure directories exist ──────────────────────────────────────────
mkdir -p "$HOME/bin" "$HOME/.local/bin" "$HOME/Projects"

# ── Install dev-station (shell-setup, claude-wrapper, nodejs, AWS CLI) ─
# Always pulled fresh from GitHub to pick up latest changes.
echo "Running dev-station installer..."
curl -fsSL 'https://raw.githubusercontent.com/dirkpetersen/dok/main/scripts/dev-station-install.sh' | bash

# ── Install maude launcher (if copied to /tmp by setup script) ────────
if [ -f /tmp/maude-launcher ] && [ ! -f "$HOME/.local/bin/maude" ]; then
    install -m 755 /tmp/maude-launcher "$HOME/.local/bin/maude"
    echo "'maude' launcher installed to ~/.local/bin/maude"
elif [ -f "$HOME/.local/bin/maude" ]; then
    echo "'maude' launcher already installed."
fi

# ── PS1: replace hostname with underscore ─────────────────────────────
# Only add if not already customized
if ! grep -q 'MAUDE_PS1' "$HOME/.bashrc" 2>/dev/null; then
    cat >> "$HOME/.bashrc" << 'PS1EOF'

# Maude PS1: show user@_ instead of user@hostname
MAUDE_PS1=1
PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u\[\033[00m\]@\[\033[01;34m\]_\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
PS1EOF
fi

echo "=== User bootstrap complete ==="
