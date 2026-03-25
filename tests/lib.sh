#!/usr/bin/env bash
# tests/lib.sh — shared helpers for maude test suite
# Source this file from individual test scripts.
#
# Note: use  PASS=$((PASS+1))  rather than  ((PASS++))  to avoid
# bash set -e treating arithmetic-zero as a non-zero exit code.

PASS=0; FAIL=0; SKIP=0
_RED='\033[0;31m'; _GREEN='\033[0;32m'; _YELLOW='\033[1;33m'
_CYAN='\033[0;36m'; _RESET='\033[0m'

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [[ "${expected}" == "${actual}" ]]; then
        echo -e "  ${_GREEN}PASS${_RESET} ${desc}"
        PASS=$((PASS+1))
    else
        echo -e "  ${_RED}FAIL${_RESET} ${desc}"
        echo -e "       expected: ${_CYAN}${expected}${_RESET}"
        echo -e "       actual:   ${_CYAN}${actual}${_RESET}"
        FAIL=$((FAIL+1))
    fi
}

assert_contains() {
    local desc="$1" needle="$2" haystack="$3"
    if [[ "${haystack}" == *"${needle}"* ]]; then
        echo -e "  ${_GREEN}PASS${_RESET} ${desc}"
        PASS=$((PASS+1))
    else
        echo -e "  ${_RED}FAIL${_RESET} ${desc}: '${needle}' not found in output"
        FAIL=$((FAIL+1))
    fi
}

assert_file_exists() {
    local desc="$1" path="$2"
    if [[ -f "${path}" ]]; then
        echo -e "  ${_GREEN}PASS${_RESET} ${desc}"
        PASS=$((PASS+1))
    else
        echo -e "  ${_RED}FAIL${_RESET} ${desc}: file not found: ${path}"
        FAIL=$((FAIL+1))
    fi
}

assert_executable() {
    local desc="$1" path="$2"
    if [[ -x "${path}" ]]; then
        echo -e "  ${_GREEN}PASS${_RESET} ${desc}"
        PASS=$((PASS+1))
    else
        echo -e "  ${_RED}FAIL${_RESET} ${desc}: not executable: ${path}"
        FAIL=$((FAIL+1))
    fi
}

assert_exit_zero() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo -e "  ${_GREEN}PASS${_RESET} ${desc}"
        PASS=$((PASS+1))
    else
        echo -e "  ${_RED}FAIL${_RESET} ${desc}: command exited non-zero: $*"
        FAIL=$((FAIL+1))
    fi
}

assert_exit_nonzero() {
    local desc="$1"; shift
    if ! "$@" >/dev/null 2>&1; then
        echo -e "  ${_GREEN}PASS${_RESET} ${desc}"
        PASS=$((PASS+1))
    else
        echo -e "  ${_RED}FAIL${_RESET} ${desc}: expected non-zero exit from: $*"
        FAIL=$((FAIL+1))
    fi
}

skip() {
    echo -e "  ${_YELLOW}SKIP${_RESET} $1"
    SKIP=$((SKIP+1))
}

suite_header() {
    echo ""
    echo -e "${_CYAN}▶ $1${_RESET}"
}

suite_summary() {
    echo ""
    echo "────────────────────────────────"
    local total=$((PASS + FAIL + SKIP))
    echo -e "Results: ${_GREEN}${PASS} passed${_RESET} / ${_RED}${FAIL} failed${_RESET} / ${_YELLOW}${SKIP} skipped${_RESET} (${total} total)"
    [[ "${FAIL}" -eq 0 ]]  # exit 0 if all pass
}
