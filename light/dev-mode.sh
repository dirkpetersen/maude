#!/bin/bash
# dev-mode.sh - run as root inside the Maude WSL distro.
# Turns on standard WSL behavior (Windows drives at /mnt/c, Windows programs,
# Windows PATH) and gives the Maude user full sudo. sudo asks for the user's
# password. If the user has no password yet, this script asks to set one first.
# WARNING: this removes the sandbox isolation. The AI agent can then reach
# all Windows drives and run any command as root.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root: wsl -d Maude -u root -e ..." >&2; exit 1; }

USERNAME=$({ sed -n 's/^default *= *//p' /etc/wsl.conf 2>/dev/null | head -n 1; } || true)
[ -n "$USERNAME" ] || { echo "No [user] default= found in /etc/wsl.conf" >&2; exit 1; }
id "$USERNAME" >/dev/null 2>&1 || { echo "User '$USERNAME' does not exist" >&2; exit 1; }

# The bootstrap creates the user with an empty password. Ubuntu PAM allows empty
# passwords (nullok), so set a real one before granting sudo.
case "$(passwd -S "$USERNAME" | awk '{print $2}')" in
  P) ;;
  *)
    [ -t 0 ] || { echo "No password set for '$USERNAME'. Run this command in an interactive PowerShell window." >&2; exit 1; }
    echo "Set a password for '$USERNAME'. You type it when you use sudo inside Maude."
    passwd "$USERNAME" || { echo "Password not set. Nothing was changed." >&2; exit 1; }
    ;;
esac

# Validate in a temp file outside /etc/sudoers.d (sudo reads every file there).
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
printf '%s ALL=(ALL) ALL\n' "$USERNAME" > "$tmp"
visudo -cf "$tmp" >/dev/null
install -m 0440 "$tmp" /etc/sudoers.d/maude-dev

cat > /etc/wsl.conf << WSLEOF
[boot]
systemd = true

[user]
default=$USERNAME

[automount]
enabled = true
mountFsTab = true

[interop]
enabled = true
appendWindowsPath = true
WSLEOF

echo "Dev mode is set for user '$USERNAME'."
echo "Now run this from Windows to apply it: wsl --terminate Maude"
