#!/usr/bin/env bash
# /etc/profile.d/maude-path.sh
# Ensures ~/bin comes FIRST in PATH and ~/.local/bin is present exactly once.
# Creates both directories if they do not exist.
# Sourced by /etc/profile for all login shells.
# Note: intentionally no 'set -e' — this is sourced into interactive shells.

mkdir -p "${HOME}/bin" "${HOME}/.local/bin" 2>/dev/null || true

# Rebuild PATH fully deduped:
#   1. ~/bin always first
#   2. existing PATH entries (skipping ~/bin and ~/.local/bin)
#   3. ~/.local/bin always last
# This handles Ubuntu's own ~/.local/bin additions and multiple sources.

_mp_new="${HOME}/bin"
_mp_seen=":${HOME}/bin:"
_mp_rest="${PATH}"

while [ -n "${_mp_rest}" ]; do
    # Peel the next colon-delimited entry
    case "${_mp_rest}" in
        *:*) _mp_dir="${_mp_rest%%:*}"; _mp_rest="${_mp_rest#*:}" ;;
        *)   _mp_dir="${_mp_rest}";     _mp_rest="" ;;
    esac
    [ -z "${_mp_dir}" ] && continue
    # Skip ~/bin and ~/.local/bin — we place them explicitly
    [ "${_mp_dir}" = "${HOME}/bin" ]        && continue
    [ "${_mp_dir}" = "${HOME}/.local/bin" ] && continue
    # Skip duplicates
    case "${_mp_seen}" in
        *":${_mp_dir}:"*) ;;
        *) _mp_new="${_mp_new}:${_mp_dir}"; _mp_seen="${_mp_seen}${_mp_dir}:" ;;
    esac
done

# Append ~/.local/bin exactly once at the end
_mp_new="${_mp_new}:${HOME}/.local/bin"

export PATH="${_mp_new}"
unset _mp_new _mp_seen _mp_rest _mp_dir
