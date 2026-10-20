#!/usr/bin/env bash
# tests/test-dev-mode.sh - run a copy of light/dev-mode.sh against a scratch dir
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

suite_header "dev-mode.sh"
bash -n "${SCRIPT_DIR}/../light/dev-mode.sh"

d=$(mktemp -d)
mkdir -p "$d/etc/sudoers.d" "$d/bin"
printf '[user]\ndefault=tester\n' > "$d/etc/wsl.conf"
# stubs: pretend to be root, user exists, sudoers file is valid
printf '#!/bin/sh\n[ "$1" = "-u" ] && echo 0\nexit 0\n' > "$d/bin/id"
printf '#!/bin/sh\nexit 0\n' > "$d/bin/visudo"
# passwd stub: -S prints $PW_STATE; plain passwd exits $PW_RC
printf '#!/bin/sh\nif [ "$1" = "-S" ]; then echo "$2 $PW_STATE"; exit 0; fi\nexit "${PW_RC:-0}"\n' > "$d/bin/passwd"
chmod +x "$d/bin/id" "$d/bin/visudo" "$d/bin/passwd"
sed -e "s#/etc/#$d/etc/#g" -e 's/\[ -t 0 \]/true/' "${SCRIPT_DIR}/../light/dev-mode.sh" > "$d/dev-mode.sh"
cp "$d/etc/wsl.conf" "$d/wsl.conf.orig"

# no password and passwd fails: nothing changes
rc=0; PW_STATE="NP" PW_RC=1 PATH="$d/bin:$PATH" bash "$d/dev-mode.sh" >/dev/null 2>&1 || rc=$?
assert_eq "passwd failure exits non-zero" "1" "$rc"
assert_eq "no sudoers file on failure" "no" "$([ -e "$d/etc/sudoers.d/maude-dev" ] && echo yes || echo no)"
assert_eq "wsl.conf unchanged on failure" "$(cat "$d/wsl.conf.orig")" "$(cat "$d/etc/wsl.conf")"

# user already has a password: no prompt, passwd would fail if called
PW_STATE="P" PW_RC=1 PATH="$d/bin:$PATH" bash "$d/dev-mode.sh" >/dev/null
conf=$(cat "$d/etc/wsl.conf")
assert_contains "systemd kept"      "systemd = true" "$conf"
assert_contains "user kept"         "default=tester" "$conf"
assert_contains "mountFsTab kept"   "mountFsTab = true" "$conf"
assert_contains "appendWindowsPath" "appendWindowsPath = true" "$conf"
assert_contains "automount on"      "[automount]"$'\n'"enabled = true" "$conf"
assert_contains "interop on"        "[interop]"$'\n'"enabled = true" "$conf"
assert_eq "sudoers line" "tester ALL=(ALL) ALL" "$(cat "$d/etc/sudoers.d/maude-dev")"

# no password and passwd succeeds
rm -f "$d/etc/sudoers.d/maude-dev"
PW_STATE="NP" PW_RC=0 PATH="$d/bin:$PATH" bash "$d/dev-mode.sh" >/dev/null
assert_eq "sudoers after passwd set" "tester ALL=(ALL) ALL" "$(cat "$d/etc/sudoers.d/maude-dev")"

rm -rf "$d"
suite_summary
