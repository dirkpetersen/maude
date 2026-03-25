#!/usr/bin/env bash
# tests/test-packages.sh
# Validates the YAML package list files for correct format.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

suite_header "Package List Files"

UBUNTU_PKGS="${SCRIPT_DIR}/../packages/ubuntu-packages.yaml"
RHEL_PKGS="${SCRIPT_DIR}/../packages/rhel-packages.yaml"

assert_file_exists "ubuntu-packages.yaml exists" "${UBUNTU_PKGS}"
assert_file_exists "rhel-packages.yaml exists"   "${RHEL_PKGS}"

# Test 1: Both files have a 'packages:' key
assert_contains "ubuntu-packages.yaml has 'packages:' key" \
    "packages:" "$(head -20 "${UBUNTU_PKGS}")"
assert_contains "rhel-packages.yaml has 'packages:' key" \
    "packages:" "$(head -20 "${RHEL_PKGS}")"

# Test 2: Valid YAML (if python3 available)
if command -v python3 &>/dev/null; then
    assert_exit_zero "ubuntu-packages.yaml is valid YAML" \
        python3 -c "import yaml; yaml.safe_load(open('${UBUNTU_PKGS}'))"
    assert_exit_zero "rhel-packages.yaml is valid YAML" \
        python3 -c "import yaml; yaml.safe_load(open('${RHEL_PKGS}'))"

    # Test 3: At least 20 packages in each list
    _ubuntu_count=$(python3 -c \
        "import yaml; d=yaml.safe_load(open('${UBUNTU_PKGS}')); print(len(d['packages']))")
    _rhel_count=$(python3 -c \
        "import yaml; d=yaml.safe_load(open('${RHEL_PKGS}')); print(len(d['packages']))")

    [[ "${_ubuntu_count}" -ge 20 ]] \
        && assert_eq "ubuntu: at least 20 packages (got ${_ubuntu_count})" "ok" "ok" \
        || assert_eq "ubuntu: at least 20 packages (got ${_ubuntu_count})" "ok" "FAIL"
    [[ "${_rhel_count}" -ge 20 ]] \
        && assert_eq "rhel: at least 20 packages (got ${_rhel_count})" "ok" "ok" \
        || assert_eq "rhel: at least 20 packages (got ${_rhel_count})" "ok" "FAIL"

    # Test 4: Essential packages present in Ubuntu list
    for pkg in git curl openssh-server tmux python3 python3-venv golang-go ufw fail2ban; do
        _found=$(python3 -c \
            "import yaml; d=yaml.safe_load(open('${UBUNTU_PKGS}')); print('yes' if '${pkg}' in d['packages'] else 'no')")
        assert_eq "ubuntu: '${pkg}' is listed" "yes" "${_found}"
    done
else
    skip "python3 not available — skipping YAML validation"
fi

# Test 5: No package names with injection characters (skip blank lines and comments)
_bad=$(grep -E '[;&|`$]' "${UBUNTU_PKGS}" | grep -v '^\s*#' | grep -v '^packages:' | grep -v '^\s*$' || true)
assert_eq "ubuntu-packages.yaml: no injection chars in package names" "" "${_bad}"

_bad=$(grep -E '[;&|`$]' "${RHEL_PKGS}" | grep -v '^\s*#' | grep -v '^packages:' | grep -v '^\s*$' || true)
assert_eq "rhel-packages.yaml: no injection chars in package names" "" "${_bad}"

suite_summary
