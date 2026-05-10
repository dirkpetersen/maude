# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **This is the active production implementation.** Maude Light is the only fully-shipped target. The parent repo (`../`) contains the long-term roadmap: macOS support, Linux VM-hosted sandboxes, Proxmox appliances, and a full multi-user appliance — but those targets are not yet production.

## What Maude Light is

A one-command Windows sandbox for agentic coding. A single PowerShell command installs an isolated Ubuntu WSL2 distro (`Maude`), mounts a shared folder on the Windows host (OneDrive or `AppData\LocalLow\Maude\`), symlinks Claude Code config into that folder so settings survive reinstalls, and drops the user into a Textual TUI where they can create projects and open them in Claude Code.

**Goal**: a non-technical Windows user pastes one line into PowerShell and has a working AI coding environment in under 3 minutes.

## Architecture

```
Windows host
    |
    +-- C:\Users\<user>\OneDrive\...\Maude\      <- shared folder (OneDrive preferred)
    |   OR AppData\LocalLow\Maude\               <- fallback (no OneDrive)
    |       +-- Projects/     <- coding projects (directly used by WSL)
    |       +-- .claude/      <- Claude Code config (symlinked from WSL home)
    |       +-- .kanna/       <- kanna web UI data (symlinked from WSL home)
    |
    +-- WSL2 distro: "Maude" (Ubuntu 26.04 default, 24.04 with -Noble flag)
          +-- ~/Maude/        <- drvfs mount of shared folder
          +-- ~/.claude       -> symlink to ~/Maude/.claude
          +-- ~/.kanna        -> symlink to ~/Maude/.kanna
          +-- ~/bin/          <- user scripts (front of PATH)
          +-- ~/.local/bin/   <- tool binaries (end of PATH)

Automount: disabled (no /mnt/c, /mnt/d — intentional security boundary)
Only mount: ~/Maude via /etc/fstab drvfs entry
```

`setup-wsl-maude.ps1` bakes packages into a reusable `Ubuntu-24.04-Template` WSL distro first, then imports it as `Maude`. Teardown + reinstall takes under a minute because the template is preserved.

**Security model**: automount disabled + no generic sudo = the AI agent can only reach the shared `Maude` folder and installed tools. Claude Code runs in `bypassPermissions` mode, which is safe inside the sandbox. A `~/.claude/yolo-mode` marker file enables this; delete it to restore normal permission prompts.

**Upstream repos** (checked out at `~/gh/` on the dev machine):
- `~/gh/web-term` — Node.js browser terminal via SSH+tmux (xterm.js frontend)
- `~/gh/appmotel` — Bash-based PaaS: deploys Python/Node/Go apps as systemd services behind Traefik
- `~/gh/mom` — Rust setuid tool allowing non-root users to install packages via apt/dnf

## Script pipeline

All files in `light/` are deployed/executed directly — there is no build step, no Makefile.

| Script | Language | Runs as | Purpose |
|--------|----------|---------|---------|
| `setup-wsl-maude.ps1` | PowerShell | Windows admin | 7-step orchestrator: WSL, WT profile, host folder, template, import, root-bootstrap, maude-bootstrap |
| `teardown-wsl-maude.ps1` | PowerShell | Windows (self-elevates) | Unregister distro, remove WT profile + shortcut, optionally wipe template |
| `root-bootstrap.sh` | Bash | root inside WSL | User creation, `wsl.conf`, `fstab` drvfs mount, `mom-inst` .deb, PATH, welcome screen, Claude Code install |
| `maude-bootstrap.sh` | Bash | maude user inside WSL | dev-station, Bun, kanna-code, skills, Claude Code config, yolo-mode marker |
| `maude` | Bash | maude user inside WSL | CLI: `maude <name>` open/create project, `maude list/delete/web/tui/github/update` |
| `maude.py` | Python (Textual) | maude user inside WSL | Full-screen TUI — always launched via `maude tui`; self-updates from GitHub daily |
| `lib.sh` | Bash | — | Shared helpers sourced by `setup-wsl-maude.ps1` (via WSL pipe) |

## Testing & linting

Tests live in `../tests/test-*.sh` (run from repo root). There is no test runner specific to `light/`; use the parent Makefile:

```bash
make test           # Run full test suite
make test-fast      # Stop on first failure
make lint           # Bash -n syntax check all scripts
bash tests/test-path-setup.sh   # Run one test file directly
```

Test helper API (source `tests/lib.sh`):

```bash
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
suite_header "My feature"
assert_eq          "description" "expected" "$actual"
assert_contains    "description" "needle" "$haystack"
assert_file_exists "description" "/path/to/file"
assert_executable  "description" "/path/to/bin"
assert_exit_zero    "description" some_command --args
assert_exit_nonzero "description" some_command --args
skip "reason"
suite_summary   # prints results; exits non-zero if any FAIL
```

**Gotcha**: use `PASS=$((PASS+1))` not `((PASS++))` — the latter exits non-zero when the value is 0 under `set -e`.

## Hot-patching during development

`setup-wsl-maude.ps1` downloads scripts from GitHub at install time, so testing local changes without a full reinstall requires one of:

- **Full reinstall** (~3 min): push to GitHub, then run `setup-wsl-maude.ps1`
- **Hot-patch bootstrap scripts**: pipe the changed file into the running distro, then re-run it manually:
  ```powershell
  Get-Content -Raw ".\root-bootstrap.sh" | wsl -d Maude -u root bash -c "cat > /tmp/root-bootstrap.sh && bash /tmp/root-bootstrap.sh"
  ```
- **Hot-patch `maude` CLI**:
  ```powershell
  Get-Content -Raw ".\maude" | wsl -d Maude -u maude bash -c "cat > ~/bin/maude && chmod +x ~/bin/maude"
  ```
- **Hot-patch `maude.py`**: copy to `~/.local/bin/maude.py` inside WSL, then delete the daily-update stamp to prevent auto-overwrite:
  ```bash
  date +%Y-%m-%d > ~/.maude-tui-last-update
  ```

## Key implementation patterns

### Piping files into WSL (no automount)
Automount is disabled, so `wslpath`/`cp` from Windows don't work. All file transfers use piped stdin:
```powershell
Get-Content -Raw ".\script.sh" | wsl -d Maude -u root bash -c "cat > /tmp/script.sh"
```
Package-install scripts use base64 to survive PowerShell's CRLF insertion:
```powershell
$encoded = [Convert]::ToBase64String([IO.File]::ReadAllBytes("script.sh"))
wsl -d Maude -- bash -c "echo '$encoded' | base64 -d | bash"
```

### Two-phase bootstrap
`root-bootstrap.sh` writes `/etc/wsl.conf` (`automount=false`, default user, `interop=false`) and `/etc/fstab`. These only take effect after `wsl --terminate`. The setup script terminates WSL after root-bootstrap, then runs `maude-bootstrap.sh` as the maude user in the restarted distro.

### Host-persistent paths
`~/.claude` and `~/.kanna` are symlinks to `~/Maude/.claude` and `~/Maude/.kanna`. These survive WSL distro teardown/reinstall because they live on the Windows drvfs mount. Any file that must survive reinstall must live there.

### MAUDE.md vs CLAUDE.md in `~/.claude`
- `~/.claude/MAUDE.md` — always overwritten on every `maude` CLI launch (sandbox rules, not user-editable)
- `~/.claude/CLAUDE.md` — created once if missing (user-owned; includes `@MAUDE.md`)

### Double brackets in bash
Always use `[[ ]]` not `[ ]` in bash conditionals.

### `curl.exe` not `curl` in PowerShell
`curl` in PowerShell aliases to `Invoke-WebRequest`. Always use `curl.exe` explicitly. Also note `iex` may be blocked by antivirus on corporate machines.

### WSL encoding gotcha
`wsl.exe --list --verbose` outputs UTF-16LE with BOM. Strip null bytes before parsing:
```powershell
$raw   = wsl.exe --list --verbose | Out-String
$clean = $raw -replace "`0", ""
```

### Windows Terminal profiles
WT auto-generates profiles with `source=Microsoft.WSL` — they cannot be deleted, only hidden with pre-hidden stubs. WT profile cleanup must run **before** self-elevation (elevated process has a different `$env:LOCALAPPDATA`).

## Maude TUI (`maude tui` / `maude.py`)

Full-screen Textual TUI — default welcome experience for every new terminal session.

```
┌─ Maude ──────────────────────────────────────────────────────────────┐
│  ASCII logo (green)         │  Projects                              │
│  ──────────────             │ ┌────────────────────────────────────┐ │
│  Start TUI with Maude  [x]  │ │ banana    Modified: Apr 10, 2026   │ │
│  ──────────────             │ │ my-app    Modified: Apr 9, 2026    │ │
│  Tips:                      │ │ ...                                │ │
│   Screen split  Alt+Sh+±    │ └────────────────────────────────────┘ │
│   Share image  drop file    │                                        │
│   Voice  Win+H              │                                        │
│  ──────────────             │                                        │
│  Claude model               │                                        │
│   ( ) opus-1m  ( ) opus     │                                        │
│   ( ) sonnet-1m  ( ) sonnet │                                        │
│   ( ) haiku                 │                                        │
├─────────────────────────────┴────────────────────────────────────────┤
│ [Open Project] [+ New] [Web UI] [Setup Git] [Set Credentials] [CLI]  │
└──────────────────────────────────────────────────────────────────────┘
```

**Key behaviors:**
- **Open project**: `app.suspend()` → `claude <model> --continue` (fallback: `claude <model>`) → resume TUI; cursor restored to the project just opened
- **Delete**: confirm modal → soft-delete to `Projects/.deleted/` (not permanent)
- **New project**: modal, spaces auto-replaced with hyphens, git init
- **Web UI**: launches kanna with `CLAUDE_EXECUTABLE=$HOME/bin/claude` so the wrapper sets auth env vars (Foundry/Azure/Bedrock/direct). Click again to stop — `SIGTERM` → `SIGKILL` + `fuser -k 3210/tcp`. Refuses to launch without credentials; pops `CredsEntryScreen` as recovery
- **Setup Git**: `GitSetupWizard` 4-step modal — (1) GitHub username → API lookup for name/email, (2) ed25519 SSH key detect/generate + verify via `ssh -T git@github.com`, (3) GPG ed25519+cv25519 detect/generate + clearsign round-trip, (4) `git config` + `mom install -y keychain` + `~/.bashrc` keychain block. Manual paste flows throughout; no `gh` auth required
- **Claude model picker**: 5-button radio (`opus-1m` default). Persists to `~/.maude-model`
- **Credential gate**: `CredsEntryScreen` blocks startup when no LLM credentials are found (`ANTHROPIC_API_KEY`, `ANTHROPIC_FOUNDRY_API_KEY`, `~/.aws/credentials`, `~/.azure/clauderc`). Parses pasted `export` lines for `ANTHROPIC_*/CLAUDE_*/AWS_*/AZURE_*` vars; writes to `~/.azure/clauderc` (mode 0600)
- **Auto-launch**: TUI launches by default on every terminal session. Opt-out: create `~/.maude-tui-disabled`. The legacy `~/.maude-tui-autostart` flag is no longer consulted — remove references when seen
- **Daily self-update**: `maybe_self_update()` runs at module entry, before `app.run()`. Compares `~/.maude-tui-last-update` (format: `YYYY-MM-DD`) against today; if stale and time ≥ noon local, downloads fresh copy from GitHub and atomically replaces itself. New code takes effect on next launch
- **Status line**: `~/.claude/statusline.sh` renders cwd + remaining context-window %. Bootstrap merges this into `~/.claude/settings.json` `statusLine.command` without overwriting existing user customisations
- **Exit hint**: after quitting TUI prints "Please type menu \<Enter\> to get back to the TUI or type maude help \<Enter\>"
- **Colors**: green logo, dusty-rose accents, warm-grey toggles (overrides Textual default blue on `Checkbox`/`RadioButton`)
- **Dependency**: `textual` — installed via `pip install textual` in `maude-bootstrap.sh`; pin `textual<8` (8.2.x crashes `CredsEntryScreen` with `KeyError: gutter`)

## PATH convention

`~/bin` must be at the **front** of PATH; `~/.local/bin` must also be present. `maude-path.sh` (written to `/etc/profile.d/` by `root-bootstrap.sh`) runs at the **end** of `~/.bashrc` — after tool installers like Claude Code that prepend their own paths — then re-enforces `~/bin` first and deduplicates `~/.local/bin`.

## Credential forwarding (kanna web UI)

Kanna is launched with `CLAUDE_EXECUTABLE=$HOME/bin/claude` so it shells out to the Maude wrapper, which sets the appropriate auth env vars per invocation. If the wrapper is missing, the env var is unset and kanna falls back to plain `claude` on PATH.

## Key files that survive reinstall (on drvfs mount)

| Path (inside WSL) | Purpose |
|-------------------|---------|
| `~/Maude/.claude/` | All Claude Code config, credentials, CLAUDE.md, yolo-mode marker |
| `~/Maude/.kanna/` | kanna web UI data |
| `~/Maude/Projects/` | User's coding projects |
| `~/Maude/DANGER-ZONE.txt` | Warning not to share Maude folder via OneDrive sharing; auto-restored on startup if deleted |
