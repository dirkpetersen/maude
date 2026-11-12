#!/usr/bin/env bash
# tests/test-kanna-layout.sh - ensure_kanna_layout: ~/.kanna local, only data/ on the host share.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "kanna layout"

LIB="${SCRIPT_DIR}/../light/lib/kanna.sh"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
lay() { ( HOME="$1"; . "$LIB"; ensure_kanna_layout ) 2>/dev/null; }
isreal() { [ -d "$1" ] && [ ! -L "$1" ] && echo yes || echo no; }

# (a) old layout: ~/.kanna -> ~/Maude/.kanna
h="$d/a"; mkdir -p "$h/Maude/.kanna/data"
echo chat > "$h/Maude/.kanna/data/chats.jsonl"; echo '{}' > "$h/Maude/.kanna/keybindings.json"
ln -s "$h/Maude/.kanna" "$h/.kanna"
lay "$h"
assert_eq "a: ~/.kanna is a real dir" yes "$(isreal "$h/.kanna")"
assert_eq "a: data is a symlink to host" "$h/Maude/.kanna/data" "$(readlink "$h/.kanna/data")"
assert_eq "a: chats.jsonl readable" chat "$(cat "$h/.kanna/data/chats.jsonl")"
assert_eq "a: host chats.jsonl intact" chat "$(cat "$h/Maude/.kanna/data/chats.jsonl")"
assert_file_exists "a: keybindings copied" "$h/.kanna/keybindings.json"

# (b) second run is a no-op
before=$(cd "$h/.kanna" && ls -la)
lay "$h"
assert_eq "b: second run no change" "$before" "$(cd "$h/.kanna" && ls -la)"

# (c) real local data dir, empty host data
h="$d/c"; mkdir -p "$h/Maude/.kanna/data" "$h/.kanna/data"
echo local > "$h/.kanna/data/x.json"
lay "$h"
assert_eq "c: content moved to host" local "$(cat "$h/Maude/.kanna/data/x.json")"
assert_file_exists "c: local backup kept" "$(ls -d "$h"/.kanna/data.local-backup.*/x.json)"
assert_eq "c: data is a symlink" "$h/Maude/.kanna/data" "$(readlink "$h/.kanna/data")"

# (d) no ~/Maude, and empty ~/Maude (unmounted mountpoint)
h="$d/d"; mkdir -p "$h"
lay "$h"
assert_eq "d: local data dir" yes "$(isreal "$h/.kanna/data")"
h="$d/d2"; mkdir -p "$h/Maude"
lay "$h"
assert_eq "d2: empty Maude: local data dir" yes "$(isreal "$h/.kanna/data")"
assert_eq "d2: no ~/Maude/.kanna created" no "$([ -e "$h/Maude/.kanna" ] && echo yes || echo no)"

# (f) real ~/.kanna with dangling data symlink, empty ~/Maude
h="$d/f"; mkdir -p "$h/Maude" "$h/.kanna"
ln -s "$h/Maude/.kanna/data" "$h/.kanna/data"
lay "$h"
assert_eq "f: dangling data repaired" yes "$(isreal "$h/.kanna/data")"

# (e) dangling symlink
h="$d/e"; mkdir -p "$h/Maude/Projects"
ln -s "$h/Maude/gone/.kanna" "$h/.kanna"
lay "$h"
assert_eq "e: ~/.kanna real dir" yes "$(isreal "$h/.kanna")"
assert_eq "e: data symlink" "$h/Maude/.kanna/data" "$(readlink "$h/.kanna/data")"
suite_summary
