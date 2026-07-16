# codex.sh — sourced by maude-bootstrap.sh and the maude CLI.
# Installs/updates OpenAI's Codex CLI (native Rust binary), refreshes the
# bundled `codex` skill into ~/.claude/skills/codex, and generates
# ~/.codex/config.toml with all three credential-mode profiles pre-declared
# (azure / amazon-bedrock / direct) so switching modes never needs a rewrite.
#
# Depends on llm-mode.sh (maude_fetch_skill); sourced before this file.

: "${MAUDE_RAW:=https://raw.githubusercontent.com/dirkpetersen/maude/main/light}"

update_codex() {
    local rc=0
    local cli_status="unchanged" skill_status="unchanged" config_status="kept (user-owned)"

    # Capture config ownership BEFORE the installer runs: some installers
    # write a default config themselves, and a file that appears during this
    # run is ours to overwrite, not a user customisation.
    local cfg="$HOME/.codex/config.toml"
    local cfg_preexisting=0 cfg_had_marker=0
    [[ -f "$cfg" ]] && cfg_preexisting=1
    [[ $cfg_preexisting -eq 1 ]] && grep -q '^# managed by maude' "$cfg" 2>/dev/null && cfg_had_marker=1

    # ── Codex CLI (official installer; npm fallback) ──
    # Codex is a native Rust binary — the curl installer both installs and
    # upgrades in place when re-run, and symlinks itself into ~/.local/bin
    # (already on Maude's PATH). npm (@openai/codex) wraps the same binary.
    # Resolve the binary by path, not bare name — the caller's PATH may not
    # include a freshly-created ~/.local/bin yet.
    local codex_bin="$HOME/.local/bin/codex"
    _codex_resolve() {
        [[ -x "$codex_bin" ]] || codex_bin=$(command -v codex 2>/dev/null)
    }
    local ver_before ver_after
    _codex_resolve
    ver_before=$([[ -n "$codex_bin" ]] && "$codex_bin" --version 2>/dev/null | grep -oP '[\d.]+' | head -1)
    # Snapshot the installer's ~/.bashrc backup files so we can remove only
    # the ones this run creates (see the PATH-block strip below).
    local _bak_before
    _bak_before=$(ls "$HOME"/.bashrc.bak.* 2>/dev/null)
    local installed=0
    # CODEX_NON_INTERACTIVE=true is required: the installer opens /dev/tty
    # directly to ask "Start Codex now?" / "Uninstall the existing ...
    # Codex now?" whenever a controlling terminal is present — a redirect
    # of THIS process's stdout/stderr does not touch /dev/tty, so without
    # this the prompt blocks forever when run from an interactive shell
    # (e.g. a real `maude update`), even though it looked fine when tested
    # from a shell with no controlling tty. `timeout` is defense-in-depth:
    # curl's --max-time only bounds the curl leg of the pipe, not `sh`.
    if timeout 300 env CODEX_NON_INTERACTIVE=true \
        sh -c 'curl -fsSL https://chatgpt.com/codex/install.sh | sh' >/dev/null 2>&1; then
        installed=1
    elif command -v npm >/dev/null 2>&1 && npm install -g @openai/codex >/dev/null 2>&1; then
        installed=1
    fi
    if [[ $installed -eq 1 ]]; then
        # The installer appends a PATH block to ~/.bashrc. That block lands
        # AFTER maude-path.sh (which runs at the END of .bashrc precisely to
        # keep ~/bin first), so it would put ~/.local/bin ahead of ~/bin and
        # bypass the claude wrapper. ~/.local/bin is already on Maude's PATH —
        # strip the block and the backup file the installer just created.
        # Only strip when BOTH markers are present — with the end marker
        # missing (interrupted install), the sed range would eat everything
        # to EOF, including user content.
        if grep -q '^# >>> Codex installer >>>$' "$HOME/.bashrc" 2>/dev/null \
           && grep -q '^# <<< Codex installer <<<$' "$HOME/.bashrc" 2>/dev/null; then
            sed -i '/^# >>> Codex installer >>>$/,/^# <<< Codex installer <<<$/d' "$HOME/.bashrc" 2>/dev/null
        fi
        # If any installer start-marker survives (vendor renamed its sentinel,
        # or an interrupted append), PATH order may now be broken — say so.
        if grep -q 'installer >>>' "$HOME/.bashrc" 2>/dev/null; then
            echo "WARNING: an installer PATH block remains in ~/.bashrc — verify ~/bin stays first on PATH." >&2
        fi
        local _bak
        for _bak in "$HOME"/.bashrc.bak.*; do
            [[ -f "$_bak" ]] || continue
            grep -qxF "$_bak" <<< "$_bak_before" || rm -f "$_bak"
        done
        # Belt-and-braces: if the installer's target moved and nothing landed
        # in ~/.local/bin, link whatever is reachable.
        codex_bin="$HOME/.local/bin/codex"
        if [[ ! -x "$codex_bin" ]]; then
            local cand found=""
            for cand in "$HOME/.codex/bin/codex" "$HOME/bin/codex" "$(command -v codex 2>/dev/null)"; do
                [[ -n "$cand" && -x "$cand" ]] && { found="$cand"; break; }
            done
            if [[ -n "$found" ]]; then
                mkdir -p "$HOME/.local/bin"
                ln -sfn "$found" "$codex_bin"
            fi
        fi
        _codex_resolve
        ver_after=$([[ -n "$codex_bin" ]] && "$codex_bin" --version 2>/dev/null | grep -oP '[\d.]+' | head -1)
        if [[ -z "$ver_after" ]]; then
            cli_status="failed (installed but codex not on PATH)"
            rc=1
        elif [[ -z "$ver_before" ]]; then
            cli_status="installed (v${ver_after})"
        elif [[ "$ver_before" != "$ver_after" ]]; then
            cli_status="updated (${ver_before} → ${ver_after})"
        else
            cli_status="up to date (v${ver_after})"
        fi
    else
        cli_status="failed"
        rc=1
    fi

    # ── Codex skill (our own SKILL.md, fetched from the repo root) ──
    if declare -F maude_fetch_skill >/dev/null; then
        if skill_status=$(maude_fetch_skill codex); then :; else rc=1; fi
    else
        skill_status="skipped (llm-mode.sh not loaded)"
        rc=1
    fi

    # ── ~/.codex/config.toml — only when absent or still maude-managed ──
    # All three provider profiles are declared up front; the skill picks one
    # with --profile per the detected mode. Model IDs live ONLY here and in
    # the skill so a vendor rename ships as a one-line lib/skill update.
    # Regenerate when: file didn't exist before this run, OR it carried our
    # marker before the installer ran (so a vendor installer clobbering it
    # mid-run can't demote it to "user-owned"), OR it carries the marker now.
    if [[ $cfg_preexisting -eq 0 || $cfg_had_marker -eq 1 ]] \
       || grep -q '^# managed by maude' "$cfg" 2>/dev/null; then
        mkdir -p "$HOME/.codex"
        local was_new="rewritten"
        [[ $cfg_preexisting -eq 1 ]] || was_new="written (new)"
        # Azure base_url can't use env expansion (TOML is static) — substitute
        # the resource name at generation time. Check clauderc too: `maude
        # update` runs in a shell that hasn't sourced it.
        local az_res="${AZURE_OPENAI_RESOURCE:-}"
        if [[ -z "$az_res" ]] && declare -F clauderc_env >/dev/null; then
            az_res=$(clauderc_env AZURE_OPENAI_RESOURCE)
        fi
        [[ -n "$az_res" ]] || az_res="YOUR-RESOURCE-NAME"
        cat > "$cfg" <<EOF
# managed by maude
# Regenerated by 'maude update' while this marker line is present.
# Remove the marker to take ownership of this file.

# Direct mode default (OPENAI_API_KEY / CODEX_API_KEY):
model = "gpt-5.3-codex"

[profiles.maude-azure]
model = "gpt-5.3-codex"          # must equal your Azure Foundry DEPLOYMENT name
model_provider = "azure"

[model_providers.azure]
name = "Azure OpenAI"
base_url = "https://${az_res}.openai.azure.com/openai/v1"
env_key = "AZURE_OPENAI_API_KEY"
wire_api = "responses"

[profiles.maude-aws]
model = "openai.gpt-5.5"
model_provider = "amazon-bedrock"

[model_providers.amazon-bedrock.aws]
region = "us-east-2"             # only region serving openai.gpt-5.5 today
EOF
        config_status="$was_new"
    fi

    # Print status lines for the caller (update_all reads them).
    printf '%s\n' "cli:$cli_status" "skill:$skill_status" "config:$config_status"
    return $rc
}
