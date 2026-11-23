#!/usr/bin/env bash
# Tests for scripts/gen-checksums.sh (light/checksums.txt generator).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GEN="${REPO}/scripts/gen-checksums.sh"
INSTALLER="${REPO}/light/setup-wsl-maude.ps1"

suite_header "gen-checksums"

d="$(mktemp -d)"
trap 'rm -rf "$d"' EXIT

assert_file_exists "generator exists" "${GEN}"
assert_exit_zero "generator passes bash -n" bash -n "${GEN}"

# Run against a throwaway clone so the real repo is never modified and
# blob hashing is exercised against committed content.
if ! git -C "${REPO}" rev-parse --git-dir >/dev/null 2>&1; then
    skip "not a git checkout"
    suite_summary
    exit $?
fi
if ! git clone -q "${REPO}" "$d/r" >/dev/null 2>&1; then
    skip "git clone failed"
    suite_summary
    exit $?
fi
# Use the working-tree versions of the files under test (may be uncommitted).
mkdir -p "$d/r/scripts"
cp "${GEN}" "$d/r/scripts/gen-checksums.sh"
cp "${INSTALLER}" "$d/r/light/setup-wsl-maude.ps1"
(cd "$d/r" && git -c user.name=t -c user.email=t@t commit -qam "wip" >/dev/null 2>&1 || true)

out="$d/out.txt"
(cd "$d/r" && bash scripts/gen-checksums.sh --out "$out" >/dev/null)
assert_exit_zero "generator writes output" test -s "$out"

# Format: 64 hex, two spaces, path; LF only
bad=0
while IFS= read -r line; do
    [[ "$line" =~ ^[0-9a-f]{64}\ \ [^[:space:]]+$ ]] || bad=$((bad+1))
done < "$out"
assert_eq "all lines match '<sha256>  <path>'" "0" "$bad"
assert_eq "no CR in output" "0" "$(grep -c $'\r' "$out" || true)"

# Listed paths == Key entries in the installer
expected="$(tr -d '\r' < "${INSTALLER}" | grep -o 'Key *= *"[^"]*"' | cut -d'"' -f2 | LC_ALL=C sort -u)"
actual="$(awk '{print $2}' "$out")"
assert_eq "paths match installer download keys" "${expected}" "${actual}"
assert_eq "installer Url count equals listed file count" \
    "$(tr -d '\r' < "${INSTALLER}" | grep -c 'Url *= *"\$GH_RAW/')" "$(wc -l < "$out" | tr -d ' ')"
assert_eq "exactly 8 files listed" "8" "$(wc -l < "$out" | tr -d ' ')"

# A reformatted entry (Url/Key split over lines) must make the generator fail
if command -v python3 >/dev/null 2>&1; then
python3 - "$d/r/light/setup-wsl-maude.ps1" <<'PY'
import sys
p = sys.argv[1]
s = open(p, newline='').read()
i = s.index('Key = "light/maude-shell.sh"')
s = s[:i].rstrip(' ') + '\n    ' + s[i:]
open(p, 'w', newline='').write(s)
PY
(cd "$d/r" && git -c user.name=t -c user.email=t@t commit -qam "reformat" >/dev/null 2>&1 || true)
assert_exit_nonzero "generator fails when an entry is reformatted" \
    bash -c "cd '$d/r' && bash scripts/gen-checksums.sh --out '$d/x.txt'"
cp "${INSTALLER}" "$d/r/light/setup-wsl-maude.ps1"
(cd "$d/r" && git -c user.name=t -c user.email=t@t commit -qam "restore" >/dev/null 2>&1 || true)

# Two entries on one line must both be found (same output as the original)
python3 - "$d/r/light/setup-wsl-maude.ps1" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p, newline='').read()
s = re.sub(r'(Key = "light/root-bootstrap\.sh" \})\r?\n\s*', r'\1 ', s, count=1)
open(p, 'w', newline='').write(s)
PY
(cd "$d/r" && git -c user.name=t -c user.email=t@t commit -qam "join" >/dev/null 2>&1 || true)
joined_lines="$(cd "$d/r" && git show HEAD:light/setup-wsl-maude.ps1 | tr -d '\r' | grep -c 'root-bootstrap.sh".*maude-bootstrap.sh"')"
assert_eq "test setup: two entries joined on one line" "1" "${joined_lines}"
(cd "$d/r" && bash scripts/gen-checksums.sh --out "$d/joined.txt" >/dev/null 2>&1)
assert_eq "two entries on one line yield the same checksums" "$(cat "$out")" "$(cat "$d/joined.txt" 2>/dev/null)"
cp "${INSTALLER}" "$d/r/light/setup-wsl-maude.ps1"
(cd "$d/r" && git -c user.name=t -c user.email=t@t commit -qam "restore2" >/dev/null 2>&1 || true)
else
    skip "python3 not available for reformat test"
fi
assert_contains "lists light/root-bootstrap.sh" "light/root-bootstrap.sh" "${actual}"
assert_contains "lists packages/ubuntu-packages.yaml" "packages/ubuntu-packages.yaml" "${actual}"

# Hash is over git blob bytes
first_path="$(head -n1 "$out" | awk '{print $2}')"
want="$(cd "$d/r" && git cat-file blob "HEAD:${first_path}" | sha256sum | cut -d' ' -f1)"
assert_eq "hash matches git blob of ${first_path}" "$want" "$(head -n1 "$out" | cut -d' ' -f1)"

# --check: fresh passes, tampered fails, missing fails
assert_exit_zero "--check passes on fresh file" bash -c "cd '$d/r' && bash scripts/gen-checksums.sh --check --out '$out'"
sed 's/^./0/' "$out" > "$d/bad.txt"
assert_exit_nonzero "--check fails on stale file" bash -c "cd '$d/r' && bash scripts/gen-checksums.sh --check --out '$d/bad.txt'"
assert_exit_nonzero "--check fails on missing file" bash -c "cd '$d/r' && bash scripts/gen-checksums.sh --check --out '$d/nope.txt'"

suite_summary
