# refresh-md.sh — sourced by the maude CLI.
# Refreshes ~/.claude/MAUDE.md once a day from GitHub, with cache fallback
# and a sanity check to avoid overwriting good rules with a corrupted download.

# Default URL for the raw repo. Allow override via MAUDE_RAW for testing.
: "${MAUDE_RAW:=https://raw.githubusercontent.com/dirkpetersen/maude/main/light}"

# Validate that a downloaded MAUDE.md looks legitimate (defends against
# corporate-proxy HTML interception or empty downloads).
_maude_md_looks_valid() {
    local f="$1"
    [[ -s "$f" ]] || return 1
    [[ $(wc -c < "$f") -ge 200 ]] || return 1
    head -1 "$f" | grep -q '^# Maude Sandbox' || return 1
    return 0
}

refresh_claude_md() {
    local claude_dir="$HOME/.claude"
    local maude_md="$claude_dir/MAUDE.md"
    local claude_md="$claude_dir/CLAUDE.md"
    local cache_dir="$HOME/.local/share/maude"
    local cache_md="$cache_dir/MAUDE.md"
    local stamp="$cache_dir/.last-md-refresh"
    mkdir -p "$claude_dir" "$cache_dir"

    # Refresh the cache once per day.
    local today; today=$(date +%Y-%m-%d)
    local last=""
    [[ -f "$stamp" ]] && last=$(cat "$stamp" 2>/dev/null)
    if [[ "$last" != "$today" ]]; then
        local tmp; tmp=$(mktemp)
        if curl -fsSL --max-time 10 "$MAUDE_RAW/MAUDE.md" -o "$tmp" 2>/dev/null \
           && _maude_md_looks_valid "$tmp"; then
            mv "$tmp" "$cache_md"
            printf '%s\n' "$today" > "$stamp"
        else
            rm -f "$tmp"
            # Network failure or invalid response — keep the existing cache.
        fi
    fi

    # Always install the cached copy if we have one. If we have neither
    # cache nor existing MAUDE.md, do one last best-effort fetch direct
    # to the destination so first-run users aren't left without rules.
    if [[ -f "$cache_md" ]]; then
        cp -f "$cache_md" "$maude_md"
    elif [[ ! -f "$maude_md" ]]; then
        local tmp; tmp=$(mktemp)
        if curl -fsSL --max-time 10 "$MAUDE_RAW/MAUDE.md" -o "$tmp" 2>/dev/null \
           && _maude_md_looks_valid "$tmp"; then
            mv "$tmp" "$maude_md"
            cp -f "$maude_md" "$cache_md"
        else
            rm -f "$tmp"
        fi
    fi

    # CLAUDE.md template is created once and then user-owned.
    if [[ ! -f "$claude_md" ]]; then
        curl -fsSL --max-time 10 "$MAUDE_RAW/CLAUDE.template.md" \
            -o "$claude_md" 2>/dev/null || true
    fi
}
