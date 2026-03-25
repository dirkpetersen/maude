# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**maude** — A ready-to-run sandbox appliance for agentic coding, deployable as a VM (VMware/KVM), WSL image, Proxmox appliance, or Docker container. It integrates three upstream projects to create a multi-user Ubuntu 26.04 environment where users get browser-based terminal access and can rapidly deploy web apps.

**Upstream repos** (checked out at `~/gh/`):
- `~/gh/web-term` — Node.js browser terminal via SSH+tmux (xterm.js frontend)
- `~/gh/appmotel` — Bash-based PaaS: deploys Python/Node/Go apps as systemd services behind Traefik
- `~/gh/mom` — Rust setuid tool allowing non-root users to install packages via apt/dnf

## Architecture

```
User browser
    │
    ▼
Traefik (reverse proxy, HTTP only for now)
    │
    ├── web-term  (Node.js app, deployed by appmotel, provides browser terminal)
    └── user apps (deployed via appmotel by users or Claude Code)

Each user → separate Linux account (JIT-created on first login)
appmotel  → runs as its own system user, manages Traefik + app systemd services
mom       → setuid binary, lets non-root users install packages
```

## Deployment Targets & Priority

1. **VM (VMware + KVM)** — highest priority; built with Packer, outputs `.ova` and `.qcow2`
2. **WSL image** — built via GitHub Actions
3. **Proxmox appliance** — lower priority
4. **Docker** — lowest priority, likely easiest

## Key Design Constraints

- **WSL detection**: `[ -f /proc/sys/fs/binfmt_misc/WSLInterop ]` — when true, Traefik binds to `127.0.0.1` only (ports 80 and 8080), preventing laptops from acting as hosting platforms
- **No TLS initially**: HTTP only until `maude setup` configures a domain (ACME handled by appmotel/Traefik)
- **No Azure AD yet**: Authentication is deferred; web-term uses SSH password auth for now
- **No containers**: appmotel deploys Python, Node.js, and Go apps only (Rust support deferred)
- MIT licensed

## Repository Structure (being built)

```
maude/
├── packer/                  # Packer templates for VM/WSL image builds
├── packages/
│   ├── ubuntu-packages.yaml # Package list installed by apt during image build
│   └── rhel-packages.yaml   # Package list installed by dnf during image build
├── scripts/
│   ├── first-boot.sh        # Runs once on first boot: sets up appmotel, deploys web-term
│   ├── maude-setup          # CLI wizard: domain, hostname, deployment-type detection
│   └── profile.d/           # Shell profile snippets dropped into /etc/profile.d/
└── .github/workflows/       # GitHub Actions: builds WSL tarball and Docker image
```

## Image Build Strategy

- **Base**: Ubuntu 26.04 (starting with beta; GA ~April 2026)
- **Builder**: Packer for VM targets; GitHub Actions for WSL/Docker
- **Package installation during build**: `apt` directly (running as root in Packer), not mom
- **mom**: installed in the image for post-boot user package management
- **appmotel + web-term**: pulled from their GitHub releases/repos during image build; no submodules
- **Pre-built images**: published as GitHub Releases via GitHub Actions

## First Boot & User Experience

1. `first-boot.sh` runs via systemd oneshot service:
   - Installs appmotel (pulls `install.sh` from its repo)
   - Deploys web-term as an appmotel app
   - Runs WSL detection; patches Traefik config to `127.0.0.1` if on WSL
2. User logs in → `maude setup` wizard prompts for domain/hostname (skippable, defaults to IP)
3. On every new user's first login:
   - Linux account auto-created (JIT via PAM or adduser in profile script)
   - `~/bin` and `~/.local/bin` created, `~/bin` prepended to PATH (via `/etc/profile.d/maude-path.sh`)
   - Prompted once to install Claude Code: `curl -fsSL https://claude.ai/install.sh | bash -s latest`

## Default User

Image ships with a `maude` user (regular Linux user, not admin). Additional users created with standard `adduser`. The `apps` operator user (appmotel tier-1) is also present per appmotel's permission model.

## PATH Convention

All users must have `~/bin` at the **front** of PATH and `~/.local/bin` also present. Enforced via `/etc/profile.d/maude-path.sh`:

```bash
mkdir -p "$HOME/bin" "$HOME/.local/bin"
export PATH="$HOME/bin:$PATH:$HOME/.local/bin"
```

## appmotel Permission Model

Three-tier sudo model (see `~/gh/appmotel/.claude/skills/appmotel/SKILL.md`):
- `apps` user → `sudo -u appmotel` → manages all apps
- `appmotel` user → `sudo systemctl` → manages only Traefik system service

Always prefix appmotel CLI calls with `sudo -u appmotel appmo ...`

## Missing Features in Upstream Repos (to be implemented by separate agents)

- **appmotel**: WSL detection to bind Traefik to `127.0.0.1` — needs adding to `install.sh`
- **appmotel**: Azure AD authentication (deferred)
- **appmotel**: Rust app support (deferred)
- **web-term**: Azure AD authentication (deferred)

## Claude Code Skill

The appmotel Claude Code skill lives at `~/gh/appmotel/.claude/skills/appmotel/SKILL.md`. It covers deploying and managing apps via `appmo` CLI. Users can invoke it via the appmotel skill in their Claude Code session.
