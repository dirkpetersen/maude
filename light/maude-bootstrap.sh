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

# ── Install Bun + kanna-code ──────────────────────────────────────────
if ! command -v bun >/dev/null 2>&1; then
    echo "Installing Bun..."
    curl -fsSL https://bun.sh/install | bash
    export BUN_INSTALL="$HOME/.bun"
    export PATH="$BUN_INSTALL/bin:$PATH"
fi
echo "Installing kanna-code..."
bun install -g kanna-code

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

# ── Clone Anthropic skills repo and symlink into ~/.claude/skills ─────
# Must run AFTER the ~/.claude symlink is created above.
SKILLS_REPO="$HOME/gh/anthropic-skills"
mkdir -p "$HOME/gh"
if [[ ! -d "$SKILLS_REPO" ]]; then
    echo "Cloning Anthropic skills repo..."
    git clone https://github.com/anthropics/skills.git "$SKILLS_REPO"
fi

SKILLS_DIR="$HOME/.claude/skills"
mkdir -p "$SKILLS_DIR"
for skill in claude-api doc-coauthoring docx mcp-builder pdf pptx skill-creator xlsx; do
    if [[ -d "$SKILLS_REPO/skills/$skill" ]]; then
        ln -sfn "$SKILLS_REPO/skills/$skill" "$SKILLS_DIR/$skill"
        echo "  Linked skill: $skill"
    else
        echo "  WARNING: skill '$skill' not found in repo"
    fi
done

# ── Claude Code: project instructions ────────────────────────────────
cat > "$HOME/.claude/CLAUDE.md" << 'CLAUDEEOF'
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

Use `mom install -y <package>` to install system packages — no sudo needed.
Always use `-y` for unattended installs.
CLAUDEEOF
echo "Claude Code: CLAUDE.md created."

echo "=== User bootstrap complete ==="
