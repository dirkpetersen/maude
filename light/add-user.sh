#!/bin/bash
# add-user.sh - run as root inside the Maude WSL distro.
# Usage: add-user.sh <username> [drive letters...]   e.g. add-user.sh dp c d
# Adds a second Linux user with sudo (password required) and mounts the given
# Windows drives at /mnt/<letter> for that user only (mode 700, so the maude
# user, which runs the AI agent, gets "permission denied"). /etc/wsl.conf is
# not changed: interop and automount stay off for the whole distro.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root: wsl -d Maude -u root -e ..." >&2; exit 1; }
[ $# -ge 1 ] || { echo "Usage: add-user.sh <username> [drive letters...]" >&2; exit 1; }

NEWUSER="$1"; shift
[[ "$NEWUSER" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || { echo "Bad user name '$NEWUSER'" >&2; exit 1; }
DEFAULT_USER=$({ sed -n 's/^default *= *//p' /etc/wsl.conf 2>/dev/null | head -n 1; } || true)
[ -n "$DEFAULT_USER" ] || { echo "No [user] default= found in /etc/wsl.conf" >&2; exit 1; }
[ "$NEWUSER" != "$DEFAULT_USER" ] || { echo "'$NEWUSER' is the Maude user. Choose another name." >&2; exit 1; }
for L in "$@"; do
  [[ "$L" =~ ^[a-zA-Z]$ ]] || { echo "Bad drive letter '$L'" >&2; exit 1; }
done

# Refuse root and system accounts (an existing normal user is fine on rerun).
if uid=$(id -u "$NEWUSER" 2>/dev/null); then
  [ "$uid" -ge 1000 ] && [ "$uid" -lt 60000 ] || { echo "'$NEWUSER' is a system account" >&2; exit 1; }
else
  useradd -m -s /bin/bash "$NEWUSER"
fi
UID_N=$(id -u "$NEWUSER"); GID_N=$(id -g "$NEWUSER")
HOME_DIR=$(getent passwd "$NEWUSER" | cut -d: -f6)

# New accounts have no usable password. Set one before granting sudo.
case "$(passwd -S "$NEWUSER" | awk '{print $2}')" in
  P) ;;
  *)
    [ -t 0 ] || { echo "No password set for '$NEWUSER'. Run this command in an interactive PowerShell window." >&2; exit 1; }
    echo "Set a password for '$NEWUSER'. You type it when you use sudo."
    passwd "$NEWUSER" || { echo "Password not set. No sudo rule was added." >&2; exit 1; }
    ;;
esac

# Validate in a temp file outside /etc/sudoers.d (sudo reads every file there).
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
printf '%s ALL=(ALL) ALL\n' "$NEWUSER" > "$tmp"
visudo -cf "$tmp" >/dev/null
install -m 0440 "$tmp" "/etc/sudoers.d/$NEWUSER"

# Windows drives for this user only.
mounted=""
for L in "$@"; do
  L=${L,,}
  mp="/mnt/$L"
  mkdir -p "$mp"
  line=$(printf '%s: %s drvfs defaults,uid=%s,gid=%s,umask=077 0 0' "${L^^}" "$mp" "$UID_N" "$GID_N")
  if ! grep -qxF "$line" /etc/fstab 2>/dev/null; then
    # Drop any stale line for this mount point (different options), then add ours.
    [ ! -f /etc/fstab ] || sed -i "\\#^${L^^}: /mnt/$L #d" /etc/fstab
    printf '%s\n' "$line" >> /etc/fstab
  fi
  if mountpoint -q "$mp" || mount "$mp"; then mounted="$mounted $mp"; else echo "WARNING: could not mount ${L^^}: at $mp" >&2; fi
done

echo "User '$NEWUSER' is ready: sudo with password, home $HOME_DIR."
echo "Windows drives mounted for this user only:${mounted:- none}"
