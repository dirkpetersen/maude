# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**maude** -- A ready-to-run sandbox appliance for agentic coding, deployable as a VM (VMware/KVM), WSL image, Proxmox appliance, or Docker container. It integrates three upstream projects to create a multi-user Ubuntu environment where users get browser-based terminal access and can rapidly deploy web apps.

**Maude Light** (`light/`) is the current production implementation -- a lightweight WSL2 sandbox that deploys Ubuntu 24.04 as an isolated distro with a single shared folder to the Windows host. Install with one PowerShell command; no VM or Docker needed.

**Upstream repos** (checked out at `~/gh/`):
- `~/gh/web-term` -- Node.js browser terminal via SSH+tmux (xterm.js frontend)
- `~/gh/appmotel` -- Bash-based PaaS: deploys Python/Node/Go apps as systemd services behind Traefik
- `~/gh/mom` -- Rust setuid tool allowing non-root users to install packages via apt/dnf

## Architecture

### Full Appliance (VM/Docker/WSL image)

```
User browser
    |
    v
Traefik (reverse proxy, HTTP only until maude setup configures a domain)
    |
    +-- web-term  (Node.js, deployed by appmotel, browser terminal)
    +-- user apps (deployed via appmotel)

Each user -> separate Linux account (JIT-created via maude-adduser)
appmotel  -> manages Traefik + app systemd services
mom       -> setuid binary, lets non-root users install packages
```

### Maude Light (WSL2 sandbox)

```
Windows host
    |
    +-- C:\Users\<user>\OneDrive\Documents\Maude\   <- shared folder (host side)
    |       +-- Projects/       <- coding projects (directly used by WSL)
    |       +-- .claude/        <- Claude Code config (symlinked from WSL)
    |       +-- .kanna/         <- kanna web UI data (symlinked from WSL)
    |
    +-- WSL2: Maude (Ubuntu 24.04)
          +-- ~/Maude/          <- drvfs mount of shared folder
          +-- ~/.claude         -> symlink to ~/Maude/.claude
          +-- ~/.kanna          -> symlink to ~/Maude/.kanna
          +-- ~/bin/            <- user scripts (front of PATH)
          +-- ~/.local/bin/     <- tool binaries (end of PATH)

Automount: disabled (no /mnt/c, /mnt/d)
Only mount: ~/Maude via /etc/fstab drvfs entry
```

The setup script (`setup-wsl-maude.ps1`) bakes packages into a reusable `Ubuntu-24.04-Template` WSL distro, then imports it as `Maude`. Teardown + reinstall takes under a minute because the template is kept.

## Commands

### Testing & Linting
```bash
make test           # Run full test suite (all tests/test-*.sh files)
make test-fast      # Run tests, stop on first failure
make lint           # Bash -n syntax check only (no execution)
bash tests/test-path-setup.sh   # Run a single test file
```

### Writing Tests

Tests live in `tests/test-*.sh`. Source `tests/lib.sh` for shared helpers:

```bash
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
suite_header "My feature"
assert_eq      "description" "expected" "$actual"
assert_contains "description" "needle" "$haystack"
assert_file_exists "description" "/path/to/file"
assert_executable  "description" "/path/to/bin"
assert_exit_zero    "description" some_command --args
assert_exit_nonzero "description" some_command --args
skip "reason"
suite_summary  # prints results; exits non-zero if any FAIL
```

**Gotcha**: use `PASS=$((PASS+1))` not `((PASS++))` — the latter exits non-zero when the value is 0 under `set -e`.

### Building

```bash
# Docker
make build-docker                          # Build Docker image locally
make run-docker                            # Run with ports :3000, :2222, :8080
make push-docker                           # Push to GHCR

# WSL (builds via Docker -- works on Linux/WSL/Mac, same as CI)
make build-wsl                             # Build WSL rootfs tarball
make wsl-import WSL_DISTRO=maude-dev WSL_DIR=C:\maude-dev  # Register in WSL
make wsl-test                              # Smoke tests (PATH, hostname, mom, users group)
make wsl-update-scripts                    # Hot-patch scripts into running WSL distro (seconds)

# VM (requires Packer + KVM or VMware)
make build-vm-kvm UBUNTU_ISO_URL=...       # Outputs .qcow2
make build-vm-vmware UBUNTU_ISO_URL=...    # Outputs .ova
```

### Releasing
```bash
make tag VERSION=v0.2.0    # Creates + pushes git tag -> triggers CI release workflow
```

## Development Workflow Tiers

- **Tier 1 -- Unit tests (instant)**: `make test` / `make lint`
- **Tier 2 -- Hot script update (seconds)**: `make wsl-update-scripts` then `make wsl-test`
- **Tier 3 -- Full local build (~3 min)**: `make build-wsl && make wsl-import && make wsl-test`
- **Tier 4 -- Official release (CI only)**: `make tag VERSION=vX.Y.Z`

## Deployment Targets & Priority

1. **Maude Light (WSL2)** -- production-ready; single PowerShell command install from GitHub
2. **VM (VMware + KVM)** -- built with Packer, outputs `.ova` and `.qcow2`
3. **WSL image** -- full appliance variant, built via GitHub Actions
4. **Docker** -- `ghcr.io/dirkpetersen/maude:latest`
5. **Proxmox appliance** -- lower priority

## Key Design Constraints

- **WSL detection**: `[ -f /proc/sys/fs/binfmt_misc/WSLInterop ]` -- when true, Traefik binds to `127.0.0.1` only
- **No TLS initially**: HTTP only until `maude setup` configures a domain (ACME handled by appmotel/Traefik)
- **No Azure AD yet**: Authentication is deferred; web-term uses SSH password auth
- **No containers**: appmotel deploys Python, Node.js, and Go apps only (Rust support deferred)
- **PowerShell install uses `curl.exe`** (not `curl`, which aliases to `Invoke-WebRequest`); `iex` may be blocked by antivirus on corporate machines
- MIT licensed

## Maude Light -- Script Pipeline

The `light/` directory contains the WSL2 sandbox implementation:

| Script | Runs as | Purpose |
|--------|---------|---------|
| `setup-wsl-maude.ps1` | Admin (PowerShell) | 7-step orchestrator: WSL, WT, host folder, template, import, bootstrap |
| `teardown-wsl-maude.ps1` | PowerShell (self-elevates) | Unregister distro, remove WT profile + shortcut, optionally remove template |
| `root-bootstrap.sh` | root (inside WSL) | User creation, wsl.conf, fstab mount, mom, PATH, welcome screen, Claude Code |
| `maude-bootstrap.sh` | maude user (inside WSL) | dev-station, Bun, kanna-code, skills, Claude Code config |
| `maude` | maude user (inside WSL) | CLI launcher: creates projects, inits git, launches Claude Code (`maude <name>`, `maude list`, `maude delete`, `maude web`, `maude tui`) |
| `maude.py` | maude user (inside WSL) | Textual TUI — always launched via `maude tui`, downloaded fresh from GitHub daily (stamp: `~/.maude-tui-last-update`) |

Key implementation details:
- Files are piped into WSL via `Get-Content -Raw | wsl ... bash -c "cat > /tmp/..."` (automount is disabled, so wslpath/cp don't work)
- `/tmp` is cleared on `wsl --terminate` -- files must be re-piped after restart (step 6 re-pipes maude-bootstrap.sh)
- Windows Terminal auto-generates profiles with `source=Microsoft.WSL` -- can't delete them, must hide with pre-hidden stubs
- WT profile cleanup runs BEFORE self-elevation (elevated process has different `$env:LOCALAPPDATA`)
- `~/.claude` is symlinked to `~/Maude/.claude` so settings persist on the host mount
- **Security model**: automount disabled + no generic sudo = AI agent can only access the shared `Maude` folder and installed tools; Claude Code runs in yolo mode (safe inside sandbox)
- The `maude` CLI auto-updates Claude Code weekly (stamp file: `~/.claude/.last-update-check`)
- **Sandbox instructions split**: `~/.claude/MAUDE.md` is always overwritten on each `maude` launch (sandbox rules, not user-editable); `~/.claude/CLAUDE.md` is created once if missing and user-owned. `CLAUDE.md` includes sandbox rules via `@MAUDE.md`
- `maude delete <name>` is a soft-delete — moves to `Projects/.deleted/`, not permanent
- The `maude` CLI launches Claude Code with `claude opus-1m` (uses the latest Opus model with extended context)

## Maude TUI (`maude tui`)

A Textual-based full-screen TUI alternative to the CLI welcome screen. Always launched via `maude tui` (never run `maude.py` directly).

### Layout

```
┌─ Maude ─────────────────────────────────────────────┐
│  ASCII logo (green, top-left)    [x] Start TUI      │
│                                      with Maude     │
│  Projects                            [+ New]        │
│ ┌──────────────────────────────────────────────┐    │
│ │ banana          Modified: Apr 10, 2026  [del]│    │
│ │ my-app          Modified: Apr 9, 2026   [del]│    │
│ └──────────────────────────────────────────────┘    │
│  [Open Project]  [Web UI]  [Command Line]           │
└─────────────────────────────────────────────────────┘
```

### Key behaviors
- **Open project**: `app.suspend()` → `claude --continue` (fallback: `claude`) → resume TUI
- **Delete**: confirm modal → soft-delete to `Projects/.deleted/`
- **New project**: modal dialog, spaces auto-replaced with hyphens, git init
- **Web UI**: launches kanna in background
- **Command Line**: exits TUI, returns to shell prompt
- **"Start TUI with Maude" checkbox**: toggles `~/.maude-tui-autostart` flag file; `maude-welcome.sh` checks this flag and `exec maude tui` instead of showing the text banner
- **Daily update**: `maude tui` checks `~/.maude-tui-last-update` stamp; downloads fresh `maude.py` from GitHub if older than 24 h
- **Colors**: green logo, cyan highlights, yellow accents (matches welcome screen palette)
- **Dependencies**: `textual` Python package — installed via `pip install textual` in `maude-bootstrap.sh`

## PATH Convention

All users must have `~/bin` at the **front** of PATH and `~/.local/bin` also present. The `maude-path.sh` profile script runs at the **end** of `~/.bashrc` so it executes after tool installers (e.g., Claude Code) that prepend their own paths -- then re-enforces `~/bin` first and deduplicates `~/.local/bin`.

## Image Build Strategy (Full Appliance)

- **Base**: Ubuntu 26.04 (full appliance) / Ubuntu 24.04 (Maude Light)
- **Package list**: `packages/ubuntu-packages.yaml` defines all packages baked into the image (also `packages/rhel-packages.yaml` for RPM targets)
- **Package installation during build**: `apt` directly (running as root), not mom
- **mom**: installed in the image for post-boot user package management
- **appmotel + web-term**: pulled from their GitHub releases/repos during image build; no submodules

## First Boot & User Experience (Full Appliance)

1. `first-boot.sh` runs via systemd oneshot (sentinel: `/etc/maude/.first-boot-done`):
   - Installs appmotel, deploys web-term, runs WSL detection, configures firewall
2. User logs in -> `maude setup` wizard prompts for domain/hostname
3. New users: Linux account JIT-created by `maude-adduser` (username regex: `^[a-z][a-z0-9_-]{0,31}$`; system names blocked)

## appmotel Permission Model

Three-tier sudo model:
- `apps` user -> `sudo -u appmotel` -> manages all apps
- `appmotel` user -> `sudo systemctl` -> manages only Traefik system service

Always prefix appmotel CLI calls with `sudo -u appmotel appmo ...`

## Key Runtime Config Files

- `/etc/maude/maude.conf` -- `MAUDE_HOSTNAME`, `MAUDE_BASE_DOMAIN`, `MAUDE_TLS_EMAIL`, `MAUDE_DEPLOY_TARGET`, `MAUDE_VERSION`
- `/etc/mom/mom.conf` -- setuid group + deny list path
- `/etc/mom/deny.list` -- blocked packages (nmap, tcpdump, wireshark, metasploit, etc.)
- `/etc/wsl.conf` -- WSL-specific boot config (hostname, systemd, interop)

## CI/CD Workflows

- **build-wsl.yml** -- Builds WSL rootfs tarball on push to main
- **build-docker.yml** -- Builds and pushes Docker image to GHCR on push to main
- **release.yml** -- Triggered on version tags (`v*.*.*`); builds WSL + Docker, creates GitHub Release with checksums
