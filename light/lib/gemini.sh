# gemini.sh — sourced by maude-bootstrap.sh and the maude CLI.
# Installs/updates Google's Gemini CLI (@google/gemini-cli) and refreshes the
# bundled `gemini` skill into ~/.claude/skills/gemini, so Claude Code can drive
# Gemini via the file-based handoff documented in that SKILL.md.

# Default URL for the raw repo. Allow override via MAUDE_RAW for testing.
: "${MAUDE_RAW:=https://raw.githubusercontent.com/dirkpetersen/maude/main/light}"

update_gemini() {
    local rc=0

    # ── Gemini CLI (installed globally via Bun, like kanna) ──
    if command -v bun >/dev/null 2>&1; then
        if bun install -g @google/gemini-cli >/dev/null 2>&1; then
            # Symlink into ~/.local/bin: child processes (Claude Code's Bash
            # tool) don't get Bun's PATH injection, the same reason kanna is
            # linked in ensure-tools.sh.
            [[ -x "$HOME/.bun/bin/gemini" ]] && \
                ln -sfn "$HOME/.bun/bin/gemini" "$HOME/.local/bin/gemini"
        else
            rc=1
        fi
    else
        rc=1
    fi

    # ── Gemini skill (our own SKILL.md, fetched from GitHub) ──
    local skill_dir="$HOME/.claude/skills/gemini"
    mkdir -p "$skill_dir"
    local tmp; tmp=$(mktemp)
    if curl -fsSL --max-time 10 "$MAUDE_RAW/skills/gemini/SKILL.md" -o "$tmp" 2>/dev/null \
       && [[ -s "$tmp" ]] && head -1 "$tmp" | grep -q '^---'; then
        mv "$tmp" "$skill_dir/SKILL.md"
    else
        rm -f "$tmp"
        rc=1
    fi

    return $rc
}
