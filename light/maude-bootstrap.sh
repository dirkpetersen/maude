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

# ── Clone Anthropic skills repo and install Claude Code skills ────────
SKILLS_REPO="$HOME/gh/anthropic-skills"
mkdir -p "$HOME/gh"
if [ ! -d "$SKILLS_REPO" ]; then
    echo "Cloning Anthropic skills repo..."
    git clone https://github.com/anthropics/skills.git "$SKILLS_REPO"
fi

SKILLS_DIR="$HOME/.claude/skills"
mkdir -p "$SKILLS_DIR"
for skill in claude-api doc-coauthoring docx mcp-builder pdf pptx skill-creator xlsx; do
    if [ -d "$SKILLS_REPO/skills/$skill" ]; then
        ln -sfn "$SKILLS_REPO/skills/$skill" "$SKILLS_DIR/$skill"
        echo "  Linked skill: $skill"
    else
        echo "  WARNING: skill '$skill' not found in repo"
    fi
done

# ── Symlink ~/.claude → ~/Maude/.claude (settings stored on host) ────
# The drvfs mount is now active (WSL was restarted between step 5 and 6).
# .claude and Projects dirs were pre-created on Windows by setup-wsl-maude.ps1.
if [ -d "$HOME/Maude/.claude" ] && [ ! -L "$HOME/.claude" ]; then
    # Remove plain ~/.claude dir if it exists (e.g. created by Claude Code installer)
    [ -d "$HOME/.claude" ] && rm -rf "$HOME/.claude"
    ln -sfn "$HOME/Maude/.claude" "$HOME/.claude"
    echo "~/.claude symlinked to ~/Maude/.claude (host-persistent)."
fi
mkdir -p "$HOME/.claude" 2>/dev/null || true

# ── Claude Code: bypass permissions (safe inside sandbox) ────────────
if [ ! -f "$HOME/.claude/settings.json" ]; then
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

# ── Claude Code: project instructions ────────────────────────────────
cat > "$HOME/.claude/CLAUDE.md" << 'CLAUDEEOF'
# Maude Sandbox

## Shared Folder

`~/Maude` is a mounted folder shared with the Windows host (via drvfs).
It is the **only** path the user can access from both Windows and WSL.

Use `~/Maude` for any files the user needs to open or exchange:
- Documents: `.docx`, `.xlsx`, `.pptx`, `.pdf`
- Data files, images, exports, downloads
- Anything the user drags in from Windows or asks you to produce for them

When the user asks you to create a document, spreadsheet, presentation,
or any file they will open on the Windows side, **always write it to
`~/Maude`** (or a subfolder of it).

## Projects

Projects live in `~/Maude/Projects` which is on the shared host mount.
Your work is automatically preserved on the Windows side even if the
WSL distro is removed. `~/.claude` is also a symlink to `~/Maude/.claude`.

## Package Installation

Use `mom install <package>` to install system packages — no sudo needed.
CLAUDEEOF
echo "Claude Code: CLAUDE.md created."

# ── Install maude launcher (if copied to /tmp by setup script) ────────
if [ -f /tmp/maude-launcher ] && [ ! -f "$HOME/.local/bin/maude" ]; then
    install -m 755 /tmp/maude-launcher "$HOME/.local/bin/maude"
    echo "'maude' launcher installed to ~/.local/bin/maude"
elif [ -f "$HOME/.local/bin/maude" ]; then
    echo "'maude' launcher already installed."
fi

# ── PS1: replace hostname with underscore ─────────────────────────────
# Only add if not already customized
if ! grep -q 'MAUDE_PS1' "$HOME/.bashrc" 2>/dev/null; then
    cat >> "$HOME/.bashrc" << 'PS1EOF'

# Maude PS1: show user@_ instead of user@hostname
MAUDE_PS1=1
PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u\[\033[00m\]@\[\033[01;34m\]_\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
PS1EOF
fi

echo "=== User bootstrap complete ==="
