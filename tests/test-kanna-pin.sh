#!/usr/bin/env bash
# tests/test-kanna-pin.sh - ensure_kanna_ready reinstalls kanna-code only on a version mismatch.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "kanna version pin"

LIB="${SCRIPT_DIR}/../light/lib/kanna.sh"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir -p "$d/bin"
printf '#!/bin/sh\necho "$@" >> "%s/calls"\n' "$d" > "$d/bin/bun"
chmod +x "$d/bin/bun"
pj="$d/home/.bun/install/global/node_modules/kanna-code"

run() { rm -f "$d/calls"; ( HOME="$d/home" PATH="$d/bin:$PATH" KANNA_PKG="kanna-code@0.88.0"; . "$LIB"; ensure_kanna_ready ) >/dev/null 2>&1 || true; }

mkdir -p "$d/home" "$pj"
run
assert_eq "no package.json: bun not called" "no" "$([ -f "$d/calls" ] && echo yes || echo no)"

echo '{ "name": "kanna-code", "version": "0.77.1" }' > "$pj/package.json"
run
assert_eq "version differs: bun install -g pin" "install -g kanna-code@0.88.0" "$(cat "$d/calls" 2>/dev/null)"

echo '{ "name": "kanna-code", "version": "0.88.0" }' > "$pj/package.json"
run
assert_eq "version matches: bun not called" "no" "$([ -f "$d/calls" ] && echo yes || echo no)"

need() { ( HOME="$d/home" PATH="$d/bin:$PATH" KANNA_PKG="kanna-code@0.88.0"; . "$LIB"; kanna_needs_fix ) >/dev/null 2>&1 && echo yes || echo no; }
assert_eq "needs_fix: match -> no" no "$(need)"
echo '{ "name": "kanna-code", "version": "0.77.1" }' > "$pj/package.json"
assert_eq "needs_fix: mismatch -> yes" yes "$(need)"
rm -f "$pj/package.json"
assert_eq "needs_fix: missing package.json -> no" no "$(need)"
suite_summary
