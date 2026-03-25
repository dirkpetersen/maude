#!/usr/bin/env bash
# Docker entrypoint for maude.
# Starts SSH daemon, Traefik (via appmotel), and web-term.
set -o errexit -o nounset -o pipefail

# Allow BASE_DOMAIN override via environment
if [[ -n "${MAUDE_BASE_DOMAIN:-}" ]]; then
    sed -i "s|^MAUDE_BASE_DOMAIN=.*|MAUDE_BASE_DOMAIN=${MAUDE_BASE_DOMAIN}|" /etc/maude/maude.conf
    if [[ -f /home/appmotel/.config/appmotel/.env ]]; then
        sed -i "s|^BASE_DOMAIN=.*|BASE_DOMAIN=${MAUDE_BASE_DOMAIN}|" \
            /home/appmotel/.config/appmotel/.env
    fi
fi

# Start SSH daemon
/usr/sbin/sshd

# Start Traefik and appmotel user services
systemctl start traefik-appmotel 2>/dev/null \
    || sudo -u appmotel sudo systemctl start traefik-appmotel 2>/dev/null \
    || true

sudo -u appmotel systemctl --user start appmotel-web-term 2>/dev/null || true

echo "maude container ready."
echo "  Web terminal: http://localhost:3000"
echo "  SSH:          ssh maude@localhost"

# Keep container alive
exec tail -f /dev/null
