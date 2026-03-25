#!/usr/bin/env bash
# Docker entrypoint for maude.
# On first run: installs mom binary and appmotel (which requires a running system).
# On subsequent runs: starts SSH and appmotel services directly.
set -o nounset -o pipefail

FIRST_RUN_SENTINEL="/etc/maude/.docker-first-run-done"

# Allow BASE_DOMAIN override via environment
if [[ -n "${MAUDE_BASE_DOMAIN:-}" ]]; then
    sed -i "s|^MAUDE_BASE_DOMAIN=.*|MAUDE_BASE_DOMAIN=${MAUDE_BASE_DOMAIN}|" \
        /etc/maude/maude.conf
fi

# ── SSH daemon ────────────────────────────────────────────────────────────────
# Regenerate host keys if missing (e.g. fresh container)
[[ -f /etc/ssh/ssh_host_rsa_key ]] || ssh-keygen -A
/usr/sbin/sshd

# ── First-run: install mom and appmotel ───────────────────────────────────────
if [[ ! -f "${FIRST_RUN_SENTINEL}" ]]; then
    echo "[maude-docker] First run — installing mom and appmotel..."

    # Install mom binary
    _arch=$(uname -m)
    case "${_arch}" in
        x86_64)  _mom_suffix="amd64" ;;
        aarch64) _mom_suffix="arm64" ;;
        *)        _mom_suffix="amd64" ;;
    esac

    if curl -fsSL \
        "https://github.com/dirkpetersen/mom/releases/latest/download/mom-linux-${_mom_suffix}" \
        -o /usr/local/bin/mom 2>/dev/null; then
        chmod 4755 /usr/local/bin/mom
        echo "[maude-docker] mom installed."
    else
        echo "[maude-docker] WARNING: mom install failed (no network?). Continuing."
    fi

    # Install appmotel
    # In Docker we run without systemd; appmotel install.sh detects this and
    # sets up files without trying to enable system services.
    # We set MAUDE_DEPLOY_TARGET=docker so install.sh can adapt if it supports it.
    export MAUDE_DEPLOY_TARGET=docker
    if curl -fsSL \
        https://raw.githubusercontent.com/dirkpetersen/appmotel/main/install.sh \
        -o /tmp/appmotel-install.sh 2>/dev/null; then
        chmod +x /tmp/appmotel-install.sh
        # Run as root; appmotel's install.sh creates the appmotel user and files
        bash /tmp/appmotel-install.sh || \
            echo "[maude-docker] WARNING: appmotel install had errors. Some features may not work."
        rm -f /tmp/appmotel-install.sh
    else
        echo "[maude-docker] WARNING: appmotel install.sh download failed."
    fi

    # Patch Traefik to localhost-only for Docker (same as WSL)
    _traefik_cfg="/home/appmotel/.config/traefik/traefik.yaml"
    if [[ -f "${_traefik_cfg}" ]]; then
        sed -i \
            -e 's/address: ":80"/address: "127.0.0.1:80"/' \
            -e 's/address: ":443"/address: "127.0.0.1:443"/' \
            "${_traefik_cfg}"
    fi

    # Deploy web-term
    if command -v appmo &>/dev/null || [[ -x /home/appmotel/.local/bin/appmo ]]; then
        _appmo="${APPMO:-/home/appmotel/.local/bin/appmo}"
        sudo -u appmotel "${_appmo}" add \
            web-term https://github.com/dirkpetersen/web-term main 2>/dev/null \
            || echo "[maude-docker] WARNING: web-term deploy failed."
    fi

    touch "${FIRST_RUN_SENTINEL}"
    echo "[maude-docker] First-run setup complete."
fi

# ── Start Traefik (via appmotel's systemd service or directly) ─────────────────
# In Docker without systemd, start Traefik binary directly in background
_traefik_bin="/home/appmotel/.local/bin/traefik"
_traefik_cfg="/home/appmotel/.config/traefik/traefik.yaml"
if [[ -x "${_traefik_bin}" && -f "${_traefik_cfg}" ]]; then
    sudo -u appmotel \
        XDG_CONFIG_HOME=/home/appmotel/.config \
        XDG_DATA_HOME=/home/appmotel/.local/share \
        "${_traefik_bin}" --configFile="${_traefik_cfg}" &
    echo "[maude-docker] Traefik started."
fi

# ── Start web-term (via appmotel user service or directly) ────────────────────
_webterm_dir="/home/appmotel/.local/share/appmotel/web-term/repo"
if [[ -d "${_webterm_dir}" ]]; then
    _port=$(grep -r 'PORT=' /home/appmotel/.config/appmotel/web-term/.env 2>/dev/null \
            | cut -d= -f2 | tr -d '"' || echo "3000")
    sudo -u appmotel bash -c \
        "cd '${_webterm_dir}' && npm start" &
    echo "[maude-docker] web-term started on port ${_port:-3000}."
fi

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║            maude container ready                 ║"
echo "╚══════════════════════════════════════════════════╝"
echo "  Web terminal:  http://localhost:3000"
echo "  SSH:           ssh maude@localhost  (default pw: maude)"
echo "  Change pw:     docker exec -it maude passwd maude"
echo ""

# Keep container alive
exec tail -f /dev/null
