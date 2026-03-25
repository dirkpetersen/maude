#!/usr/bin/env bash
# /etc/profile.d/maude-firstlogin.sh
# Guards the per-user first-login setup script with a sentinel file.
# Runs for every login shell; exits immediately after first time.

SENTINEL="${HOME}/.config/maude/.first-login-done"

if [[ ! -f "${SENTINEL}" ]]; then
    # Run non-interactively safe — the setup script checks tty itself
    if [[ -x /etc/maude/new-user-login.sh ]]; then
        /etc/maude/new-user-login.sh || true
    fi
fi
