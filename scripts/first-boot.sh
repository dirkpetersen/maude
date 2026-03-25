#!/usr/bin/env bash
# /etc/maude/first-boot.sh
# Runs exactly once as root via systemd oneshot service (maude-first-boot.service).
# Sets up the full maude runtime environment:
#   - Detects deployment target (WSL vs VM)
#   - Installs mom (package manager helper)
#   - Installs appmotel
#   - Deploys web-term as an appmotel app
#   - Patches Traefik for WSL localhost-only binding
#   - Drops profile.d scripts into place
#   - Writes sentinel to prevent re-run
set -o errexit -o nounset -o pipefail
IFS=$'\n\t'

SENTINEL="/etc/maude/.first-boot-done"
LOG="/var/log/maude-first-boot.log"
MAUDE_ETC="/etc/maude"
MOM_GROUP="mom"

# ── Redirect all output to log ────────────────────────────────────────────────
exec > >(tee -a "${LOG}") 2>&1
echo "[maude-first-boot] Starting at $(date -Iseconds)"

# Already ran
if [[ -f "${SENTINEL}" ]]; then
    echo "[maude-first-boot] Sentinel found — skipping."
    exit 0
fi

# ── Helper functions ──────────────────────────────────────────────────────────

log()  { echo "[maude-first-boot] $*"; }
die()  { echo "[maude-first-boot] ERROR: $*" >&2; exit 1; }

is_wsl() {
    [[ -f /proc/sys/fs/binfmt_misc/WSLInterop ]]
}

arch() {
    local a
    a=$(uname -m)
    case "${a}" in
        x86_64)  echo "amd64" ;;
        aarch64) echo "arm64" ;;
        armv7l)  echo "armv7" ;;
        *)        die "Unsupported architecture: ${a}" ;;
    esac
}

# ── 1. Detect deployment target ───────────────────────────────────────────────
DEPLOY_TARGET="vm"
if is_wsl; then
    DEPLOY_TARGET="wsl"
    log "Deployment target: WSL (Traefik will bind to 127.0.0.1 only)"
else
    log "Deployment target: VM/bare-metal"
fi
echo "MAUDE_DEPLOY_TARGET=${DEPLOY_TARGET}" >> /etc/maude/maude.conf

# ── 2. Install mom ────────────────────────────────────────────────────────────
log "Installing mom..."
_arch=$(arch)
_mom_url="https://github.com/dirkpetersen/mom/releases/latest/download/mom-linux-${_arch}"

# Prefer .deb on Debian/Ubuntu
if command -v dpkg &>/dev/null; then
    _mom_deb_url="https://github.com/dirkpetersen/mom/releases/latest/download/mom_latest_amd64.deb"
    if [[ "${_arch}" == "amd64" ]]; then
        curl -fsSL "${_mom_deb_url}" -o /tmp/mom.deb
        dpkg -i /tmp/mom.deb || apt-get -f install -y
        rm -f /tmp/mom.deb
    else
        curl -fsSL "${_mom_url}" -o /usr/local/bin/mom
        chmod 4755 /usr/local/bin/mom  # setuid root
    fi
else
    curl -fsSL "${_mom_url}" -o /usr/local/bin/mom
    chmod 4755 /usr/local/bin/mom
fi

# Create mom group and config
groupadd --system "${MOM_GROUP}" 2>/dev/null || true
mkdir -p /etc/mom
if [[ ! -f /etc/mom/mom.conf ]]; then
    cat > /etc/mom/mom.conf <<'EOF'
# mom configuration — see: github.com/dirkpetersen/mom
group = "mom"
deny_list = "/etc/mom/deny.list"
log_file = "/var/log/mom.log"
EOF
fi
if [[ ! -f /etc/mom/deny.list ]]; then
    cat > /etc/mom/deny.list <<'EOF'
# mom deny list — one glob pattern per line
# Block packages that could compromise multi-user isolation
nmap
tcpdump
wireshark*
aircrack*
metasploit*
EOF
fi
log "mom installed."

# ── 3. Install appmotel ───────────────────────────────────────────────────────
log "Installing appmotel..."
curl -fsSL https://raw.githubusercontent.com/dirkpetersen/appmotel/main/install.sh \
    -o /tmp/appmotel-install.sh
chmod +x /tmp/appmotel-install.sh
# Run as root first (creates appmotel user, systemd service, sudoers)
/tmp/appmotel-install.sh
rm -f /tmp/appmotel-install.sh
log "appmotel installed."

# ── 4. WSL: patch Traefik to localhost-only ───────────────────────────────────
if [[ "${DEPLOY_TARGET}" == "wsl" ]]; then
    log "WSL detected — binding Traefik to 127.0.0.1..."
    _traefik_cfg="/home/appmotel/.config/traefik/traefik.yaml"
    if [[ -f "${_traefik_cfg}" ]]; then
        # Replace address: ":80" with "127.0.0.1:80" and ":443" with "127.0.0.1:443"
        sed -i \
            -e 's/address: ":80"/address: "127.0.0.1:80"/' \
            -e 's/address: ":443"/address: "127.0.0.1:443"/' \
            -e 's/address: ":8080"/address: "127.0.0.1:8080"/' \
            "${_traefik_cfg}"
        # Also add 8080 as backup entrypoint if not present
        if ! grep -q "8080" "${_traefik_cfg}"; then
            cat >> "${_traefik_cfg}" <<'EOF'

  # WSL backup entrypoint
  web-alt:
    address: "127.0.0.1:8080"
EOF
        fi
        systemctl restart traefik-appmotel 2>/dev/null || true
        log "Traefik patched for WSL (127.0.0.1 only)."
    else
        log "WARNING: Traefik config not found at ${_traefik_cfg} — skipping WSL patch."
    fi
fi

# ── 5. Deploy web-term via appmotel ──────────────────────────────────────────
log "Deploying web-term..."
# Give appmotel user a moment to start its timer
sleep 2
if command -v appmo &>/dev/null || [[ -x /home/appmotel/.local/bin/appmo ]]; then
    sudo -u appmotel /home/appmotel/.local/bin/appmo add \
        web-term \
        https://github.com/dirkpetersen/web-term \
        main \
        || log "WARNING: web-term deploy failed — can retry with: sudo -u appmotel appmo add web-term dirkpetersen/web-term main"
else
    log "WARNING: appmo CLI not found — web-term not deployed."
fi

# ── 6. Drop profile.d scripts ────────────────────────────────────────────────
log "Installing profile.d scripts..."
cp "${MAUDE_ETC}/profile.d/"*.sh /etc/profile.d/ 2>/dev/null || true
chmod 644 /etc/profile.d/maude-*.sh

# ── 7. Install new-user-login.sh ─────────────────────────────────────────────
if [[ -f "${MAUDE_ETC}/new-user-login.sh" ]]; then
    chmod 755 "${MAUDE_ETC}/new-user-login.sh"
fi

# ── 8. Enable UFW (VM only, not WSL) ─────────────────────────────────────────
if [[ "${DEPLOY_TARGET}" == "vm" ]] && command -v ufw &>/dev/null; then
    log "Configuring UFW firewall..."
    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow ssh
    ufw allow http
    ufw allow https
    ufw --force enable
    log "UFW enabled."
fi

# ── 9. Write sentinel ────────────────────────────────────────────────────────
touch "${SENTINEL}"
log "First-boot setup complete at $(date -Iseconds)"
