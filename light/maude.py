#!/usr/bin/env python3
"""
maude.py — Textual TUI for the Maude sandbox.
Always launched via:  maude tui
"""

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from rich.text import Text
from datetime import datetime
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Log,
    RadioButton,
    RadioSet,
    Static,
    TextArea,
)

PROJECTS_DIR = Path.home() / "Maude" / "Projects"
DELETED_DIR  = PROJECTS_DIR / ".deleted"
DISABLE_FLAG   = Path.home() / ".maude-tui-disabled"
KANNA_CMD    = "kanna"
KANNA_PORT   = 3210

UPDATE_URL   = "https://raw.githubusercontent.com/dirkpetersen/maude/main/light/maude.py"
UPDATE_STAMP = Path.home() / ".maude-tui-last-update"
UPDATE_HOUR  = 12  # local-time hour (noon) after which the daily refresh fires

MODELS        = ("opus-1m", "opus", "sonnet-1m", "sonnet", "haiku")
DEFAULT_MODEL = "opus-1m"
MODEL_FILE    = Path.home() / ".maude-model"

LOGO = (
    "  __  __                 _      \n"
    " |  \\/  | __ _ _   _  __| | ___ \n"
    " | |\\/| |/ _` | | | |/ _` |/ _ \\\n"
    " | |  | | (_| | |_| | (_| |  __/\n"
    " |_|  |_|\\__,_|\\__,_|\\__,_|\\___|"
)


# ── Helpers ────────────────────────────────────────────────────────────────

def maybe_self_update() -> None:
    """Refresh maude.py from GitHub once per day, at or after noon local time.
    The new version takes effect on the next launch."""
    now = datetime.now()
    if now.hour < UPDATE_HOUR:
        return
    today = now.date().isoformat()
    try:
        if UPDATE_STAMP.read_text().strip() == today:
            return
    except OSError:
        pass

    target = Path(__file__).resolve()
    # Cache-bust the URL — raw.githubusercontent.com sits behind a CDN
    # whose stale copies have hit us before.
    bust_url = f"{UPDATE_URL}?cache={int(time.time())}"
    try:
        with urllib.request.urlopen(bust_url, timeout=10) as resp:
            new_bytes = resp.read()
        tmp = target.with_name(target.name + ".new")
        tmp.write_bytes(new_bytes)
        tmp.replace(target)
        UPDATE_STAMP.write_text(today)
    except Exception as err:
        print(f"maude: update check failed ({err}); using cached version",
              file=sys.stderr)


def kill_port(port: int) -> None:
    """Forcefully kill anything listening on the given TCP port (best-effort)."""
    subprocess.run(
        ["fuser", "-k", "-KILL", f"{port}/tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )


def stop_kanna(proc: subprocess.Popen | None) -> None:
    """Kill kanna and the rest of its process group, then free the port."""
    if proc is not None and proc.poll() is None:
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = None
        if pgid is not None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pgid, sig)
                except ProcessLookupError:
                    break
                try:
                    proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
    kill_port(KANNA_PORT)


def kanna_env() -> dict[str, str]:
    """Build the environment for kanna: point it at the maude claude wrapper.

    The wrapper at ~/bin/claude sets the right auth env vars
    (Foundry/Azure/Bedrock/direct) per launch, so we only need to tell
    kanna where it is. Falls back to plain `claude` on PATH if the
    wrapper isn't installed yet.
    """
    extra: dict[str, str] = {}
    wrapper = Path.home() / "bin" / "claude"
    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        extra["CLAUDE_EXECUTABLE"] = str(wrapper)
    return extra


def check_credentials() -> bool:
    """Return True if Claude Code credentials are configured."""
    # Azure AI Foundry
    if os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"):
        return True
    # Anthropic direct
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    # AWS Bedrock
    aws_creds = Path.home() / ".aws" / "credentials"
    if aws_creds.exists() and aws_creds.stat().st_size > 0:
        return True
    # Azure clauderc
    azure_rc = Path.home() / ".azure" / "clauderc"
    if azure_rc.exists() and azure_rc.stat().st_size > 0:
        return True
    return False


def git_remote_origin_url(project_path: Path) -> str:
    """Return `git config --get remote.origin.url` for the project, or ''."""
    if not (project_path / ".git").exists():
        return ""
    try:
        r = subprocess.run(
            ["git", "-C", str(project_path), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def is_github_remote(url: str) -> bool:
    """True for git@github.com:… or https://github.com/… style remotes."""
    if not url:
        return False
    return "github.com" in url


def parse_github_owner_repo(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub remote URL, or None.

    Handles git@github.com:owner/repo(.git) and
    https://github.com/owner/repo(.git).
    """
    m = re.match(r"^git@github\.com:([^/]+)/(.+?)(?:\.git)?$", url)
    if not m:
        m = re.match(r"^https?://github\.com/([^/]+)/(.+?)(?:\.git)?/?$", url)
    if not m:
        return None
    return m.group(1), m.group(2)


def gh_user_login() -> str:
    """Return the authed GitHub username, or empty string."""
    if not shutil.which("gh"):
        return ""
    try:
        r = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def list_projects() -> list[dict]:
    """Return projects sorted by last-modified time, newest first."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    projects = []
    for p in PROJECTS_DIR.iterdir():
        if p.is_dir() and not p.name.startswith("."):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            modified = (datetime.fromtimestamp(mtime).strftime("%b %d, %Y")
                        if mtime else "unknown")
            origin = git_remote_origin_url(p)
            projects.append({
                "name":     p.name,
                "modified": modified,
                "path":     p,
                "mtime":    mtime,
                "origin":   origin,
                "github":   is_github_remote(origin),
            })
    projects.sort(key=lambda d: d["mtime"], reverse=True)
    return projects


def slugify(name: str) -> str:
    """Replace spaces with hyphens; keep letters, digits, dots, dashes, underscores."""
    name = name.replace(" ", "-")
    name = re.sub(r"[^a-zA-Z0-9._-]", "", name)
    return name.strip("-")


def open_project(project_path: Path, model: str, *, fresh: bool = False) -> None:
    """Launch Claude Code for a project.

    By default tries `--continue` first (resume the previous session),
    falling back to a fresh launch if there's nothing to continue.
    When `fresh=True`, skip `--continue` entirely so the conversation
    starts with no history.
    """
    os.chdir(project_path)
    if fresh:
        subprocess.run(["claude", model], check=False)
        return
    ret = subprocess.run(["claude", model, "--continue"], check=False).returncode
    if ret != 0:
        subprocess.run(["claude", model], check=False)


def read_model() -> str:
    """Return the user's saved Claude model alias, or the default."""
    try:
        m = MODEL_FILE.read_text().strip()
        if m in MODELS:
            return m
    except OSError:
        pass
    return DEFAULT_MODEL


def save_model(name: str) -> None:
    if name in MODELS:
        try:
            MODEL_FILE.write_text(name)
        except OSError:
            pass


def soft_delete(project_path: Path) -> None:
    """Move project to .deleted/."""
    DELETED_DIR.mkdir(parents=True, exist_ok=True)
    dest = DELETED_DIR / project_path.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(project_path), dest)


# ── Git Setup Wizard helpers ──────────────────────────────────────────────

SSH_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"


def git_config_get(key: str) -> str:
    """Return `git config --global <key>` or empty string."""
    try:
        r = subprocess.run(
            ["git", "config", "--global", key],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def git_config_set(key: str, value: str) -> None:
    """Set `git config --global <key> <value>`. Silent on failure."""
    try:
        subprocess.run(
            ["git", "config", "--global", key, value],
            check=False, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def fetch_github_user(username: str, timeout: int = 8) -> dict | None:
    """Fetch a user's public profile from the GitHub API.

    Returns dict with keys: login, name, email, html_url. Returns None
    on 404 or any other error.
    """
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", username):
        return None
    url = f"https://api.github.com/users/{username}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        login = data.get("login", username)
        # GitHub may hide the email; fall back to the GitHub-recommended
        # noreply form so the user always gets a usable git author email.
        if data.get("email"):
            email = data["email"]
        elif data.get("id"):
            email = f"{data['id']}+{login}@users.noreply.github.com"
        else:
            email = f"{login}@users.noreply.github.com"
        return {
            "login": login,
            "name":  data.get("name") or login,
            "email": email,
            "html_url": data.get("html_url", f"https://github.com/{login}"),
        }
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None


def existing_ssh_key() -> str | None:
    """Return path to an existing SSH key (prefer ed25519), else None."""
    candidates = [
        Path.home() / ".ssh" / "id_ed25519",
        Path.home() / ".ssh" / "id_rsa",
    ]
    for p in candidates:
        if p.exists() and (p.with_suffix(".pub")).exists():
            return str(p)
    return None


def generate_ssh_key(passphrase: str, comment: str) -> tuple[bool, str]:
    """Generate an ed25519 SSH key at ~/.ssh/id_ed25519.

    Returns (ok, message). Refuses to overwrite if the key already exists.
    """
    SSH_KEY_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    if SSH_KEY_PATH.exists():
        return False, f"{SSH_KEY_PATH} already exists; refusing to overwrite."
    try:
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-C", comment,
             "-f", str(SSH_KEY_PATH), "-N", passphrase],
            check=True, capture_output=True, text=True, timeout=30,
        )
        SSH_KEY_PATH.chmod(0o600)
        SSH_KEY_PATH.with_suffix(".pub").chmod(0o644)
        return True, "SSH key generated."
    except subprocess.CalledProcessError as err:
        return False, f"ssh-keygen failed: {err.stderr or err}"
    except (subprocess.TimeoutExpired, FileNotFoundError) as err:
        return False, str(err)


def read_ssh_pubkey(key_path: str) -> str:
    """Read the public key file matching `key_path`."""
    pub = Path(key_path).with_suffix(".pub")
    try:
        return pub.read_text().strip()
    except OSError:
        return ""


def verify_ssh_github(passphrase: str = "", timeout: int = 15) -> tuple[bool, str]:
    """Run `ssh -T git@github.com` and check for a successful auth line.

    If `passphrase` is provided, feeds it via SSH_ASKPASS so a
    passphrase-protected key can be unlocked without an interactive
    prompt (ssh would otherwise hang or — with BatchMode=yes — refuse
    to read the key and fall back to "Permission denied").
    """
    env = os.environ.copy()
    askpass_path: str | None = None

    if passphrase:
        # Write a tiny shell script that prints the passphrase to stdout.
        fd, askpass_path = tempfile.mkstemp(prefix="maude-askpass-", suffix=".sh")
        with os.fdopen(fd, "w") as f:
            f.write("#!/bin/sh\n")
            f.write(f"printf '%s' {shlex.quote(passphrase)}\n")
        os.chmod(askpass_path, 0o700)
        env["SSH_ASKPASS"] = askpass_path
        env["SSH_ASKPASS_REQUIRE"] = "force"     # OpenSSH 8.4+
        env.setdefault("DISPLAY", ":0")          # older OpenSSH still needs this

    cmd = ["ssh", "-T",
           "-o", "StrictHostKeyChecking=accept-new",
           "-o", "IdentitiesOnly=yes",
           "-i", str(SSH_KEY_PATH),
           "git@github.com"]
    if not passphrase:
        # Without a passphrase, fail fast instead of prompting.
        cmd[2:2] = ["-o", "BatchMode=yes"]

    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=env, stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as err:
        return False, str(err)
    finally:
        if askpass_path:
            try:
                os.unlink(askpass_path)
            except OSError:
                pass

    output = (r.stdout + r.stderr).strip()
    # GitHub returns exit code 1 even on successful auth, with this message.
    if "successfully authenticated" in output.lower():
        return True, output
    return False, output or "no response from GitHub"


def ssh_key_has_passphrase(key_path: Path) -> bool:
    """True if the key file is encrypted (passphrase required to use)."""
    try:
        r = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(key_path), "-P", ""],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return r.returncode != 0


def ssh_key_in_agent(key_path: Path) -> bool:
    """True if the running ssh-agent already has this key loaded."""
    pub = key_path.with_suffix(".pub")
    try:
        key_blob = pub.read_text().strip().split()[1]
    except (OSError, IndexError):
        return False
    try:
        r = subprocess.run(
            ["ssh-add", "-L"], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return r.returncode == 0 and key_blob in r.stdout


def ssh_agent_running() -> bool:
    """True if SSH_AUTH_SOCK points at a usable agent."""
    if not os.environ.get("SSH_AUTH_SOCK"):
        return False
    try:
        r = subprocess.run(
            ["ssh-add", "-l"], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    # Exit codes: 0 = keys loaded, 1 = no keys, 2 = no agent.
    return r.returncode != 2


def ensure_ssh_agent() -> bool:
    """Start an ssh-agent if none is running and import SSH_AUTH_SOCK /
    SSH_AGENT_PID into os.environ so subsequent ssh-add / git push
    can use it. No-op if an agent is already reachable. Returns True
    on success."""
    if ssh_agent_running():
        return True
    if not shutil.which("ssh-agent"):
        return False
    try:
        r = subprocess.run(
            ["ssh-agent", "-s"], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if r.returncode != 0:
        return False
    # ssh-agent -s prints lines like:
    #   SSH_AUTH_SOCK=/tmp/...; export SSH_AUTH_SOCK;
    #   SSH_AGENT_PID=12345; export SSH_AGENT_PID;
    for line in r.stdout.splitlines():
        m = re.match(r"^([A-Z_]+)=([^;]+);", line)
        if m:
            os.environ[m.group(1)] = m.group(2)
    return ssh_agent_running()


def ssh_add_with_passphrase(key_path: Path, passphrase: str) -> tuple[bool, str]:
    """Add the key to ssh-agent using SSH_ASKPASS to feed `passphrase`."""
    if not key_path.exists():
        return False, f"key not found: {key_path}"
    fd, askpass = tempfile.mkstemp(prefix="maude-askpass-", suffix=".sh")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("#!/bin/sh\n")
            f.write(f"printf '%s' {shlex.quote(passphrase)}\n")
        os.chmod(askpass, 0o700)
        env = os.environ.copy()
        env["SSH_ASKPASS"] = askpass
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env.setdefault("DISPLAY", ":0")
        r = subprocess.run(
            ["ssh-add", str(key_path)],
            env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=30,
        )
        ok = r.returncode == 0
        return ok, (r.stdout + r.stderr).strip()
    finally:
        try:
            os.unlink(askpass)
        except OSError:
            pass


def gh_is_authed() -> bool:
    """True if `gh auth status` reports a logged-in user."""
    if not shutil.which("gh"):
        return False
    try:
        r = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return r.returncode == 0


def gh_upload_key(kind: str, armored: str, title: str) -> tuple[bool, str]:
    """Upload an SSH or GPG public key via gh CLI.

    `kind` is "ssh-key" or "gpg-key". Returns (ok, message). If the key
    is already on the account, that's treated as success.
    """
    if not shutil.which("gh"):
        return False, "gh is not installed"
    cmd = ["gh", kind, "add", "-", "--title", title]
    try:
        r = subprocess.run(
            cmd, input=armored, text=True, capture_output=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as err:
        return False, str(err)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        return True, out or "uploaded"
    if "already" in out.lower() or "duplicate" in out.lower():
        return True, "already on GitHub"
    return False, out or "upload failed"


def existing_gpg_key(email: str) -> str | None:
    """Return the long key-id of a secret GPG key matching `email`, or None."""
    if not email:
        return None
    try:
        r = subprocess.run(
            ["gpg", "--list-secret-keys", "--keyid-format=long", email],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith(("sec", "ssb")):
            # Format:  sec   ed25519/ABCD1234EF567890 2026-…
            parts = line.split()
            if len(parts) >= 2 and "/" in parts[1]:
                return parts[1].split("/", 1)[1]
    return None


def generate_gpg_key(name: str, email: str, passphrase: str) -> tuple[str | None, str]:
    """Generate an ed25519 GPG key (with cv25519 subkey) for signing.

    Returns (key_id, message). key_id is None on failure.
    """
    batch = (
        "%echo Generating Maude signing key\n"
        "Key-Type: EDDSA\n"
        "Key-Curve: ed25519\n"
        "Key-Usage: sign\n"
        "Subkey-Type: ECDH\n"
        "Subkey-Curve: cv25519\n"
        "Subkey-Usage: encrypt\n"
        f"Name-Real: {name}\n"
        f"Name-Email: {email}\n"
        "Expire-Date: 0\n"
        f"{'Passphrase: ' + passphrase if passphrase else '%no-protection'}\n"
        "%commit\n"
        "%echo done\n"
    )
    try:
        subprocess.run(
            ["gpg", "--batch", "--pinentry-mode", "loopback", "--gen-key"],
            input=batch, text=True, capture_output=True,
            check=True, timeout=120,
        )
    except subprocess.CalledProcessError as err:
        return None, f"gpg --gen-key failed: {err.stderr or err}"
    except (subprocess.TimeoutExpired, FileNotFoundError) as err:
        return None, str(err)
    key_id = existing_gpg_key(email)
    if not key_id:
        return None, "gpg succeeded but the new key was not found."
    return key_id, "GPG key generated."


def export_gpg_pubkey(key_id: str) -> str:
    """Return the ASCII-armored public key for `key_id`."""
    try:
        r = subprocess.run(
            ["gpg", "--armor", "--export", key_id],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def verify_gpg_signing(key_id: str, passphrase: str) -> tuple[bool, str]:
    """Sign a temp string with `key_id` to confirm signing works end-to-end."""
    cmd = ["gpg", "--batch", "--pinentry-mode", "loopback", "-u", key_id,
           "--clearsign", "--output", "-"]
    if passphrase:
        cmd[3:3] = ["--passphrase", passphrase]
    try:
        r = subprocess.run(
            cmd, input="maude-git-setup-test\n", text=True,
            capture_output=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as err:
        return False, str(err)
    if r.returncode == 0 and "BEGIN PGP SIGNATURE" in r.stdout:
        return True, "GPG signing verified."
    return False, (r.stderr or "signing failed").strip()


def install_keychain_via_mom() -> tuple[bool, str]:
    """Install the keychain package via mom (idempotent)."""
    if shutil.which("keychain"):
        return True, "keychain already installed."
    if not shutil.which("mom"):
        return False, "mom is not available; cannot install keychain."
    try:
        r = subprocess.run(
            ["mom", "install", "-y", "keychain"],
            capture_output=True, text=True, timeout=180,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as err:
        return False, str(err)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()
    return True, "keychain installed."


# Sentinel-bracketed block so re-running the wizard can find and replace
# the exact same range without parsing bash. We start an empty ssh-agent
# on shell login; the Maude TUI loads the key (with a passphrase prompt)
# on startup, so we deliberately don't pass a key file here — otherwise
# keychain would prompt at .bashrc time, before the TUI ever appears.
KEYCHAIN_BEGIN = "# >>> Maude keychain BEGIN"
KEYCHAIN_END   = "# >>> Maude keychain END"
KEYCHAIN_BLOCK = f"""
{KEYCHAIN_BEGIN}
# Maude: ssh-agent via keychain (TUI loads the key)
if command -v keychain >/dev/null 2>&1; then
    eval "$(keychain --quiet --eval --agents ssh)"
fi
{KEYCHAIN_END}
"""

# Marker line emitted by older versions of this wizard, before sentinels.
KEYCHAIN_LEGACY_MARKER = "Maude: ssh-agent via keychain"


def _strip_sentinel_block(text: str) -> str:
    """Remove our `# >>> Maude keychain BEGIN … END` block if present."""
    if KEYCHAIN_BEGIN not in text:
        return text
    out: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        if not inside and KEYCHAIN_BEGIN in line:
            inside = True
            continue
        if inside:
            if KEYCHAIN_END in line:
                inside = False
            continue
        out.append(line)
    return "".join(out)


def _strip_legacy_keychain_block(text: str) -> str:
    """Remove the older unmarked block.

    The legacy block looked like:
        # Maude: ssh-agent via keychain
        if command -v keychain ...; then
            if [[ -f "$HOME/.ssh/id_ed25519" ]]; then
                eval "$(keychain ... id_ed25519)"
            fi
        fi
    Two nested `if`s, two `fi`s. The earlier stripper exited on the
    first `fi`, leaving the outer one orphaned and breaking bash. We
    track if/fi depth so we close cleanly even with nested blocks.
    """
    if KEYCHAIN_LEGACY_MARKER not in text:
        return text
    out: list[str] = []
    skipping = False
    depth = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not skipping and KEYCHAIN_LEGACY_MARKER in line:
            skipping = True
            depth = 0
            continue
        if skipping:
            if stripped.startswith("if "):
                depth += 1
            elif stripped == "fi":
                depth -= 1
                if depth <= 0:
                    skipping = False
                    continue   # also drop this closing fi
            continue
        out.append(line)
    return "".join(out)


def _strip_orphan_fi_before_keychain(text: str) -> str:
    """Remove a stray `fi` line that sits right before a Maude keychain
    block. This is the artefact left behind by the older buggy stripper
    that didn't count nesting; we clean it up on the way through."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].strip() == "fi":
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n and (KEYCHAIN_BEGIN in lines[j]
                          or KEYCHAIN_LEGACY_MARKER in lines[j]):
                # Skip this orphan fi (and absorb the leading whitespace
                # if any).
                i += 1
                continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def _normalise_keychain_pipeline(text: str) -> str:
    """Strip our blocks and the orphan-fi artefact in the right order.

    Orphan-fi cleanup runs *first* — while every keychain marker is
    still in place — because the stripper anchors the orphan against
    the marker it sits above. If we stripped a sentinel block first,
    the orphan would no longer have anything to anchor on and would
    survive. Same logic for the legacy stripper.
    """
    text = _strip_orphan_fi_before_keychain(text)
    text = _strip_sentinel_block(text)
    text = _strip_legacy_keychain_block(text)
    return text


def fix_bashrc_orphan_fi() -> tuple[bool, str]:
    """Standalone repair: remove the orphan fi from a previous buggy
    wizard run. Re-emits the current sentinel block only if one (any
    form) was already present, so we never plant a keychain block on
    a system that didn't have one.
    """
    rc = Path.home() / ".bashrc"
    try:
        text = rc.read_text() if rc.exists() else ""
    except OSError as err:
        return False, f"could not read ~/.bashrc: {err}"

    had_block = (KEYCHAIN_BEGIN in text) or (KEYCHAIN_LEGACY_MARKER in text)
    new_text = _normalise_keychain_pipeline(text)
    if had_block:
        if not new_text.endswith("\n"):
            new_text += "\n"
        new_text += KEYCHAIN_BLOCK.lstrip("\n")

    if new_text == text:
        return False, "~/.bashrc is already clean."
    try:
        rc.write_text(new_text)
        return True, "~/.bashrc fixed (orphan fi removed, block normalised)."
    except OSError as err:
        return False, f"could not write ~/.bashrc: {err}"


def add_keychain_to_bashrc() -> bool:
    """Idempotently install the sentinel-bracketed keychain block.

    Strips any existing sentinel block and any unmarked legacy block,
    cleans up the orphan-`fi` artefact left by an earlier buggy
    stripper, then appends the current block.
    """
    rc = Path.home() / ".bashrc"
    try:
        text = rc.read_text() if rc.exists() else ""
    except OSError:
        return False

    new_text = _normalise_keychain_pipeline(text)
    if not new_text.endswith("\n"):
        new_text += "\n"
    new_text += KEYCHAIN_BLOCK.lstrip("\n")

    if new_text == text:
        return True
    try:
        rc.write_text(new_text)
        return True
    except OSError:
        return False


# ── Modal screens ──────────────────────────────────────────────────────────

CLAUDERC_PATH    = Path.home() / ".azure"  / "clauderc"
AWS_CREDS_PATH   = Path.home() / ".aws"    / "credentials"
AWS_CONFIG_PATH  = Path.home() / ".aws"    / "config"
GEMINI_ENV_PATH  = Path.home() / ".gemini" / ".env"

# Recognised credential-related env vars, for parsing pasted exports.
CRED_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "AWS_", "AZURE_", "OPENAI_",
                 "GEMINI_", "GOOGLE_")
# Vars that belong to the Gemini CLI: routed to ~/.gemini/.env (where the
# CLI looks by default) instead of ~/.azure/clauderc, which is Claude's
# Azure-vs-AWS routing file and unrelated to Gemini.
GEMINI_PREFIXES = ("GEMINI_", "GOOGLE_")
CRED_KEY_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")


def parse_creds_text(text: str) -> dict[str, str]:
    """Parse pasted shell-style exports into a {KEY: VALUE} dict.

    Accepts:
        export FOO=bar
        FOO=bar
        FOO="bar baz"
        FOO='bar baz'
    Comments and blank lines are ignored. Only KEYs starting with one
    of the known credential prefixes are kept, to avoid writing junk.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        m = CRED_KEY_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if (val.startswith('"') and val.endswith('"')) or \
           (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if key.startswith(CRED_PREFIXES):
            out[key] = val
    return out


def write_clauderc(values: dict[str, str]) -> None:
    """Write env vars to ~/.azure/clauderc as `export KEY=VALUE` lines."""
    CLAUDERC_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'export {k}="{v}"' for k, v in values.items()]
    CLAUDERC_PATH.write_text("\n".join(lines) + "\n")
    try:
        CLAUDERC_PATH.chmod(0o600)
    except OSError:
        pass


def merge_clauderc(values: dict[str, str]) -> None:
    """Merge `values` into ~/.azure/clauderc, preserving any existing
    exports that aren't in `values`. Values from `values` win on collision.
    """
    existing: dict[str, str] = {}
    if CLAUDERC_PATH.exists():
        try:
            for raw in CLAUDERC_PATH.read_text().splitlines():
                line = raw.strip()
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                m = CRED_KEY_RE.match(line)
                if m:
                    k, v = m.group(1), m.group(2).strip()
                    if (v.startswith('"') and v.endswith('"')) or \
                       (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    existing[k] = v
        except OSError:
            pass
    existing.update(values)
    write_clauderc(existing)


def merge_gemini_env(values: dict[str, str]) -> None:
    """Merge `values` into ~/.gemini/.env (dotenv format), the file the
    Gemini CLI auto-loads. Preserves existing keys; `values` win on collision.
    """
    existing: dict[str, str] = {}
    if GEMINI_ENV_PATH.exists():
        try:
            for raw in GEMINI_ENV_PATH.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                m = CRED_KEY_RE.match(line)
                if m:
                    k, v = m.group(1), m.group(2).strip()
                    if (v.startswith('"') and v.endswith('"')) or \
                       (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    existing[k] = v
        except OSError:
            pass
    existing.update(values)
    GEMINI_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    # dotenv format — plain KEY="VALUE", no `export` (the CLI's loader
    # does not strip it).
    lines = [f'{k}="{v}"' for k, v in existing.items()]
    GEMINI_ENV_PATH.write_text("\n".join(lines) + "\n")
    try:
        GEMINI_ENV_PATH.chmod(0o600)
    except OSError:
        pass


def write_aws_credentials(access_key: str, secret_key: str, region: str) -> None:
    """Persist AWS creds to ~/.aws/credentials and ~/.aws/config so that
    boto3 / claude-wrapper / kanna can pick them up.

    Uses the [bedrock] profile to match the convention in the maude
    welcome banner (which suggests `aws --profile bedrock configure`).
    """
    AWS_CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AWS_CREDS_PATH.write_text(
        f"[bedrock]\n"
        f"aws_access_key_id = {access_key}\n"
        f"aws_secret_access_key = {secret_key}\n"
    )
    AWS_CONFIG_PATH.write_text(
        f"[profile bedrock]\n"
        f"region = {region}\n"
    )
    try:
        AWS_CREDS_PATH.chmod(0o600)
        AWS_CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def have_bedrock_creds() -> bool:
    return AWS_CREDS_PATH.exists() and AWS_CREDS_PATH.stat().st_size > 0


def have_foundry_creds() -> bool:
    if os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"):
        return True
    # Check both the active clauderc and the parked .inactive sibling
    # (the latter exists when Bedrock is currently the default).
    inactive = CLAUDERC_PATH.with_name(CLAUDERC_PATH.name + ".inactive")
    for p in (CLAUDERC_PATH, inactive):
        if not p.exists():
            continue
        try:
            if "ANTHROPIC_FOUNDRY_API_KEY" in p.read_text():
                return True
        except OSError:
            pass
    return False


class CredsEntryScreen(ModalScreen[bool]):
    """Modal for setting LLM credentials.

    Three input modes selectable via tabs at the top:
      • Paste exports — original `export FOO=bar` paste flow
      • AWS Bedrock  — three named fields (access key / secret / region)
      • Azure Foundry — base URL + API key
    All modes save to ~/.azure/clauderc (merged) so existing creds
    survive across modes; AWS Bedrock additionally writes
    ~/.aws/credentials + ~/.aws/config under the [bedrock] profile.

    When *both* Bedrock and Foundry creds exist on disk, a checkbox
    appears at the bottom: "Default to AWS Bedrock". Unchecked routes
    Claude to Foundry (CLAUDE_CODE_USE_FOUNDRY=1); checked routes it
    to Bedrock (CLAUDE_CODE_USE_BEDROCK=1).
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    MODE_PASTE   = "paste"
    MODE_BEDROCK = "bedrock"
    MODE_FOUNDRY = "foundry"

    def __init__(self) -> None:
        super().__init__()
        self.mode = self.MODE_PASTE

    def action_cancel(self) -> None:
        self.dismiss(False)

    def compose(self) -> ComposeResult:
        with Container(id="creds-box"):
            yield Label("Set LLM Credentials", id="creds-title")
            with Horizontal(id="creds-tabs"):
                yield Button("Paste exports", id="btn-creds-mode-paste",
                             variant="primary")
                yield Button("AWS Bedrock",   id="btn-creds-mode-bedrock",
                             variant="default")
                yield Button("Azure Foundry", id="btn-creds-mode-foundry",
                             variant="default")
            yield Container(id="creds-body")
            yield Label("", id="creds-status")
            yield Checkbox("Default to AWS Bedrock (uncheck → Foundry)",
                           id="creds-default-bedrock", value=False)
            with Horizontal(id="creds-buttons"):
                yield Button("Save",   variant="success", id="btn-creds-save")
                yield Button("Cancel", variant="primary", id="btn-creds-cancel")

    async def on_mount(self) -> None:
        await self._render_body()
        self._update_default_checkbox()

    # ── Mode switcher ──────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-creds-mode-paste")
    async def mode_paste(self) -> None:
        await self._switch_mode(self.MODE_PASTE)

    @on(Button.Pressed, "#btn-creds-mode-bedrock")
    async def mode_bedrock(self) -> None:
        await self._switch_mode(self.MODE_BEDROCK)

    @on(Button.Pressed, "#btn-creds-mode-foundry")
    async def mode_foundry(self) -> None:
        await self._switch_mode(self.MODE_FOUNDRY)

    async def _switch_mode(self, mode: str) -> None:
        self.mode = mode
        for btn_id, btn_mode in (
            ("#btn-creds-mode-paste",   self.MODE_PASTE),
            ("#btn-creds-mode-bedrock", self.MODE_BEDROCK),
            ("#btn-creds-mode-foundry", self.MODE_FOUNDRY),
        ):
            btn = self.query_one(btn_id, Button)
            btn.variant = "primary" if mode == btn_mode else "default"
        await self._render_body()

    async def _render_body(self) -> None:
        body = self.query_one("#creds-body", Container)
        await body.remove_children()
        if self.mode == self.MODE_PASTE:
            body.mount(Label(
                "Paste shell `export` lines or KEY=VALUE pairs. Recognised "
                "prefixes: ANTHROPIC_*, CLAUDE_*, AWS_*, AZURE_*, OPENAI_*. "
                "GEMINI_*/GOOGLE_* are saved to ~/.gemini/.env for the gemini CLI."
            ))
            body.mount(TextArea("", id="creds-text",
                                language=None, show_line_numbers=False))
            self.call_after_refresh(
                lambda: self.query_one("#creds-text", TextArea).focus()
            )
        elif self.mode == self.MODE_BEDROCK:
            body.mount(Label("AWS Bedrock — region defaults to us-west-2."))
            body.mount(Label("AWS_ACCESS_KEY_ID:"))
            body.mount(Input(placeholder="AKIA…", id="creds-aws-key"))
            body.mount(Label("AWS_SECRET_ACCESS_KEY:"))
            body.mount(Input(placeholder="secret", id="creds-aws-secret",
                             password=True))
            body.mount(Label("AWS_REGION:"))
            body.mount(Input(value="us-west-2", id="creds-aws-region"))
            self.call_after_refresh(
                lambda: self.query_one("#creds-aws-key", Input).focus()
            )
        else:  # MODE_FOUNDRY
            body.mount(Label("Azure AI Foundry — Anthropic-compatible endpoint."))
            body.mount(Label("ANTHROPIC_FOUNDRY_BASE_URL:"))
            body.mount(Input(
                placeholder="https://<resource>.services.ai.azure.com/anthropic",
                id="creds-foundry-url"))
            body.mount(Label("ANTHROPIC_FOUNDRY_API_KEY:"))
            body.mount(Input(placeholder="api key",
                             id="creds-foundry-key", password=True))
            self.call_after_refresh(
                lambda: self.query_one("#creds-foundry-url", Input).focus()
            )

    def _update_default_checkbox(self) -> None:
        """Show the Bedrock-vs-Foundry default toggle only when both
        credential families are present on disk (active or parked)."""
        cb = self.query_one("#creds-default-bedrock", Checkbox)
        if have_bedrock_creds() and have_foundry_creds():
            cb.display = True
            # If clauderc is parked as .inactive, Bedrock is the
            # currently-default provider; check the box accordingly.
            inactive = CLAUDERC_PATH.with_name(CLAUDERC_PATH.name + ".inactive")
            parked = inactive.exists() and not CLAUDERC_PATH.exists()
            cb.value = parked or (
                bool(os.environ.get("CLAUDE_CODE_USE_BEDROCK"))
                and not bool(os.environ.get("CLAUDE_CODE_USE_FOUNDRY"))
            )
        else:
            cb.display = False

    # ── Save ───────────────────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        self.query_one("#creds-status", Label).update(text)

    @on(Button.Pressed, "#btn-creds-save")
    def save(self) -> None:
        try:
            if self.mode == self.MODE_PASTE:
                self._save_paste()
            elif self.mode == self.MODE_BEDROCK:
                self._save_bedrock()
            else:
                self._save_foundry()
        except _CredsValidation:
            # The handler already populated the status label.
            return
        except OSError as err:
            self._set_status(f"[bold red]Could not save: {err}[/]")
            return
        # Apply the routing checkbox if it's visible.
        self._apply_default_provider()
        self.dismiss(True)

    def _save_paste(self) -> None:
        text = self.query_one("#creds-text", TextArea).text
        # Empty textarea on the Paste tab is fine — the user may only
        # want to flip the routing checkbox at the bottom. Don't error.
        if not text.strip():
            return
        values = parse_creds_text(text)
        if not values:
            self._set_status("[bold red]No recognised credential lines found.[/]")
            raise _CredsValidation()
        # Route Gemini/Google vars to ~/.gemini/.env (the Gemini CLI's default
        # location); everything else to ~/.azure/clauderc for the claude wrapper.
        gemini_vals = {k: v for k, v in values.items()
                       if k.startswith(GEMINI_PREFIXES)}
        other_vals = {k: v for k, v in values.items()
                      if not k.startswith(GEMINI_PREFIXES)}
        if other_vals:
            merge_clauderc(other_vals)
        if gemini_vals:
            merge_gemini_env(gemini_vals)
        os.environ.update(values)

    def _save_bedrock(self) -> None:
        ak = self.query_one("#creds-aws-key", Input).value.strip()
        sk = self.query_one("#creds-aws-secret", Input).value.strip()
        rg = self.query_one("#creds-aws-region", Input).value.strip() or "us-west-2"
        # If both auth fields are empty, the user is here just to flip
        # the routing checkbox — don't error, fall through to apply.
        if not ak and not sk:
            return
        if not ak or not sk:
            self._set_status(
                "[bold red]Access key id and secret are required.[/]"
            )
            raise _CredsValidation()
        write_aws_credentials(ak, sk, rg)
        # Also stash in clauderc so non-AWS consumers see env vars.
        env = {
            "AWS_ACCESS_KEY_ID":     ak,
            "AWS_SECRET_ACCESS_KEY": sk,
            "AWS_REGION":            rg,
            "AWS_PROFILE":           "bedrock",
            "CLAUDE_CODE_USE_BEDROCK": "1",
        }
        merge_clauderc(env)
        os.environ.update(env)

    def _save_foundry(self) -> None:
        url = self.query_one("#creds-foundry-url", Input).value.strip()
        key = self.query_one("#creds-foundry-key", Input).value.strip()
        # Same as Bedrock: empty form just means "flip routing only".
        if not url and not key:
            return
        if not url or not key:
            self._set_status(
                "[bold red]Both URL and API key are required.[/]"
            )
            raise _CredsValidation()
        env = {
            "ANTHROPIC_FOUNDRY_BASE_URL": url,
            "ANTHROPIC_FOUNDRY_API_KEY":  key,
            "CLAUDE_CODE_USE_FOUNDRY":    "1",
        }
        merge_clauderc(env)
        os.environ.update(env)

    def _apply_default_provider(self) -> None:
        """Honour the Bedrock/Foundry default checkbox if it's visible.

        On top of toggling the CLAUDE_CODE_USE_* env vars, we also
        physically rename ~/.azure/clauderc <-> ~/.azure/clauderc.inactive
        so the claude wrapper (which sources clauderc on each launch)
        can't accidentally re-set Foundry env vars when Bedrock is the
        intended target.
        """
        cb = self.query_one("#creds-default-bedrock", Checkbox)
        if not cb.display:
            return
        active   = CLAUDERC_PATH
        inactive = active.with_suffix(active.suffix + ".inactive") \
                   if active.suffix else active.with_name(active.name + ".inactive")
        if cb.value:
            # Bedrock active → park clauderc out of the way so the
            # wrapper doesn't source Foundry creds.
            if active.exists():
                try:
                    if inactive.exists():
                        inactive.unlink()
                    active.rename(inactive)
                except OSError:
                    pass
            os.environ.pop("CLAUDE_CODE_USE_FOUNDRY", None)
            os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
        else:
            # Foundry active → restore clauderc if it was parked.
            if inactive.exists() and not active.exists():
                try:
                    inactive.rename(active)
                except OSError:
                    pass
            os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)
            os.environ["CLAUDE_CODE_USE_FOUNDRY"] = "1"
            # Persist the routing flag (only when clauderc is the
            # active file — otherwise we'd be writing to .inactive).
            if active.exists():
                merge_clauderc({"CLAUDE_CODE_USE_FOUNDRY": "1"})

    @on(Button.Pressed, "#btn-creds-cancel")
    def cancel(self) -> None:
        self.dismiss(False)


class _CredsValidation(Exception):
    """Raised internally when a Save sub-handler has already set the
    status label and wants the outer Save to bail without dismissing."""
    pass


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Ask the user to confirm deletion."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def action_cancel(self) -> None:
        self.dismiss(False)

    def __init__(self, project_name: str) -> None:
        super().__init__()
        self.project_name = project_name

    def compose(self) -> ComposeResult:
        with Container(id="confirm-box"):
            yield Label(f"Delete project '{self.project_name}'?", id="confirm-title")
            yield Label("It will be moved to .deleted/  and can be recovered manually.",
                        id="confirm-sub")
            with Horizontal(id="confirm-buttons"):
                yield Button("Delete", variant="error",   id="btn-yes")
                yield Button("Cancel", variant="primary", id="btn-no")

    @on(Button.Pressed, "#btn-yes")
    def confirmed(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#btn-no")
    def cancelled(self) -> None:
        self.dismiss(False)


class SSHKeyUnlockScreen(ModalScreen[None]):
    """Prompt for the SSH key passphrase and load it into ssh-agent.

    Triggered at TUI startup when ~/.ssh/id_ed25519 is encrypted but the
    running ssh-agent doesn't have it yet.
    """

    BINDINGS = [Binding("escape", "skip", show=False)]

    def action_skip(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Container(id="unlock-box"):
            yield Label("Unlock SSH key", id="unlock-title")
            yield Label(
                "Your ~/.ssh/id_ed25519 is passphrase-protected. Enter the\n"
                "passphrase to load it into ssh-agent so git push, gh, etc.\n"
                "work without prompting later.",
                id="unlock-help",
            )
            yield Input(placeholder="passphrase", id="unlock-pass", password=True)
            yield Label("", id="unlock-status")
            with Horizontal(id="unlock-buttons"):
                yield Button("Unlock", variant="success", id="btn-unlock-yes")
                yield Button("Skip",   variant="warning", id="btn-unlock-no")

    def on_mount(self) -> None:
        self.query_one("#unlock-pass", Input).focus()

    def _try_unlock(self) -> None:
        passphrase = self.query_one("#unlock-pass", Input).value
        if not passphrase:
            self.query_one("#unlock-status", Label).update(
                "[bold red]Enter a passphrase first.[/]"
            )
            return
        # Disable inputs and show a "working" message — ssh-add can take
        # a few seconds, especially if the agent has to be (re)started.
        # Run it in a worker thread so the UI stays responsive.
        for sel in ("#unlock-pass", "#btn-unlock-yes", "#btn-unlock-no"):
            try:
                self.query_one(sel).disabled = True
            except Exception:
                pass
        self.query_one("#unlock-status", Label).update("Adding key to agent…")
        self.run_worker(
            lambda: self._unlock_worker(passphrase),
            exclusive=True, thread=True,
        )

    def _unlock_worker(self, passphrase: str) -> None:
        ok, msg = ssh_add_with_passphrase(SSH_KEY_PATH, passphrase)
        # Hop back to the UI thread to update widgets / dismiss.
        self.app.call_from_thread(self._unlock_done, ok, msg)

    def _unlock_done(self, ok: bool, msg: str) -> None:
        if ok:
            self.app.notify("SSH key added to agent.")
            self.dismiss(None)
            return
        # Re-enable controls so the user can retry.
        for sel in ("#unlock-pass", "#btn-unlock-yes", "#btn-unlock-no"):
            try:
                self.query_one(sel).disabled = False
            except Exception:
                pass
        last = (msg.splitlines()[-1] if msg else "ssh-add failed").strip()
        self.query_one("#unlock-status", Label).update(
            f"[bold red]{last}[/]"
        )
        # Re-focus the passphrase field for retry.
        try:
            self.query_one("#unlock-pass", Input).focus()
        except Exception:
            pass

    @on(Button.Pressed, "#btn-unlock-yes")
    def on_unlock(self) -> None:
        self._try_unlock()

    @on(Input.Submitted, "#unlock-pass")
    def on_submit(self) -> None:
        self._try_unlock()

    @on(Button.Pressed, "#btn-unlock-no")
    def on_skip(self) -> None:
        self.dismiss(None)


class NewProjectScreen(ModalScreen[str | None]):
    """Prompt for a new project name."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def action_cancel(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Container(id="new-box"):
            yield Label("New Project", id="new-title")
            yield Label("Spaces are replaced with hyphens automatically.", id="new-sub")
            yield Input(placeholder="project-name", id="new-input")
            with Horizontal(id="new-buttons"):
                yield Button("Create", variant="success", id="btn-create")
                yield Button("Cancel", variant="primary", id="btn-cancel")

    @on(Button.Pressed, "#btn-create")
    def create(self) -> None:
        raw = self.query_one("#new-input", Input).value.strip()
        name = slugify(raw)
        self.dismiss(name if name else None)

    @on(Button.Pressed, "#btn-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def submitted(self) -> None:
        self.create()


class GitSetupWizard(ModalScreen[bool]):
    """Multi-step wizard that walks the user through Git/SSH/GPG setup.

    Steps:
        1. GitHub identity (username → fetch name + email)
        2. SSH key (generate or detect, then paste pubkey at github.com)
        3. Authenticate the gh CLI via web flow (TUI suspends).
           When gh auth succeeds, the wizard also silently generates an
           ed25519 GPG signing key (no passphrase) and uploads it via
           `gh gpg-key add` — no GPG step is shown to the user.
        4. Final config + keychain
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    STEP_TITLES = (
        "Step 1 of 4 — GitHub Identity",
        "Step 2 of 4 — SSH Key",
        "Step 3 of 4 — Authenticate gh CLI",
        "Step 4 of 4 — Git Config & Keychain",
    )

    def __init__(self) -> None:
        super().__init__()
        self.step = 0
        # Carried across steps:
        self.username = ""
        self.full_name = git_config_get("user.name")
        self.email     = git_config_get("user.email")
        self.ssh_key_path: str | None = None
        self.ssh_passphrase = ""
        self.gpg_key_id: str | None = None
        self.gpg_passphrase = ""
        self.gpg_uploaded = False
        self._dismissed = False

    # ── Top-level layout ────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Container(id="wiz-box"):
            yield Label(self.STEP_TITLES[0], id="wiz-title")
            yield Container(id="wiz-body")
            yield Log(id="wiz-log", auto_scroll=True, max_lines=200)
            with Horizontal(id="wiz-buttons"):
                yield Button("Back",   id="wiz-back",   variant="default")
                yield Button("Skip",   id="wiz-skip",   variant="warning")
                yield Button("Next",   id="wiz-next",   variant="success")
                yield Button("Cancel", id="wiz-cancel", variant="error")

    async def on_mount(self) -> None:
        await self._render_step()

    def action_cancel(self) -> None:
        if not self._dismissed:
            self._dismissed = True
            self.dismiss(False)

    # ── Step rendering ──────────────────────────────────────────────

    def _set_buttons(self, *, back=True, skip=False, next_label="Next") -> None:
        self.query_one("#wiz-back",  Button).disabled = not back
        skip_btn = self.query_one("#wiz-skip", Button)
        skip_btn.display = skip
        self.query_one("#wiz-next",  Button).label = next_label

    def _log(self, msg: str) -> None:
        log = self.query_one("#wiz-log", Log)
        log.write_line(msg)
        # auto_scroll=True on the Log already calls scroll_end after each
        # write_line, but doing it explicitly here is belt-and-suspenders
        # for the case where writes come from a worker thread and the
        # widget hasn't measured yet.
        log.scroll_end(animate=False)

    async def _render_step(self) -> None:
        # IMPORTANT: remove_children() returns an AwaitRemove and only
        # actually detaches the old widgets when awaited. Without the
        # await, mounting fresh widgets with the same id would collide
        # with the still-mounted old ones (DuplicateIds at runtime).
        self.query_one("#wiz-title", Label).update(self.STEP_TITLES[self.step])
        body = self.query_one("#wiz-body", Container)
        await body.remove_children()
        if self.step == 0:
            self._render_identity(body)
            self._set_buttons(back=False, skip=False, next_label="Next")
        elif self.step == 1:
            self._render_ssh(body)
            self._set_buttons(back=True, skip=True, next_label="Next")
        elif self.step == 2:
            self._render_gh_auth(body)
            self._set_buttons(back=True, skip=True, next_label="Next")
        elif self.step == 3:
            self._render_final(body)
            self._set_buttons(back=True, skip=False, next_label="Finish")
        # Park focus on the most useful widget for the current step,
        # after mount has flushed.
        self.call_after_refresh(self._focus_step_default)

    def _focus_step_default(self) -> None:
        candidates = {
            0: ["#wiz-username"],
            1: ["#wiz-ssh-pass", "#wiz-ssh-pass-verify"],
            2: ["#wiz-gh-auth"],
            3: ["#wiz-next"],
        }
        for sel in candidates.get(self.step, []):
            try:
                self.query_one(sel).focus()
                return
            except Exception:
                continue

    @staticmethod
    def _link(url: str) -> Text:
        """Build a clickable URL fragment without going through the
        markup parser (which doesn't accept unquoted colons in values)."""
        return Text(url, style=f"link {url} #72c09a")

    # Step 1: GitHub identity
    def _render_identity(self, body: Container) -> None:
        msg = Text(
            "Enter your GitHub username and press Enter — we'll fetch your "
            "public name and email from github.com automatically.\n"
        )
        msg.append("No account yet? Sign up at ")
        msg.append_text(self._link("https://github.com/signup"))
        msg.append(" (Ctrl+click).")
        body.mount(Label(msg))
        body.mount(Input(placeholder="github-username", id="wiz-username",
                         value=self.username))
        body.mount(Button("Look up", id="wiz-lookup", variant="primary"))

        # Confirmation/override fields — hidden until the API lookup succeeds.
        # The user sees them only when there's something to confirm.
        already_have = bool(self.full_name and self.email)
        name_label = Label("Full name:", id="wiz-name-label")
        name_input = Input(placeholder="Your Name", id="wiz-name",
                           value=self.full_name)
        email_label = Label("Email:", id="wiz-email-label")
        email_input = Input(placeholder="you@example.com", id="wiz-email",
                            value=self.email)
        for w in (name_label, name_input, email_label, email_input):
            w.display = already_have
        body.mount(name_label)
        body.mount(name_input)
        body.mount(email_label)
        body.mount(email_input)

    # Step 2: SSH key
    def _render_ssh(self, body: Container) -> None:
        existing = existing_ssh_key()
        self.ssh_key_path = existing
        if existing:
            pub = read_ssh_pubkey(existing)
            body.mount(Label(Text.from_markup(
                f"Found existing SSH key: [bold]{existing}[/]\n"
                "Public key (already on disk — copy this if you haven't yet "
                "added it to GitHub):"
            )))
            body.mount(TextArea(pub, id="wiz-ssh-pub", read_only=True,
                                show_line_numbers=False))
            paste_msg = Text("Paste the public key at ")
            paste_msg.append_text(self._link("https://github.com/settings/ssh/new"))
            paste_msg.append_text(Text.from_markup(
                " (Ctrl+click), then press [bold]Verify[/]."
            ))
            body.mount(Label(paste_msg))
            body.mount(Label("Passphrase (only if your key has one):"))
            body.mount(Input(placeholder="passphrase", id="wiz-ssh-pass-verify",
                             password=True, value=self.ssh_passphrase))
            body.mount(Horizontal(
                Button("Verify GitHub auth", id="wiz-ssh-verify", variant="primary"),
                id="wiz-ssh-actions",
            ))
        else:
            body.mount(Label("No SSH key found at ~/.ssh/id_ed25519."))
            body.mount(Label(
                "Set a passphrase (strongly recommended), confirm it,"
            ))
            body.mount(Label("then click Generate ed25519 key."))
            body.mount(Label("Passphrase (leave blank for none):"))
            body.mount(Input(placeholder="passphrase", id="wiz-ssh-pass",
                             password=True))
            body.mount(Label("Confirm passphrase:"))
            body.mount(Input(placeholder="confirm",   id="wiz-ssh-pass2",
                             password=True))
            body.mount(Horizontal(
                Button("Generate ed25519 key", id="wiz-ssh-gen", variant="success"),
                id="wiz-ssh-actions",
            ))

    # Step 3: Authenticate the gh CLI
    def _render_gh_auth(self, body: Container) -> None:
        already = gh_is_authed()
        if not shutil.which("gh"):
            body.mount(Label(Text.from_markup(
                "[yellow]gh CLI is not installed.[/]\n\n"
                "Run [bold]mom install -y gh[/] in another terminal, then "
                "come back. Or click [bold]Skip[/] to skip GitHub auth."
            )))
            return
        if already:
            body.mount(Label(Text.from_markup(
                "[green]✓ gh CLI is already authenticated.[/]\n\n"
                "Click [bold]Next[/] to move on. Use "
                "[bold]Re-authenticate[/] to switch accounts."
            )))
        else:
            tip = Text(
                "We'll run `gh auth login --web --skip-ssh-key`. The TUI will "
                "suspend; gh prints a URL and a one-time code in the terminal.\n"
                "Ctrl+click the URL to open it on the Windows side, paste the "
                "code, and return here.\n\n"
                "When you come back, the wizard will silently generate an "
                "ed25519 GPG signing key (no passphrase) and upload it via gh."
            )
            body.mount(Label(tip))
        body.mount(Horizontal(
            Button("Re-authenticate" if already else "Authenticate now",
                   id="wiz-gh-auth", variant="primary"),
            id="wiz-gh-actions",
        ))

    # Step 4: Final config + keychain
    def _render_final(self, body: Container) -> None:
        body.mount(Label(
            "Finalise the setup:\n"
            f"  • git config user.name = [bold]{self.full_name}[/]\n"
            f"  • git config user.email = [bold]{self.email}[/]\n"
            "  • init.defaultBranch = [bold]main[/]\n"
            "  • Install [bold]keychain[/] via mom (if missing)\n"
            "  • Add a keychain block to ~/.bashrc\n\n"
            "Click [bold]Finish[/] to apply, or [bold]Back[/] to revisit a step."
        ))

    # ── Button dispatch ─────────────────────────────────────────────

    @on(Button.Pressed, "#wiz-cancel")
    def on_cancel(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#wiz-back")
    async def on_back(self) -> None:
        if self.step > 0:
            self.step -= 1
            await self._render_step()

    @on(Button.Pressed, "#wiz-skip")
    async def on_skip(self) -> None:
        self._log(f"[skipped] {self.STEP_TITLES[self.step]}")
        await self._advance()

    @on(Button.Pressed, "#wiz-next")
    async def on_next(self) -> None:
        if self.step == 0:
            await self._capture_identity_then_advance()
        elif self.step == 1:
            await self._advance_if_ssh_ready()
        elif self.step == 2:
            await self._advance_if_gh_authed()
        elif self.step == 3:
            self._finish()

    async def _advance(self) -> None:
        if self.step < 3:
            self.step += 1
            await self._render_step()

    # ── Step 1 actions ──────────────────────────────────────────────

    @on(Button.Pressed, "#wiz-lookup")
    def on_lookup_button(self) -> None:
        self._do_github_lookup()

    @on(Input.Submitted, "#wiz-username")
    def on_username_submitted(self) -> None:
        # Pressing Enter in the username input triggers the lookup.
        self._do_github_lookup()

    def _do_github_lookup(self) -> None:
        username = self.query_one("#wiz-username", Input).value.strip()
        if not username:
            self._log("Enter a GitHub username first.")
            return
        self._log(f"Looking up github.com/{username}…")
        info = fetch_github_user(username)
        if info is None:
            self._log(f"User '{username}' not found. Create one at "
                      "https://github.com/signup")
            return
        self.username   = info["login"]
        self.full_name  = info["name"]
        self.email      = info["email"]
        # Reveal & populate the confirmation fields.
        for wid in ("#wiz-name-label", "#wiz-name",
                    "#wiz-email-label", "#wiz-email"):
            self.query_one(wid).display = True
        self.query_one("#wiz-name",  Input).value = info["name"]
        self.query_one("#wiz-email", Input).value = info["email"]
        noreply = "users.noreply.github.com" in info["email"]
        suffix = "  (email private — using GitHub noreply alias)" if noreply else ""
        self._log(f"Found: {info['name']} <{info['email']}>"
                  f"  →  {info['html_url']}{suffix}")

    async def _capture_identity_then_advance(self) -> None:
        self.username  = self.query_one("#wiz-username", Input).value.strip()
        self.full_name = self.query_one("#wiz-name",     Input).value.strip()
        self.email     = self.query_one("#wiz-email",    Input).value.strip()
        if not self.full_name or not self.email:
            self._log("Name and email are required to continue.")
            return
        await self._advance()

    # ── Step 2 actions ──────────────────────────────────────────────

    @on(Button.Pressed, "#wiz-ssh-gen")
    async def on_ssh_gen(self) -> None:
        p1 = self.query_one("#wiz-ssh-pass",  Input).value
        p2 = self.query_one("#wiz-ssh-pass2", Input).value
        if p1 != p2:
            self._log("Passphrases do not match.")
            return
        self._log("Generating ed25519 SSH key…")
        ok, msg = generate_ssh_key(p1, comment=self.email or self.username or "maude")
        self._log(msg)
        if ok:
            self.ssh_key_path   = str(SSH_KEY_PATH)
            self.ssh_passphrase = p1   # carry over so Verify can unlock the key
            await self._render_step()  # re-render to show the pubkey

    @on(Button.Pressed, "#wiz-ssh-verify")
    def on_ssh_verify(self) -> None:
        # Pull the passphrase from the verify-time input so users with an
        # existing passphrase-protected key can also test.
        try:
            self.ssh_passphrase = self.query_one("#wiz-ssh-pass-verify", Input).value
        except Exception:
            pass
        self._log("Running: ssh -T git@github.com …")
        ok, msg = verify_ssh_github(self.ssh_passphrase)
        self._log(msg)
        if ok:
            self._log("[green]✓ SSH auth to GitHub works.[/]")

    async def _advance_if_ssh_ready(self) -> None:
        if not self.ssh_key_path:
            self._log("Generate an SSH key first, or click Skip.")
            return
        await self._advance()

    # ── Step 3 actions: gh CLI auth + silent GPG provisioning ──────

    @on(Button.Pressed, "#wiz-gh-auth")
    def on_gh_auth(self) -> None:
        if not shutil.which("gh"):
            self._log("gh CLI is not installed (try `mom install -y gh`).")
            return
        # Run gh in a worker thread and stream its stdout into the Log
        # widget. Suspending the TUI doesn't work reliably here — gh's
        # interactive "Press Enter" prompt closes immediately on EOF —
        # so we keep the TUI up, feed Enter on stdin ourselves, and let
        # the user Ctrl+click the URL straight from the wizard log.
        self.query_one("#wiz-gh-auth", Button).disabled = True
        self._log("─────────────── How this works ───────────────")
        self._log("1. gh prints a one-time code below (e.g. ABCD-1234)")
        self._log("2. On your Windows host, open this URL (Ctrl+click):")
        self._log("     https://github.com/login/device")
        self._log("3. Paste the code, then click 'Authorize GitHub CLI'")
        self._log("4. Come back here — gh detects the auth automatically")
        self._log("─────────────────────────────────────────────")
        self.run_worker(self._gh_auth_worker, exclusive=True, thread=True)

    def _gh_auth_worker(self) -> None:
        """Spawn gh, stream output to the Log, and call back when it exits.

        Older gh versions don't support `--skip-ssh-key`. To suppress
        the "Upload your SSH public key?" prompt we temporarily move
        the .pub file aside while gh runs, then restore it. Step 2
        already uploaded it manually, so gh has nothing to do here
        beyond authenticating.
        """
        pub      = SSH_KEY_PATH.with_suffix(".pub")
        pub_tmp  = pub.with_suffix(".pub.maude-tmp")
        moved    = False
        if pub.exists():
            try:
                pub.rename(pub_tmp)
                moved = True
            except OSError:
                pass
        # Output we don't want cluttering the log: xdg-open / wslview etc.
        # exhausting their list of "browser not found" candidates.
        noise_substrings = (
            "xdg-open:",
            "x-www-browser",
            "www-browser",
            "firefox", "iceweasel", "seamonkey", "mozilla",
            "epiphany", "konqueror", "chromium", "google-chrome",
            "links2", "elinks", "links", "lynx", "w3m",
            "no method available for opening",
        )
        try:
            try:
                proc = subprocess.Popen(
                    ["gh", "auth", "login",
                     "--hostname", "github.com",
                     "--git-protocol", "ssh",
                     "--web",
                     # Request the scope needed to upload a GPG signing
                     # key after auth completes.
                     "--scopes", "write:gpg_key"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
            except FileNotFoundError as err:
                self.app.call_from_thread(self._log, f"gh failed to launch: {err}")
                self.app.call_from_thread(self._gh_auth_done, 1)
                return
            # gh prints the one-time code, then "Press Enter to open browser".
            # Send a newline so it proceeds past that prompt to the polling
            # phase. xdg-open will fail (no browser inside WSL) but gh keeps
            # polling until the user completes auth in their Windows browser.
            if proc.stdin:
                try:
                    proc.stdin.write("\n")
                    proc.stdin.flush()
                    proc.stdin.close()
                except OSError:
                    pass
            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    line = line.rstrip()
                    if not line:
                        continue
                    if any(s in line for s in noise_substrings):
                        continue
                    self.app.call_from_thread(self._log, line)
            rc = proc.wait()
        finally:
            if moved:
                try:
                    pub_tmp.rename(pub)
                except OSError:
                    pass
        self.app.call_from_thread(self._gh_auth_done, rc)

    def _gh_auth_done(self, returncode: int) -> None:
        try:
            self.query_one("#wiz-gh-auth", Button).disabled = False
        except Exception:
            pass
        if gh_is_authed():
            self._log("[green]✓ gh CLI authenticated.[/]")
            self._provision_gpg_now()
        else:
            self._log(f"gh auth did not complete (exit code: {returncode}).")
        # Refresh the step so the button label flips to "Re-authenticate".
        self.app.call_after_refresh(self._rerender_current_step)

    def _rerender_current_step(self) -> None:
        # Schedule the async re-render from sync context.
        self.run_worker(self._render_step, exclusive=False)

    def _provision_gpg_now(self) -> None:
        """Silent ed25519 GPG signing key + auto-upload via gh."""
        if not shutil.which("gpg"):
            self._log("gpg is not installed; skipping signing key. "
                      "Install with `mom install -y gnupg` and re-run.")
            return
        if not self.gpg_key_id:
            existing_id = existing_gpg_key(self.email)
            if existing_id:
                self._log(f"Reusing existing GPG key for {self.email}: {existing_id}")
                self.gpg_key_id = existing_id
            else:
                self._log("Generating ed25519 GPG signing key (no passphrase)…")
                key_id, msg = generate_gpg_key(self.full_name, self.email, "")
                self._log(msg)
                if not key_id:
                    return
                self.gpg_key_id = key_id
        self._log("Uploading GPG public key via gh…")
        pub = export_gpg_pubkey(self.gpg_key_id)
        if not pub:
            self._log("Could not export GPG public key.")
            return
        ok, msg = gh_upload_key("gpg-key", pub, "Maude (TUI)")
        if ok:
            self._log("✓ " + msg)
            self.gpg_uploaded = True
        else:
            self._log("✗ " + msg)
            if "scope" in msg.lower() or "scopes" in msg.lower():
                self._log("Click 'Re-authenticate' above — the wizard now")
                self._log("requests write:gpg_key, which will let the upload succeed.")

    async def _advance_if_gh_authed(self) -> None:
        # If authed, ensure the GPG key is generated AND uploaded. Both
        # paths are idempotent, so it's safe to call again after a
        # re-auth that granted the missing scope.
        if gh_is_authed() and (not self.gpg_key_id or not self.gpg_uploaded):
            self._provision_gpg_now()
        await self._advance()

    # ── Step 4: finalise ────────────────────────────────────────────

    def _finish(self) -> None:
        self._log("Applying git config…")
        if self.full_name: git_config_set("user.name",          self.full_name)
        if self.email:     git_config_set("user.email",         self.email)
        git_config_set("init.defaultBranch", "main")
        if self.gpg_key_id:
            git_config_set("user.signingkey", self.gpg_key_id)
            git_config_set("commit.gpgsign",  "true")
            self._log(f"  user.signingkey = {self.gpg_key_id}, commit.gpgsign = true")

        self._log("Installing keychain via mom (if missing)…")
        ok, msg = install_keychain_via_mom()
        self._log(msg)

        self._log("Wiring keychain into ~/.bashrc…")
        if add_keychain_to_bashrc():
            self._log("  ✓ ~/.bashrc updated.")
        else:
            self._log("  ! could not update ~/.bashrc (already present or read-only).")

        self._log("[green]✓ Setup complete.[/]")
        self._dismissed = True
        self.dismiss(True)


class PushToGithubScreen(ModalScreen[bool]):
    """Modal that creates a GitHub repo + pushes the current project to it.

    Two states:
      - gh CLI not authed → show a hint pointing at "Setup Git(hub)" and only Cancel.
      - gh CLI authed     → input field pre-filled with
        `git@github.com:<login>/<project>.git`, plus Push and Cancel buttons.
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, project_path: Path) -> None:
        super().__init__()
        self.project_path = project_path
        self.gh_authed    = gh_is_authed()
        self.gh_login     = gh_user_login() if self.gh_authed else ""
        self.default_url  = (
            f"git@github.com:{self.gh_login or 'YOUR-USER'}/"
            f"{project_path.name}.git"
        )
        self._busy = False

    def action_cancel(self) -> None:
        if not self._busy:
            self.dismiss(False)

    def compose(self) -> ComposeResult:
        with Container(id="push-box"):
            yield Label("Push project to GitHub", id="push-title")
            if not self.gh_authed:
                yield Label(
                    "gh CLI is not authenticated.\n\n"
                    "Click [bold]Setup Git(hub)[/] in the bottom bar to "
                    "authenticate first, then come back.",
                    id="push-help",
                )
                yield Label("", id="push-status")
                with Horizontal(id="push-buttons"):
                    yield Button("Close", variant="primary", id="btn-push-cancel")
                return
            existing = git_remote_origin_url(self.project_path)
            if existing:
                yield Label(Text.from_markup(
                    f"This project already has a remote: [cyan]{existing}[/]\n"
                    "Pushing again will [bold]git push -u origin HEAD[/]."
                ), id="push-help")
            else:
                yield Label(
                    f"Create a new GitHub repo for "
                    f"[bold]{self.project_path.name}[/] and push HEAD.",
                    id="push-help",
                )
            yield Label("Repository URL:")
            yield Input(value=self.default_url, id="push-url")
            yield Checkbox("Public repo (unchecked = private)",
                           id="push-public", value=False)
            yield Label("", id="push-status")
            with Horizontal(id="push-buttons"):
                yield Button("Push",   variant="success", id="btn-push-go")
                yield Button("Cancel", variant="primary", id="btn-push-cancel")

    def on_mount(self) -> None:
        if self.gh_authed:
            try:
                self.query_one("#push-url", Input).focus()
            except Exception:
                pass

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#push-status", Label).update(text)
        except Exception:
            pass

    @on(Button.Pressed, "#btn-push-cancel")
    def cancel(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#btn-push-go")
    def go(self) -> None:
        if self._busy:
            return
        url = self.query_one("#push-url", Input).value.strip()
        owner_repo = parse_github_owner_repo(url)
        if not owner_repo:
            self._set_status(
                "[bold red]URL must be git@github.com:owner/repo.git[/]"
            )
            return
        owner, repo = owner_repo
        public = False
        try:
            public = self.query_one("#push-public", Checkbox).value
        except Exception:
            pass
        visibility = "public" if public else "private"
        self._busy = True
        for sel in ("#btn-push-go", "#btn-push-cancel", "#push-url"):
            try:
                self.query_one(sel).disabled = True
            except Exception:
                pass
        self._set_status("Pushing… (creating repo if needed)")
        self.run_worker(
            lambda: self._push_worker(owner, repo, url, visibility),
            exclusive=True, thread=True,
        )

    def _push_worker(self, owner: str, repo: str,
                     url: str, visibility: str) -> None:
        ok, msg = self._push_now(owner, repo, url, visibility)
        self.app.call_from_thread(self._push_done, ok, msg)

    def _push_now(self, owner: str, repo: str,
                  url: str, visibility: str) -> tuple[bool, str]:
        cwd = str(self.project_path)
        # Guard against the SSH-key passphrase trap. If the user has an
        # encrypted SSH key but no agent has it loaded, both
        # `gh repo create --push` and `git push` will block forever
        # waiting for a passphrase prompt that no one can type. We
        # short-circuit with a clear error so the caller can pop the
        # unlock modal instead.
        if (SSH_KEY_PATH.exists()
                and (not ssh_agent_running()
                     or not ssh_key_in_agent(SSH_KEY_PATH))
                and ssh_key_has_passphrase(SSH_KEY_PATH)):
            return False, ("ssh key needs to be unlocked first — "
                           "exit the TUI and run `menu` again, or "
                           "click Setup Git(hub) to load the key.")
        # Force ssh into batch mode for *this* push so a misconfigured
        # state errors out fast (5–10s) instead of blocking on a
        # passphrase prompt for 60+s.
        push_env = {
            **os.environ,
            "GIT_SSH_COMMAND": "ssh -o BatchMode=yes "
                               "-o StrictHostKeyChecking=accept-new",
        }
        # If the project has no README.md (any case), seed one with the
        # repo name as the H1 so GitHub has something to render.
        readme_present = any(
            (self.project_path / name).exists()
            for name in ("README.md", "readme.md", "Readme.md", "README.MD")
        )
        if not readme_present:
            try:
                (self.project_path / "README.md").write_text(f"# {repo}\n")
            except OSError:
                pass  # not fatal — push will still work without it
        # Make sure the project has at least one commit, otherwise gh
        # repo create --push has nothing to send.
        log = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if log.returncode != 0:
            # No commits yet — try to make an initial one from existing files.
            subprocess.run(["git", "-C", cwd, "add", "-A"], capture_output=True)
            commit = subprocess.run(
                ["git", "-C", cwd, "commit", "-m", "Initial commit",
                 "--allow-empty"],
                capture_output=True, text=True, timeout=15,
            )
            if commit.returncode != 0:
                return False, (commit.stderr or commit.stdout).strip() or \
                              "could not create initial commit"
        elif not readme_present:
            # Project already has commits but no README — stage and
            # commit the new file so the push includes it.
            subprocess.run(
                ["git", "-C", cwd, "add", "README.md"],
                capture_output=True, text=True, timeout=5,
            )
            subprocess.run(
                ["git", "-C", cwd, "commit", "-m", "Add README.md"],
                capture_output=True, text=True, timeout=15,
            )

        # Does the remote already exist on GitHub?
        check = subprocess.run(
            ["gh", "repo", "view", f"{owner}/{repo}"],
            capture_output=True, text=True, timeout=15,
        )
        if check.returncode != 0:
            create = subprocess.run(
                ["gh", "repo", "create", f"{owner}/{repo}",
                 f"--{visibility}", "--source", cwd, "--remote", "origin",
                 "--push"],
                capture_output=True, text=True, timeout=180, env=push_env,
            )
            if create.returncode != 0:
                return False, (create.stderr or create.stdout).strip() or \
                              "gh repo create failed"
            return True, f"Created {owner}/{repo} and pushed HEAD."
        # Repo exists. Make sure origin is wired up, then push.
        existing = git_remote_origin_url(self.project_path)
        if not existing:
            r = subprocess.run(
                ["git", "-C", cwd, "remote", "add", "origin", url],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return False, (r.stderr or r.stdout).strip()
        elif existing != url:
            r = subprocess.run(
                ["git", "-C", cwd, "remote", "set-url", "origin", url],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return False, (r.stderr or r.stdout).strip()
        push = subprocess.run(
            ["git", "-C", cwd, "push", "-u", "origin", "HEAD"],
            capture_output=True, text=True, timeout=180, env=push_env,
        )
        if push.returncode != 0:
            return False, (push.stderr or push.stdout).strip()
        return True, f"Pushed to {owner}/{repo}."

    def _push_done(self, ok: bool, msg: str) -> None:
        self._busy = False
        if ok:
            self.app.notify(msg)
            self.dismiss(True)
            return
        # Re-enable controls for retry.
        for sel in ("#btn-push-go", "#btn-push-cancel", "#push-url"):
            try:
                self.query_one(sel).disabled = False
            except Exception:
                pass
        # If the failure was the SSH-key-not-loaded case, auto-pop the
        # unlock modal so the user can fix it without leaving the wizard.
        if "ssh key needs to be unlocked" in msg.lower():
            self._set_status("[yellow]SSH key not loaded — unlock first.[/]")
            ensure_ssh_agent()
            self.app.push_screen(SSHKeyUnlockScreen())
            return
        last = (msg.splitlines()[-1] if msg else "push failed").strip()
        self._set_status(f"[bold red]{last}[/]")


class DetachFromGithubScreen(ModalScreen[bool]):
    """Confirm and remove the local `origin` remote so this project
    stops pushing to GitHub. Does NOT delete the GitHub-side repo;
    that has to be done manually on github.com."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, project_path: Path) -> None:
        super().__init__()
        self.project_path = project_path
        self.origin_url   = git_remote_origin_url(project_path)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def compose(self) -> ComposeResult:
        with Container(id="detach-box"):
            yield Label("Stop pushing to GitHub?", id="detach-title")
            yield Label(Text.from_markup(
                f"This project's git remote points at:\n"
                f"  [cyan]{self.origin_url}[/]\n\n"
                "Confirming will remove the local [bold]origin[/] remote so "
                "future pushes won't reach GitHub.\n\n"
                "[bold yellow]The GitHub-side repository is NOT deleted by "
                "this action.[/]\n"
                "It becomes [bold]orphaned[/] — you have to delete it "
                "yourself at:\n"
                "  https://github.com/<owner>/<repo>/settings → 'Delete this "
                "repository'."
            ), id="detach-help")
            yield Label("", id="detach-status")
            with Horizontal(id="detach-buttons"):
                yield Button("Stop pushing", variant="error",   id="btn-detach-go")
                yield Button("Cancel",       variant="primary", id="btn-detach-cancel")

    @on(Button.Pressed, "#btn-detach-cancel")
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#btn-detach-go")
    def go(self) -> None:
        cwd = str(self.project_path)
        try:
            r = subprocess.run(
                ["git", "-C", cwd, "remote", "remove", "origin"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as err:
            self.query_one("#detach-status", Label).update(
                f"[bold red]{err}[/]"
            )
            return
        if r.returncode != 0 and "No such remote" not in r.stderr:
            last = (r.stderr or r.stdout).strip().splitlines()[-1]
            self.query_one("#detach-status", Label).update(
                f"[bold red]{last}[/]"
            )
            return
        self.app.notify(
            "origin removed. The GitHub repo is orphaned — delete it "
            "manually on github.com.",
            timeout=10,
        )
        self.dismiss(True)


# ── Main app ───────────────────────────────────────────────────────────────

class MaudeApp(App):
    """Maude TUI — project launcher for Claude Code."""

    CSS = """
    /* ── Claude Code style: light gray base, dusty rose accents ── */

    Screen {
        background: #1e1e1e;
    }

    Header {
        background: #2a2a2a;
        color: #d4a0a0;
    }

    Footer {
        background: #2a2a2a;
        color: #a09090;
    }

    #layout {
        height: 1fr;
    }

    #sidebar {
        width: 40;
        padding: 1 1;
        background: #242424;
        border-right: solid #b87878;
    }

    #logo {
        height: 5;
        color: #72c09a;
        text-style: bold;
        margin-bottom: 1;
    }

    #divider {
        color: #6a5058;
        height: 1;
        margin-bottom: 1;
    }

    #autostart-label {
        color: #c09898;
        margin-top: 1;
        margin-bottom: 0;
    }

    #autostart {
        margin-top: 0;
    }

    #fresh-label {
        color: #c09898;
        margin-top: 1;
        margin-bottom: 0;
    }

    #fresh-context {
        margin-top: 0;
    }

    #divider2 {
        color: #6a5058;
        height: 1;
        margin-top: 1;
        margin-bottom: 1;
    }

    #tips-title {
        color: #d4a0a0;
        text-style: bold;
        margin-bottom: 0;
    }

    #tips {
        color: #a09090;
    }

    #divider3 {
        color: #6a5058;
        height: 1;
        margin-top: 1;
        margin-bottom: 1;
    }

    #model-label {
        color: #d4a0a0;
        text-style: bold;
        margin-bottom: 0;
    }

    #model-select {
        background: #242424;
        border: none;
        padding: 0;
        height: auto;
    }

    #model-select:focus-within {
        background: #2a2424;
    }

    /* Tame Textual's default blue toggle/focus accents → warm greys */
    Checkbox, RadioButton {
        background: #242424;
        color: #c09898;
    }

    Checkbox:focus, RadioButton:focus {
        background: #2a2424;
        color: #f0d0d0;
    }

    Checkbox > .toggle--button,
    RadioButton > .toggle--button {
        background: #3a3030;
        color: #d4a0a0;
    }

    Checkbox.-on > .toggle--button,
    RadioButton.-on > .toggle--button {
        color: #72c09a;
    }

    #main {
        padding: 1 2;
    }

    #section-title {
        color: #d4a0a0;
        text-style: bold;
        margin-bottom: 1;
    }

    #projects-table {
        height: 1fr;
        border: solid #b87878;
    }

    DataTable > .datatable--header {
        color: #d4a0a0;
    }

    DataTable > .datatable--cursor {
        background: #383030;
        color: #f0d8d8;
    }

    #bottom-bar {
        height: auto;
        min-height: 3;
        padding: 1 2;
        align: left middle;
        background: #242424;
        border-top: solid #b87878;
        margin-bottom: 1;
    }

    #bottom-bar Button {
        margin-right: 1;
        min-width: 14;
    }

    #btn-open { color: #f0c8c8; }
    #btn-new  { color: #d0b8b8; }
    #btn-web  { color: #c0a8a8; }
    #btn-cli  { color: #b09898; }

    #kanna-url {
        color: #72c09a;
        margin-left: 2;
        content-align: left middle;
        height: 100%;
    }

    /* Modal: confirm delete */
    ConfirmDeleteScreen {
        align: center middle;
    }

    #confirm-box {
        padding: 2 4;
        width: 60;
        height: auto;
        border: solid #c07070;
        background: $surface;
    }

    #confirm-title {
        text-style: bold;
        color: #e09090;
        margin-bottom: 1;
    }

    #confirm-sub {
        color: #c09898;
        margin-bottom: 2;
    }

    #confirm-buttons {
        height: auto;
        align: center middle;
    }

    #confirm-buttons Button {
        margin: 0 1;
    }

    /* Modal: new project */
    NewProjectScreen {
        align: center middle;
    }

    #new-box {
        padding: 2 4;
        width: 60;
        height: auto;
        border: solid #b87878;
        background: $surface;
    }

    #new-title {
        text-style: bold;
        color: #72c09a;
        margin-bottom: 1;
    }

    #new-sub {
        color: #c09898;
        margin-bottom: 1;
    }

    #new-input {
        margin-bottom: 2;
    }

    #new-buttons {
        height: auto;
        align: center middle;
    }

    #new-buttons Button {
        margin: 0 1;
    }

    /* Modal: credential entry */
    CredsEntryScreen {
        align: center middle;
        background: #1e1e1e 90%;
    }

    #creds-box {
        padding: 2 3;
        width: 90;
        height: 38;
        border: heavy #b87878;
        background: #242424;
    }

    #creds-title {
        text-style: bold;
        color: #d4a0a0;
        margin-bottom: 1;
    }

    #creds-tabs {
        height: auto;
        align: left middle;
        margin-bottom: 1;
    }

    #creds-tabs Button {
        margin-right: 1;
    }

    #creds-body {
        height: 1fr;
        overflow-y: auto;
    }

    #creds-body Label {
        color: #c09898;
        margin-top: 0;
        margin-bottom: 0;
    }

    #creds-body Input {
        margin-bottom: 1;
    }

    #creds-body TextArea {
        height: 10;
        border: solid #6a5058;
        background: #1e1e1e;
    }

    #creds-status {
        height: 1;
        color: #c09898;
        margin-top: 1;
    }

    #creds-default-bedrock {
        margin-top: 0;
        margin-bottom: 0;
    }

    #creds-buttons {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    #creds-buttons Button {
        margin: 0 1;
    }

    /* Modal: SSH key unlock */
    SSHKeyUnlockScreen {
        align: center middle;
        background: #1e1e1e 90%;
    }

    #unlock-box {
        padding: 2 3;
        width: 70;
        height: auto;
        border: heavy #b87878;
        background: #242424;
    }

    #unlock-title {
        text-style: bold;
        color: #d4a0a0;
        margin-bottom: 1;
    }

    #unlock-help {
        color: #c09898;
        margin-bottom: 1;
    }

    #unlock-status {
        height: 1;
        color: #c09898;
        margin-top: 1;
    }

    #unlock-buttons {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    #unlock-buttons Button {
        margin: 0 1;
    }

    /* Modal: Push to GitHub */
    PushToGithubScreen {
        align: center middle;
        background: #1e1e1e 90%;
    }

    #push-box {
        padding: 2 3;
        width: 90;
        height: 22;
        border: heavy #b87878;
        background: #242424;
    }

    #push-title {
        text-style: bold;
        color: #d4a0a0;
        margin-bottom: 1;
    }

    #push-help {
        color: #c09898;
        margin-bottom: 1;
    }

    #push-box Label {
        color: #c09898;
        margin-top: 0;
        margin-bottom: 0;
    }

    #push-box Input {
        margin-bottom: 1;
    }

    #push-public {
        margin-top: 0;
        margin-bottom: 1;
    }

    #push-status {
        height: 1;
        color: #c09898;
        margin-top: 1;
    }

    #push-buttons {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    #push-buttons Button {
        margin: 0 1;
    }

    /* Modal: Detach from GitHub */
    DetachFromGithubScreen {
        align: center middle;
        background: #1e1e1e 90%;
    }

    #detach-box {
        padding: 2 3;
        width: 86;
        height: auto;
        border: heavy #c07070;
        background: #2a1818;
    }

    #detach-title {
        text-style: bold;
        color: #ff8080;
        margin-bottom: 1;
    }

    #detach-help {
        color: #d8b0b0;
        margin-bottom: 1;
    }

    #detach-status {
        height: 1;
        color: #c09898;
        margin-top: 1;
    }

    #detach-buttons {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    #detach-buttons Button {
        margin: 0 1;
    }

    /* Modal: Git Setup Wizard */
    GitSetupWizard {
        align: center middle;
        background: #1e1e1e 90%;
    }

    #wiz-box {
        padding: 1 2;
        width: 100;
        height: 44;
        border: heavy #b87878;
        background: #242424;
    }

    #wiz-title {
        text-style: bold;
        color: #d4a0a0;
        margin-bottom: 1;
    }

    #wiz-body {
        height: 1fr;
        overflow-y: auto;
    }

    #wiz-body Label {
        color: #c0a8a8;
        margin-bottom: 1;
    }

    #wiz-body Input {
        margin-bottom: 1;
    }

    #wiz-body TextArea {
        height: 8;
        border: solid #6a5058;
        background: #1e1e1e;
        margin-bottom: 1;
    }

    #wiz-log {
        height: 10;
        border: solid #6a5058;
        background: #1e1e1e;
        color: #a09090;
        margin-top: 1;
        margin-bottom: 1;
    }

    #wiz-buttons {
        height: auto;
        align: right middle;
    }

    #wiz-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("enter", "open_selected", "Open", show=True),
        Binding("n",     "new_project",   "New",  show=True),
        Binding("d",     "delete_selected","Delete",show=True),
        Binding("q",     "quit_to_shell", "Quit", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._model = read_model()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Static(LOGO, id="logo", markup=False)
                yield Static("─" * 28, id="divider")
                yield Label("Start TUI with Maude", id="autostart-label")
                yield Checkbox("", value=not DISABLE_FLAG.exists(), id="autostart")
                yield Static("─" * 28, id="divider2")
                yield Label("Tips", id="tips-title")
                yield Static(
                    "Screen split:  Alt+Shift+Plus | Minus\n"
                    "Share image:   drop into ~/Maude\n"
                    "Voice dictate: Win+H (Windows mic)",
                    id="tips",
                )
                yield Static("─" * 28, id="divider3")
                yield Label("Claude model", id="model-label")
                with RadioSet(id="model-select"):
                    for m in MODELS:
                        yield RadioButton(m, value=(m == self._model))
                yield Label("Clear context (no history)", id="fresh-label")
                yield Checkbox("", value=False, id="fresh-context")
            with Vertical(id="main"):
                yield Label("Projects", id="section-title")
                yield DataTable(id="projects-table", cursor_type="row",
                                zebra_stripes=True)
        with Horizontal(id="bottom-bar"):
            yield Button("Open Project",    id="btn-open")
            yield Button("+ New",           id="btn-new")
            yield Button("Web UI",          id="btn-web")
            yield Button("To Github",       id="btn-togithub")
            yield Button("Setup Git(hub)",  id="btn-github")
            yield Button("Set Creds",       id="btn-creds")
            yield Button("Command Line",    id="btn-cli")
            yield Static("", id="kanna-url")
        yield Footer()

    def on_mount(self) -> None:
        self._kanna_proc: subprocess.Popen | None = None
        self._refresh_table()
        self.query_one("#projects-table", DataTable).focus()
        # Sync the model RadioSet's keyboard cursor with the actually-
        # pressed (saved) model. Textual tracks the cursor and the
        # pressed index separately; without this they drift on first
        # render and arrow-key navigation starts at the top of the list
        # instead of the selected option.
        self.call_after_refresh(self._sync_model_cursor)
        # If the user has a passphrase-protected SSH key that hasn't been
        # added to the agent yet, prompt for it now (before any git/gh
        # operation needs it). Defer until after the first refresh so the
        # main UI is visible underneath.
        self.call_after_refresh(self._maybe_prompt_ssh_unlock)

    def _sync_model_cursor(self) -> None:
        try:
            radioset = self.query_one("#model-select", RadioSet)
        except Exception:
            return
        try:
            idx = MODELS.index(self._model)
        except ValueError:
            return
        # `_selected` is the keyboard-cursor index. Move it via the
        # public `action_move_selection` if available, otherwise fall
        # back to the private attribute (Textual exposes both across
        # versions).
        try:
            radioset._selected = idx           # type: ignore[attr-defined]
        except Exception:
            pass
        radioset.refresh()

    def _maybe_prompt_ssh_unlock(self) -> None:
        if not SSH_KEY_PATH.exists():
            return
        # Start an agent if one isn't already exposed in the env. On a
        # fresh terminal `keychain` may not have run yet (the .bashrc
        # block executes before `maude tui`, but the wizard's keychain
        # block was just appended and won't take effect until the next
        # login). Spinning up our own ssh-agent is cheap and keeps git
        # push from blocking on a passphrase prompt.
        if not ensure_ssh_agent():
            return
        if ssh_key_in_agent(SSH_KEY_PATH):
            return  # already loaded
        if not ssh_key_has_passphrase(SSH_KEY_PATH):
            # Unencrypted key — load silently with empty passphrase.
            ssh_add_with_passphrase(SSH_KEY_PATH, "")
            return
        self.push_screen(SSHKeyUnlockScreen())

    def _refresh_table(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        table.clear(columns=True)
        table.add_columns("  Project", "Last modified", "GitHub")
        for proj in list_projects():
            table.add_row(
                f"  {proj['name']}",
                proj["modified"],
                "yes" if proj["github"] else "",
                key=proj["name"],
            )

    def _selected_project(self) -> Path | None:
        table = self.query_one("#projects-table", DataTable)
        if table.cursor_row < 0:
            return None
        row_key = table.get_row_at(table.cursor_row)
        name = str(row_key[0]).strip()
        path = PROJECTS_DIR / name
        return path if path.exists() else None

    # ── Actions ───────────────────────────────────────────────────────

    def action_open_selected(self) -> None:
        path = self._selected_project()
        if path:
            self._launch_project(path)

    def action_new_project(self) -> None:
        self.push_screen(NewProjectScreen(), self._on_new_project)

    def action_delete_selected(self) -> None:
        path = self._selected_project()
        if path:
            self.push_screen(ConfirmDeleteScreen(path.name), self._on_confirm_delete)

    def action_quit_to_shell(self) -> None:
        self.exit()

    # ── Button handlers ───────────────────────────────────────────────

    @on(Button.Pressed, "#btn-open")
    def btn_open(self) -> None:
        self.action_open_selected()

    @on(Button.Pressed, "#btn-new")
    def btn_new(self) -> None:
        self.action_new_project()

    @on(Button.Pressed, "#btn-web")
    def btn_web(self) -> None:
        btn = self.query_one("#btn-web", Button)
        # If kanna is (or appears to be) running, stop it forcefully.
        if self._kanna_proc is not None:
            stop_kanna(self._kanna_proc)
            self._kanna_proc = None
            btn.label = "Web UI"
            self.query_one("#kanna-url", Static).update("")
            return
        # Refuse to launch without credentials — pop the entry modal first.
        if not check_credentials():
            self.push_screen(CredsEntryScreen(), self._on_creds_for_web)
            return
        self._start_kanna()

    def _on_creds_for_web(self, saved: bool) -> None:
        if saved:
            self._start_kanna()

    def _start_kanna(self) -> None:
        # Make sure the port isn't held by a stale instance before launching.
        kill_port(KANNA_PORT)
        env = {**os.environ, **kanna_env()}
        # Send kanna's output to a temp log so we can surface errors if it
        # dies on startup. Use process_group=0 (new pgroup, same session)
        # so we can SIGTERM the whole tree on Stop without detaching kanna
        # from the controlling tty (start_new_session can break some Node
        # CLIs that expect a tty at startup).
        fd, log_path = tempfile.mkstemp(prefix="maude-kanna-", suffix=".log")
        self._kanna_log = Path(log_path)
        try:
            self._kanna_proc = subprocess.Popen(
                [KANNA_CMD, "--no-open"], env=env,
                stdin=subprocess.DEVNULL,
                stdout=fd, stderr=subprocess.STDOUT,
                process_group=0,
            )
        except FileNotFoundError as err:
            os.close(fd)
            self.notify(f"Could not launch kanna: {err}", severity="error",
                        timeout=10)
            return
        finally:
            os.close(fd)
        btn = self.query_one("#btn-web", Button)
        btn.label = "Stop Web UI"
        url = f"http://localhost:{KANNA_PORT}"
        label = Text("Web UI: ")
        label.append(url, style=f"link {url} #72c09a")
        label.append(" (Ctrl+Click)", style="#a09090")
        self.query_one("#kanna-url", Static).update(label)
        # Verify kanna is still alive after a moment; if it died, surface
        # the log instead of leaving the user with a dead "Stop Web UI".
        self.set_timer(1.5, self._check_kanna_started)

    def _check_kanna_started(self) -> None:
        if self._kanna_proc is None:
            return
        if self._kanna_proc.poll() is None:
            return  # still running, all good
        try:
            tail = self._kanna_log.read_text()[-1500:]
        except OSError:
            tail = "(log unavailable)"
        self._kanna_proc = None
        btn = self.query_one("#btn-web", Button)
        btn.label = "Web UI"
        self.query_one("#kanna-url", Static).update("")
        self.notify(
            f"kanna exited unexpectedly. Log tail:\n{tail}",
            severity="error", timeout=15,
        )

    @on(Button.Pressed, "#btn-creds")
    def btn_creds(self) -> None:
        self.push_screen(CredsEntryScreen(), self._on_creds_updated)

    def _on_creds_updated(self, saved: bool) -> None:
        if saved:
            self.notify("Credentials saved.")

    @on(Button.Pressed, "#btn-github")
    def btn_setup_git(self) -> None:
        self.push_screen(GitSetupWizard(), self._on_git_setup_done)

    def _on_git_setup_done(self, completed: bool) -> None:
        if completed:
            self.notify("Git setup complete.")
            # Brand-new key — the agent is empty in the current process.
            # Prompt now so subsequent `git push` in this session works.
            self.call_after_refresh(self._maybe_prompt_ssh_unlock)
        else:
            self.notify("Git setup cancelled.")

    @on(Button.Pressed, "#btn-togithub")
    def btn_togithub(self) -> None:
        path = self._selected_project()
        if path is None:
            self.notify("Select a project first.", severity="warning")
            return
        # If the project is already on GitHub, second click means "stop
        # pushing here". Otherwise it's the create/push flow.
        origin = git_remote_origin_url(path)
        if is_github_remote(origin):
            self.push_screen(DetachFromGithubScreen(path), self._on_detach_done)
        else:
            self.push_screen(PushToGithubScreen(path), self._on_push_done)

    def _on_push_done(self, pushed: bool) -> None:
        if pushed:
            self._refresh_table()

    def _on_detach_done(self, detached: bool) -> None:
        if detached:
            self._refresh_table()

    @on(Button.Pressed, "#btn-cli")
    def btn_cli(self) -> None:
        self.exit()

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        """Double-click / Enter on a row opens the project."""
        name = str(event.row_key.value).strip()
        path = PROJECTS_DIR / name
        if path.exists():
            self._launch_project(path)

    @on(Checkbox.Changed, "#autostart")
    def autostart_toggled(self, event: Checkbox.Changed) -> None:
        if event.value:
            DISABLE_FLAG.unlink(missing_ok=True)
            self.notify("TUI will launch automatically with Maude")
        else:
            DISABLE_FLAG.touch()
            self.notify("TUI auto-start disabled (text banner instead)")

    @on(RadioSet.Changed, "#model-select")
    def model_changed(self, event: RadioSet.Changed) -> None:
        self._model = str(event.pressed.label)
        save_model(self._model)
        self.query_one("#projects-table", DataTable).focus()

    # ── Callbacks ─────────────────────────────────────────────────────

    def _launch_project(self, path: Path) -> None:
        # Gate on credentials so we don't drop the user into a Claude Code
        # session that immediately fails on first turn.
        if not check_credentials():
            self._pending_project = path
            self.push_screen(CredsEntryScreen(), self._on_creds_for_project)
            return
        self._launch_project_now(path)

    def _on_creds_for_project(self, saved: bool) -> None:
        path = getattr(self, "_pending_project", None)
        self._pending_project = None
        if saved and path is not None:
            self._launch_project_now(path)

    def _launch_project_now(self, path: Path) -> None:
        name = path.name
        # Honour the sidebar checkbox: when checked, skip --continue so
        # Claude starts with a clean conversation.
        try:
            fresh = self.query_one("#fresh-context", Checkbox).value
        except Exception:
            fresh = False
        with self.suspend():
            open_project(path, self._model, fresh=fresh)
        self._refresh_table()
        self._select_project(name)

    def _select_project(self, name: str) -> None:
        """Move the table cursor to the row whose key is `name`, if it exists."""
        table = self.query_one("#projects-table", DataTable)
        try:
            index = table.get_row_index(name)
        except KeyError:
            index = -1
        if index >= 0:
            table.move_cursor(row=index)
        table.focus()

    def _on_new_project(self, name: str | None) -> None:
        if not name:
            return
        path = PROJECTS_DIR / name
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(path), "init", "--quiet"], check=False)
        self._refresh_table()
        self._launch_project(path)

    def _on_confirm_delete(self, confirmed: bool) -> None:
        if not confirmed:
            return
        path = self._selected_project()
        if path:
            soft_delete(path)
            self.notify(f"'{path.name}' moved to .deleted/")
            self._refresh_table()


# ── Entry point ────────────────────────────────────────────────────────────

def run_wizard_only() -> int:
    """Standalone wizard mode for `maude github` — opens just the wizard."""
    class _WizardApp(App):
        CSS = MaudeApp.CSS
        def on_mount(self) -> None:
            self.push_screen(GitSetupWizard(), lambda result: self.exit(0 if result else 1))
    return _WizardApp().run() or 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--github":
        sys.exit(run_wizard_only())
    if len(sys.argv) > 1 and sys.argv[1] == "--fix-bashrc":
        # Used by `maude update` to repair the orphan-fi syntax error
        # left in ~/.bashrc by an older buggy keychain stripper.
        changed, msg = fix_bashrc_orphan_fi()
        print(msg)
        sys.exit(0 if (changed or "already clean" in msg) else 1)
    maybe_self_update()
    app = MaudeApp()
    app.run()
