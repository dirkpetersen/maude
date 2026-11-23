#!/usr/bin/env bash
# tests/test-perplexity-sync.sh - sync_perplexity() decision table with a fake `claude`.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "perplexity connector sync"

MAUDE_PY="${SCRIPT_DIR}/../light/maude.py"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir -p "$d/bin" "$d/home/.local/bin"
# Fake claude: logs args; `mcp get` result is driven by FAKE_GET (missing|nc|ok).
cat > "$d/home/.local/bin/claude" <<'FAKE'
#!/bin/sh
echo "$*" >> "$CALLS"
if [ "$1 $2" = "mcp get" ]; then
  case "$FAKE_GET" in
    missing) exit 1 ;;
    nc) printf 'perplexity-computer:\n  Status: x Needs authentication\n'; exit 0 ;;
    ok) printf 'perplexity-computer:\n  Status: v Connected\n'; exit 0 ;;
  esac
fi
exit 0
FAKE
chmod +x "$d/home/.local/bin/claude"
# Decoy wrapper on PATH: must never be used for `mcp` (the real wrapper breaks --transport).
printf '#!/bin/sh\necho WRAPPER-USED >> "$CALLS"\nexit 1\n' > "$d/bin/claude"
chmod +x "$d/bin/claude"

# textual may be absent: pull only the needed names out of maude.py.
cat > "$d/run.py" <<'PY'
import ast, os, subprocess, sys
from pathlib import Path
src = open(sys.argv[1]).read()
want = {"PLATFORM", "IS_WIN", "IS_MAC", "PERPLEXITY_FLAG", "PERPLEXITY_NAME", "PERPLEXITY_URL", "_claude_mcp",
        "sync_perplexity", "remove_perplexity", "plat_tool", "plat_real_claude", "plat_claude_env", "_child_env"}
ns = {"os": os, "sys": sys, "subprocess": subprocess, "Path": Path, "shutil": __import__("shutil")}
for n in ast.parse(src).body:
    names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
    if names & want:
        exec(compile(ast.Module([n], []), "maude.py", "exec"), ns)
ns[sys.argv[2] if len(sys.argv) > 2 else "sync_perplexity"]()
PY

run() { # flag(on|off) get-mode [function]
  rm -f "$d/calls" "$d/home/.maude-perplexity"
  [ "$1" = on ] && : > "$d/home/.maude-perplexity"
  ( HOME="$d/home" PATH="$d/bin:$PATH" CALLS="$d/calls" FAKE_GET="$2" \
    python3 -I "$d/run.py" "$MAUDE_PY" ${3:-sync_perplexity} ) >/dev/null 2>&1 || true
  { tr "\n" ";" < "$d/calls"; } 2>/dev/null || true
}

U=perplexity-computer
URL=https://www.perplexity.ai/rest/computer/mcp
assert_eq "on, missing"        "mcp get $U;mcp add --transport http --scope user $U $URL;mcp login $U;" "$(run on missing)"
assert_eq "on, not connected"  "mcp get $U;mcp login $U;" "$(run on nc)"
assert_eq "on, connected"      "mcp get $U;" "$(run on ok)"
assert_eq "off, exists: untouched" ""  "$(run off ok)"
assert_eq "off, missing: untouched" "" "$(run off missing)"
assert_eq "untick removes"     "mcp remove --scope user $U;" "$(run off ok remove_perplexity)"

# Missing claude binary must not crash (PATH holds python3 only, via a symlink dir).
mkdir -p "$d/pybin"; ln -s "$(command -v python3)" "$d/pybin/python3"
( HOME="$d/home" PATH="$d/pybin" python3 -I "$d/run.py" "$MAUDE_PY" ${3:-sync_perplexity} ) >/dev/null 2>&1 \
  && ok=yes || ok=no
assert_eq "no claude binary: no crash" "yes" "$ok"
suite_summary
