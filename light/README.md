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

Maude has two phases:

- **Admin phase** (`-Admin`) — first-time machine setup. Installs WSL2 and Windows Terminal (if missing) and builds the Ubuntu template with all packages baked in. **Runs once per machine** (or when refreshing to a new Ubuntu version).
- **User phase** (no flag, no admin) — runs on every install **and reinstall**. Imports the Ubuntu template as the `Maude` distro, runs both bootstraps, sets up the shared folder, Windows Terminal profile, and desktop shortcut.

Once the admin phase has run on a machine, **all reinstalls are admin-free** — no UAC prompt, no elevation. Just one user-phase command.

### First-time install (admin phase, ~5 minutes)

Open **PowerShell as Administrator** (right-click → "Run as Administrator") and run:

```powershell
curl.exe -sLo $env:TEMP\setup-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\setup-wsl-maude.ps1 -Admin
```

This installs WSL2, installs Windows Terminal, and builds the Ubuntu template (with all 90+ packages pre-installed). It also adds a Windows Defender exclusion for the Maude install path so future reinstalls aren't blocked by AV scanning. When it finishes, **close the elevated window**.

### Install / Reinstall (user phase, ~30 seconds)

Open a **non-elevated** PowerShell and run:

```powershell
curl.exe -sLo $env:TEMP\setup-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\setup-wsl-maude.ps1
```

This is also the command for every future reinstall — no admin needed.

> **Note:** Use `curl.exe` (not `curl`) — in PowerShell, `curl` is an alias for `Invoke-WebRequest`. Piping via `iex` may be blocked by antivirus on corporate machines; the file-based approach above works reliably everywhere.

### Install options

| Flag | Phase | Effect |
|------|-------|--------|
| *(default)* | user | `AppData\LocalLow\Maude` (new install) or the previous location (reinstall) |
| `-OneDrive` | user | Shared folder in OneDrive (Business > Personal > generic) |
| `-NoOneDrive` | user | Force `AppData\LocalLow\Maude` |
| `-Noble` | both | Use Ubuntu 24.04 instead of the default 26.04 |
| `-NoDefenderExclusion` | admin | Don't add the Windows Defender exclusion (use only if your security policy forbids it; reinstalls may then hit Defender locks and need to be re-run with `-Admin`) |
| `-Release <tag>` | both | Pin to a tagged release and verify SHA-256 of every downloaded file against `light/checksums.txt` from that tag (see below) |

Flags can be combined, e.g. `-OneDrive -Noble`.

> **OneDrive sharing risk:** if you choose `-OneDrive`, the user-phase script will print a warning about prompt-injection risk via OneDrive sharing. If anyone has edit access to the Maude folder (or any ancestor) through OneDrive sharing, they can drop files that the AI will execute as instructions. Verify the folder and its parents are not shared, or use `-NoOneDrive` instead.

### Refreshing the template (e.g., new Ubuntu version)

To upgrade from Ubuntu 24.04 to 26.04, or to pick up upstream package changes baked into the template:

```powershell
# 1. Tear down (with -IncludeTemplate to remove the existing template)
... teardown-wsl-maude.ps1 -IncludeTemplate

# 2. Admin phase rebuilds the template
... setup-wsl-maude.ps1 -Admin -Noble       # (or omit -Noble for 26.04)

# 3. User phase imports as Maude
... setup-wsl-maude.ps1
```

### Verified install (recommended for production)

`-Release main` (the default) downloads the latest scripts from `main` without checksum verification. For production, pin to a tagged release:

```powershell
# Admin phase
... setup-wsl-maude.ps1 -Admin -Release v0.4.0

# User phase
... setup-wsl-maude.ps1 -Release v0.4.0
```

When `-Release` is anything other than `main`, the script downloads `light/checksums.txt` from that tag and verifies every file's SHA-256 before using it. A mismatch aborts the install.

### What each phase does

**Admin phase** (rare):
1. Install WSL2 and the VM Platform Windows feature (if missing)
2. Install Windows Terminal (if missing)
3. Build the Ubuntu template: download Ubuntu, bake in all packages from `packages/ubuntu-packages.yaml`
4. Add a permanent Windows Defender exclusion for the Maude install path

**User phase** (every install/reinstall):
1. `wsl --export` the template, `wsl --import` it as `Maude`
2. Run root-level setup (user creation, sandbox isolation, mom, PATH, welcome screen)
3. Create the `Maude` shared folder and pin it to Quick Access
4. Run user-level setup (dev-station, Bun, kanna-code, Claude Code skills, maude launcher)
5. Configure the Windows Terminal profile and desktop shortcut

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
  maude tui            Interactive project launcher (recommended)
  maude project-name   Create or open a coding project
  maude list           Show your projects
  maude delete name    Delete a project (moves to .deleted/)
  maude help           Full usage info

  mom install <pkg>    Install system packages (no sudo needed)
```

Type `menu` at any shell prompt to reopen the TUI.

### Interactive TUI (`maude tui`)

The TUI is a full-screen Textual interface that replaces the command-line workflow for most users.

**Sidebar (left panel)**

| Control | Description |
|---------|-------------|
| *Start TUI with Maude* checkbox | Toggle TUI autostart — checked means the TUI opens automatically on every new terminal session instead of the text welcome banner. |
| *Claude model* radio buttons | Choose which Claude model opens for your projects: `opus-1m` (default), `opus`, `sonnet-1m`, `sonnet`, or `haiku`. The selection is saved to `~/.maude-model` and persists across sessions. |

**Project list (right panel)**

Projects are sorted newest-first by last-modified time. Press **Enter** or click **Open Project** to launch Claude Code for the selected project; the cursor returns to that project when you come back.

**Bottom bar**

| Button | Description |
|--------|-------------|
| Open Project | Launch Claude Code for the selected project |
| + New | Create a new project (spaces become hyphens; git is initialized automatically) |
| Web UI / Stop Web UI | Start or forcefully stop the [kanna](https://github.com/jakemor/kanna) web interface at `http://localhost:3210`. All LLM auth env vars (Anthropic, Foundry/Azure, AWS Bedrock) are forwarded automatically. |
| Command Line | Exit the TUI and return to the shell prompt |

**Keyboard shortcuts**

| Key | Action |
|-----|--------|
| `Enter` | Open selected project |
| `n` | New project |
| `d` | Delete selected project (soft-delete to `.deleted/`) |
| `q` | Quit to shell |

**Self-update**: `maude tui` downloads a fresh copy of `maude.py` from GitHub once per day, at or after noon local time. The new version takes effect on the next launch.

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
  └── WSL2: Maude (Ubuntu 26.04, or 24.04 with -Noble)
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
| `setup-wsl-maude.ps1` | Two phases (PowerShell) | `-Admin` (elevated): WSL install, distro import, root bootstrap. *(no flag, unelevated)*: host folder, WT profile, shortcut, user bootstrap |
| `teardown-wsl-maude.ps1` | Admin (PowerShell) | Unregister distro, remove WT profile + shortcut, optionally remove template |
| `root-bootstrap.sh` | root (inside WSL) | User creation, wsl.conf, fstab mount, mom, PATH, welcome screen |
| `maude-bootstrap.sh` | maude user (inside WSL) | dev-station, Bun, kanna-code, skills, Claude Code config |
| `maude` | maude user (inside WSL) | CLI launcher: creates projects, inits git, launches Claude Code; warns on suspicious repos |
| `maude.py` | maude user (inside WSL) | Textual TUI: project launcher, model picker, Web UI toggle; self-updates daily at noon |
