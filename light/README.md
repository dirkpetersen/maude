# Maude Light

A secure WSL2 sandbox for agentic AI coding. By default, WSL instances mount the entire Windows file system, giving both the user and any AI agent unrestricted access to OneDrive, Documents, and everything else on disk. Maude changes that.

Maude creates a single `Maude` subfolder inside OneDrive (or `AppData\LocalLow` if OneDrive is not available) and shares **only** that empty directory with a standard Ubuntu WSL instance. It removes generic `sudo` access (unlike the default Ubuntu configuration) so the user and the AI agent can only run tools that are already installed. New packages can be added through the [`mom`](https://github.com/dirkpetersen/mom) package manager, which supports install, update, and repo refresh — but cannot add arbitrary repositories or run unvetted code. The user decides which files to expose to the AI agent by copying them into the `Maude` folder.

Beyond security, Maude addresses **manageability**: IT departments are often concerned about another OS to manage. Maude is narrow in scope and stores all relevant settings in the `Maude` folder on OneDrive, so the sandbox can be torn down and reinstalled at any time without losing configuration or project data.

Use a TUI
<img width="981" height="604" alt="image" src="https://github.com/user-attachments/assets/f223d378-2471-4c70-8145-798e2bcd3439" />

or a simple CLI
<img width="988" height="466" alt="image" src="https://github.com/user-attachments/assets/4a222cef-227f-49dd-8f4a-9cdfeb14e029" />


## What you get

- **Sandboxed Ubuntu 26.04** (or 24.04 with `-Noble`) — Windows drive automount is disabled; only one folder (`~/Maude`) is shared with the host via drvfs
- **Claude Code in yolo mode** — all tool permissions auto-approved (safe inside the sandbox)
- **Pre-installed dev tools** — Python, Node.js 24, Go, Rust, build-essential, git, GitHub CLI, ripgrep, and [90+ packages](../packages/ubuntu-packages.yaml)
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

That's it — one line. Installs Ubuntu 26.04 (Resolute Raccoon) with the shared folder in `AppData\LocalLow\Maude`, pinned to Quick Access in File Explorer. On reinstalls, the script automatically reuses the previous folder location.

> **Note:** Use `curl.exe` (not `curl`) — in PowerShell, `curl` is an alias for `Invoke-WebRequest`. Piping via `iex` may be blocked by antivirus on corporate machines; the file-based approach above works reliably everywhere.

### Install with OneDrive sync

Store the shared folder inside OneDrive for cross-device sync (Business > Personal > generic):

```powershell
curl.exe -sLo $env:TEMP\setup-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\setup-wsl-maude.ps1 -OneDrive
```

### Install without OneDrive

Force `AppData\LocalLow\Maude` even on reinstall (ignores previous location):

```powershell
curl.exe -sLo $env:TEMP\setup-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\setup-wsl-maude.ps1 -NoOneDrive
```

### Install with Ubuntu 24.04

Use Ubuntu 24.04 LTS (Noble Numbat) instead of the default 26.04:

```powershell
curl.exe -sLo $env:TEMP\setup-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\setup-wsl-maude.ps1 -Noble
```

### Install options summary

| Flag | Effect |
|------|--------|
| *(default)* | Ubuntu 26.04, `AppData\LocalLow\Maude` (new) or previous location (reinstall) |
| `-OneDrive` | Shared folder in OneDrive |
| `-NoOneDrive` | Force `AppData\LocalLow\Maude` |
| `-Noble` | Use Ubuntu 24.04 instead of 26.04 |

Flags can be combined, e.g. `-OneDrive -Noble`.

The setup script will:

1. Install WSL and Windows Terminal (if not already present)
2. Create a `Maude` shared folder (see options above) and pin it to Quick Access
3. Download Ubuntu from the Microsoft Store and bake in all packages as a reusable template
4. Import the template as a new `Maude` WSL distro
5. Run root-level setup (user creation, sandbox isolation, mom, PATH, welcome screen)
6. Run user-level setup (dev-station, Bun, kanna-code, Claude Code skills, maude launcher)
7. Create a Windows Terminal profile and desktop shortcut with the Maude icon

On subsequent runs, step 3 is skipped (the template already exists), so rebuilds are fast.

### Disk space

The installer checks free space on C: at startup and prints it in gigabytes:

- **>= 10 GB free** — normal install; the Ubuntu template is kept for fast future reinstalls
- **5--10 GB free** — install proceeds but the template is automatically removed afterward to reclaim space (reinstalls will be slower)
- **< 5 GB free** — warning that Maude may not function properly; you get 10 seconds to press Ctrl+C to cancel before the install continues

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

### Document analysis

Drop files into your `Maude` folder on Windows, then ask Claude to analyze them. Supported formats:

- **PDFs** -- extract text, tables, and structured data
- **Word docs** (.docx) -- read and parse content
- **Spreadsheets** (.xlsx, .csv) -- read, analyze, and compute on data
- **PowerPoint** (.pptx) -- extract text and structure from slides

Claude can summarize, compare multiple documents, extract specific data (names, dates, figures, clauses), identify patterns or inconsistencies, and generate new documents from the analysis. Output files (reports, spreadsheets, presentations) are written to `~/Maude` so you can open them directly on Windows.

Example workflow:
1. Copy your documents into the `Maude` folder on Windows
2. Open the Maude terminal and run `maude my-analysis`
3. Ask Claude: *"Summarize the three PDFs in ~/Maude"* or *"Compare these two contracts and list the differences"*
4. Find the output in your `Maude` folder on Windows

### Web apps & browser

[kanna](https://github.com/jakemor/kanna) provides a web-based UI for Claude Code. WSL2 forwards localhost ports to Windows natively, so kanna is accessible from any Windows browser without enabling interop.

```
maude web
```

This launches kanna and prints a URL (`http://127.0.0.1:3210`). Ctrl+click the link in the terminal to open it in your Windows browser. Kanna data is stored in `~/Maude/.kanna` (host-persistent via the shared mount).

### Shared folder

`~/Maude` is mounted from your Windows host (OneDrive, or `AppData\LocalLow` with `-NoOneDrive`). Use it to exchange files between Windows and the sandbox — documents, exports, data files, anything you need Claude to read or produce. The folder is pinned to Quick Access in File Explorer for easy access.

## How it works

```
Windows host
  │
  ├── C:\Users\you\OneDrive\...\Maude\          ← shared folder (OneDrive)
  │   OR  AppData\LocalLow\Maude\              ← with -NoOneDrive
  │       ├── Projects/                          ← coding projects (directly used by WSL)
  │       ├── .claude/                           ← Claude Code config (symlinked from WSL)
  │       └── .kanna/                            ← kanna web UI data (symlinked from WSL)
  │
  └── WSL2: Maude (Ubuntu 24.04)
        ├── ~/Maude/           ← drvfs mount of shared folder
        │     ├── Projects/    ← coding projects (maude CLI)
        │     ├── .claude/     ← Claude Code config + skills
        │     └── .kanna/      ← kanna web UI data
        ├── ~/.claude          → symlink to ~/Maude/.claude
        ├── ~/.kanna           → symlink to ~/Maude/.kanna
        ├── ~/bin/             ← user scripts (front of PATH)
        └── ~/.local/bin/      ← tool binaries (end of PATH)

  Automount: disabled (no /mnt/c, /mnt/d, etc.)
  Only mount: ~/Maude via /etc/fstab drvfs entry
```

## Future: macOS Support

Maude Light could be ported to macOS using [Lima](https://github.com/lima-vm/lima) (lightweight Linux VMs via Apple Virtualization.framework, `brew install lima`). [OrbStack](https://orbstack.dev/) is a polished commercial alternative.

**What changes:**

| Windows (current) | Mac equivalent |
|---|---|
| `setup-wsl-maude.ps1` (PowerShell) | `setup-maude.sh` (bash) |
| `wsl --import` / `--export` | `limactl create` with a YAML config |
| drvfs mount via `/etc/fstab` | Lima `mounts:` config (virtiofs) |
| `wsl.conf` automount=false | Lima default -- no host mounts unless configured |
| Windows Terminal profile + icon | Not needed -- `limactl shell Maude` or alias |
| Desktop `.lnk` shortcut | macOS `.app` bundle or Dock alias (optional) |
| `curl.exe` (not `curl`) | `curl` works natively |
| OneDrive folder detection | `~/Documents` or iCloud Drive detection |

**What stays the same:** `root-bootstrap.sh`, `maude-bootstrap.sh`, the `maude` launcher, the `~/.claude` symlink strategy, `ubuntu-packages.yaml`, and all tooling (Claude Code, mom, dev-station, skills) run identically inside the Linux VM. The main work is replacing the ~500-line PowerShell orchestrator with a ~200-line bash script that drives Lima instead of WSL.

## Files

| File | Runs as | Purpose |
|------|---------|---------|
| `setup-wsl-maude.ps1` | Admin (PowerShell) | 7-step orchestrator: WSL, WT, host folder, template, import, bootstrap, open |
| `teardown-wsl-maude.ps1` | Admin (PowerShell) | Unregister distro, remove WT profile + shortcut, optionally remove template |
| `root-bootstrap.sh` | root (inside WSL) | User creation, wsl.conf, fstab mount, mom, PATH, welcome screen |
| `maude-bootstrap.sh` | maude user (inside WSL) | dev-station, Bun, kanna-code, skills, Claude Code config |
| `maude` | maude user (inside WSL) | CLI launcher: creates projects, inits git, launches Claude Code |
