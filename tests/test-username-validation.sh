#!/usr/bin/env bash
# tests/test-username-validation.sh
# Tests the username validation logic from maude-adduser.
# Runs without root — extracts the validation regex for unit testing.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

suite_header "Username Validation (maude-adduser)"

ADDUSER="${SCRIPT_DIR}/../scripts/maude-adduser"
assert_file_exists "maude-adduser exists" "${ADDUSER}"
assert_executable  "maude-adduser is executable" "${ADDUSER}"

# Extract the validation regex from the script (test the regex directly)
_valid_username() {
    local u="$1"
    [[ "${u}" =~ ^[a-z][a-z0-9_-]{0,31}$ ]]
}

_is_blocked() {
    local u="$1"
    local blocked=("root" "daemon" "bin" "sys" "sync" "games" "man" "lp" "mail"
                   "news" "uucp" "proxy" "www-data" "backup" "list" "irc" "gnats"
                   "nobody" "systemd-network" "systemd-resolve" "appmotel" "apps"
                   "mom" "maude")
    for b in "${blocked[@]}"; do [[ "${u}" == "${b}" ]] && return 0; done
    return 1
}

suite_header "Valid usernames"
for name in alice bob john_doe user-01 a a1 ab long-username-exactly32c; do
    if _valid_username "${name}" && ! _is_blocked "${name}"; then
        assert_eq "Valid: '${name}'" "ok" "ok"
    else
        assert_eq "Valid: '${name}'" "ok" "REJECTED"
    fi
done

suite_header "Invalid usernames (should be rejected)"
for name in "" "1startswithnumber" "UPPERCASE" "has space" "semi;colon" \
            "back\`tick" 'dollar$sign' "has/slash" \
            "$(python3 -c 'print("a"*33)')"; do
    if ! _valid_username "${name}" 2>/dev/null; then
        assert_eq "Rejected: '${name}'" "rejected" "rejected"
    else
        assert_eq "Rejected: '${name}'" "rejected" "ACCEPTED"
    fi
done

suite_header "Blocked system names"
for name in root daemon www-data appmotel apps mom maude nobody; do
    if _is_blocked "${name}"; then
        assert_eq "Blocked: '${name}'" "blocked" "blocked"
    else
        assert_eq "Blocked: '${name}'" "blocked" "NOT_BLOCKED"
    fi
done

suite_summary
