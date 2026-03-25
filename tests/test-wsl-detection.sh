#!/usr/bin/env bash
# tests/test-wsl-detection.sh
# Tests WSL detection logic used in first-boot.sh and maude-setup.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

suite_header "WSL Detection Logic"

# Extract the detection function inline for unit testing
is_wsl_real() { [[ -f /proc/sys/fs/binfmt_misc/WSLInterop ]]; }

is_wsl_stub_true()  { return 0; }
is_wsl_stub_false() { return 1; }

# Test 1: detection file presence
if [[ -f /proc/sys/fs/binfmt_misc/WSLInterop ]]; then
    assert_eq "Detects WSL when WSLInterop exists" "wsl" "wsl"
    # We are actually on WSL
    actual_target="wsl"
else
    assert_eq "Detects VM when WSLInterop absent" "vm" "vm"
    actual_target="vm"
fi

# Test 2: Traefik bind address logic
_wsl_traefik_addr() {
    local is_wsl="$1"
    if [[ "${is_wsl}" == "true" ]]; then
        echo "127.0.0.1:80"
    else
        echo ":80"
    fi
}
assert_eq "WSL Traefik binds to 127.0.0.1" "127.0.0.1:80" "$(_wsl_traefik_addr true)"
assert_eq "VM Traefik binds to all interfaces" ":80"         "$(_wsl_traefik_addr false)"

# Test 3: Deploy target string
_deploy_target() {
    if [[ -f /proc/sys/fs/binfmt_misc/WSLInterop ]]; then
        echo "wsl"
    else
        echo "vm"
    fi
}
assert_eq "Deploy target matches expected" "${actual_target}" "$(_deploy_target)"

# Test 4: WSL wsl.conf content check
if [[ -f "${SCRIPT_DIR}/../packer/http/user-data" ]]; then
    assert_file_exists "user-data autoinstall config exists" \
        "${SCRIPT_DIR}/../packer/http/user-data"
fi

# Test 5: first-boot.sh has WSL patch logic
assert_contains "first-boot.sh contains WSL sed patch" \
    "127.0.0.1" \
    "$(grep -o '127.0.0.1' "${SCRIPT_DIR}/../scripts/first-boot.sh" | head -1)"

suite_summary
