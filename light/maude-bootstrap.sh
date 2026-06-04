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
# Pin <8 — Textual 8.2.x has a 'text-area--gutter' regression that
# crashes the Set Creds modal. Drop the cap when fixed upstream.
pip install --quiet --break-system-packages 'textual<8'

# ── Source ensure_tool_symlinks() helper ──────────────────────────────
mkdir -p "$HOME/.local/lib/maude"
curl -fsSL "$GH_RAW/lib/ensure-tools.sh" -o "$HOME/.local/lib/maude/ensure-tools.sh"
# shellcheck source=/dev/null
. "$HOME/.local/lib/maude/ensure-tools.sh"

# ── Install Bun + kanna-code ──────────────────────────────────────────
if ! command -v bun >/dev/null 2>&1; then
    echo "Installing Bun..."
    curl -fsSL https://bun.sh/install | bash
    export BUN_INSTALL="$HOME/.bun"
    export PATH="$BUN_INSTALL/bin:$PATH"
fi
echo "Installing kanna-code..."
bun install -g kanna-code
ensure_tool_symlinks
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

# ── Copy Anthropic skills into ~/.claude/skills ──────────────────────
update_skills() {
    local skills_dir="$HOME/.claude/skills"
    mkdir -p "$skills_dir"
    local tmp; tmp=$(mktemp -d)
    echo "Cloning Anthropic skills repo..."
    if git clone --depth 1 https://github.com/anthropics/skills.git "$tmp" 2>/dev/null; then
        for skill in claude-api doc-coauthoring docx mcp-builder pdf pptx skill-creator xlsx; do
            if [[ -d "$tmp/skills/$skill" ]]; then
                [[ -L "$skills_dir/$skill" ]] && rm -f "$skills_dir/$skill"
                cp -af "$tmp/skills/$skill" "$skills_dir/"
                echo "  Copied skill: $skill"
            else
                echo "  WARNING: skill '$skill' not found in repo"
            fi
        done
    else
        echo "WARNING: could not clone skills repo — skipping"
    fi
    rm -rf "$tmp"
}
update_skills

# ── Claude Code: project instructions ────────────────────────────────
# MAUDE.md is always overwritten with latest sandbox rules.
# CLAUDE.md is only created if missing (user may have customized it).
curl -fsSL "$GH_RAW/MAUDE.md" -o "$HOME/.claude/MAUDE.md"
echo "Claude Code: MAUDE.md installed."

if [[ ! -f "$HOME/.claude/CLAUDE.md" ]]; then
    curl -fsSL "$GH_RAW/CLAUDE.template.md" -o "$HOME/.claude/CLAUDE.md"
    echo "Claude Code: CLAUDE.md created from template."
fi

echo "=== User bootstrap complete ==="
