#!/usr/bin/env bash
# tests/test-cli-tools.sh - light/lib/cli-tools.sh: install-if-missing for Antigravity (agy) and
# GitHub Copilot CLI (copilot), plus the wiring of all four new agents (Codex, Antigravity, Grok,
# Copilot) into the bootstrap and `maude update`. NO real network: `curl` is replaced by a fake first
# on PATH that serves local files and logs every URL. Every scratch HOME is a mktemp dir, removed by path.
set -u
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$REPO_ROOT/light/lib/cli-tools.sh"
suite_header "cli-tools.sh: Antigravity + Copilot CLI installers"

assert_exit_zero "bash -n cli-tools.sh" bash -n "$LIB"
# shellcheck source=/dev/null
source "$LIB"
for _fn in update_kiro update_crush update_antigravity update_copilot link_kiro_skills; do
    assert_exit_zero "function $_fn defined" declare -F "$_fn"
done

d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir -p "$d/bin" "$d/serve"

# What the fake curl serves: <mode> selects good / badsha / foreign url
case "$(uname -m)" in aarch64|arm64) _arch=arm64 ;; *) _arch=amd64 ;; esac
printf 'agy-binary' > "$d/serve/antigravity"
( cd "$d/serve" && tar -czf agy.tar.gz antigravity )
_sha=$(sha512sum "$d/serve/agy.tar.gz" | cut -d' ' -f1)
_zero=$(printf '0%.0s' $(seq 128))
AGY_URL=https://storage.googleapis.com/antigravity-public/x/cli_linux.tar.gz
manifest() { printf '{\n  "version": "1",\n  "url": "%s",\n  "sha512": "%s"\n}\n' "$1" "$2"; }
manifest "$AGY_URL" "$_sha" > "$d/serve/good.json"
manifest "$AGY_URL" "$_zero" > "$d/serve/badsha.json"
manifest "https://evil.example/agy.tar.gz" "$_sha" > "$d/serve/foreign.json"
manifest "https://storage.googleapis.com/antigravity-public/../other-bucket/x.tar.gz" "$_sha" > "$d/serve/dotdot.json"
manifest "https://storage.googleapis.com/antigravity-public//x.tar.gz" "$_sha" > "$d/serve/dslash.json"
manifest "http://storage.googleapis.com/antigravity-public/x/a.tar.gz" "$_sha" > "$d/serve/http.json"
# a stand-in for the Copilot vendor script: records its environment, installs $PREFIX/bin/copilot
cat > "$d/serve/copilot-install.sh" <<'EOS'
#!/usr/bin/env bash
echo "PREFIX=$PREFIX" >> "$FAKE_LOG"
echo "PATHHAS=$([[ ":$PATH:" == *":$HOME/.local/bin:"* ]] && echo yes || echo no)" >> "$FAKE_LOG"
echo "STDIN=$([[ -t 0 ]] && echo tty || echo notty)" >> "$FAKE_LOG"
[[ "${FAKE_MODE:-}" == "installfail" ]] && exit 1
mkdir -p "$PREFIX/bin" && printf '#!/bin/sh\n' > "$PREFIX/bin/copilot" && chmod +x "$PREFIX/bin/copilot"
EOS
cat > "$d/bin/curl" <<'EOS'
#!/usr/bin/env bash
# fake curl: logs the URL; -o FILE gets a local file chosen by URL
out="" url=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        --max-time|--proto|--proto-redir|--speed-limit|--speed-time|--connect-timeout) echo "FLAG $1 $2" >> "$FAKE_LOG"; shift 2 ;;
        --path-as-is) echo "FLAG --path-as-is" >> "$FAKE_LOG"; shift ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
echo "$url" >> "$FAKE_LOG"
case "$url" in
    */manifests/linux_*.json) src="$FAKE_SERVE/${FAKE_MANIFEST:-good}.json" ;;
    https://storage.googleapis.com/*) src="$FAKE_SERVE/agy.tar.gz" ;;
    https://gh.io/copilot-install) src="$FAKE_SERVE/copilot-install.sh" ;;
    *) exit 22 ;;
esac
[[ "${FAKE_MODE:-}" == "offline" ]] && exit 6
cp "$src" "$out"
EOS
chmod +x "$d/bin/curl"

# run a function with a scratch HOME, the fake curl first on PATH, output in $RUN_OUT, status in $RUN_RC
run() {  # run <home-dir> <manifest> <mode> <function>
    : > "$d/log"
    RUN_OUT=$(HOME="$1" PATH="$d/bin:$PATH" FAKE_LOG="$d/log" FAKE_SERVE="$d/serve" FAKE_MANIFEST="$2" FAKE_MODE="$3" \
              MAUDE_AGY_MANIFEST_BASE="https://manifest.example" "$4" 2>&1)
    RUN_RC=$?
}

# -- Antigravity --
h="$d/h1"; mkdir -p "$h"
run "$h" good ok update_antigravity
assert_eq "agy: installed" "cli: installed" "$RUN_OUT"
assert_eq "agy: exit 0" "0" "$RUN_RC"
assert_executable "agy: ~/.local/bin/agy" "$h/.local/bin/agy"
assert_eq "agy: binary content from the archive" "agy-binary" "$(cat "$h/.local/bin/agy")"
assert_contains "agy: manifest of this CPU" "/manifests/linux_${_arch}.json" "$(cat "$d/log")"
assert_eq "agy: every URL is https" "0" "$(grep -v '^FLAG' "$d/log" | grep -vc '^https://')"
run "$h" good ok update_antigravity
assert_eq "agy: second run is a no-op" "cli: installed (updates itself)" "$RUN_OUT"
assert_eq "agy: no network on the no-op run" "0" "$(wc -l < "$d/log" | tr -d ' ')"

h="$d/h2"; mkdir -p "$h"
run "$h" badsha ok update_antigravity
assert_eq "agy: wrong SHA-512 -> failed" "cli: failed" "$RUN_OUT"
assert_eq "agy: wrong SHA-512 -> non-zero" "1" "$RUN_RC"
assert_eq "agy: nothing installed on a checksum mismatch" "no" "$([[ -e "$h/.local/bin/agy" ]] && echo yes || echo no)"
h="$d/h3"; mkdir -p "$h"
run "$h" foreign ok update_antigravity
assert_eq "agy: manifest pointing off storage.googleapis.com -> failed" "cli: failed" "$RUN_OUT"
assert_eq "agy: the foreign URL is never requested" "0" "$(grep -c evil.example "$d/log")"
run "$h" http ok update_antigravity
assert_eq "agy: http archive url refused" "cli: failed" "$RUN_OUT"
run "$h" dotdot ok update_antigravity
assert_eq "agy: /../ in the archive url refused" "cli: failed" "$RUN_OUT"
assert_eq "agy: the dot-dot url is never requested" "0" "$(grep -c other-bucket "$d/log")"
run "$h" dslash ok update_antigravity
assert_eq "agy: // in the archive url refused" "cli: failed" "$RUN_OUT"
run "$h" good ok update_antigravity
assert_contains "agy: https-only, no path normalisation, stall-based timeout" "FLAG --speed-limit 10000" "$(cat "$d/log")"
assert_contains "agy: --proto =https" "FLAG --proto =https" "$(cat "$d/log")"
assert_contains "agy: --proto-redir =https" "FLAG --proto-redir =https" "$(cat "$d/log")"
assert_contains "agy: --path-as-is" "FLAG --path-as-is" "$(cat "$d/log")"
assert_contains "agy: generous total cap on the archive" "FLAG --max-time 3600" "$(cat "$d/log")"
assert_contains "agy: connect timeout" "FLAG --connect-timeout 30" "$(cat "$d/log")"
rm -f "$h/.local/bin/agy"
run "$h" good offline update_antigravity
assert_eq "agy: offline -> failed, not an error trace" "cli: failed" "$RUN_OUT"

# -- Copilot CLI --
h="$d/h4"; mkdir -p "$h"
run "$h" good ok update_copilot
assert_eq "copilot: installed" "cli: installed" "$RUN_OUT"
assert_executable "copilot: ~/.local/bin/copilot" "$h/.local/bin/copilot"
assert_contains "copilot: official script URL" "https://gh.io/copilot-install" "$(cat "$d/log")"
assert_contains "copilot: PREFIX is ~/.local" "PREFIX=$h/.local" "$(cat "$d/log")"
assert_contains "copilot: ~/.local/bin on PATH for the run (no shell-profile prompt)" "PATHHAS=yes" "$(cat "$d/log")"
assert_contains "copilot: script gets no terminal on stdin" "STDIN=notty" "$(cat "$d/log")"
assert_contains "copilot: curl is https-only and path-as-is" "FLAG --path-as-is" "$(cat "$d/log")"
assert_contains "copilot: --proto-redir =https" "FLAG --proto-redir =https" "$(cat "$d/log")"
run "$h" good ok update_copilot
assert_eq "copilot: second run is a no-op" "cli: installed (update with 'copilot update')" "$RUN_OUT"
h="$d/h5"; mkdir -p "$h"
run "$h" good installfail update_copilot
assert_eq "copilot: failing vendor script -> failed" "cli: failed" "$RUN_OUT"
assert_eq "copilot: failing vendor script -> non-zero" "1" "$RUN_RC"
run "$h" good offline update_copilot
assert_eq "copilot: offline -> failed" "cli: failed" "$RUN_OUT"

# -- Wiring: bootstrap and `maude update` install all four new agents --
BOOT="$REPO_ROOT/light/maude-bootstrap.sh"
MAUDE="$REPO_ROOT/light/maude"
assert_contains "bootstrap installs antigravity + copilot" "for _tool in kiro crush antigravity copilot; do" "$(cat "$BOOT")"
assert_contains "bootstrap installs codex + grok (reviewer libs)" "for _tool in codex opencode grok; do" "$(cat "$BOOT")"
assert_eq "maude update runs codex/grok/antigravity/copilot jobs and prints them" "2" "$(grep -c 'for _rtool in codex opencode grok kiro crush antigravity copilot; do' "$MAUDE")"
assert_contains "maude update: cli-tools names antigravity/copilot" "kiro|crush|antigravity|copilot) _lf=cli-tools" "$(cat "$MAUDE")"
assert_contains "maude update: install-if-missing message covers them" "kiro|crush|antigravity|copilot) echo" "$(cat "$MAUDE")"
assert_contains "codex installer lib installs the codex binary" 'codex_bin="$HOME/.local/bin/codex"' "$(cat "$REPO_ROOT/light/lib/codex.sh")"
assert_contains "grok installer lib installs the grok binary" 'grok_bin="$HOME/.local/bin/grok"' "$(cat "$REPO_ROOT/light/lib/grok.sh")"

suite_summary
