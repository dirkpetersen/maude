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
The TUI checks `~/.maude-tui-last-update` and downloads a fresh copy from GitHub if the stamp is older than 24 hours (checked at/after noon local time). To disable auto-overwrite during development, `touch ~/.maude-tui-last-update` with today's date.

## WSL encoding gotcha
`wsl.exe --list --verbose` outputs UTF-16LE with BOM. The setup script strips null bytes before parsing:
```powershell
$raw = wsl.exe --list --verbose | Out-String
$clean = $raw -replace "`0", ""
```

## Credential forwarding (kanna web UI)
`maude web` runs `claude --wdebug` to extract the active auth env vars, then parses the JSON output for `ANTHROPIC_*`, `CLAUDE_*`, and `AWS_*` keys before launching kanna as a subprocess with those vars injected.
