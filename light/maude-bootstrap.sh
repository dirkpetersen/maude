#!/bin/bash
# maude-bootstrap.sh — Runs as the maude user inside the Maude WSL distro.
# Installs dev-station, the maude launcher, and user-level configuration.
#
# Usage:  maude-bootstrap.sh
set -e

GH_RAW="${MAUDE_RAW:-https://raw.githubusercontent.com/dirkpetersen/maude/main/light}"

echo "=== Maude user bootstrap ==="

# ── Ensure directories and PATH ──────────────────────────────────────
mkdir -p "$HOME/bin" "$HOME/.local/bin" "$HOME/Maude/Projects"
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

# ── Install dev-station (shell-setup, claude-wrapper, nodejs, AWS CLI) ─
# Always pulled fresh from GitHub to pick up latest changes.
# Run without set -e so partial failures don't abort the rest of bootstrap.
echo "Running dev-station installer..."
set +e
curl -fsSL 'https://raw.githubusercontent.com/dirkpetersen/dok/main/scripts/dev-station-install.sh' | bash
set -e

# ── Install textual (Maude TUI dependency) ───────────────────────────
echo "Installing textual..."
# 8.2.3 (and only 8.2.3) crashes the Set Creds modal with KeyError
# 'text-area--gutter' (textual#6528); >=8.2.4 is verified fine against
# maude.py. <9 shields against an unknown future major-version break.
pip install --quiet --break-system-packages 'textual>=8.2.4,<9'

# ── Source maude shell libraries ──────────────────────────────────────
# Warn-and-continue like update_all: one transient fetch failure (or a
# corporate proxy returning HTML) must not abort the whole bootstrap under
# set -e. Content-validate before sourcing; keep a cached copy if present.
mkdir -p "$HOME/.local/lib/maude"
for _lib in ensure-tools.sh refresh-md.sh update-skills.sh gemini.sh llm-mode.sh codex.sh opencode.sh grok.sh; do
    _lib_dst="$HOME/.local/lib/maude/$_lib"
    if curl -fsSL "$GH_RAW/lib/$_lib" -o "$_lib_dst.tmp" 2>/dev/null \
       && grep -q '^[a-z_]*()' "$_lib_dst.tmp"; then
        mv "$_lib_dst.tmp" "$_lib_dst"
    else
        rm -f "$_lib_dst.tmp"
        echo "WARNING: could not fetch lib/$_lib (kept cached copy if any)"
    fi
    if [[ -f "$_lib_dst" ]]; then
        # shellcheck source=/dev/null
        . "$_lib_dst" || echo "WARNING: failed to source $_lib"
    fi
done
unset _lib _lib_dst

# ── Install Bun + kanna-code ──────────────────────────────────────────
if ! command -v bun >/dev/null 2>&1; then
    echo "Installing Bun..."
    curl -fsSL https://bun.sh/install | bash
    export BUN_INSTALL="$HOME/.bun"
    export PATH="$BUN_INSTALL/bin:$PATH"
fi
echo "Installing kanna-code..."
bun install -g kanna-code
declare -F ensure_tool_symlinks >/dev/null && ensure_tool_symlinks
echo "Tool symlinks updated in ~/.local/bin"

# ── Symlink ~/.claude → ~/Maude/.claude (settings stored on host) ────
# The drvfs mount is now active (WSL was restarted between step 5 and 6).
# .claude and Projects dirs were pre-created on Windows by setup-wsl-maude.ps1.
if [[ -d "$HOME/Maude/.claude" ]]; then
    if [[ -d "$HOME/.claude" ]] && [[ ! -L "$HOME/.claude" ]]; then
        rm -rf "$HOME/.claude"
    fi
    if [[ ! -L "$HOME/.claude" ]]; then
        ln -sfn "$HOME/Maude/.claude" "$HOME/.claude"
        echo "~/.claude symlinked to ~/Maude/.claude (host-persistent)."
    fi
else
    mkdir -p "$HOME/.claude"
    echo "WARNING: ~/Maude/.claude not found, using local ~/.claude"
fi

# ── Symlink ~/.kanna → ~/Maude/.kanna (kanna data stored on host) ────
if [[ -d "$HOME/Maude/.kanna" ]]; then
    if [[ -d "$HOME/.kanna" ]] && [[ ! -L "$HOME/.kanna" ]]; then
        rm -rf "$HOME/.kanna"
    fi
    if [[ ! -L "$HOME/.kanna" ]]; then
        ln -sfn "$HOME/Maude/.kanna" "$HOME/.kanna"
        echo "~/.kanna symlinked to ~/Maude/.kanna (host-persistent)."
    fi
else
    mkdir -p "$HOME/.kanna"
    echo "WARNING: ~/Maude/.kanna not found, using local ~/.kanna"
fi

# ── Claude Code: status line (cwd + context-window % free) ───────────
curl -fsSL "$GH_RAW/statusline.sh" -o "$HOME/.claude/statusline.sh"
chmod +x "$HOME/.claude/statusline.sh"
echo "Claude Code: statusline.sh installed."

# ── Claude Code: settings.json (bypassPermissions + statusLine) ──────
# Merge with jq so existing user customisations are preserved.
SETTINGS="$HOME/.claude/settings.json"
[[ -s "$SETTINGS" ]] || echo '{}' > "$SETTINGS"
jq --arg cmd "$HOME/.claude/statusline.sh" '
    .permissions.defaultMode = "bypassPermissions"
    | .skipDangerousModePermissionPrompt = true
    | .statusLine = {type: "command", command: $cmd}
' "$SETTINGS" > "$SETTINGS.tmp" && mv "$SETTINGS.tmp" "$SETTINGS"
echo "Claude Code: settings.json updated (bypassPermissions + statusLine)."

# ── Claude Code: enable YOLO mode marker file ────────────────────────
if [[ ! -f "$HOME/.claude/yolo-mode" ]]; then
    echo "Remove this file to disable YOLO mode for Maude" > "$HOME/.claude/yolo-mode"
    echo "Claude Code: yolo-mode marker created."
fi

# ── Copy Anthropic skills into ~/.claude/skills (via update_skills lib) ─
echo "Cloning Anthropic skills repo..."
if count=$(update_skills); then
    echo "  Copied $count skills."
else
    echo "  WARNING: could not clone skills repo — skipping."
fi
unset count

# ── Install Google's Gemini CLI + gemini skill ───────────────────────
echo "Installing Gemini CLI + skill..."
if update_gemini; then
    echo "  Gemini CLI + skill installed."
else
    echo "  WARNING: Gemini CLI/skill install had problems — continuing."
fi

# ── Install reviewer CLIs (Codex, OpenCode, Grok) + skills ───────────
# Same warn-and-continue posture as Gemini: a reviewer failing to install
# must never abort the bootstrap.
for _tool in codex opencode grok; do
    echo "Installing ${_tool} CLI + skill..."
    if "update_${_tool}"; then
        echo "  ${_tool} CLI + skill installed."
    else
        echo "  WARNING: ${_tool} install had problems — continuing."
    fi
done
unset _tool

# ── Claude Code: project instructions (MAUDE.md + CLAUDE.md template) ─
# Uses refresh_claude_md() from refresh-md.sh, which validates the download
# and populates the local cache for subsequent project opens.
if declare -F refresh_claude_md >/dev/null; then
    MAUDE_RAW="$GH_RAW" refresh_claude_md
else
    echo "WARNING: refresh-md.sh not loaded — MAUDE.md refresh skipped."
fi
echo "Claude Code: MAUDE.md installed."

# ── Ensure key env vars are in ~/.bashrc ─────────────────────────────
for _ev in \
    "export COLORTERM=truecolor" \
    "export PYTHONDONTWRITEBYTECODE=1" \
    "export GEMINI_CLI_TRUST_WORKSPACE=true"; do
    _key=$(echo "$_ev" | sed 's/export //;s/=.*//')
    if ! grep -q "export ${_key}=" "$HOME/.bashrc" 2>/dev/null; then
        printf '\n%s\n' "$_ev" >> "$HOME/.bashrc"
    fi
done
unset _ev _key

# ── Write initial version stamp ──────────────────────────────────────
# 'maude update' will overwrite this with the real commit SHA; 'dev' is
# the right default for a fresh install that hasn't updated yet.
_ver_stamp="$HOME/.local/share/maude/version"
if [[ ! -f "$_ver_stamp" ]]; then
    mkdir -p "$(dirname "$_ver_stamp")"
    printf 'dev\n' > "$_ver_stamp"
fi
unset _ver_stamp

echo "=== User bootstrap complete ==="
