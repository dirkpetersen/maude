# cli-tools.sh — sourced by maude-bootstrap.sh and the maude CLI.
# Install-if-missing for the extra TUI "AI tool" CLIs: Kiro CLI and Crush.
# Both print one `cli:` status line and return non-zero on failure, like the
# reviewer libs, so update_all and the bootstrap loops can treat them the same.

update_kiro() {
    local bin="$HOME/.local/bin/kiro-cli"
    if [[ -x "$bin" ]]; then echo "cli: installed (update with 'kiro-cli update')"; return 0; fi
    # Download to a private temp dir first, then run (no curl | bash).
    # --no-modify-path: Maude manages ~/.bashrc and ~/.local/bin is on PATH.
    local d rc=1
    d=$(mktemp -d) || { echo "cli: failed (no temp dir)"; return 1; }
    if curl -fsSL --max-time 60 https://cli.kiro.dev/install -o "$d/install.sh" \
       && timeout 300 bash "$d/install.sh" --no-modify-path --force </dev/null >/dev/null 2>&1 \
       && [[ -x "$bin" ]]; then
        rc=0
    fi
    rm -rf "$d"
    if [[ $rc -eq 0 ]]; then echo "cli: installed"; else echo "cli: failed"; fi
    return $rc
}

update_crush() {
    local bin="$HOME/.local/bin/crush"
    if [[ -x "$bin" ]]; then echo "cli: installed (update with 'npm update -g @charmland/crush')"; return 0; fi
    if ! command -v npm >/dev/null 2>&1; then echo "cli: skipped (npm not found)"; return 1; fi
    # npm (not bun): the package downloads its binary in a postinstall script,
    # which bun skips for untrusted packages. flock: npm's global prefix is
    # shared with the concurrent `maude update` jobs.
    if ( flock -w 600 9 2>/dev/null || true; timeout 300 npm install -g @charmland/crush >/dev/null 2>&1 ) 9>"$HOME/.maude-pkg-install.lock"; then
        local nb; nb="$(npm prefix -g 2>/dev/null)/bin/crush"
        mkdir -p "$HOME/.local/bin"
        [[ -x "$nb" ]] && ln -sfn "$nb" "$bin"
    fi
    if [[ -x "$bin" ]]; then echo "cli: installed"; return 0; fi
    echo "cli: failed"; return 1
}

# Antigravity CLI (Google, command `agy`). The vendor script (antigravity.google/cli/install.sh)
# installs ~/.local/bin/agy and then runs `agy install`, which sets up the shell environment (it
# may edit rc files, which Maude manages itself). So Maude does the part that matters: read the
# vendor's own per-platform manifest, download the archive over HTTPS, check the manifest's SHA-512
# and unpack the binary. Same manifest the vendor script reads. agy updates itself in the background.
# MAUDE_AGY_MANIFEST_BASE overrides the manifest host (tests).
update_antigravity() {
    local bin="$HOME/.local/bin/agy"
    if [[ -x "$bin" ]]; then echo "cli: installed (updates itself)"; return 0; fi
    local base="${MAUDE_AGY_MANIFEST_BASE:-https://antigravity-cli-auto-updater-974169037036.us-central1.run.app}"
    local arch
    case "$(uname -m)" in
        x86_64|amd64)  arch=amd64 ;;
        aarch64|arm64) arch=arm64 ;;
        *) echo "cli: skipped (unsupported CPU)"; return 1 ;;
    esac
    command -v python3 >/dev/null 2>&1 || { echo "cli: skipped (python3 not found)"; return 1; }
    local d rc=1
    d=$(mktemp -d) || { echo "cli: failed (no temp dir)"; return 1; }
    local url sha
    if curl -fsSL --path-as-is --proto '=https' --proto-redir '=https' --max-time 60 "$base/manifests/linux_${arch}.json" -o "$d/manifest.json"; then
        # prints "<url> <sha512>" only if both look right: an https storage.googleapis.com archive and 128 hex digits
        read -r url sha < <(python3 -I - "$d/manifest.json" <<'PY' 2>/dev/null
import json, re, sys
m = json.load(open(sys.argv[1]))
u, s = m.get("url", ""), m.get("sha512", "")
if ".." not in u and "//" not in u[8:] and re.fullmatch(r"https://storage\.googleapis\.com/antigravity-public/[A-Za-z0-9._/-]+\.tar\.gz", u) and re.fullmatch(r"[0-9a-fA-F]{128}", s):
    print(u, s.lower())
PY
) || true
    fi
    if [[ -n "${url:-}" && -n "${sha:-}" ]] \
       && curl -fsSL --path-as-is --proto '=https' --proto-redir '=https' --speed-limit 10000 --speed-time 60 --connect-timeout 30 --max-time 3600 "$url" -o "$d/agy.tar.gz" \
       && [[ "$(sha512sum "$d/agy.tar.gz" | cut -d' ' -f1)" == "$sha" ]] \
       && tar -xzf "$d/agy.tar.gz" -C "$d" antigravity 2>/dev/null \
       && mkdir -p "$HOME/.local/bin" \
       && install -m 755 "$d/antigravity" "$bin"; then
        rc=0
    fi
    rm -rf "$d"
    if [[ $rc -eq 0 ]]; then echo "cli: installed"; else echo "cli: failed"; fi
    return $rc
}

# GitHub Copilot CLI (command `copilot`). The vendor script (gh.io/copilot-install) downloads the
# release tarball and checks it against the release's SHA256SUMS.txt itself; as a non-root user it
# installs to ~/.local/bin. Download first, then run (no curl | bash). ~/.local/bin is put on PATH
# for the run so the script does not stop to ask about editing a shell profile. The npm package
# @github/copilot needs Node.js 22+ and is not used.
update_copilot() {
    local bin="$HOME/.local/bin/copilot"
    if [[ -x "$bin" ]]; then echo "cli: installed (update with 'copilot update')"; return 0; fi
    local d rc=1
    d=$(mktemp -d) || { echo "cli: failed (no temp dir)"; return 1; }
    mkdir -p "$HOME/.local/bin"
    if curl -fsSL --path-as-is --proto '=https' --proto-redir '=https' --max-time 60 https://gh.io/copilot-install -o "$d/install.sh" \
       && PREFIX="$HOME/.local" PATH="$HOME/.local/bin:$PATH" timeout 300 bash "$d/install.sh" </dev/null >/dev/null 2>&1 \
       && [[ -x "$bin" ]]; then
        rc=0
    fi
    rm -rf "$d"
    if [[ $rc -eq 0 ]]; then echo "cli: installed"; else echo "cli: failed"; fi
    return $rc
}

# Make ~/.kiro/skills a symlink to ~/.claude/skills so Kiro sees the same
# skills as Claude Code. Idempotent; anything else already there is moved
# aside (never deleted).
link_kiro_skills() {
    local link="$HOME/.kiro/skills" want="$HOME/.claude/skills"
    mkdir -p "$HOME/.kiro" || return 1
    [[ "$(readlink "$link" 2>/dev/null)" == "$want" ]] && return 0
    if [[ -e "$link" || -L "$link" ]]; then
        mv "$link" "$link.bak.$(date +%s)" || return 1
    fi
    ln -sfn "$want" "$link"
}
