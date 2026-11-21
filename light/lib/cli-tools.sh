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
