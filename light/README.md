# Maude Light

A secure WSL2 sandbox for agentic AI coding. By default, WSL instances mount the entire Windows file system, giving both the user and any AI agent unrestricted access to your documents, files, and everything else on disk. Maude changes that.

Maude creates a single `Maude` subfolder inside `%LOCALAPPDATA%\Maude\Data\Maude` and shares **only** that empty directory with a standard Ubuntu WSL instance. It removes generic `sudo` access (unlike the default Ubuntu configuration) so the user and the AI agent can only run tools that are already installed. New packages can be added through the [`mom`](https://github.com/dirkpetersen/mom) package manager, which supports install, update, and repo refresh — but cannot add arbitrary repositories or run unvetted code. The user decides which files to expose to the AI agent by copying them into the `Maude` folder.

Beyond security, Maude addresses **manageability**: IT departments are often concerned about another OS to manage. Maude is narrow in scope and stores all relevant settings in the `Maude` folder under `%LOCALAPPDATA%`, so the sandbox can be torn down and reinstalled at any time without losing configuration or project data.

Use a TUI
<img width="1330" height="1075" alt="image" src="https://github.com/user-attachments/assets/5c97eecf-d4c7-48af-a2e3-6f3de060f743" />


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

The hard prerequisite for Maude is WSL2 itself. Enabling the WSL Windows feature is the only step that genuinely needs admin — everything else (downloading Ubuntu, building the template, importing the Maude distro, setting up the shared folder, configuring Windows Terminal) is per-user state and runs without elevation.

### If WSL2 is already installed (most modern Windows 10/11)

Open a **non-elevated** PowerShell and run:

```powershell
curl.exe -sLo $env:TEMP\setup-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\setup-wsl-maude.ps1
```

The first run takes ~3–5 minutes (downloading Ubuntu and baking in packages — the template build). Future reinstalls reuse the template and complete in ~30 seconds.

> **Tip:** to check if WSL is installed, run `wsl --status` in any PowerShell. If you see version info, you're good. If you see "Windows Subsystem for Linux has no installed distributions" or no command, you need the admin step below.

### If WSL2 is NOT installed yet (rare, admin once)

Open **PowerShell as Administrator** (right-click → "Run as Administrator") and run the admin phase:

```powershell
curl.exe -sLo $env:TEMP\setup-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\setup-wsl-maude.ps1 -Admin
```

This installs WSL2, installs Windows Terminal, builds the Ubuntu template, and adds a Windows Defender exclusion for the Maude install path. When it finishes, **close the elevated window** and run the non-elevated user-phase command above to finish setup.

### Reinstall

Just the non-elevated user-phase command — no admin, no UAC.

> **Note:** Use `curl.exe` (not `curl`) — in PowerShell, `curl` is an alias for `Invoke-WebRequest`. Piping via `iex` may be blocked by antivirus on corporate machines; the file-based approach above works reliably everywhere.

### Install options

| Flag | Phase | Effect |
|------|-------|--------|
| *(default)* | user | `%LOCALAPPDATA%\Maude\Data\Maude` |
| `-Noble` | both | Use Ubuntu 24.04 instead of the default 26.04 |
| `-Admin` | admin | First-time install on a machine where WSL2 isn't yet installed. Required only for the WSL Windows-feature install + Windows Terminal install + Defender exclusion. Once those are present, all subsequent installs run unelevated. |
| `-NoDefenderExclusion` | admin | Don't add the Windows Defender exclusion (use only if your security policy forbids it; user-phase template builds may then hit AV locks) |
| `-Release <tag>` | both | Pin to a tagged release and verify SHA-256 of every downloaded file against `light/checksums.txt` from that tag (see below) |

### Refreshing the template (e.g., new Ubuntu version)

To upgrade from Ubuntu 24.04 to 26.04, or to pick up upstream package changes baked into the template:

```powershell
# 1. Tear down (with -IncludeTemplate to remove the existing template)
... teardown-wsl-maude.ps1 -IncludeTemplate

# 2. User phase rebuilds the template AND imports it as Maude (no admin)
... setup-wsl-maude.ps1 -Noble       # (or omit -Noble for 26.04)
```

### Verified install (recommended for production)

`-Release main` (the default) downloads the latest scripts from `main` without checksum verification. For production, pin to a tagged release:

```powershell
# Add -Admin only if WSL2 itself isn't installed yet:
... setup-wsl-maude.ps1 -Admin -Release v0.4.0

# Otherwise just the user phase:
... setup-wsl-maude.ps1 -Release v0.4.0
```

When `-Release` is anything other than `main`, the script downloads `light/checksums.txt` from that tag and verifies every file's SHA-256 before using it. A mismatch aborts the install.

### What each phase does

**Admin phase** (only when WSL2 isn't yet installed):
1. Install WSL2 and the VM Platform Windows feature
2. Install Windows Terminal (if missing)
3. Build the Ubuntu template: download Ubuntu, bake in all packages from `packages/ubuntu-packages.yaml`
4. Add a permanent Windows Defender exclusion for the Maude install path

**User phase** (default; every install/reinstall, no admin):
1. If the Ubuntu template doesn't exist yet, build it (Store install or Canonical download — per-user, no admin)
2. `wsl --export` the template, `wsl --import` it as `Maude`
3. Run root-level setup (user creation, sandbox isolation, mom, PATH, welcome screen)
4. Create the `Maude` shared folder and pin it to Quick Access
5. Run user-level setup (dev-station, Bun, kanna-code, Claude Code skills, maude launcher)
6. Configure the Windows Terminal profile and desktop shortcut

### Disk space

The installer checks free space on C: at startup and prints it in gigabytes:

- **>= 10 GB free** — normal install; the Ubuntu template is kept for fast future reinstalls
- **5--10 GB free** — install proceeds but the template is automatically removed afterward to reclaim space (reinstalls will be slower)
- **< 5 GB free** — warning that Maude may not function properly; you get 10 seconds to press Ctrl+C to cancel before the install continues

## Uninstall

The default teardown runs as your normal user — **no admin/UAC needed**. From any PowerShell:

```powershell
curl.exe -sLo $env:TEMP\teardown-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/teardown-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\teardown-wsl-maude.ps1
```

This removes the Maude distro, Windows Terminal profile, desktop shortcut, and Quick Access pin. The Ubuntu template is kept for fast reinstalls. To remove the template too:

```powershell
... teardown-wsl-maude.ps1 -IncludeTemplate
```

For full machine cleanup (also removes the Windows Defender exclusion the admin phase added — requires elevation):

```powershell
... teardown-wsl-maude.ps1 -Admin -IncludeTemplate
```

## Usage

After install, open the **Maude** profile in Windows Terminal (or the desktop shortcut). The TUI launches automatically.

```
  maude tui            Interactive project launcher (auto-launched on login)
  maude project-name   Create or open a coding project
  maude github         Wizard for GitHub identity, SSH/GPG keys, signing
  maude list           Show your projects
  maude delete name    Delete a project (moves to .deleted/)
  maude help           Full usage info

  mom install <pkg>    Install system packages (no sudo needed)
```

Type `menu` at any shell prompt to reopen the TUI. To opt out of TUI auto-launch, uncheck the *Start TUI with Maude* checkbox in the sidebar.

### Interactive TUI (`maude tui`)

The TUI is a full-screen Textual interface that replaces the command-line workflow for most users.

**Sidebar (left panel)**

| Control | Description |
|---------|-------------|
| *Start TUI with Maude* checkbox | TUI auto-launch (default ON). Unchecking creates `~/.maude-tui-disabled` so future logins drop straight to the shell with the text banner. |
| *Claude model* radio buttons | Choose which Claude model opens for your projects: `fable` (default), `opus`, `sonnet`, or `haiku`. The selection is saved to `~/.maude-model` and persists across sessions. |

**Project list (right panel)**

Projects are sorted newest-first by last-modified time. Press **Enter** or click **Open Project** to launch Claude Code for the selected project; the cursor returns to that project when you come back.

**Bottom bar**

| Button | Description |
|--------|-------------|
| Open Project | Launch Claude Code for the selected project |
| + New | Create a new project (spaces become hyphens; git is initialized automatically) |
| Web UI / Stop Web UI | Start or forcefully stop the [kanna](https://github.com/jakemor/kanna) web interface at `http://localhost:3210`. The kanna process inherits Maude's auth via `CLAUDE_EXECUTABLE=~/bin/claude`. |
| Setup Git(hub) | 4-step wizard for GitHub identity, SSH key, GPG key + commit signing, and final git/keychain config. Also runnable as `maude github`. |
| Set Credentials | Open a modal to paste Anthropic / Foundry / AWS / Azure `export` lines; saved to `~/.azure/clauderc` (mode 0600). |
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

`~/Maude` is mounted from `%LOCALAPPDATA%\Maude\Data\Maude` on your Windows host. Use it to exchange files between Windows and the sandbox — documents, exports, data files, anything you need Claude to read or produce. The folder is pinned to Quick Access in File Explorer for easy access.

## Troubleshooting

### `maude` command prints `value too great for base (error token is "09")`

Symptom (somewhere on a line near `last_s` arithmetic):

```
/home/maude/.local/bin/maude: line 147: 2026-05-09: value too great for base (error token is "09")
Opening project: tui
```

Cause: an old version of the `maude` bash CLI is on disk with a newer `~/.maude-tui-last-update` stamp file. The old CLI used the stamp as a Unix-epoch integer; the newer TUI writes it as `YYYY-MM-DD`, and bash interprets `05` / `09` as octal, which `09` isn't.

Fix — refresh the CLI directly (the running version can't `maude update` past its own arithmetic error):

```bash
curl -fsSL "https://raw.githubusercontent.com/dirkpetersen/maude/main/light/maude?cache=$(date +%s)" \
  -o ~/.local/bin/maude && chmod +x ~/.local/bin/maude
rm -f ~/.maude-tui-last-update
```

Then `maude update` to bring everything else current.

### Bash login fails with `syntax error near unexpected token 'fi'`

Symptom on every new shell:

```
-bash: /home/maude/.bashrc: line 189: syntax error near unexpected token `fi'
```

Cause: an earlier version of `maude github` step 4 left an orphan `fi` in `~/.bashrc` when stripping the previous keychain block (it stopped at the first `fi` of a nested `if`/`fi` pair).

Fix:

```bash
maude update
```

The current `maude update` runs `python3 ~/.local/bin/maude.py --fix-bashrc`, which scrubs the orphan `fi` and re-emits the sentinel-bracketed keychain block. Idempotent — it does nothing if your `~/.bashrc` is already clean. Open a new terminal afterwards.

### TUI won't auto-launch on login

The TUI auto-launches by default; if it isn't, check whether the opt-out flag exists:

```bash
ls -la ~/.maude-tui-disabled
```

Delete it (or untick the **Start TUI with Maude** sidebar checkbox) to re-enable auto-launch. Note: the legacy `~/.maude-tui-autostart` flag is no longer consulted — only `~/.maude-tui-disabled` matters now.

### Web UI button doesn't start kanna

Click the **Web UI** button again to confirm it's idle. The TUI runs a 1.5s post-launch check and surfaces the kanna log tail in a notification if it died — most causes:

- No LLM credentials configured. Click **Set Creds** and paste `export ANTHROPIC_API_KEY=…` (or AWS / Foundry / Azure equivalents).
- Port 3210 still held by a previous run. The TUI runs `fuser -k 3210/tcp` before each launch as a backstop, but a wedged kanna from outside the TUI may need `fuser -k 3210/tcp` manually.
- Wrapper at `~/bin/claude` missing. The TUI sets `CLAUDE_EXECUTABLE` to that path so kanna inherits Maude's auth; if the wrapper is gone, kanna falls back to plain `claude` on PATH and may fail to authenticate.

### `gh` upload of GPG key fails with `insufficient OAuth scopes`

`gh auth login` needs the `write:gpg_key` scope. The wizard now requests it automatically; if you authed with an earlier version, click **Re-authenticate** in step 3 and complete the device flow again.

## How it works

```
Windows host
  │
  ├── %LOCALAPPDATA%\Maude\
  │     ├── OS\                                  ← WSL Maude distro (ext4.vhdx)
  │     ├── Template\                            ← Ubuntu template distro
  │     └── Data\
  │           └── Maude\                         ← shared folder (default, has icon)
  │                 ├── Projects/                ← coding projects (directly used by WSL)
  │                 ├── .claude/                 ← Claude Code config (symlinked from WSL)
  │                 └── .kanna/                  ← kanna web UI data (symlinked from WSL)
  │
  └── WSL2: Maude (Ubuntu 26.04, or 24.04 with -Noble)
        ├── ~/Maude/           ← drvfs mount of the shared folder
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

**Why this layout?** `%LOCALAPPDATA%\Maude\Data\Maude` lives under medium-integrity-level `%LOCALAPPDATA%`, where Explorer reliably honours the `desktop.ini` custom-icon mechanism. The earlier `AppData\LocalLow` location was a Low-Integrity-Level folder that quietly rejected the `+S`/`+R` attribute change needed for icons. Teardown only removes `OS\` and `Template\` — the `Data\` subtree is preserved across teardown/reinstall.

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
| `%LOCALAPPDATA%` folder detection | `~/Documents` or iCloud Drive detection |

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
