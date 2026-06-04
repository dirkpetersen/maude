# ensure-tools.sh — sourced by maude-bootstrap.sh and the maude CLI.
# Symlinks runtime tools into ~/.local/bin so they're reachable by any
# process — child processes spawned by the TUI/kanna don't source ~/.bashrc,
# so NVM and Bun's PATH injections never run for them.

ensure_tool_symlinks() {
    local bin="$HOME/.local/bin"
    mkdir -p "$bin"

    # Bun + kanna (kanna's shebang resolves `bun` via env, so both must be linked)
    [[ -x "$HOME/.bun/bin/bun"   ]] && ln -sfn "$HOME/.bun/bin/bun"   "$bin/bun"
    [[ -x "$HOME/.bun/bin/kanna" ]] && ln -sfn "$HOME/.bun/bin/kanna" "$bin/kanna"

    # python / pip → python3 / pip3 (Ubuntu 26.04 ships only the versioned names)
    local _py _pip
    _py=$(command -v python3 2>/dev/null)
    _pip=$(command -v pip3 2>/dev/null)
    [[ -x "$_py"  ]] && ln -sfn "$_py"  "$bin/python"
    [[ -x "$_pip" ]] && ln -sfn "$_pip" "$bin/pip"

    # Node via NVM: prefer default alias, fall back to latest installed version
    local nvm_dir="${NVM_DIR:-$HOME/.nvm}"
    local node_bin=""
    if [[ -d "$nvm_dir/versions/node" ]]; then
        local _ver
        _ver=$(cat "$nvm_dir/alias/default" 2>/dev/null | tr -d '[:space:]')
        if [[ -n "$_ver" && -d "$nvm_dir/versions/node/$_ver/bin" ]]; then
            node_bin="$nvm_dir/versions/node/$_ver/bin"
        else
            node_bin=$(ls -td "$nvm_dir/versions/node"/*/bin 2>/dev/null | head -1)
        fi
    fi
    if [[ -n "$node_bin" ]]; then
        local _t
        for _t in node npm npx; do
            [[ -x "$node_bin/$_t" ]] && ln -sfn "$node_bin/$_t" "$bin/$_t"
        done
    fi
}
