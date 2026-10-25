#!/usr/bin/env bash
# tests/test-add-user.sh - run a copy of light/add-user.sh against a scratch dir
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

suite_header "add-user.sh"
bash -n "${SCRIPT_DIR}/../light/add-user.sh"

d=$(mktemp -d)
mkdir -p "$d/etc/sudoers.d" "$d/bin" "$d/home" "$d/mnt"
printf '[user]\ndefault=tester\n' > "$d/etc/wsl.conf"
echo 1000 > "$d/user-tester"; echo 65534 > "$d/user-nobody"; echo 5 > "$d/user-daemon"
# stubs: root; id reads $d/user-<name>; useradd creates it with uid 1001
cat > "$d/bin/id" << STUB
#!/bin/sh
[ -z "\$2" ] && { echo 0; exit 0; }
[ -e "$d/user-\$2" ] && { cat "$d/user-\$2"; exit 0; }
exit 1
STUB
printf '#!/bin/sh\nfor a; do u=$a; done\necho 1001 > "%s/user-$u"\nmkdir -p "%s/home/$u"\n' "$d" "$d" > "$d/bin/useradd"
printf '#!/bin/sh\nif [ "$1" = "-S" ]; then echo "$2 $PW_STATE"; exit 0; fi\nexit "${PW_RC:-0}"\n' > "$d/bin/passwd"
printf '#!/bin/sh\nexit 0\n' > "$d/bin/visudo"
printf '#!/bin/sh\necho "$2:x:1:1::%s/home/$2:/bin/bash"\n' "$d" > "$d/bin/getent"
cp "$d/bin/visudo" "$d/bin/chown"; cp "$d/bin/visudo" "$d/bin/mount"
printf '#!/bin/sh\nexit 1\n' > "$d/bin/mountpoint"
chmod +x "$d"/bin/*
sed -e "s#/etc/#$d/etc/#g" -e "s#/mnt/#$d/mnt/#g" -e "s#/home/#$d/home/#g" -e 's/\[ -t 0 \]/true/' \
  "${SCRIPT_DIR}/../light/add-user.sh" > "$d/add-user.sh"
run() { PATH="$d/bin:$PATH" bash "$d/add-user.sh" "$@" >/dev/null 2>&1; }

rc=0; run Bad_Name c || rc=$?;   assert_eq "bad username rejected" "1" "$rc"
rc=0; run tester c || rc=$?;     assert_eq "default user rejected" "1" "$rc"
rc=0; run daemon c || rc=$?;     assert_eq "system account rejected" "1" "$rc"
rc=0; run nobody c || rc=$?;    assert_eq "uid 65534 rejected" "1" "$rc"
mv "$d/etc/wsl.conf" "$d/wsl.bak"
rc=0; run dp c || rc=$?;         assert_eq "missing default= rejected" "1" "$rc"
mv "$d/wsl.bak" "$d/etc/wsl.conf"
rc=0; run dp 1 || rc=$?;         assert_eq "bad drive letter rejected" "1" "$rc"

rc=0; PW_STATE=NP PW_RC=1 run dp c || rc=$?
assert_eq "passwd failure exits non-zero" "1" "$rc"
assert_eq "no sudoers file on passwd failure" "no" "$([ -e "$d/etc/sudoers.d/dp" ] && echo yes || echo no)"

PW_STATE=P run dp c d
assert_eq "sudoers line" "dp ALL=(ALL) ALL" "$(cat "$d/etc/sudoers.d/dp")"
assert_eq "no NOPASSWD" "0" "$(grep -c NOPASSWD "$d/etc/sudoers.d/dp" || true)"
for l in C D; do
  assert_contains "fstab $l" "$l: $d/mnt/${l,,} drvfs defaults,uid=1001,gid=1001,umask=077 0 0" "$(cat "$d/etc/fstab")"
done
PW_STATE=P run dp c d
assert_eq "rerun keeps two fstab lines" "2" "$(wc -l < "$d/etc/fstab")"

# stale hand-written line is replaced, and a rerun keeps one line
printf 'C: %s/mnt/c drvfs defaults 0 0\n' "$d" > "$d/etc/fstab"
PW_STATE=P run dp c
PW_STATE=P run dp c
assert_eq "stale fstab line replaced" "C: $d/mnt/c drvfs defaults,uid=1001,gid=1001,umask=077 0 0" "$(cat "$d/etc/fstab")"

rm -rf "$d"
suite_summary
