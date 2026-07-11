# llm-mode.sh — sourced by maude-bootstrap.sh and the maude CLI.
# Shared helpers for the reviewer-CLI libs (codex.sh, opencode.sh, grok.sh):
#   detect_llm_mode()   — which credential mode this sandbox is in
#   maude_fetch_skill() — pull a skill's SKILL.md from the repo root
#
# This file must be sourced BEFORE the per-tool libs (the bootstrap and
# update_all loops list it first for that reason).

# Default URL for the raw repo. Allow override via MAUDE_RAW for testing.
: "${MAUDE_RAW:=https://raw.githubusercontent.com/dirkpetersen/maude/main/light}"

# Echo the credential mode: "azure", "aws", or "direct".
# Mirrors the claude wrapper's precedence exactly: ~/.azure/clauderc is the
# single source of truth the TUI writes; Foundry beats Bedrock beats direct.
# Runs in a subshell so sourcing clauderc never pollutes the caller's env.
detect_llm_mode() {
    (
        # Ignore ambient session flags — clauderc is the persistent source of
        # truth (the wrapper re-sources it on every launch anyway).
        unset CLAUDE_CODE_USE_FOUNDRY CLAUDE_CODE_USE_BEDROCK
        set -a
        # shellcheck source=/dev/null
        [ -f "$HOME/.azure/clauderc" ] && . "$HOME/.azure/clauderc" 2>/dev/null
        set +a
        if [[ "${CLAUDE_CODE_USE_FOUNDRY:-0}" == "1" ]]; then
            echo "azure"
        elif [[ "${CLAUDE_CODE_USE_BEDROCK:-0}" == "1" || -f "$HOME/.aws/credentials" ]]; then
            echo "aws"
        else
            echo "direct"
        fi
    )
}

# Echo the value of VAR from the current environment, falling back to
# ~/.azure/clauderc (where the TUI persists credential exports). Empty when
# neither defines it. Runs in a subshell — nothing leaks into the caller.
# Used by the config generators: `maude update` runs in a plain shell that
# has NOT sourced clauderc, so ambient env alone would miss these values.
clauderc_env() {
    local var="$1"
    (
        if [[ -z "${!var:-}" ]]; then
            set -a
            # shellcheck source=/dev/null
            [ -f "$HOME/.azure/clauderc" ] && . "$HOME/.azure/clauderc" 2>/dev/null
            set +a
        fi
        printf '%s' "${!var:-}"
    )
}

# Fetch .claude/skills/<name>/SKILL.md from the repo root into
# ~/.claude/skills/<name>/. Echoes a one-line status ("installed (new)",
# "updated", "up to date", or "failed (<url>)"); returns non-zero on failure.
# Same download validation as update_gemini: non-empty + frontmatter sentinel.
maude_fetch_skill() {
    local name="$1"
    local repo_raw="${MAUDE_RAW%/light}"
    local skill_dir="$HOME/.claude/skills/$name"
    mkdir -p "$skill_dir"
    local tmp; tmp=$(mktemp)
    local url="$repo_raw/.claude/skills/$name/SKILL.md"
    if curl -fsSL --max-time 10 "$url" -o "$tmp" 2>/dev/null \
       && [[ -s "$tmp" ]] && head -n 1 "$tmp" | grep -q '^---'; then
        local existing="$skill_dir/SKILL.md" status
        if [[ ! -f "$existing" ]]; then
            status="installed (new)"
        elif ! diff -q "$existing" "$tmp" >/dev/null 2>&1; then
            status="updated"
        else
            status="up to date"
        fi
        # Report success only if the write actually landed — ~/.claude can be
        # a dangling symlink (host folder missing) or a failing drvfs mount.
        if mv "$tmp" "$existing" 2>/dev/null; then
            echo "$status"
            return 0
        fi
        rm -f "$tmp"
        echo "failed (cannot write $existing)"
        return 1
    fi
    rm -f "$tmp"
    echo "failed ($url)"
    return 1
}
