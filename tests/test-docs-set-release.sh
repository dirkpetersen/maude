#!/usr/bin/env bash
# tests/test-docs-set-release.sh - scripts/docs-set-release.py rewrites maude.exe links.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "docs-set-release"

REPO="${SCRIPT_DIR}/.."
TOOL="${REPO}/scripts/docs-set-release.py"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

fresh() { rm -rf "$d/docs"; mkdir -p "$d/docs"; cp "$REPO/docs/index.md" "$REPO/docs/windows-exe.md" "$d/docs/"; }

fresh
out=$("$PY" "$TOOL" v9.9.9 "$d/docs")
assert_contains "reports update" "2 file(s) updated to v9.9.9" "$out"
assert_contains "index link url" "releases/download/v9.9.9/maude.exe)" "$(cat "$d/docs/index.md")"
assert_contains "index link text" "Download maude.exe (v9.9.9)" "$(cat "$d/docs/index.md")"
assert_contains "sha256 url" "releases/download/v9.9.9/maude.exe.sha256" "$(cat "$d/docs/windows-exe.md")"
# Other projects' release URLs are untouched
assert_contains "Git for Windows url kept" "git-for-windows/git/releases/download/<tag>/<asset>" "$(cat "$d/docs/windows-exe.md")"
assert_contains "gh url kept" "cli/cli/releases/download/<tag>/" "$(cat "$d/docs/windows-exe.md")"
other_before=$(grep -c 'for-windows/git/releases/download\|cli/cli/releases/download' "$REPO/docs/windows-exe.md" || true)
other_after=$(grep -c 'for-windows/git/releases/download\|cli/cli/releases/download' "$d/docs/windows-exe.md" || true)
assert_eq "other release url count unchanged" "$other_before" "$other_after"

for bad in "v1.2" "v1.2.3-rc1" ""; do
    fresh
    rm -rf "$d/orig"
    cp -r "$d/docs" "$d/orig"
    "$PY" "$TOOL" "$bad" "$d/docs" >/dev/null
    if diff -r "$d/orig" "$d/docs" >/dev/null; then
        assert_eq "no-op for '${bad}'" "same" "same"
    else
        assert_eq "no-op for '${bad}'" "same" "changed"
    fi
done

suite_summary
