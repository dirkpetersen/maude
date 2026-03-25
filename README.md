```
███╗   ███╗ █████╗ ██╗   ██╗██████╗ ███████╗
████╗ ████║██╔══██╗██║   ██║██╔══██╗██╔════╝
██╔████╔██║███████║██║   ██║██║  ██║█████╗
██║╚██╔╝██║██╔══██║██║   ██║██║  ██║██╔══╝
██║ ╚═╝ ██║██║  ██║╚██████╔╝██████╔╝███████╗
╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝
```

**Ready-to-run sandbox appliance for agentic coding**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ubuntu 26.04](https://img.shields.io/badge/Ubuntu-26.04%20LTS-E95420?logo=ubuntu&logoColor=white)](https://releases.ubuntu.com/26.04/)
[![Build WSL](https://github.com/dirkpetersen/maude/actions/workflows/build-wsl.yml/badge.svg)](https://github.com/dirkpetersen/maude/actions/workflows/build-wsl.yml)
[![Build Docker](https://github.com/dirkpetersen/maude/actions/workflows/build-docker.yml/badge.svg)](https://github.com/dirkpetersen/maude/actions/workflows/build-docker.yml)
[![Release](https://img.shields.io/github/v/release/dirkpetersen/maude?include_prereleases)](https://github.com/dirkpetersen/maude/releases)
[![Docker Pulls](https://img.shields.io/badge/ghcr.io-dirkpetersen%2Fmaude-blue?logo=docker)](https://github.com/dirkpetersen/maude/pkgs/container/maude)

---

maude is a pre-configured Ubuntu 26.04 appliance that gives developers a browser-based terminal, rapid web-app deployment, and an opinionated Claude Code environment — all in a single image you can run on WSL, VMware, KVM/Proxmox, or Docker.

## Features

- **Browser terminal** via [web-term](https://github.com/dirkpetersen/web-term) — full Linux shell in your browser using xterm.js + tmux (session persistence across disconnects)
- **One-command app deployment** via [appmotel](https://github.com/dirkpetersen/appmotel) — deploy Python, Node.js, or Go apps from a Git URL; Traefik handles routing and TLS
- **Non-root package management** via [mom](https://github.com/dirkpetersen/mom) — a setuid helper that lets users install packages without full sudo
- **Claude Code ready** — first-login wizard prompts to install Claude Code + the appmotel skill is pre-configured
- **Multi-user** — JIT Linux user provisioning on first login; each user gets their own isolated home
- **WSL-safe** — when running under WSL, Traefik binds to `127.0.0.1` only (your laptop won't accidentally become a hosting platform)
- **Multiple deployment targets** — WSL2, VMware, KVM/QEMU, Proxmox, Docker

---

## Quick Start

### WSL (Windows Subsystem for Linux)

```powershell
# Download the latest release
$url = "https://github.com/dirkpetersen/maude/releases/latest/download/maude-wsl-ubuntu2604-latest.tar.gz"
Invoke-WebRequest $url -OutFile maude-wsl.tar.gz

# Import and start
wsl --import maude C:\maude .\maude-wsl.tar.gz
wsl -d maude
```

On first boot, maude will install appmotel and deploy web-term automatically. Open your browser at [http://localhost:3000](http://localhost:3000).

### Docker

```bash
docker run -d \
  -p 3000:3000 \
  -p 2222:22 \
  -p 8080:8080 \
  --name maude \
  ghcr.io/dirkpetersen/maude:latest

# Open browser terminal
open http://localhost:3000
```

### VMware / KVM

Download the VM image from [Releases](https://github.com/dirkpetersen/maude/releases):

| File | Format | Use with |
|------|--------|----------|
| `maude-ubuntu2604-<version>.ova` | OVA | VMware Workstation/Fusion/ESXi |
| `maude-ubuntu2604-<version>.qcow2` | QCOW2 | KVM, Proxmox, QEMU |

```bash
# KVM quick start
qemu-system-x86_64 \
  -enable-kvm \
  -m 4G \
  -smp 2 \
  -drive file=maude-ubuntu2604-latest.qcow2,format=qcow2 \
  -net nic -net user,hostfwd=tcp::3000-:3000,hostfwd=tcp::2222-:22 \
  -daemonize
```

After boot, run `sudo maude-setup` to configure your domain name and enable HTTPS.

---

## First Boot Experience

```
┌──────────────────────────────────────────────────────┐
│  System boots →  maude-first-boot.service            │
│    ├── Detects WSL or VM                             │
│    ├── Installs appmotel + Traefik                   │
│    ├── Deploys web-term as an app                    │
│    └── Configures firewall (VM only)                 │
│                                                      │
│  User logs in → browser terminal at :3000           │
│    ├── ~/bin and ~/.local/bin added to PATH          │
│    └── "Install Claude Code? [Y/n]"                  │
└──────────────────────────────────────────────────────┘
```

---

## Architecture

```
User browser
     │  HTTP (or HTTPS after maude-setup)
     ▼
 Traefik  (:80 / :443)
 [WSL: 127.0.0.1 only]
     │
     ├─── web-term.yourdomain.com ──► web-term (Node.js :3000)
     │                                    │ SSH
     │                                    ▼
     │                               localhost:22 (sshd)
     │                                    │
     │                               user's shell session (tmux)
     │
     └─── myapp.yourdomain.com ────► user app (Python/Node/Go)
                                     managed by appmotel systemd service

mom (setuid) ──► apt-get / dnf       [group-gated, audited]
```

**Users:** Each authenticated user gets a separate Linux account. Users share the `appmotel` service user for app deployments but have isolated home directories.

---

## Configuration

Run `sudo maude-setup` after first boot to configure:

| Setting | Description | Default |
|---------|-------------|---------|
| `MAUDE_HOSTNAME` | System hostname | `maude` |
| `MAUDE_BASE_DOMAIN` | Base domain for app routing | Auto-detected IP |
| `MAUDE_TLS_EMAIL` | Let's Encrypt email (optional) | — |
| `MAUDE_DEPLOY_TARGET` | `vm`, `wsl`, or `docker` | Auto-detected |

Config file: `/etc/maude/maude.conf`

### WSL-specific notes

- Traefik listens on `127.0.0.1:80` and `127.0.0.1:8080`
- Apps are accessible at `http://localhost` (port 80) or `http://localhost:8080`
- No TLS setup required for local development

---

## Deploying Apps (via appmotel)

Once logged in, deploy any Python/Node.js/Go app from a Git repo:

```bash
# As the apps user (or via Claude Code with the appmotel skill)
sudo -u appmotel appmo add myapp https://github.com/you/myapp main

# Check status
sudo -u appmotel appmo status

# View logs
sudo -u appmotel appmo logs myapp 50

# Update from git
sudo -u appmotel appmo update myapp
```

appmotel auto-detects the language, builds the app, creates a systemd service, and configures Traefik routing. Apps are available at `http://myapp.<your-domain>`.

See the [appmotel skill](https://github.com/dirkpetersen/appmotel/blob/main/.claude/skills/appmotel/SKILL.md) for Claude Code usage.

---

## Package Management (mom)

Users in the `mom` group can install packages without full sudo:

```bash
mom install ripgrep bat
mom update curl
mom refresh          # apt-get update / dnf makecache
```

All operations are logged to `/var/log/mom.log` (JSON) and syslog.

---

## Building from Source

### Prerequisites

- Linux with Docker (for Docker builds)
- `debootstrap` (for WSL builds)
- [Packer](https://developer.hashicorp.com/packer) >= 1.10 (for VM builds)
- KVM/QEMU (for KVM builds) or VMware Workstation/Fusion (for OVA)

```bash
# Run tests
make test

# Build Docker image
make build-docker

# Build WSL tarball (Linux only, requires debootstrap)
make build-wsl

# Build VM images (requires Packer + KVM)
make build-vm-kvm UBUNTU_ISO_URL=https://cdimage.ubuntu.com/...

# Create a release
make tag VERSION=v0.2.0
```

---

## Security

See [SECURITY.md](SECURITY.md) for the full security analysis, threat model, and hardening checklist.

Key points:
- HTTP-only by default; enable HTTPS with `sudo maude-setup` (Let's Encrypt via Traefik)
- WSL deployments are `127.0.0.1`-only — no external exposure
- `PermitRootLogin no` enforced; fail2ban active on VM deployments
- `mom` uses a setuid binary with strict environment sanitization and a deny list
- Report vulnerabilities privately via [GitHub Security Advisories](https://github.com/dirkpetersen/maude/security/advisories/new)

---

## Integrated Projects

| Project | Role | Repo |
|---------|------|------|
| web-term | Browser terminal (xterm.js + SSH + tmux) | [dirkpetersen/web-term](https://github.com/dirkpetersen/web-term) |
| appmotel | PaaS: Git→systemd→Traefik deployment | [dirkpetersen/appmotel](https://github.com/dirkpetersen/appmotel) |
| mom | Non-root package management (setuid Rust) | [dirkpetersen/mom](https://github.com/dirkpetersen/mom) |

---

## Contributing

1. Fork and clone the repo
2. Run `make test` to verify your environment
3. Make changes, add tests in `tests/`
4. Open a PR — CI runs syntax checks and the test suite automatically

---

## License

[MIT](LICENSE) © 2026 Dirk Petersen
