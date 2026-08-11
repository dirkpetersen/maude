# gemini.sh — sourced by maude-bootstrap.sh and the maude CLI.
# Installs/updates Google's Gemini CLI (@google/gemini-cli) and refreshes the
# bundled `gemini` skill into ~/.claude/skills/gemini, so Claude Code can drive
# Gemini via the file-based handoff documented in that SKILL.md.
#
# The canonical skill lives at the repository ROOT (.claude/skills/gemini/SKILL.md),
# alongside any other shared skills — NOT under light/. We derive the repo-root
# raw URL by stripping the trailing /light from MAUDE_RAW.

# Default URL for the raw repo. Allow override via MAUDE_RAW for testing.
: "${MAUDE_RAW:=https://raw.githubusercontent.com/dirkpetersen/maude/main/light}"

update_gemini() {
    local rc=0
    local cli_status="unchanged" skill_status="unchanged"

    # ── Gemini CLI (installed globally via Bun, like kanna) ──
    if command -v bun >/dev/null 2>&1; then
        local ver_before ver_after
        ver_before=$(gemini --version 2>/dev/null | grep -oP '[\d.]+' | head -1)
        # flock serializes global package installs across the concurrent
        # `maude update` jobs (bun's global store is shared).
        if ( flock -w 600 9 2>/dev/null || true; bun install -g @google/gemini-cli >/dev/null 2>&1 ) 9>"$HOME/.maude-pkg-install.lock"; then
            # Symlink into ~/.local/bin: child processes (Claude Code's Bash
            # tool) don't get Bun's PATH injection, the same reason kanna is
            # linked in ensure-tools.sh.
            [[ -x "$HOME/.bun/bin/gemini" ]] && \
                ln -sfn "$HOME/.bun/bin/gemini" "$HOME/.local/bin/gemini"
            ver_after=$(gemini --version 2>/dev/null | grep -oP '[\d.]+' | head -1)
            if [[ -n "$ver_after" && "$ver_before" != "$ver_after" ]]; then
                cli_status="updated (${ver_before:-?} → ${ver_after})"
            else
                cli_status="up to date${ver_after:+ (v${ver_after})}"
            fi
        else
            cli_status="failed"
            rc=1
        fi
    else
        cli_status="skipped (bun not found)"
        rc=1
    fi

    # ── Gemini skill (our own SKILL.md, fetched from GitHub) ──
    # Canonical location is the repo root .claude/skills/ (a global Claude Code
    # skill in the user's home), NOT light/. Strip the trailing /light from
    # MAUDE_RAW so fork/branch overrides still resolve correctly. We try the
    # canonical URL first, then fall back to the legacy light/ path so a machine
    # mid-migration still gets *a* skill; the exact URL + curl exit is reported
    # on total failure so this never fails silently again.
    local repo_raw="${MAUDE_RAW%/light}"
    local skill_dir="$HOME/.claude/skills/gemini"
    mkdir -p "$skill_dir"
    local tmp; tmp=$(mktemp)
    local url got_url="" tried=""
    for url in "$repo_raw/.claude/skills/gemini/SKILL.md" \
               "$MAUDE_RAW/skills/gemini/SKILL.md"; do
        : > "$tmp"   # clear any garbage a prior failed attempt may have left
        tried="${tried:+$tried, }$url"
        if curl -fsSL --max-time 10 "$url" -o "$tmp" 2>/dev/null \
           && [[ -s "$tmp" ]] && head -n 1 "$tmp" | grep -q '^---'; then
            got_url="$url"
            break
        fi
    done
    if [[ -n "$got_url" ]]; then
        local existing="$skill_dir/SKILL.md"
        if [[ ! -f "$existing" ]]; then
            skill_status="installed (new)"
        elif ! diff -q "$existing" "$tmp" >/dev/null 2>&1; then
            skill_status="updated"
        else
            skill_status="up to date"
        fi
        mv "$tmp" "$existing"
    else
        rm -f "$tmp"
        skill_status="failed (all urls failed: $tried)"
        rc=1
    fi

    # ── Ensure ~/.gemini/.env has COLORTERM and trust vars ──
    # The Gemini CLI loads this file on every run, including when spawned as a
    # non-interactive subprocess by Claude Code's Bash tool (which doesn't
    # source ~/.bashrc, so the profile-level exports are never seen).
    local gemini_env="$HOME/.gemini/.env"
    mkdir -p "$(dirname "$gemini_env")"
    touch "$gemini_env"
    local line
    for line in "COLORTERM=truecolor" "GEMINI_CLI_TRUST_WORKSPACE=true"; do
        local key="${line%%=*}"
        if ! grep -q "^${key}=" "$gemini_env" 2>/dev/null; then
            printf '%s\n' "$line" >> "$gemini_env"
        fi
    done

    # Print status lines for the caller (update_all reads them).
    printf '%s\n' "cli:$cli_status" "skill:$skill_status"
    return $rc
}
