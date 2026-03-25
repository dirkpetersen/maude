## maude MAUDE_VERSION

Ready-to-run sandbox appliance for agentic coding on Ubuntu 26.04.

### Artifacts

| File | Description |
|------|-------------|
| `maude-wsl-ubuntu2604-MAUDE_VERSION.tar.gz` | WSL2 image — import with `wsl --import` |
| `maude-wsl-ubuntu2604-MAUDE_VERSION.tar.gz.sha256` | SHA-256 checksum |
| `ghcr.io/dirkpetersen/maude:MAUDE_VERSION` | Docker image |

### Quick Start

**WSL (PowerShell):**
```powershell
wsl --import maude C:\maude .\maude-wsl-ubuntu2604-MAUDE_VERSION.tar.gz
wsl -d maude
```

**Docker:**
```bash
docker run -d -p 3000:3000 --name maude ghcr.io/dirkpetersen/maude:MAUDE_VERSION
```

See the [README](https://github.com/dirkpetersen/maude#readme) for full documentation.
