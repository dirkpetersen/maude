# grok.sh — sourced by maude-bootstrap.sh and the maude CLI.
# Installs/updates xAI's official Grok Build CLI (native Rust binary, command
# `grok` — NOT the community superagent-ai/grok-cli, which claims the same
# binary name), refreshes the bundled `grok` skill, and generates
# ~/.grok/config.toml with model aliases for all three credential modes.
#
# Depends on llm-mode.sh (maude_fetch_skill); sourced before this file.

: "${MAUDE_RAW:=https://raw.githubusercontent.com/dirkpetersen/maude/main/light}"

update_grok() {
    local rc=0
    local cli_status="unchanged" skill_status="unchanged" config_status="kept (user-owned)"

    # Capture config ownership BEFORE the installer runs: the x.ai installer
    # writes a default ~/.grok/config.toml itself, and a file that appears
    # during this run is ours to overwrite, not a user customisation.
    local cfg="$HOME/.grok/config.toml"
    local cfg_preexisting=0 cfg_had_marker=0
    [[ -f "$cfg" ]] && cfg_preexisting=1
    [[ $cfg_preexisting -eq 1 ]] && grep -q '^# managed by maude' "$cfg" 2>/dev/null && cfg_had_marker=1

    # ── Grok Build CLI (official installer; npm fallback) ──
    # Re-running the installer upgrades in place; it installs to ~/.grok/bin
    # and symlinks into ~/.local/bin itself. auto_update is disabled in the
    # generated config — updates flow through 'maude update' like all tools.
    # Resolve the binary by path, not bare name — the caller's PATH may not
    # include a freshly-created ~/.local/bin yet.
    local grok_bin="$HOME/.local/bin/grok"
    _grok_resolve() {
        [[ -x "$grok_bin" ]] || grok_bin="$HOME/.grok/bin/grok"
        [[ -x "$grok_bin" ]] || grok_bin=$(command -v grok 2>/dev/null)
    }
    local ver_before ver_after
    _grok_resolve
    ver_before=$([[ -n "$grok_bin" ]] && "$grok_bin" --version 2>/dev/null | grep -oP '[\d.]+' | head -1)
    local installed=0
    # Serialize the whole install+strip against update_codex — the only other
    # ~/.bashrc writer. The vendor installer APPENDS its own PATH block, which
    # we can't lock directly, so acquire the shared ~/.bashrc.lock BEFORE the
    # installer and hold it through our strip + backup sweep. Both tools take
    # this same advisory lock (fd 200) before their installer, so codex's and
    # grok's installers never run concurrently and their appends/sed strips
    # can never interleave on ~/.bashrc. Released after the install block below.
    # (Lock order note: fd 200 here is always taken before the fd-9
    # pkg-install lock in the npm fallback; nothing that holds fd 9 ever wants
    # fd 200, so there is no deadlock cycle.)
    exec 200>"$HOME/.bashrc.lock"
    flock -w 1800 200 2>/dev/null \
        || echo "WARNING: timed out waiting for ~/.bashrc.lock — proceeding without it; ~/.bashrc PATH edits may race with the other reviewer CLI." >&2
    # Snapshot the installer's ~/.bashrc backup files INSIDE the lock, so a
    # concurrent peer holding it can't create a bak between our snapshot and
    # our sweep (which deletes any bak not in this snapshot) — that would wipe
    # the peer's pre-damage backup of ~/.bashrc.
    local _bak_before
    _bak_before=$(ls "$HOME"/.bashrc.bak.* 2>/dev/null)
    # `timeout 300` bounds the whole `curl | bash`, not just curl (--max-time
    # only bounds curl's leg of the pipe). This matters more now that we hold
    # the shared ~/.bashrc.lock across the installer: a hung install would
    # otherwise pin the lock and block update_codex until its own timeout.
    if timeout 300 bash -c 'curl -fsSL --max-time 300 https://x.ai/cli/install.sh | bash' >/dev/null 2>&1; then
        installed=1
    # flock serializes global package installs across the concurrent
    # `maude update` jobs (npm's global prefix is shared).
    elif command -v npm >/dev/null 2>&1 && ( flock -w 600 9 2>/dev/null || true; timeout 300 npm install -g @xai-official/grok >/dev/null 2>&1 ) 9>"$HOME/.maude-pkg-install.lock"; then
        installed=1
    fi
    if [[ $installed -eq 1 ]]; then
        # The installer appends a PATH block to ~/.bashrc. That block lands
        # AFTER maude-path.sh (which runs at the END of .bashrc precisely to
        # keep ~/bin first), so it would put ~/.grok/bin ahead of ~/bin and
        # break the wrapper-first PATH convention. We symlink grok into
        # ~/.local/bin ourselves — strip the block and the installer's backup.
        # Only strip when BOTH markers are present — with the end marker
        # missing (interrupted install), the sed range would eat everything
        # to EOF, including user content.
        # 'maude update' can run this concurrently with update_codex (both
        # edit ~/.bashrc) — the whole install+strip is serialized against
        # codex via the function-level flock held above, so the two sed -i
        # calls can't race and clobber each other's rename(2).
        if grep -q '^# >>> grok installer >>>$' "$HOME/.bashrc" 2>/dev/null \
           && grep -q '^# <<< grok installer <<<$' "$HOME/.bashrc" 2>/dev/null; then
            sed -i '/^# >>> grok installer >>>$/,/^# <<< grok installer <<<$/d' "$HOME/.bashrc" 2>/dev/null
        fi
        # If any installer start-marker survives (vendor renamed its
        # sentinel, or an interrupted append), PATH order may now be
        # broken — say so.
        if grep -q 'installer >>>' "$HOME/.bashrc" 2>/dev/null; then
            echo "WARNING: an installer PATH block remains in ~/.bashrc — verify ~/bin stays first on PATH." >&2
        fi
        local _bak
        for _bak in "$HOME"/.bashrc.bak.*; do
            [[ -f "$_bak" ]] || continue
            grep -qxF "$_bak" <<< "$_bak_before" || rm -f "$_bak"
        done
        # Belt-and-braces: ensure a ~/.local/bin link exists. This matters —
        # we strip the installer's PATH block from ~/.bashrc above, so this
        # link is how grok stays reachable.
        grok_bin="$HOME/.local/bin/grok"
        if [[ ! -x "$grok_bin" && -x "$HOME/.grok/bin/grok" ]]; then
            mkdir -p "$HOME/.local/bin"
            ln -sfn "$HOME/.grok/bin/grok" "$grok_bin"
        fi
        _grok_resolve
        ver_after=$([[ -n "$grok_bin" ]] && "$grok_bin" --version 2>/dev/null | grep -oP '[\d.]+' | head -1)
        if [[ -z "$ver_after" ]]; then
            cli_status="failed (installed but grok not on PATH)"
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

    # Done touching ~/.bashrc — release the serialization lock so update_codex
    # (or a later run) can proceed.
    flock -u 200 2>/dev/null || true
    exec 200>&- 2>/dev/null || true

    # ── Grok skill ──
    if declare -F maude_fetch_skill >/dev/null; then
        if skill_status=$(maude_fetch_skill grok); then :; else rc=1; fi
    else
        skill_status="skipped (llm-mode.sh not loaded)"
        rc=1
    fi

    # ── ~/.grok/config.toml — only when absent or still maude-managed ──
    # One model alias per credential mode; the skill selects with -m.
    # AWS uses Bedrock's OpenAI-compatible "mantle" endpoint, which needs a
    # Bedrock API key (AWS_BEARER_TOKEN_BEDROCK) — SigV4-only credentials
    # cannot reach it; those users fall back to grok-direct.
    # Regenerate when: file didn't exist before this run, OR it carried our
    # marker before the installer ran (the x.ai installer writes its own
    # config — a future version overwriting ours must not demote it to
    # "user-owned"), OR it carries the marker now.
    if [[ $cfg_preexisting -eq 0 || $cfg_had_marker -eq 1 ]] \
       || grep -q '^# managed by maude' "$cfg" 2>/dev/null; then
        mkdir -p "$HOME/.grok"
        local was_new="rewritten"
        [[ $cfg_preexisting -eq 1 ]] || was_new="written (new)"
        # Check clauderc too: `maude update` runs in a shell that hasn't
        # sourced it, so ambient env alone would miss these values.
        local az_res="${AZURE_AI_RESOURCE:-}"
        local aws_region="${AWS_REGION:-}"
        if declare -F clauderc_env >/dev/null; then
            [[ -n "$az_res"     ]] || az_res=$(clauderc_env AZURE_AI_RESOURCE)
            [[ -n "$aws_region" ]] || aws_region=$(clauderc_env AWS_REGION)
        fi
        [[ -n "$az_res"     ]] || az_res="YOUR-RESOURCE-NAME"
        [[ -n "$aws_region" ]] || aws_region="us-west-2"
        cat > "$cfg" <<EOF
# managed by maude
# Regenerated by 'maude update' while this marker line is present.
# Remove the marker to take ownership of this file.

auto_update = false

[models]
default = "grok-direct"

[model.grok-direct]
model = "grok-4.5"
# auth: XAI_API_KEY from the environment

[model.grok-azure]
model = "grok-4"                 # must equal your Azure Foundry DEPLOYMENT name
base_url = "https://${az_res}.services.ai.azure.com/openai/v1"
env_key = "AZURE_AI_API_KEY"
api_backend = "chat_completions"

[model.grok-aws]
model = "xai.grok-4.3"
base_url = "https://bedrock-mantle.${aws_region}.api.aws/openai/v1"
env_key = "AWS_BEARER_TOKEN_BEDROCK"
api_backend = "chat_completions"
EOF
        config_status="$was_new"
    fi

    # Print status lines for the caller (update_all reads them).
    printf '%s\n' "cli:$cli_status" "skill:$skill_status" "config:$config_status"
    return $rc
}
