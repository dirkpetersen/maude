#!/bin/bash
# maude-bootstrap.sh — Runs as the maude user inside the Maude WSL distro.
# Installs dev-station, the maude launcher, and user-level configuration.
#
# Usage:  maude-bootstrap.sh
set -e

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
pip install --quiet --break-system-packages textual

# ── Install Bun + kanna-code ──────────────────────────────────────────
if ! command -v bun >/dev/null 2>&1; then
    echo "Installing Bun..."
    curl -fsSL https://bun.sh/install | bash
    export BUN_INSTALL="$HOME/.bun"
    export PATH="$BUN_INSTALL/bin:$PATH"
fi
echo "Installing kanna-code..."
bun install -g kanna-code
# Symlink kanna into ~/.local/bin so it's on PATH
# (Bun's ~/.bun/bin may be stripped by maude-path.sh)
if [[ -x "$HOME/.bun/bin/kanna" ]] && [[ ! -e "$HOME/.local/bin/kanna" ]]; then
    ln -sfn "$HOME/.bun/bin/kanna" "$HOME/.local/bin/kanna"
    echo "kanna symlinked to ~/.local/bin/"
fi

# ── Symlink ~/.claude → ~/Maude/.claude (settings stored on host) ────
# The drvfs mount is now active (WSL was restarted between step 5 and 6).
# .claude and Projects dirs were pre-created on Windows by setup-wsl-maude.ps1.
if [[ -d "$HOME/Maude/.claude" ]]; then
    # Remove plain ~/.claude dir if it exists (e.g. created by Claude Code installer)
    if [[ -d "$HOME/.claude" ]] && [[ ! -L "$HOME/.claude" ]]; then
        rm -rf "$HOME/.claude"
    fi
    if [[ ! -L "$HOME/.claude" ]]; then
        ln -sfn "$HOME/Maude/.claude" "$HOME/.claude"
        echo "~/.claude symlinked to ~/Maude/.claude (host-persistent)."
    fi
else
    # Mount not active — create plain directory as fallback
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

# ── Claude Code: bypass permissions (safe inside sandbox) ────────────
if [[ ! -f "$HOME/.claude/settings.json" ]]; then
    cat > "$HOME/.claude/settings.json" << 'SETTINGSEOF'
{
  "permissions": {
    "defaultMode": "bypassPermissions"
  },
  "skipDangerousModePermissionPrompt": true
}
SETTINGSEOF
    echo "Claude Code: bypassPermissions mode enabled (sandbox-safe)."
fi

# ── Claude Code: enable YOLO mode marker file ────────────────────────
if [[ ! -f "$HOME/.claude/yolo-mode" ]]; then
    echo "Remove this file to disable YOLO mode for Maude" > "$HOME/.claude/yolo-mode"
    echo "Claude Code: yolo-mode marker created."
fi

# ── Copy Anthropic skills into ~/.claude/skills ──────────────────────
# Must run AFTER the ~/.claude symlink is created above.
# Clone repo to a temp dir, copy skill folders, then remove the clone.
SKILLS_DIR="$HOME/.claude/skills"
mkdir -p "$SKILLS_DIR"
SKILLS_TMP=$(mktemp -d)
echo "Cloning Anthropic skills repo..."
if git clone --depth 1 https://github.com/anthropics/skills.git "$SKILLS_TMP" 2>/dev/null; then
    for skill in claude-api doc-coauthoring docx mcp-builder pdf pptx skill-creator xlsx; do
        if [[ -d "$SKILLS_TMP/skills/$skill" ]]; then
            cp -af "$SKILLS_TMP/skills/$skill" "$SKILLS_DIR/"
            echo "  Copied skill: $skill"
        else
            echo "  WARNING: skill '$skill' not found in repo"
        fi
    done
else
    echo "WARNING: could not clone skills repo — skipping"
fi
rm -rf "$SKILLS_TMP"

# ── Claude Code: project instructions ────────────────────────────────
# MAUDE.md is always overwritten with latest sandbox rules.
# CLAUDE.md is only created if missing (user may have customized it).
cat > "$HOME/.claude/MAUDE.md" << 'MAUDEEOF'
# Maude Sandbox

## File Access Rules

- **Read** from `~/Maude` (top-level files) and the current project folder
  `~/Maude/Projects/<project>/`. Do NOT read from other project folders.
- **Write** only to the current project folder: `~/Maude/Projects/<project>/`.
- If the user explicitly asks you to read or write elsewhere, do so --
  but these are the defaults.

`~/Maude` is a drvfs mount shared with the Windows host. It is the
**only** path accessible from both Windows and WSL. Files the user
drags into the `Maude` folder on Windows are immediately visible here.

## Projects

Projects live in `~/Maude/Projects` which is on the shared host mount.
Your work is automatically preserved on the Windows side even if the
WSL distro is removed. `~/.claude` is also a symlink to `~/Maude/.claude`.

## Package Installation

Use `mom install -y <package>` to install system packages -- no sudo needed.
Always use `-y` for unattended installs.
MAUDEEOF
echo "Claude Code: MAUDE.md created."

if [[ ! -f "$HOME/.claude/CLAUDE.md" ]]; then
    cat > "$HOME/.claude/CLAUDE.md" << 'CLAUDEEOF'
<!-- DO NOT remove the line below -- it loads Maude sandbox rules -->
@MAUDE.md

# User Instructions

Add your own instructions here. This file persists across reinstalls.
CLAUDEEOF
    echo "Claude Code: CLAUDE.md created."
fi

echo "=== User bootstrap complete ==="
