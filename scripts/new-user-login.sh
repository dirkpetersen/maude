#!/usr/bin/env bash
# /etc/maude/new-user-login.sh
# Runs once per user on their very first login.
# Called from /etc/profile.d/maude-firstlogin.sh (which guards with a sentinel).
#
# Actions:
#   1. Create standard user dirs
#   2. Prompt to install Claude Code (once, non-blocking)
#   3. Write sentinel to prevent re-running
set -o errexit -o nounset -o pipefail
IFS=$'\n\t'

SENTINEL="${HOME}/.config/maude/.first-login-done"
MAUDE_DIR="${HOME}/.config/maude"

# Already ran — do nothing
[[ -f "${SENTINEL}" ]] && exit 0

# --- 1. Create standard dirs ---
mkdir -p "${HOME}/bin" "${HOME}/.local/bin" "${HOME}/.local/share" "${MAUDE_DIR}"

# --- 2. Claude Code install prompt ---
# Only show when we have a real interactive terminal
if [[ -t 0 && -t 1 ]]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║          Welcome to maude — agentic coding sandbox       ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
    echo "Would you like to install Claude Code (AI coding assistant)?"
    echo "  Install command: curl -fsSL https://claude.ai/install.sh | bash -s latest"
    echo ""
    read -r -p "Install Claude Code now? [Y/n] " _reply
    _reply="${_reply:-Y}"
    if [[ "${_reply}" =~ ^[Yy] ]]; then
        echo "Installing Claude Code..."
        if curl -fsSL https://claude.ai/install.sh | bash -s latest; then
            echo "Claude Code installed. Run: claude"
        else
            echo "Install failed — you can retry later with:"
            echo "  curl -fsSL https://claude.ai/install.sh | bash -s latest"
        fi
    else
        echo "Skipped. Install later with:"
        echo "  curl -fsSL https://claude.ai/install.sh | bash -s latest"
    fi
    echo ""
fi

# --- 3. Write sentinel ---
touch "${SENTINEL}"
