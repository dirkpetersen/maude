#!/usr/bin/env bash
# tests/run-all-tests.sh
# Runs the full maude test suite.
# Usage: ./tests/run-all-tests.sh [--fail-fast]
set -o nounset -o pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAIL_FAST=false
[[ "${1:-}" == "--fail-fast" ]] && FAIL_FAST=true

_RED='\033[0;31m'; _GREEN='\033[0;32m'; _CYAN='\033[0;36m'
_BOLD='\033[1m'; _RESET='\033[0m'

TOTAL_PASS=0; TOTAL_FAIL=0; TOTAL_SKIP=0
FAILED_SUITES=()

# Discover test files
mapfile -t TEST_FILES < <(
    find "${SCRIPT_DIR}" -name 'test-*.sh' | sort
)

echo ""
echo -e "${_BOLD}╔══════════════════════════════════════════════╗${_RESET}"
echo -e "${_BOLD}║           maude test suite                   ║${_RESET}"
echo -e "${_BOLD}╚══════════════════════════════════════════════╝${_RESET}"
echo ""

for test_file in "${TEST_FILES[@]}"; do
    suite_name="$(basename "${test_file}")"
    echo -e "${_CYAN}Running: ${suite_name}${_RESET}"

    # Run in subshell, capture exit code
    output=$(bash "${test_file}" 2>&1)
    exit_code=$?

    echo "${output}"

    # Parse pass/fail/skip counts from output
    _pass=$(echo "${output}" | grep -c '✔\|PASS' 2>/dev/null || true)
    _fail=$(echo "${output}" | grep -c '✘\|FAIL' 2>/dev/null || true)
    _skip=$(echo "${output}" | grep -c 'SKIP' 2>/dev/null || true)
    TOTAL_PASS=$((TOTAL_PASS + _pass))
    TOTAL_FAIL=$((TOTAL_FAIL + _fail))
    TOTAL_SKIP=$((TOTAL_SKIP + _skip))

    if [[ "${exit_code}" -ne 0 ]]; then
        FAILED_SUITES+=("${suite_name}")
        ${FAIL_FAST} && break
    fi
    echo ""
done

echo "════════════════════════════════════════════════"
echo -e "${_BOLD}Test Suite Summary${_RESET}"
echo "════════════════════════════════════════════════"

if [[ "${#FAILED_SUITES[@]}" -eq 0 ]]; then
    echo -e "${_GREEN}All suites passed.${_RESET}"
else
    echo -e "${_RED}Failed suites:${_RESET}"
    for s in "${FAILED_SUITES[@]}"; do
        echo -e "  ${_RED}✘ ${s}${_RESET}"
    done
fi

echo ""
echo -e "  Suites run:   ${#TEST_FILES[@]}"
echo -e "  Suites failed: ${#FAILED_SUITES[@]}"
echo ""

[[ "${#FAILED_SUITES[@]}" -eq 0 ]]
