#!/usr/bin/env bash
# tests/test-self-update.sh - update validation for maude.py (Python updater)
# and light/maude (_validate_maude_py). Static checks always run; the cases that
# really import maude.py need textual/rich and are skipped without them.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "maude.py self-update validation"

MAUDE_PY="${SCRIPT_DIR}/../light/maude.py"
MAUDE_SH="${SCRIPT_DIR}/../light/maude"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

have_textual=0
python3 -c 'import textual, rich' >/dev/null 2>&1 && have_textual=1

# Pull only the update helpers out of maude.py (no textual import needed).
# No -I when running: textual is usually a user-site install, which -I hides.
cat > "$d/run.py" <<'PY'
import ast, re, subprocess, sys
mode, src_path, target = sys.argv[1:4]
src = open(src_path, "rb").read()
ns = {"re": re, "subprocess": subprocess, "sys": sys}
want = {"valid_update", "_IMPORT_CHECK_CODE", "import_check_update"}
for n in ast.parse(src).body:
    names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
    if names & want:
        exec(compile(ast.Module([n], []), "maude.py", "exec"), ns)
if mode == "static":
    print("True" if ns["valid_update"](open(target, "rb").read(), "maude.py") else "False")
else:
    print("OK" if ns["import_check_update"](target) == "" else "REJECT")
PY
static() { python3 "$d/run.py" static "$MAUDE_PY" "$1"; }
imp()    { python3 "$d/run.py" import "$MAUDE_PY" "$1"; }

# ── fixtures ───────────────────────────────────────────────────────────
cp "$MAUDE_PY" "$d/good.py"
sed 's/^import urllib\.error$/import urllib.erro/' "$MAUDE_PY" > "$d/badimport.py"
sed 's/^from pathlib import Path$/from pathlib import Pat/' "$MAUDE_PY" > "$d/badfrom.py"
sed 's/^IS_WIN = sys\.platform/IS_WIN = sy.platform/' "$MAUDE_PY" > "$d/nameerror.py"
{ echo "<html><body>"; for i in $(seq 1 500); do echo "Blocked by proxy policy $i"; done; echo "</body></html>"; } > "$d/page.html"
half=$(( $(wc -c < "$MAUDE_PY") / 2 ))
head -c "$half" "$MAUDE_PY" > "$d/half.py"
{ cat "$MAUDE_PY"; echo "def broken(:"; } > "$d/syntax.py"

# ── static checks (always run) ─────────────────────────────────────────
assert_eq "static: real maude.py passes"        "True"  "$(static "$d/good.py")"
assert_eq "static: HTML block page rejected"    "False" "$(static "$d/page.html")"
assert_eq "static: truncated file rejected"     "False" "$(static "$d/half.py")"
assert_eq "static: syntax error rejected"       "False" "$(static "$d/syntax.py")"
grep -v '^if __name__ == "__main__":' "$MAUDE_PY" > "$d/noguard.py"
assert_eq "static: missing __main__ guard rejected" "False" "$(static "$d/noguard.py")"

# ── real import checks (need textual) ──────────────────────────────────
if [[ $have_textual -eq 1 ]]; then
  assert_eq "import: real maude.py loads"            "OK"     "$(imp "$d/good.py")"
  assert_eq "import: urllib.erro rejected"           "REJECT" "$(imp "$d/badimport.py")"
  assert_eq "import: from pathlib import Pat rejected" "REJECT" "$(imp "$d/badfrom.py")"
  assert_eq "import: module-level NameError rejected" "REJECT" "$(imp "$d/nameerror.py")"
else
  skip "textual/rich not installed: import-check cases skipped"
fi

# ── bash validator in light/maude ──────────────────────────────────────
sed -n '/^_MAUDE_PY_CHECK=/,/^'"'"'$/p;/^_validate_maude_py() {/,/^}/p' "$MAUDE_SH" > "$d/validator.sh"
source "$d/validator.sh"
assert_exit_nonzero "bash: HTML page rejected"        _validate_maude_py "$d/page.html"
assert_exit_nonzero "bash: truncated file rejected"   _validate_maude_py "$d/half.py"
assert_exit_nonzero "bash: missing __main__ guard rejected" _validate_maude_py "$d/noguard.py"
if [[ $have_textual -eq 1 ]]; then
  assert_exit_zero    "bash: real maude.py accepted"          _validate_maude_py "$d/good.py"
  assert_exit_nonzero "bash: urllib.erro rejected"            _validate_maude_py "$d/badimport.py"
  assert_exit_nonzero "bash: from pathlib import Pat rejected" _validate_maude_py "$d/badfrom.py"
  assert_exit_nonzero "bash: module-level NameError rejected" _validate_maude_py "$d/nameerror.py"
else
  skip "textual/rich not installed: bash import-check cases skipped"
fi

suite_summary
