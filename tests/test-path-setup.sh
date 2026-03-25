#!/usr/bin/env bash
# tests/test-path-setup.sh
# Tests the PATH setup logic from scripts/profile.d/maude-path.sh.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

suite_header "PATH Setup (maude-path.sh)"

PROFILE="${SCRIPT_DIR}/../scripts/profile.d/maude-path.sh"
assert_file_exists "maude-path.sh exists" "${PROFILE}"

_tmpdir=$(mktemp -d)
trap 'rm -rf "${_tmpdir}"' EXIT

suite_header "Directory creation"
(
    HOME="${_tmpdir}"
    export HOME
    PATH="/usr/local/bin:/usr/bin:/bin"
    # shellcheck source=/dev/null
    source "${PROFILE}"
    [[ -d "${HOME}/bin" ]]        && echo "bin_exists=true"       || echo "bin_exists=false"
    [[ -d "${HOME}/.local/bin" ]] && echo "local_bin_exists=true" || echo "local_bin_exists=false"
) > /tmp/_path_test_out.txt

assert_contains "~/bin is created"       "bin_exists=true"       "$(cat /tmp/_path_test_out.txt)"
assert_contains "~/.local/bin is created" "local_bin_exists=true" "$(cat /tmp/_path_test_out.txt)"

suite_header "PATH ordering and deduplication"

# Test with a PATH that already contains ~/.local/bin (Ubuntu default behaviour)
_path_result=$(
    HOME="${_tmpdir}"
    export HOME
    PATH="/usr/local/bin:/usr/bin:/bin:${_tmpdir}/.local/bin"
    # shellcheck source=/dev/null
    source "${PROFILE}"
    echo "${PATH}"
)

_first="${_path_result%%:*}"
assert_eq "~/bin is first in PATH" "${_tmpdir}/bin" "${_first}"
assert_contains "~/.local/bin is in PATH" "${_tmpdir}/.local/bin" "${_path_result}"

_localbin_count=$(echo "${_path_result}" | tr ':' '\n' | grep -cF "${_tmpdir}/.local/bin" || true)
assert_eq "~/.local/bin appears exactly once" "1" "${_localbin_count}"

# Test: ~/bin stays first even when it was already in PATH
_path_double=$(
    HOME="${_tmpdir}"
    export HOME
    PATH="/usr/local/bin:/usr/bin:/bin"
    # shellcheck source=/dev/null
    source "${PROFILE}"
    # shellcheck source=/dev/null
    source "${PROFILE}"
    echo "${PATH}"
)
_bin_count=$(echo "${_path_double}" | tr ':' '\n' | grep -cF "${_tmpdir}/bin" || true)
assert_eq "No duplicate ~/bin after double-source" "1" "${_bin_count}"

_local_count=$(echo "${_path_double}" | tr ':' '\n' | grep -cF "${_tmpdir}/.local/bin" || true)
assert_eq "No duplicate ~/.local/bin after double-source" "1" "${_local_count}"

_first_double="${_path_double%%:*}"
assert_eq "~/bin still first after double-source" "${_tmpdir}/bin" "${_first_double}"

# Test: system paths are preserved
assert_contains "/usr/local/bin preserved" "/usr/local/bin" "${_path_double}"
assert_contains "/usr/bin preserved"       "/usr/bin"       "${_path_double}"

# Test: Ubuntu-style PATH with .local/bin already present does not duplicate
_path_ubuntu=$(
    HOME="${_tmpdir}"
    export HOME
    PATH="${_tmpdir}/.local/bin:/usr/local/bin:/usr/bin:/bin"
    # shellcheck source=/dev/null
    source "${PROFILE}"
    source "${PROFILE}"
    echo "${PATH}"
)
_first_ubuntu="${_path_ubuntu%%:*}"
assert_eq "~/bin first even when .local/bin was first before" "${_tmpdir}/bin" "${_first_ubuntu}"
_local_ubuntu=$(echo "${_path_ubuntu}" | tr ':' '\n' | grep -cF "${_tmpdir}/.local/bin" || true)
assert_eq "~/.local/bin exactly once with Ubuntu-style initial PATH" "1" "${_local_ubuntu}"

suite_header "Simulated bashrc-sourced scenario (real WSL boot order)"
# Simulate what happens in WSL:
#   1. profile.d runs (sets ~/bin first)
#   2. Ubuntu .bashrc prepends ~/bin and ~/.local/bin
#   3. Claude Code prepends ~/.local/bin again
#   4. Our .bashrc hook re-sources maude-path.sh at the end
_path_bashrc=$(
    HOME="${_tmpdir}"
    export HOME
    PATH="/usr/local/bin:/usr/bin:/bin"
    # Step 1: profile.d runs
    source "${PROFILE}"
    # Step 2: Ubuntu .bashrc prepends (simulated)
    PATH="${HOME}/bin:${PATH}"
    PATH="${HOME}/.local/bin:${PATH}"
    # Step 3: Claude Code prepends (simulated)
    PATH="${HOME}/.local/bin:${PATH}"
    # Step 4: .bashrc hook re-runs maude-path.sh
    source "${PROFILE}"
    echo "${PATH}"
)
_first_bashrc="${_path_bashrc%%:*}"
assert_eq "~/bin first after full WSL boot sequence"          "${_tmpdir}/bin"        "${_first_bashrc}"
_bin_final=$(echo "${_path_bashrc}" | tr ':' '\n' | grep -cF "${_tmpdir}/bin" || true)
assert_eq "~/bin exactly once after full boot"                "1"                     "${_bin_final}"
_local_final=$(echo "${_path_bashrc}" | tr ':' '\n' | grep -cF "${_tmpdir}/.local/bin" || true)
assert_eq "~/.local/bin exactly once after full boot"         "1"                     "${_local_final}"

rm -f /tmp/_path_test_out.txt
suite_summary
