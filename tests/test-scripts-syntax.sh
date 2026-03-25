#!/usr/bin/env bash
# tests/test-scripts-syntax.sh
# Bash syntax check on all maude shell scripts.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

suite_header "Shell Script Syntax (bash -n)"

SCRIPTS_DIR="${SCRIPT_DIR}/../scripts"

# Collect all .sh files and scripts without extension
mapfile -t SCRIPTS < <(
    find "${SCRIPTS_DIR}" -type f \( -name "*.sh" -o -name "maude-*" \) | sort
)

for script in "${SCRIPTS[@]}"; do
    name="${script#${SCRIPTS_DIR}/}"
    if bash -n "${script}" 2>/dev/null; then
        assert_eq "Syntax OK: ${name}" "ok" "ok"
    else
        _err=$(bash -n "${script}" 2>&1 || true)
        assert_eq "Syntax OK: ${name}" "ok" "SYNTAX_ERROR: ${_err}"
    fi
done

suite_header "Shebang lines present"
for script in "${SCRIPTS[@]}"; do
    name="${script#${SCRIPTS_DIR}/}"
    _shebang=$(head -1 "${script}")
    if [[ "${_shebang}" == "#!/"* ]]; then
        assert_eq "Has shebang: ${name}" "ok" "ok"
    else
        assert_eq "Has shebang: ${name}" "ok" "MISSING_SHEBANG"
    fi
done

suite_header "set -o errexit in scripts (excludes profile.d — sourced into interactive shells)"
for script in "${SCRIPTS[@]}"; do
    name="${script#${SCRIPTS_DIR}/}"
    # profile.d scripts are sourced; set -e there causes interactive shell breakage
    if [[ "${name}" == profile.d/* ]]; then
        skip "errexit exempt (sourced profile.d): ${name}"
        continue
    fi
    if grep -q "set -o errexit" "${script}" || grep -q "set -e" "${script}"; then
        assert_eq "Has errexit: ${name}" "ok" "ok"
    else
        assert_eq "Has errexit: ${name}" "ok" "MISSING"
    fi
done

suite_summary
