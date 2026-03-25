#!/usr/bin/env bash
# tests/test-path-setup.sh
# Tests the PATH setup logic from scripts/profile.d/maude-path.sh.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

suite_header "PATH Setup (maude-path.sh)"

PROFILE="${SCRIPT_DIR}/../scripts/profile.d/maude-path.sh"
assert_file_exists "maude-path.sh exists" "${PROFILE}"

# Test in a subshell with a fake HOME
_run_profile() {
    export HOME
    HOME="$(mktemp -d)"
    # Source the profile and print PATH
    bash -c "HOME='${HOME}'; source '${PROFILE}'; echo \"\${PATH}\""
    rm -rf "${HOME}"
}

# Test 1: ~/bin is created
suite_header "Directory creation"
_tmpdir=$(mktemp -d)
(
    HOME="${_tmpdir}"
    export HOME
    # shellcheck source=/dev/null
    source "${PROFILE}"
    [[ -d "${HOME}/bin" ]] && echo "bin_exists=true" || echo "bin_exists=false"
    [[ -d "${HOME}/.local/bin" ]] && echo "local_bin_exists=true" || echo "local_bin_exists=false"
) > /tmp/_path_test_out.txt

assert_contains "~/bin is created" "bin_exists=true" "$(cat /tmp/_path_test_out.txt)"
assert_contains "~/.local/bin is created" "local_bin_exists=true" "$(cat /tmp/_path_test_out.txt)"

# Test 2: ~/bin is at the front of PATH
_path_result=$(
    HOME="${_tmpdir}"
    export HOME
    # shellcheck source=/dev/null
    source "${PROFILE}"
    echo "${PATH}"
)
_first_component="${_path_result%%:*}"
assert_eq "~/bin is first in PATH" "${_tmpdir}/bin" "${_first_component}"

# Test 3: ~/.local/bin is in PATH
assert_contains "~/.local/bin is in PATH" \
    "${_tmpdir}/.local/bin" \
    "${_path_result}"

# Test 4: No duplicate entries when sourced twice
_path_double=$(
    HOME="${_tmpdir}"
    export HOME
    # shellcheck source=/dev/null
    source "${PROFILE}"
    # shellcheck source=/dev/null
    source "${PROFILE}"
    echo "${PATH}"
)
_bin_count=$(echo "${_path_double}" | tr ':' '\n' | grep -cF "${_tmpdir}/bin" || true)
assert_eq "No duplicate ~/bin entries after double-source" "1" "${_bin_count}"

rm -rf "${_tmpdir}" /tmp/_path_test_out.txt
suite_summary
