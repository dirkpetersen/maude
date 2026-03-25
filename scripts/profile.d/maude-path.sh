#!/usr/bin/env bash
# /etc/profile.d/maude-path.sh
# Ensures ~/bin (front) and ~/.local/bin are in PATH for every user.
# Creates the directories if they do not exist.
# Sourced by /etc/profile for all login shells.
# Note: intentionally no 'set -e' — this is sourced into interactive shells.

# Create dirs silently — harmless if they already exist
mkdir -p "${HOME}/bin" "${HOME}/.local/bin" 2>/dev/null || true

# Prepend ~/bin to PATH, removing any existing occurrence first to avoid duplicates
_maude_path_clean="${PATH}"
# Strip all existing occurrences of ~/bin (with or without surrounding colons)
_maude_path_clean="${_maude_path_clean//${HOME}\/bin:/}"
_maude_path_clean="${_maude_path_clean//:${HOME}\/bin/}"
_maude_path_clean="${_maude_path_clean//${HOME}\/bin/}"
PATH="${HOME}/bin:${_maude_path_clean}"
unset _maude_path_clean

# Append ~/.local/bin (only if not already present)
case ":${PATH}:" in
    *:"${HOME}/.local/bin":*)
        ;;
    *)
        PATH="${PATH}:${HOME}/.local/bin"
        ;;
esac

export PATH
