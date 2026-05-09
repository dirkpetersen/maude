# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See the parent `../CLAUDE.md` for the full project overview, architecture diagram, and build commands.

## This directory

`light/` is a self-contained set of scripts — there is no Makefile here. All scripts are deployed/executed directly; there is no build step. Changes are tested by running the scripts themselves.

| File | Language | Runs as |
|------|----------|---------|
| `setup-wsl-maude.ps1` | PowerShell | Windows admin |
| `teardown-wsl-maude.ps1` | PowerShell | Windows (self-elevates) |
| `root-bootstrap.sh` | Bash | root inside WSL |
| `maude-bootstrap.sh` | Bash | maude user inside WSL |
| `maude` | Bash | maude user inside WSL |
| `maude.py` | Python (Textual) | maude user inside WSL |

## Deployment / hot-patching

Because `setup-wsl-maude.ps1` downloads scripts from GitHub at install time, testing changes requires one of:

- **Full reinstall**: push to GitHub, then run `setup-wsl-maude.ps1` (slow, ~3 min)
- **Hot-patch bootstrap scripts**: copy a changed script directly into the running WSL distro, then re-run it manually — fast for `root-bootstrap.sh` and `maude-bootstrap.sh`
- **Hot-patch `maude` CLI**: `wsl -d Maude -u maude bash -c "cat > ~/bin/maude" < maude` then mark executable
- **Hot-patch `maude.py`**: copy to `~/.local/bin/maude.py` inside WSL; the daily-update stamp (`~/.maude-tui-last-update`) prevents auto-overwrite until you delete it

## Key patterns

### Piping files into WSL (no automount)
Automount is disabled (`automount=false`), so `wslpath`/`cp` from Windows won't work. All file transfers use piped stdin:
```powershell
Get-Content -Raw ".\script.sh" | wsl -d Maude -u root bash -c "cat > /tmp/script.sh"
```
Package-install scripts use base64 encode/decode to survive PowerShell's CRLF insertion:
```powershell
$encoded = [Convert]::ToBase64String([IO.File]::ReadAllBytes("script.sh"))
wsl ... bash -c "echo '$encoded' | base64 -d | bash"
```

### Two-phase bootstrap
`root-bootstrap.sh` writes `/etc/wsl.conf` (automount=false, default user, interop=false) and `/etc/fstab` — these only take effect after `wsl --terminate`. The setup script terminates WSL after root-bootstrap, then runs `maude-bootstrap.sh` as the maude user in the restarted distro.

### Host-persistent paths
`~/.claude` and `~/.kanna` are symlinks to `~/Maude/.claude` and `~/Maude/.kanna`. These survive WSL distro teardown/reinstall because they live on the Windows drvfs mount. Any file that must survive reinstall must go there.

### MAUDE.md vs CLAUDE.md in `~/.claude`
- `~/.claude/MAUDE.md` — always overwritten on every `maude` CLI launch (sandbox rules, not user-editable)
- `~/.claude/CLAUDE.md` — created once if missing (user-owned, includes `@MAUDE.md`)

### Double brackets in bash
Always use `[[ ]]` not `[ ]` in bash conditionals.

### `maude.py` self-update
On launch (and at/after noon local time), `maude.py` compares the date stored in `~/.maude-tui-last-update` against today (`YYYY-MM-DD`). If they don't match, it downloads a fresh copy from GitHub and atomically replaces itself; the new code takes effect on the *next* launch.

To disable auto-overwrite during development:
```bash
date +%Y-%m-%d > ~/.maude-tui-last-update
```

## WSL encoding gotcha
`wsl.exe --list --verbose` outputs UTF-16LE with BOM. The setup script strips null bytes before parsing:
```powershell
$raw = wsl.exe --list --verbose | Out-String
$clean = $raw -replace "`0", ""
```

## Credential forwarding (kanna web UI)
Kanna is launched with `CLAUDE_EXECUTABLE=$HOME/bin/claude` so it shells out to the Maude wrapper, which sets the appropriate auth env vars (Foundry/Azure/Bedrock/direct) per invocation. If the wrapper is missing, the env var isn't set and kanna falls back to plain `claude` on PATH. Both `maude web` (CLI) and the TUI's Web UI button refuse to launch when no credentials are detected (`ANTHROPIC_*` env, `~/.aws/credentials`, or `~/.azure/clauderc`).

## Git Setup Wizard (`maude github`)
A 4-step Textual wizard (`GitSetupWizard` modal) for first-run users:
1. **GitHub identity** — username → `api.github.com/users/<name>` lookup → pre-fill name/email
2. **SSH key** — detect or generate ed25519, paste pubkey into `https://github.com/settings/ssh/new`, verify via `ssh -T git@github.com`
3. **GPG key + signing** — detect or generate ed25519 + cv25519, paste at `https://github.com/settings/gpg/new`, verify via clearsign round-trip
4. **Final config** — `git config user.name/email/init.defaultBranch=main`, run `mom install -y keychain`, append keychain block to `~/.bashrc`

Manual key paste flow throughout (no `gh` auth needed). Reachable from the **Setup Git(hub)** TUI bottom-bar button, the `maude github` CLI command, or `python3 maude.py --github` (standalone wizard mode). The legacy `maude setup-git` / `--setup-git` aliases are still accepted for backward compatibility.

## Status line
`maude-bootstrap.sh` installs `~/.claude/statusline.sh` (cwd + remaining context-window %) and merges `~/.claude/settings.json` to set `statusLine.command`. Existing user customisations in `settings.json` are preserved (the merge is a JSON load → mutate → dump, not an overwrite).

## TUI default flag
TUI auto-launches by default. Opt-out is `~/.maude-tui-disabled` (presence of this file = TUI off). The legacy `~/.maude-tui-autostart` flag is no longer consulted — clean up references when seen.
