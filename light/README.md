# Maude Light

A lightweight WSL2 sandbox for agentic coding with [Claude Code](https://claude.ai/code). Deploys Ubuntu 24.04 as an isolated WSL distro with a single shared folder to the Windows host.

<img width="582" height="386" alt="image" src="https://github.com/user-attachments/assets/03c212a9-d248-4196-ace7-118a44acba09" />

## What you get

- **Sandboxed Ubuntu 24.04** — Windows drive automount is disabled; only one folder (`~/Maude`) is shared with the host via drvfs
- **Claude Code in yolo mode** — all tool permissions auto-approved (safe inside the sandbox)
- **Pre-installed dev tools** — Python, Go, Rust, build-essential, git, GitHub CLI, ripgrep, and [90+ packages](../packages/ubuntu-packages.yaml)
- **[mom](https://github.com/dirkpetersen/mom)** — install additional system packages without sudo (`mom install <pkg>`)
- **[Claude Code skills](https://github.com/anthropics/skills)** — pdf, docx, xlsx, pptx, and more pre-linked
- **Project launcher** — `maude project-name` creates a project folder, initializes git, and launches Claude Code
- **Host-persistent storage** — projects live in `~/Maude/Projects` (on the host mount), `~/.claude` is symlinked to `~/Maude/.claude` — all work survives distro removal
- **Fast rebuilds** — packages are baked into a reusable WSL template; teardown + reinstall takes under a minute

## Install

Open **PowerShell as Administrator** (right-click → "Run as Administrator") and run:

```powershell
curl.exe -sLo $env:TEMP\setup-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\setup-wsl-maude.ps1
```

That's it — one line. It downloads the setup script to a temp file and runs it. All companion files (bootstrap scripts, packages list, icon) are downloaded automatically from GitHub during setup.

> **Note:** Piping directly via `iex` (`Invoke-Expression`) may be blocked by antivirus on corporate machines. The `curl.exe` approach above works reliably everywhere. Use `curl.exe` (not `curl`) — in PowerShell, `curl` is an alias for `Invoke-WebRequest`.

The setup script will:

1. Install WSL and Windows Terminal (if not already present)
2. Detect your OneDrive (or Documents) folder and create a `Maude` subfolder as the shared mount point
3. Download Ubuntu 24.04 from the Microsoft Store and bake in all packages as a reusable template
4. Import the template as a new `Maude` WSL distro
5. Run root-level setup (user creation, sandbox isolation, mom, PATH, welcome screen)
6. Run user-level setup (dev-station, Bun, kanna-code, Claude Code skills, maude launcher)
7. Create a Windows Terminal profile and desktop shortcut with the Maude icon

On subsequent runs, step 3 is skipped (the template already exists), so rebuilds are fast.

## Uninstall

```powershell
curl.exe -sLo $env:TEMP\teardown-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/teardown-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\teardown-wsl-maude.ps1
```

This removes the Maude distro, Windows Terminal profile, and desktop shortcut. The template is kept for fast reinstalls. To remove everything including the template:

```powershell
curl.exe -sLo $env:TEMP\teardown-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/teardown-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\teardown-wsl-maude.ps1 -IncludeTemplate
```

## Usage

After install, open the **Maude** profile in Windows Terminal (or the desktop shortcut):

```
  maude project-name   Create or open a coding project
  maude list           Show your projects
  maude delete name    Delete a project (moves to .deleted/)
  maude help           Full usage info

  mom install <pkg>    Install system packages (no sudo needed)
```

### Shared folder

`~/Maude` is mounted from your Windows host (OneDrive or Documents). Use it to exchange files between Windows and the sandbox — documents, exports, data files, anything you need Claude to read or produce.

## How it works

```
Windows host
  │
  ├── C:\Users\you\OneDrive\Documents\Maude\   ← shared folder (host side)
  │       ├── Projects/                          ← coding projects (directly used by WSL)
  │       └── .claude/                           ← Claude Code config (symlinked from WSL)
  │
  └── WSL2: Maude (Ubuntu 24.04)
        ├── ~/Maude/           ← drvfs mount of shared folder
        │     ├── Projects/    ← coding projects (maude CLI)
        │     └── .claude/     ← Claude Code config + skills
        ├── ~/.claude          → symlink to ~/Maude/.claude
        ├── ~/bin/             ← user scripts (front of PATH)
        └── ~/.local/bin/      ← tool binaries (end of PATH)

  Automount: disabled (no /mnt/c, /mnt/d, etc.)
  Only mount: ~/Maude via /etc/fstab drvfs entry
```

## Files

| File | Runs as | Purpose |
|------|---------|---------|
| `setup-wsl-maude.ps1` | Admin (PowerShell) | 7-step orchestrator: WSL, WT, host folder, template, import, bootstrap, open |
| `teardown-wsl-maude.ps1` | Admin (PowerShell) | Unregister distro, remove WT profile + shortcut, optionally remove template |
| `root-bootstrap.sh` | root (inside WSL) | User creation, wsl.conf, fstab mount, mom, PATH, welcome screen |
| `maude-bootstrap.sh` | maude user (inside WSL) | dev-station, Bun, kanna-code, skills, Claude Code config |
| `maude` | maude user (inside WSL) | CLI launcher: creates projects, inits git, launches Claude Code |
