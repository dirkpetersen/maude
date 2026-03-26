## maude MAUDE_VERSION

Ready-to-run sandbox appliance for agentic coding on Ubuntu 26.04.

### What's new in MAUDE_VERSION

- **PATH fixed**: `~/bin` is always first, `~/.local/bin` appears exactly once — survives Ubuntu defaults and Claude Code install
- **mom pre-installed**: package manager available immediately, no first-boot download needed
- **mom group = `users`**: every new Linux user automatically gets `mom` access
- **Hostname**: WSL prompt correctly shows `user@maude:~$`
- **Home directory**: starts in `/home/maude`, not Windows profile dir
- **Local dev workflow**: `make build-wsl`, `make wsl-import`, `make wsl-test`, `make wsl-update-scripts`
- **Windows setup guide**: step-by-step CMD-based install (no PowerShell required)

### Artifacts

| File | Description |
|------|-------------|
| `maude-wsl-ubuntu2604-MAUDE_VERSION.tar.gz` | WSL2 image — import with `wsl --import` |
| `maude-wsl-ubuntu2604-MAUDE_VERSION.tar.gz.sha256` | SHA-256 checksum |
| `ghcr.io/dirkpetersen/maude:MAUDE_VERSION` | Docker image |

### Quick Start

**WSL — open Command Prompt and run:**
```cmd
curl -L -o maude-wsl.tar.gz https://github.com/dirkpetersen/maude/releases/download/MAUDE_VERSION/maude-wsl-ubuntu2604-MAUDE_VERSION.tar.gz
mkdir C:\maude
wsl --import maude C:\maude maude-wsl.tar.gz --version 2
wsl -d maude
```

**Docker:**
```bash
docker run -d -p 3000:3000 --name maude ghcr.io/dirkpetersen/maude:MAUDE_VERSION
```

See the [README](https://github.com/dirkpetersen/maude#readme) and [Windows Setup Guide](https://github.com/dirkpetersen/maude/blob/main/docs/windows-setup.md) for full documentation.
