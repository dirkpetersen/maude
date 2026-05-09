#!/usr/bin/env python3
"""
maude.py — Textual TUI for the Maude sandbox.
Always launched via:  maude tui
"""

import json
import os
import re
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
    try:
        with urllib.request.urlopen(UPDATE_URL, timeout=10) as resp:
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
            projects.append({"name": p.name, "modified": modified,
                             "path": p, "mtime": mtime})
    projects.sort(key=lambda d: d["mtime"], reverse=True)
    return projects


def slugify(name: str) -> str:
    """Replace spaces with hyphens; keep letters, digits, dots, dashes, underscores."""
    name = name.replace(" ", "-")
    name = re.sub(r"[^a-zA-Z0-9._-]", "", name)
    return name.strip("-")


def open_project(project_path: Path, model: str) -> None:
    """Launch Claude Code for a project. Try --continue first, then fresh."""
    os.chdir(project_path)
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
        return {
            "login": data.get("login", username),
            "name":  data.get("name") or data.get("login", username),
            "email": data.get("email") or "",
            "html_url": data.get("html_url", f"https://github.com/{username}"),
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


def verify_ssh_github(timeout: int = 10) -> tuple[bool, str]:
    """Run `ssh -T git@github.com` and check for a successful auth line."""
    try:
        r = subprocess.run(
            ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new",
             "-o", "BatchMode=yes", "git@github.com"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as err:
        return False, str(err)
    output = (r.stdout + r.stderr).strip()
    # GitHub returns exit code 1 even on successful auth, with this message.
    if "successfully authenticated" in output.lower():
        return True, output
    return False, output or "no response from GitHub"


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


KEYCHAIN_BLOCK = """
# Maude: ssh-agent via keychain
if command -v keychain >/dev/null 2>&1; then
    if [[ -f "$HOME/.ssh/id_ed25519" ]]; then
        eval "$(keychain --quiet --eval --agents ssh id_ed25519)"
    fi
fi
"""


def add_keychain_to_bashrc() -> bool:
    """Append the keychain block to ~/.bashrc if not already present."""
    rc = Path.home() / ".bashrc"
    try:
        text = rc.read_text() if rc.exists() else ""
    except OSError:
        return False
    if "Maude: ssh-agent via keychain" in text:
        return True
    try:
        with rc.open("a") as f:
            f.write(KEYCHAIN_BLOCK)
        return True
    except OSError:
        return False


# ── Modal screens ──────────────────────────────────────────────────────────

CLAUDERC_PATH = Path.home() / ".azure" / "clauderc"

# Recognised credential-related env vars, for parsing pasted exports.
CRED_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "AWS_", "AZURE_", "OPENAI_")
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


class CredsEntryScreen(ModalScreen[bool]):
    """Modal that lets the user paste credential `export` lines.

    On Save: parses the input, persists the recognised vars, updates
    os.environ, and dismisses with True. On Cancel: dismisses with False.
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def action_cancel(self) -> None:
        self.dismiss(False)

    def compose(self) -> ComposeResult:
        with Container(id="creds-box"):
            yield Label("Set LLM Credentials", id="creds-title")
            yield Label(
                "Paste your credentials below — accepts shell `export` lines "
                "or KEY=VALUE pairs.\n"
                "Recognised prefixes: ANTHROPIC_*, CLAUDE_*, AWS_*, AZURE_*.",
                id="creds-help",
            )
            yield TextArea(
                "",
                id="creds-text",
                language=None,
                show_line_numbers=False,
            )
            yield Label("", id="creds-status")
            with Horizontal(id="creds-buttons"):
                yield Button("Save",   variant="success", id="btn-creds-save")
                yield Button("Cancel", variant="primary", id="btn-creds-cancel")

    def on_mount(self) -> None:
        self.query_one("#creds-text", TextArea).focus()

    @on(Button.Pressed, "#btn-creds-save")
    def save(self) -> None:
        text = self.query_one("#creds-text", TextArea).text
        values = parse_creds_text(text)
        if not values:
            self.query_one("#creds-status", Label).update(
                "[bold red]No recognised credential lines found.[/]"
            )
            return
        try:
            write_clauderc(values)
        except OSError as err:
            self.query_one("#creds-status", Label).update(
                f"[bold red]Could not save: {err}[/]"
            )
            return
        os.environ.update(values)
        self.dismiss(True)

    @on(Button.Pressed, "#btn-creds-cancel")
    def cancel(self) -> None:
        self.dismiss(False)


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
        3. GPG key + commit signing
        4. Final config + keychain
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    STEP_TITLES = (
        "Step 1 of 4 — GitHub Identity",
        "Step 2 of 4 — SSH Key",
        "Step 3 of 4 — GPG Key & Signing",
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
        self.gpg_key_id: str | None = None
        self.gpg_passphrase = ""
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

    def on_mount(self) -> None:
        self._render_step()

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
        self.query_one("#wiz-log", Log).write_line(msg)

    def _render_step(self) -> None:
        self.query_one("#wiz-title", Label).update(self.STEP_TITLES[self.step])
        body = self.query_one("#wiz-body", Container)
        body.remove_children()
        if self.step == 0:
            self._render_identity(body)
            self._set_buttons(back=False, skip=False, next_label="Next")
        elif self.step == 1:
            self._render_ssh(body)
            self._set_buttons(back=True, skip=True, next_label="Next")
        elif self.step == 2:
            self._render_gpg(body)
            self._set_buttons(back=True, skip=True, next_label="Next")
        elif self.step == 3:
            self._render_final(body)
            self._set_buttons(back=True, skip=False, next_label="Finish")

    # Step 1: GitHub identity
    def _render_identity(self, body: Container) -> None:
        body.mount(Label(
            "Enter your GitHub username — we'll look up your public name and email.\n"
            "If you don't have an account yet, create one at "
            "[link=https://github.com/signup]https://github.com/signup[/link] "
            "(Ctrl+click)."
        ))
        body.mount(Input(placeholder="github-username", id="wiz-username",
                         value=self.username))
        body.mount(Label("Full name (will be used for git config user.name):"))
        body.mount(Input(placeholder="Your Name", id="wiz-name", value=self.full_name))
        body.mount(Label("Email (will be used for git config user.email):"))
        body.mount(Input(placeholder="you@example.com", id="wiz-email", value=self.email))
        body.mount(Button("Look up GitHub user", id="wiz-lookup", variant="primary"))

    # Step 2: SSH key
    def _render_ssh(self, body: Container) -> None:
        existing = existing_ssh_key()
        self.ssh_key_path = existing
        if existing:
            pub = read_ssh_pubkey(existing)
            body.mount(Label(
                f"Found existing SSH key: [bold]{existing}[/]\n"
                "Public key (already on disk — copy this if you haven't yet "
                "added it to GitHub):"
            ))
            body.mount(TextArea(pub, id="wiz-ssh-pub", read_only=True,
                                show_line_numbers=False))
            body.mount(Label(
                "Paste the public key at "
                "[link=https://github.com/settings/ssh/new]"
                "https://github.com/settings/ssh/new[/link] "
                "(Ctrl+click), then press [bold]Verify[/]."
            ))
            body.mount(Horizontal(
                Button("Verify GitHub auth", id="wiz-ssh-verify", variant="primary"),
                id="wiz-ssh-actions",
            ))
        else:
            body.mount(Label(
                "No SSH key found at ~/.ssh/id_ed25519. Set a passphrase "
                "(strongly recommended) and click [bold]Generate[/]."
            ))
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

    # Step 3: GPG key + signing
    def _render_gpg(self, body: Container) -> None:
        if not shutil.which("gpg"):
            body.mount(Label(
                "[yellow]gpg is not installed.[/]\n\n"
                "Run [bold]mom install -y gnupg[/] in another terminal, then "
                "come back. Or click [bold]Skip[/] to leave commit signing for later."
            ))
            return
        existing_id = existing_gpg_key(self.email)
        self.gpg_key_id = existing_id
        if existing_id:
            pub = export_gpg_pubkey(existing_id)
            body.mount(Label(
                f"Found existing GPG key for [bold]{self.email}[/]: "
                f"[cyan]{existing_id}[/]\n"
                "Public key (paste at "
                "[link=https://github.com/settings/gpg/new]"
                "https://github.com/settings/gpg/new[/link] if not already added):"
            ))
            body.mount(TextArea(pub, id="wiz-gpg-pub", read_only=True,
                                show_line_numbers=False))
            body.mount(Label("Existing passphrase (only if you set one):"))
            body.mount(Input(placeholder="passphrase", id="wiz-gpg-pass",
                             password=True))
            body.mount(Horizontal(
                Button("Verify signing", id="wiz-gpg-verify", variant="primary"),
                id="wiz-gpg-actions",
            ))
        else:
            body.mount(Label(
                "Generate an ed25519 GPG key for commit signing. "
                "Set a passphrase (recommended) or leave blank.\n"
                "Generation can take several seconds — be patient."
            ))
            body.mount(Label("Passphrase (leave blank for none):"))
            body.mount(Input(placeholder="passphrase", id="wiz-gpg-pass",
                             password=True))
            body.mount(Label("Confirm passphrase:"))
            body.mount(Input(placeholder="confirm",    id="wiz-gpg-pass2",
                             password=True))
            body.mount(Horizontal(
                Button("Generate GPG key", id="wiz-gpg-gen", variant="success"),
                id="wiz-gpg-actions",
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
    def on_back(self) -> None:
        if self.step > 0:
            self.step -= 1
            self._render_step()

    @on(Button.Pressed, "#wiz-skip")
    def on_skip(self) -> None:
        self._log(f"[skipped] {self.STEP_TITLES[self.step]}")
        self._advance()

    @on(Button.Pressed, "#wiz-next")
    def on_next(self) -> None:
        if self.step == 0:
            self._capture_identity_then_advance()
        elif self.step == 1:
            self._advance_if_ssh_ready()
        elif self.step == 2:
            self._advance_if_gpg_ready()
        elif self.step == 3:
            self._finish()

    def _advance(self) -> None:
        if self.step < 3:
            self.step += 1
            self._render_step()

    # ── Step 1 actions ──────────────────────────────────────────────

    @on(Button.Pressed, "#wiz-lookup")
    def on_lookup(self) -> None:
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
        self.username = info["login"]
        if info["name"]:
            self.full_name = info["name"]
            self.query_one("#wiz-name", Input).value = info["name"]
        if info["email"]:
            self.email = info["email"]
            self.query_one("#wiz-email", Input).value = info["email"]
        self._log(f"Found: {info['name']} <{info['email'] or '(email private)'}>"
                  f"  →  {info['html_url']}")

    def _capture_identity_then_advance(self) -> None:
        self.username  = self.query_one("#wiz-username", Input).value.strip()
        self.full_name = self.query_one("#wiz-name",     Input).value.strip()
        self.email     = self.query_one("#wiz-email",    Input).value.strip()
        if not self.full_name or not self.email:
            self._log("Name and email are required to continue.")
            return
        self._advance()

    # ── Step 2 actions ──────────────────────────────────────────────

    @on(Button.Pressed, "#wiz-ssh-gen")
    def on_ssh_gen(self) -> None:
        p1 = self.query_one("#wiz-ssh-pass",  Input).value
        p2 = self.query_one("#wiz-ssh-pass2", Input).value
        if p1 != p2:
            self._log("Passphrases do not match.")
            return
        self._log("Generating ed25519 SSH key…")
        ok, msg = generate_ssh_key(p1, comment=self.email or self.username or "maude")
        self._log(msg)
        if ok:
            self.ssh_key_path = str(SSH_KEY_PATH)
            self._render_step()  # re-render to show the pubkey

    @on(Button.Pressed, "#wiz-ssh-verify")
    def on_ssh_verify(self) -> None:
        self._log("Running: ssh -T git@github.com …")
        ok, msg = verify_ssh_github()
        self._log(msg)
        if ok:
            self._log("[green]✓ SSH auth to GitHub works.[/]")

    def _advance_if_ssh_ready(self) -> None:
        if not self.ssh_key_path:
            self._log("Generate an SSH key first, or click Skip.")
            return
        self._advance()

    # ── Step 3 actions ──────────────────────────────────────────────

    @on(Button.Pressed, "#wiz-gpg-gen")
    def on_gpg_gen(self) -> None:
        if not shutil.which("gpg"):
            self._log("gpg is not installed; install it with `mom install -y gnupg`.")
            return
        p1 = self.query_one("#wiz-gpg-pass",  Input).value
        p2 = self.query_one("#wiz-gpg-pass2", Input).value
        if p1 != p2:
            self._log("Passphrases do not match.")
            return
        self.gpg_passphrase = p1
        self._log("Generating ed25519 GPG key (this may take a few seconds)…")
        key_id, msg = generate_gpg_key(self.full_name, self.email, p1)
        self._log(msg)
        if key_id:
            self.gpg_key_id = key_id
            self._render_step()

    @on(Button.Pressed, "#wiz-gpg-verify")
    def on_gpg_verify(self) -> None:
        if not self.gpg_key_id:
            self._log("Generate a GPG key first.")
            return
        passphrase = ""
        try:
            passphrase = self.query_one("#wiz-gpg-pass", Input).value
        except Exception:
            pass
        self._log(f"Test-signing with {self.gpg_key_id}…")
        ok, msg = verify_gpg_signing(self.gpg_key_id, passphrase)
        self._log(msg)

    def _advance_if_gpg_ready(self) -> None:
        # GPG is optional; skipping is allowed via the Skip button.
        self._advance()

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
        height: 24;
        border: heavy #b87878;
        background: #242424;
    }

    #creds-title {
        text-style: bold;
        color: #d4a0a0;
        margin-bottom: 1;
    }

    #creds-help {
        color: #c09898;
        margin-bottom: 1;
    }

    #creds-text {
        height: 12;
        border: solid #6a5058;
        background: #1e1e1e;
    }

    #creds-status {
        height: 1;
        color: #c09898;
        margin-top: 1;
    }

    #creds-buttons {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    #creds-buttons Button {
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
        height: 36;
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
        height: 6;
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
                    "Paste image:   Alt+V (in Claude Code)\n"
                    "Voice dictate: Win+H (Windows mic)",
                    id="tips",
                )
                yield Static("─" * 28, id="divider3")
                yield Label("Claude model", id="model-label")
                with RadioSet(id="model-select"):
                    for m in MODELS:
                        yield RadioButton(m, value=(m == self._model))
            with Vertical(id="main"):
                yield Label("Projects", id="section-title")
                yield DataTable(id="projects-table", cursor_type="row",
                                zebra_stripes=True)
        with Horizontal(id="bottom-bar"):
            yield Button("Open Project",    id="btn-open")
            yield Button("+ New",           id="btn-new")
            yield Button("Web UI",          id="btn-web")
            yield Button("Setup Git(hub)",  id="btn-setup-git")
            yield Button("Set Credentials", id="btn-creds")
            yield Button("Command Line",    id="btn-cli")
            yield Static("", id="kanna-url")
        yield Footer()

    def on_mount(self) -> None:
        self._kanna_proc: subprocess.Popen | None = None
        self._refresh_table()
        self.query_one("#projects-table", DataTable).focus()

    def _refresh_table(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        table.clear(columns=True)
        table.add_columns("  Project", "Last modified")
        for proj in list_projects():
            table.add_row(
                f"  {proj['name']}",
                proj["modified"],
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

    @on(Button.Pressed, "#btn-setup-git")
    def btn_setup_git(self) -> None:
        self.push_screen(GitSetupWizard(), self._on_git_setup_done)

    def _on_git_setup_done(self, completed: bool) -> None:
        if completed:
            self.notify("Git setup complete.")
        else:
            self.notify("Git setup cancelled.")

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
        with self.suspend():
            open_project(path, self._model)
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
    """Standalone wizard mode for `maude setup-git` — opens just the wizard."""
    class _WizardApp(App):
        CSS = MaudeApp.CSS
        def on_mount(self) -> None:
            self.push_screen(GitSetupWizard(), lambda result: self.exit(0 if result else 1))
    return _WizardApp().run() or 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--setup-git":
        sys.exit(run_wizard_only())
    maybe_self_update()
    app = MaudeApp()
    app.run()
