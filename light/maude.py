#!/usr/bin/env python3
"""
maude.py — Textual TUI for the Maude sandbox.
Always launched via:  maude tui
"""
from __future__ import annotations  # macOS /usr/bin/python3 is 3.9: `X | None` annotations must not run at def time

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from rich.text import Text
from rich.color import Color
from datetime import datetime
from pathlib import Path
from typing import Optional

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.css.query import NoMatches
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
    RichLog,
    RadioButton,
    RadioSet,
    Static,
    TextArea,
)

# ── Platform layer ─────────────────────────────────────────────────────────
# One source for every platform. Any platform that is neither Windows nor macOS keeps the
# Linux sandbox behaviour. Windows (maude.exe) and macOS run natively, outside the sandbox.
FROZEN = bool(getattr(sys, "frozen", False))  # inside maude.exe (PyInstaller)
PLATFORM = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux-sandbox"
IS_WIN = PLATFORM == "windows"
IS_MAC = PLATFORM == "macos"
SANDBOXED = PLATFORM == "linux-sandbox"  # running inside the Maude WSL sandbox (or any Linux)
NATIVE = not SANDBOXED                   # running on the host OS, outside any sandbox
if IS_WIN:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # maude_exe.py sits next to us
    import maude_exe as mx

# Feature map: one line per feature, one column per platform. Later packages flip their own line.
# Web UI and Autostart stay off on Windows and macOS permanently.
FEATURES = {
    #  feature      linux-sandbox  windows  macos
    "web":       {"linux-sandbox": True, "windows": False, "macos": False},
    "togithub":  {"linux-sandbox": True, "windows": False, "macos": False},
    "github":    {"linux-sandbox": True, "windows": False, "macos": False},  # Setup Git(hub)
    "creds":     {"linux-sandbox": True, "windows": True,  "macos": True},   # Set Creds -> ~/.maude/clauderc
    "update":    {"linux-sandbox": True, "windows": True,  "macos": True},
    "autostart": {"linux-sandbox": True, "windows": False, "macos": False},
}
FEATURE_WIDGETS = {"web": "#btn-web", "togithub": "#btn-togithub", "github": "#btn-github",
                   "creds": "#btn-creds", "update": "#btn-update", "autostart": "#autostart"}


def current_platform() -> str:
    """PLATFORM, re-derived from IS_WIN/IS_MAC so tests that force those flags are honoured."""
    return "windows" if IS_WIN else "macos" if IS_MAC else "linux-sandbox"


def is_native() -> bool:
    """Running on the host OS (Windows/macOS), not in the sandbox. Honours forced IS_WIN/IS_MAC like current_platform()."""
    return current_platform() != "linux-sandbox"


def feature_on(name: str) -> bool:
    return FEATURES[name][current_platform()]


def hidden_widgets() -> tuple:
    """CSS ids of the buttons/toggles to hide on this platform."""
    return tuple(w for f, w in FEATURE_WIDGETS.items() if not feature_on(f))


def plat_projects_dir() -> Path:
    return Path(mx.projects_dir()) if IS_WIN else Path.home() / "Maude" / "Projects"


def _outside_json(file: Path | None = None) -> Path:
    return file or (Path.home() / ".maude" / "projects.json")


def _load_outside_json(file: Path | None = None) -> tuple[list, bool]:
    """(folders, ok). ok is False when the file exists but is not a JSON list (or cannot be read)."""
    f = _outside_json(file)
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return [], True
    except (OSError, ValueError, RecursionError):
        return [], False
    if not isinstance(data, list):
        return [], False
    return [p for p in data if isinstance(p, str) and p], True


def read_outside_json(file: Path | None = None) -> list:
    """macOS: folders outside the share, from ~/.maude/projects.json (a JSON list of paths)."""
    return _load_outside_json(file)[0]


def _write_outside_json(items: list, file: Path | None = None) -> None:
    """Atomic write via a unique temp file in the same dir, mode 0600."""
    f = _outside_json(file)
    f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f.name + ".", suffix=".tmp", dir=str(f.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(items, indent=2))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, f)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _update_outside_json(change, file: Path | None = None) -> None:
    """Read-modify-write under an advisory lock (POSIX). An unparseable file is never overwritten
    silently: a copy is kept as projects.json.bad[.N] first."""
    f = _outside_json(file)
    f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = None
    try:
        import fcntl
        lock = open(str(f) + ".lock", "a")
        fcntl.flock(lock, fcntl.LOCK_EX)
    except (ImportError, OSError):
        pass
    try:
        items, ok = _load_outside_json(file)
        if not ok and f.exists():
            n, bad = 0, Path(str(f) + ".bad")
            while bad.exists():
                n += 1
                bad = Path(f"{f}.bad.{n}")
            try:
                shutil.copy2(f, bad)
            except OSError:
                return  # cannot keep a copy: refuse to overwrite
        new = change(items)
        if new != items or not ok:
            _write_outside_json(new, file)
    finally:
        if lock is not None:
            lock.close()


def add_outside_json(folder: str, file: Path | None = None) -> None:
    _update_outside_json(lambda items: items if folder in items else items + [folder], file)


def remove_outside_json(folder: str, file: Path | None = None) -> None:
    _update_outside_json(lambda items: [p for p in items if p != folder], file)


def plat_outside_projects() -> list:
    """Folders registered outside the share: Windows registry (via mx), macOS JSON, else none."""
    return list(mx.read_projects()) if IS_WIN else read_outside_json() if IS_MAC else []


def plat_add_outside(folder: str) -> None:
    if IS_WIN:
        mx.add_project(folder)
    elif IS_MAC:
        add_outside_json(folder)


def plat_remove_outside(folder: str) -> None:
    if IS_WIN:
        mx.remove_project(folder)
    elif IS_MAC:
        remove_outside_json(folder)


MAC_TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def plat_tool(name: str) -> str | None:
    """Full path of an executable, or None. Windows delegates to maude_exe; macOS also looks in the
    Homebrew and per-user bin dirs (a GUI-launched or fresh shell may not have them on PATH)."""
    if IS_WIN:
        return mx.find_git() if name == "git" else mx.find_gh() if name == "gh" else mx.find_tool(name)
    found = shutil.which(name)
    if found or not IS_MAC:
        return found
    home = Path.home()
    for d in (*MAC_TOOL_DIRS, str(home / ".local" / "bin"), str(home / ".bun" / "bin")):
        found = shutil.which(name, path=d)
        if found:
            return found
    return None


def plat_real_claude() -> str:
    """The real claude binary (never the ~/bin/claude sandbox wrapper). Falls back to plain "claude"."""
    if IS_WIN:
        return mx.find_claude(os.environ["USERPROFILE"]) or "claude"
    real = Path.home() / ".local" / "bin" / "claude"
    if os.access(real, os.X_OK):
        return str(real)
    return (plat_tool("claude") or "claude") if IS_MAC else "claude"


def clauderc_path(create: bool = False) -> Path:
    """The one place Claude credentials (`export K="V"` lines) live.
    Sandbox: ~/.azure/clauderc (read by the ~/bin/claude wrapper). Windows/macOS: ~/.maude/clauderc
    (there is no wrapper; Maude itself loads it, see plat_claude_env). create=True makes ~/.maude
    (mode 0700) through maude_dir(); reads must not create anything."""
    if current_platform() == "linux-sandbox":
        return Path.home() / ".azure" / "clauderc"
    return (maude_dir() if create else STATE_DIR) / "clauderc"


def clauderc_inactive_path() -> Path:
    """Where clauderc is parked while "Default to AWS Bedrock" is on (same toggle on every platform)."""
    p = clauderc_path()
    return p.with_name(p.name + ".inactive")


_BEDROCK_PROFILE_RE = re.compile(r"^\[(profile\s+)?bedrock\]", re.MULTILINE)
_DUMMY_API_KEY = "sk-ant-dummy"


def claude_env_from(rc: dict, environ, logged_in: bool, aws_config: str, parked_bedrock: bool = False) -> dict:
    """The environment the ~/bin/claude wrapper would give Claude Code, for Windows/macOS (no wrapper).
    Pure. `rc` = parsed clauderc, `environ` = ambient env, `logged_in` = a native claude.ai login
    exists, `aws_config` = text of ~/.aws/config, `parked_bedrock` = clauderc is parked as .inactive
    because the user made Bedrock the default (that is a Maude-saved state too). Result: the clauderc
    exports plus what the wrapper sets in the same order of precedence (custom ANTHROPIC_BASE_URL,
    native login, Foundry, Bedrock). Applies ONLY to a Maude-saved state: with no clauderc (and nothing
    parked) the result is {} and the child inherits the environment unchanged; ambient files alone
    (~/.aws/config, a login whose Keychain token cannot be read) never select a backend. Bedrock counts
    only when the user saved it through Set Creds (CLAUDE_CODE_USE_BEDROCK=1 in the clauderc, or parked).
    A value of None means "remove this variable". Model ids are not set: the wrapper pins ids that
    change with every release, Claude Code's own defaults apply."""
    if not rc and not parked_bedrock:
        return {}
    env: dict = dict(rc)
    merged = {**environ, **rc}  # the wrapper sources clauderc over the environment

    def set_(k: str, v) -> None:
        if v is None:
            if k in merged:
                env[k] = None
        else:
            env[k] = v

    if merged.get("ANTHROPIC_BASE_URL"):  # custom endpoint: no cloud backend
        for k in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_FOUNDRY"):
            if k in merged:
                set_(k, "0")
        if not str(merged.get("ANTHROPIC_API_KEY", "")).startswith("sk-ant-"):
            env["ANTHROPIC_API_KEY"] = _DUMMY_API_KEY
    elif logged_in:  # native login wins: clear every cloud setting
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
            set_(k, None)
        for k in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_FOUNDRY"):
            if k in merged:
                set_(k, "0")
    elif merged.get("CLAUDE_CODE_USE_FOUNDRY") == "1":
        url, key = merged.get("ANTHROPIC_FOUNDRY_BASE_URL"), merged.get("ANTHROPIC_FOUNDRY_API_KEY")
        if url and key:  # incomplete: the wrapper would refuse to start, we just add nothing
            env["ANTHROPIC_BASE_URL"] = url
            env["ANTHROPIC_API_KEY"] = _DUMMY_API_KEY
            env["CLAUDE_CODE_USE_BEDROCK"] = "0"
    elif (rc.get("CLAUDE_CODE_USE_BEDROCK") == "1" or parked_bedrock) and _BEDROCK_PROFILE_RE.search(aws_config):
        env.update(CLAUDE_CODE_USE_BEDROCK="1", CLAUDE_CODE_USE_FOUNDRY="0",
                   AWS_DEFAULT_REGION="us-west-2", AWS_PROFILE="bedrock")
        set_("ANTHROPIC_API_KEY", None)
    return env


def _decode_text_strict(raw: bytes) -> str:
    """UTF-8, UTF-8 with BOM or UTF-16 (BOM) text. Raises ValueError (UnicodeDecodeError) when the
    bytes are none of those, so a caller never mistakes an undecodable file for an empty one."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def read_clauderc(path: Path | None = None) -> dict:
    """Parsed clauderc ({} when missing or unreadable or undecodable). The parked .inactive file is
    a different name, so it is never read here."""
    try:
        return parse_clauderc(_decode_text_strict((path or clauderc_path()).read_bytes()))
    except (OSError, ValueError):
        return {}


def plat_claude_env() -> dict:
    """Extra environment for Claude Code (None value = remove the variable).
    Sandbox: {} (the ~/bin/claude wrapper does this). Windows/macOS: clauderc + wrapper rules,
    only when a clauderc exists (see claude_env_from)."""
    if current_platform() == "linux-sandbox":
        return {}
    rc = read_clauderc()
    inactive = clauderc_inactive_path()
    parked = False
    if not rc and not clauderc_path().exists() and inactive.exists():
        parked = read_clauderc(inactive).get("CLAUDE_CODE_USE_BEDROCK") == "1"
    if not rc and not parked:
        return {}
    try:
        aws = AWS_CONFIG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        aws = ""
    return claude_env_from(rc, os.environ, have_claude_login(), aws, parked)


def plat_tool_env() -> dict:
    """Extra environment for the non-Claude tools (Kiro, OpenCode, Crush): the raw clauderc exports
    only, no wrapper rewrites (no dummy key, no login stripping)."""
    return {} if current_platform() == "linux-sandbox" else read_clauderc()


def _child_env(tools: bool = False) -> dict | None:
    """env= for child processes: None (inherit) unless there is something to add.
    tools=True: for Kiro/OpenCode/Crush (raw clauderc exports), else for Claude Code."""
    extra = plat_tool_env() if tools else plat_claude_env()
    if not extra:
        return None
    env = dict(os.environ)
    for k, v in extra.items():
        if IS_WIN:  # Windows names are case-insensitive: drop any differently-cased twin
            for e in [e for e in env if e.upper() == k.upper()]:
                del env[e]
        else:
            env.pop(k, None)
        if v is not None:
            env[k] = v
    return env

if IS_WIN:
    mx.child_env = _child_env  # maude_exe.run_windows (Explorer / --windows route) picks up the same env


def plat_open_url(url: str) -> bool:
    """Open a URL in the user's browser. No-op in the sandbox (interop is off there)."""
    if not (IS_WIN or IS_MAC):
        return False
    try:
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception:
        return False


PROJECTS_DIR = plat_projects_dir()
DELETED_DIR  = PROJECTS_DIR / ".deleted"
DISABLE_FLAG   = Path.home() / ".maude-tui-disabled"
PERPLEXITY_FLAG = Path.home() / ".maude-perplexity"
PERPLEXITY_NAME = "perplexity-computer"
PERPLEXITY_URL  = "https://www.perplexity.ai/rest/computer/mcp"
KANNA_CMD    = "kanna"
KANNA_PORT   = 3210
UPDATE_URL   = "https://raw.githubusercontent.com/dirkpetersen/maude/main/light/maude.py"
UPDATE_STAMP = Path.home() / ".maude-tui-last-update"
UPDATE_HOUR  = 12  # local-time hour (noon) after which the daily refresh fires

MODELS        = ("fable", "opus", "sonnet", "haiku")
DEFAULT_MODEL = "opus"
STATE_DIR     = Path.home() / ".maude"
MODEL_FILE    = STATE_DIR / "maude-model"   # "<model>[,<effort>]", e.g. "opus,high" or "opus"
OLD_MODEL_FILE = Path.home() / ".maude-model"  # previous location, migrated on first read
AI_TOOLS      = ("Claude Code", "Kiro CLI", "OpenCode", "Crush")
DEFAULT_TOOL  = "Claude Code"
TOOL_FILE     = STATE_DIR / "maude-tool"
OLD_TOOL_FILE = Path.home() / ".maude-tool"
# Claude Code `--effort <level>` (verified in `claude --help`); "" = Claude Code's own default.
EFFORTS       = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = ""
EFFORT_LABELS = ("auto",) + EFFORTS  # "auto" = Claude Code's own default (passes nothing)
VERSION_FILE  = Path.home() / ".local" / "share" / "maude" / "version"

LOGO = (
    "  __  __                 _      \n"
    " |  \\/  | __ _ _   _  __| | ___ \n"
    " | |\\/| |/ _` | | | |/ _` |/ _ \\\n"
    " | |  | | (_| | |_| | (_| |  __/\n"
    " |_|  |_|\\__,_|\\__,_|\\__,_|\\___|"
)

# Truecolor gradient: each row of the logo gets its own hex colour,
# sweeping from bright spring-green at the top to deep teal at the bottom.
_LOGO_GRADIENT = [
    "#7de8b0",  # row 0 — bright spring green
    "#5ccda2",  # row 1
    "#3fb294",  # row 2
    "#249886",  # row 3
    "#148177",  # row 4 — deep teal (brighter than #0a8880 for contrast)
]

_LOGO_GRADIENT_WIN = ["#ff9a9a", "#f27474", "#e05252", "#c93a3a", "#a82a2a"]  # reds: outside the sandbox
WIN_CAPTION = "running outside the sandbox"
# Windows only: is the Maude WSL distro installed? The red logo is always used on Windows, but the
# caption only makes sense when there is a sandbox to be outside of. Unknown counts as installed.
# macOS has no sandbox at all: red logo, no caption (like Windows without the WSL distro).
HAS_SANDBOX = mx.has_sandbox() if IS_WIN else not IS_MAC

def make_gradient_logo() -> Text:
    grad = _LOGO_GRADIENT_WIN if (IS_WIN or IS_MAC) else _LOGO_GRADIENT
    text = Text()
    for i, line in enumerate(LOGO.splitlines()):
        colour = grad[i] if i < len(grad) else grad[-1]
        text.append(line + "\n", style=f"bold {colour}")
    if IS_WIN and HAS_SANDBOX:
        text.append(" " + WIN_CAPTION + "\n", style="dim #e05252")
    return text


# ── Helpers ────────────────────────────────────────────────────────────────

def get_maude_version() -> str:
    """Return the current maude version string.

    Priority: git describe (dev machines) → VERSION_FILE stamp written by
    'maude update' → 'dev'.
    """
    import subprocess as _sp
    if IS_WIN:
        return mx.VERSION
    try:
        v = _sp.check_output(
            ["git", "-C", str(Path(__file__).parent), "describe", "--tags", "--always"],
            stderr=_sp.DEVNULL, text=True, timeout=2,
        ).strip()
        if v:
            return v
    except Exception:
        pass
    try:
        v = VERSION_FILE.read_text().strip()
        if v:
            return v
    except OSError:
        pass
    return "dev"


def valid_update(data: bytes, filename: str) -> bool:
    """Sanity-check a downloaded maude.py before it replaces the running copy.
    Static, pure checks only (size, shebang, markers, syntax); the real import
    check is import_check_update()."""
    try:
        if len(data) < 10_000:
            return False
        # NOTE: the shebang, the `def maybe_self_update` marker and the
        # `__main__` guard below are also what already-installed older copies
        # (and light/maude's _validate_maude_py) look for -- do not rename or
        # remove them without keeping the old markers.
        if not data.startswith(b"#!/usr/bin/env python3"):
            return False
        if b"def maybe_self_update" not in data:
            return False
        # Anchored at line start so this very line does not satisfy the check.
        if not re.search(rb'(?m)^if __name__ == "__main__":', data):
            return False
        compile(data, filename, "exec")
        return True
    except Exception:
        return False


_IMPORT_CHECK_CODE = (
    "import importlib.machinery, importlib.util, sys\n"
    "sys.dont_write_bytecode = True\n"
    "p = sys.argv[1]\n"
    "ldr = importlib.machinery.SourceFileLoader('maude_update_check', p)\n"
    "spec = importlib.util.spec_from_file_location('maude_update_check', p, loader=ldr)\n"
    "mod = importlib.util.module_from_spec(spec)\n"
    "ldr.exec_module(mod)\n"
)


def import_check_update(path: str, timeout: int = 30) -> str:
    """Import the downloaded file as a non-main module in a subprocess (runs its
    module-level code, so bad imports / missing dependencies / NameErrors show
    up, but the TUI never starts). Returns "" on success, else a short reason.
    Not isolated (-I) on purpose: user-site packages such as textual must be visible."""
    try:
        # `-c` puts the cwd first on sys.path, so a stray json.py etc. in the
        # caller's cwd would shadow stdlib: run from the file's own directory
        # (and add -P where supported) instead.
        path = os.path.abspath(path)
        cmd = [sys.executable]
        if sys.version_info >= (3, 11):
            cmd.append("-P")
        cmd += ["-c", _IMPORT_CHECK_CODE, path]
        r = subprocess.run(
            cmd, cwd=os.path.dirname(path),
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"import check timed out after {timeout}s"
    except Exception as err:
        return f"import check could not run ({err})"
    if r.returncode != 0:
        lines = (r.stderr or "").strip().splitlines()
        return lines[-1] if lines else f"import check exited {r.returncode}"
    return ""


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
        if not valid_update(new_bytes, str(target)):
            print("maude: downloaded update failed validation; keeping current version",
                  file=sys.stderr)
            return
        tmp = target.with_name(target.name + ".new")
        try:
            tmp.write_bytes(new_bytes)
            why = import_check_update(str(tmp))
            if why:
                print("maude: downloaded update failed validation; keeping current version"
                      f" ({why})", file=sys.stderr)
                return
            tmp.replace(target)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
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
    extra: dict[str, str] = {"KANNA_DISABLE_SELF_UPDATE": "1"}  # pinned; self-update fails (no bun in its PATH)
    wrapper = Path.home() / "bin" / "claude"
    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        extra["CLAUDE_EXECUTABLE"] = str(wrapper)
    return extra


def have_claude_login() -> bool:
    """Return True if a valid native claude.ai OAuth login exists.

    Mirrors _is_claude_logged_in() in the dok claude-wrapper: a non-empty
    claudeAiOauth.accessToken in ~/.claude/.credentials.json whose expiresAt
    (ms epoch) is in the future. A missing/malformed expiresAt means "can't
    tell", not "expired".
    """
    creds = Path.home() / ".claude" / ".credentials.json"
    try:
        data = json.loads(creds.read_text())
    except (OSError, ValueError):
        return False
    oauth = data.get("claudeAiOauth") or {}
    if not oauth.get("accessToken"):
        return False
    expires_ms = oauth.get("expiresAt")
    # type() not isinstance(): bool is a subclass of int, and a JSON `true`
    # here must mean "can't tell", not "expired at 1ms epoch".
    if type(expires_ms) in (int, float) and expires_ms > 0:
        now_ms = time.time() * 1000
        if expires_ms > now_ms:
            return True
        # The access token is short-lived and lapses several times a day; Claude Code refreshes it
        # silently while the refresh token is valid (same rule as the wrapper's _is_claude_logged_in).
        if not oauth.get("refreshToken"):
            return False
        refresh_exp = oauth.get("refreshTokenExpiresAt")
        if type(refresh_exp) in (int, float) and refresh_exp > 0:
            return refresh_exp > now_ms
        return True
    return True


NATIVE_CRED_ENV = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_VERTEX",
                   "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")  # also count as configured on Windows/macOS


def credentials_configured() -> bool:
    """True if any non-login credentials are configured (env vars, ~/.aws/credentials, clauderc)."""
    # Azure AI Foundry
    if os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"):
        return True
    # Anthropic direct
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if is_native() and any(os.environ.get(k) for k in NATIVE_CRED_ENV):
        return True
    # AWS Bedrock
    aws_creds = Path.home() / ".aws" / "credentials"
    if aws_creds.exists() and aws_creds.stat().st_size > 0:
        return True
    # clauderc (sandbox: ~/.azure/clauderc, Windows/macOS: ~/.maude/clauderc)
    rc = clauderc_path()
    if rc.exists() and rc.stat().st_size > 0:
        return True
    return False


def check_credentials() -> bool:
    """Return True if Claude Code credentials are configured."""
    # Native claude.ai login (claude /login)
    if have_claude_login():
        return True
    return credentials_configured()


_LOGIN_OK = [False]  # a True answer of `claude auth status` is remembered for the session


def claude_login_status(timeout: float = 10.0) -> bool | None:
    """Windows/macOS fallback when ~/.claude/.credentials.json does not show a login (macOS keeps the
    token in the Keychain): `claude auth status` (JSON, verified in `claude auth --help`). True/False
    when claude answered, None when it could not be asked (missing, timeout, unparseable)."""
    try:
        r = subprocess.run([plat_real_claude(), "auth", "status", "--json"], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        data = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    flag = data.get("loggedIn") if isinstance(data, dict) else None
    if flag is True:
        _LOGIN_OK[0] = True
    return flag if isinstance(flag, bool) else None


def creds_gate_decision(native: bool, login_file: bool, configured: bool, status) -> bool:
    """True = the credential gate (CredsEntryScreen) shows before a project opens.
    Sandbox: whenever nothing is configured. Windows/macOS: never on a login file, configured
    credentials or a status that says logged in; and not when the status could not be determined
    (None): only when claude itself answered "not logged in". Even then the screen can be
    cancelled and the project still opens (see _on_creds_for_project)."""
    if login_file or configured or status is True:
        return False
    return True if not native else status is False


def creds_gate_precheck():
    """The cheap part of the gate (no process spawned): False = no gate, True = show the gate,
    None = Windows/macOS and only `claude auth status` can tell (ask it off the UI thread)."""
    if have_claude_login() or credentials_configured() or (is_native() and _LOGIN_OK[0]):
        return False
    return None if is_native() else True


def creds_gate_blocks() -> bool:
    """Blocking variant (tests, scripts): the precheck, then `claude auth status` when needed."""
    pre = creds_gate_precheck()
    if pre is not None:
        return pre
    return creds_gate_decision(True, False, False, claude_login_status())


_GIT_SECTION = re.compile(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\](.*)$')


def _git_config_value(raw: str) -> str | None:
    """Value part of a git config line (after '='): handles quotes, escapes and #/; comments.
    None for constructs we do not handle (line continuation)."""
    out, quoted, i = [], False, 0
    raw = raw.strip()
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            if i + 1 >= len(raw):
                return None  # line continuation
            out.append({"n": "\n", "t": "\t", "b": "\b"}.get(raw[i + 1], raw[i + 1]))
            i += 2
            continue
        if c == '"':
            quoted = not quoted
        elif not quoted and c in "#;":
            break
        else:
            out.append(c)
        i += 1
    return "".join(out).strip() if not quoted else None


def parse_git_origin_url(text: str) -> str | None:
    """remote.origin.url from the text of a git config file ('' if none). The last url wins, like
    `git config --get`. None when the file uses [include]/[includeIf] or something we cannot parse
    (the caller then asks git itself)."""
    # Only the repo config (.git/config) is read: not config.worktree, ~/.gitconfig or /etc/gitconfig.
    in_origin, url = False, ""
    if "\x00" in text:
        return None
    lines = text.lstrip("\ufeff").split("\n")  # git breaks lines on \n only
    if any(ln.rstrip().endswith("\\") for ln in lines):
        return None  # a line continuation anywhere: let git parse the file
    for line in lines:
        s = line.strip()
        if not s or s[0] in "#;":
            continue
        rest = s
        if s[0] == "[":
            m = _GIT_SECTION.match(s)
            if not m:
                return None
            name, sub, rest = m.group(1).lower(), m.group(2), m.group(3).strip()
            if name in ("include", "includeif"):
                return None
            in_origin = (name == "remote" and sub == "origin") or name == "remote.origin"
            if not rest or rest[0] in "#;":
                continue
        if in_origin:
            key, eq, val = rest.partition("=")
            if key.strip().lower() == "url":
                if not eq:
                    return None  # `url` without a value
                v = _git_config_value(val)
                if v is None:
                    return None
                url = v
    return url


def _git_config_path(project_path: Path) -> Path | None:
    """Path of the config file git reads for the project: <p>/.git/config, or for a `.git` FILE
    (worktree/submodule: `gitdir: <path>`) the target dir's config (worktrees: its commondir's)."""
    dotgit = project_path / ".git"
    if stat.S_ISDIR(os.stat(dotgit).st_mode):
        return dotgit / "config"
    text = _read_small(dotgit)
    first = text.split("\n")[0].strip() if text else ""
    if not first.lower().startswith("gitdir:"):
        return None
    gitdir = Path(first[7:].strip())
    if not gitdir.is_absolute():
        gitdir = project_path / gitdir
    cd = gitdir / "commondir"
    if os.path.lexists(cd):
        ctext = _read_small(cd)
        if not ctext or not ctext.strip():
            return None
        common = Path(ctext.strip())
        gitdir = common if common.is_absolute() else gitdir / common
    return gitdir / "config"


_SMALL_FILE_MAX = 1 << 20  # 1 MB: git config/.git/commondir files are tiny


def _read_small(path: Path) -> str | None:
    """Text of a regular file of at most 1 MB (BOM dropped); None for FIFOs, devices, big files.
    The file is opened first (O_NONBLOCK, so a FIFO swapped in at any time cannot block) and the
    checks run on the open handle, so there is no stat/open race."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > _SMALL_FILE_MAX:
            return None
        data = b""
        while len(data) <= _SMALL_FILE_MAX:
            chunk = os.read(fd, _SMALL_FILE_MAX + 1 - len(data))
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    return None if len(data) > _SMALL_FILE_MAX else data.decode("utf-8-sig", errors="replace")


def git_origin_from_config(project_path: Path) -> str | None:
    """Fast pure-Python read of remote.origin.url (no subprocess). None = could not tell (any
    problem at all: the caller then asks git itself, so this can never break startup)."""
    try:
        cfg = _git_config_path(project_path)
        if cfg is None:
            return None
        text = _read_small(cfg)
        return None if text is None else parse_git_origin_url(text)
    except Exception:
        return None


def git_remote_origin_url(project_path: Path) -> str:
    """Return `git config --get remote.origin.url` for the project, or ''.
    Reads .git/config directly (a git subprocess costs ~100 ms each on Windows); asks git only
    when that cannot be parsed."""
    if not (project_path / ".git").exists():
        return ""
    fast = git_origin_from_config(project_path)
    if fast is not None:
        return fast
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


REG_ORIG: dict[str, str] = {}  # Windows/macOS: resolved registered project path -> path as stored


def _pkey(folder: str) -> str:
    """Comparison key for a project folder (case-insensitive on Windows and macOS)."""
    if IS_WIN:
        return mx.project_key(folder)
    k = os.path.normpath(folder)
    return k.casefold() if IS_MAC else k


def plat_banner_text(folder: str) -> str:
    """The red warning bar text (Windows: maude_exe's wording; macOS: the same, for macOS)."""
    if IS_WIN:
        return mx.banner_text(folder, HAS_SANDBOX)
    return (f"Maude: running on macOS in {folder}. "
            "Maude's AI agents have your full macOS user rights here.")


def _outside_share(folder: str, share: str) -> bool:
    """True if folder (realpath()ed) is not the share or inside it."""
    if IS_WIN:
        return mx.folder_mode(folder, share) == "windows"
    f, s = _pkey(folder), _pkey(share)
    return not (f == s or f.startswith(s.rstrip(os.sep) + os.sep))


def list_projects(extra: tuple = ()) -> list[dict]:
    """Return projects sorted by last-modified time, newest first.

    Windows: outside-sandbox projects (registry) first, then share projects, each group sorted
    by the newest mtime of the folder content. Missing registry folders are hidden."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    projects = []
    dirs = [(p, False) for p in PROJECTS_DIR.iterdir()]
    if IS_WIN or IS_MAC:
        # A registry entry must not duplicate a share row: compare fully resolved paths
        # (realpath expands 8.3 short names and drops trailing separators).
        have = {_pkey(os.path.realpath(p)) for p, _ in dirs}
        share = os.path.realpath(PROJECTS_DIR.parent)
        for r in plat_outside_projects() + [str(e) for e in extra]:  # extra: shown even if the registry write failed
            rp = os.path.realpath(r)
            if _pkey(rp) not in have and _outside_share(rp, share):
                dirs.append((Path(rp), True))
                REG_ORIG[rp] = r  # Delete must remove the value as stored, not the resolved path
                have.add(_pkey(rp))  # two values for one folder (short/long name, junction) -> one row
    for p, outside in dirs:
        if p.is_dir() and (outside or not p.name.startswith(".")):
            try:
                mtime = mx.content_mtime(str(p)) if IS_WIN else p.stat().st_mtime
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
                "outside":  outside,
                "label":    p.name,
            })
    if IS_WIN:
        return mx.merge_projects([d for d in projects if d["outside"]],
                                 [d for d in projects if not d["outside"]])
    if IS_MAC:  # outside projects first, then share projects, each newest first
        return sorted(projects, key=lambda d: (not d["outside"], -d["mtime"]))
    projects.sort(key=lambda d: d["mtime"], reverse=True)
    return projects


def slugify(name: str) -> str:
    """Replace spaces with hyphens; keep letters, digits, dots, dashes, underscores."""
    name = name.replace(" ", "-")
    name = re.sub(r"[^a-zA-Z0-9._-]", "", name)
    return name.strip("-")


def _has_claude_sessions(path: Path) -> bool:
    """True if Claude Code has saved sessions for `path` (the current dir).

    Sessions live in ~/.claude/projects/<resolved cwd, non-alphanumerics
    -> '-'>/*.jsonl. Needed because `--resume` with no sessions opens an
    empty picker that blocks until Esc.
    """
    try:
        enc = re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
        return any((Path.home() / ".claude" / "projects" / enc).glob("*.jsonl"))
    except OSError:
        return False


def _claude_mcp(*args: str, capture: bool = False):
    """Run `claude mcp ...`. Capture mode is for status checks; otherwise the
    child inherits the terminal so the user can follow the sign-in link.

    Uses the real Claude Code binary: the ~/bin/claude wrapper treats `mcp` as a
    model/prompt word and injects --model, which breaks `mcp add --transport`."""
    exe = plat_real_claude()
    if capture:
        return subprocess.run([exe, "mcp", *args], capture_output=True,
                              text=True, timeout=60, env=_child_env())
    return subprocess.run([exe, "mcp", *args], check=False, env=_child_env())


def sync_perplexity() -> None:
    """Add + sign in to the user-scope Perplexity MCP connector when PERPLEXITY_FLAG is set.

    Never raises: the project must open even if this fails. Removal happens only
    when the box is unticked (remove_perplexity), so a connector the user added
    by hand is not touched while the box has never been ticked.
    """
    try:
        if not PERPLEXITY_FLAG.exists():
            return
        r = _claude_mcp("get", PERPLEXITY_NAME, capture=True)
        exists = r.returncode == 0
        connected = exists and "Connected" in (r.stdout or "")
        if not connected:
            if not exists:
                print("Setting up the Perplexity connector...")
                _claude_mcp("add", "--transport", "http", "--scope", "user",
                            PERPLEXITY_NAME, PERPLEXITY_URL)
            print("Sign in to Perplexity if asked...")
            _claude_mcp("login", PERPLEXITY_NAME)
    except KeyboardInterrupt:
        print("Perplexity sign-in skipped.")
    except Exception as e:
        print(f"Warning: Perplexity connector step failed ({e}); continuing.")


def remove_perplexity() -> None:
    """Remove the user-scope Perplexity connector (best effort, never raises)."""
    try:
        _claude_mcp("remove", "--scope", "user", PERPLEXITY_NAME, capture=True)
    except Exception:
        pass


def tool_command(tool: str, model: str, fresh: bool, effort: str = "") -> list[str]:
    """Command line for an AI tool. Only Claude Code takes the model: the
    others have no stable alias mapping, so they start on their own default."""
    cont = [] if fresh else ["--continue"]
    return {
        "Claude Code": ["claude", model, *effort_args(effort), *cont],
        "Kiro CLI": ["kiro-cli", "chat", *([] if fresh else ["--resume"])],
        "OpenCode": ["opencode", *cont],
        "Crush": ["crush", *cont],
    }[tool]


def effort_args(effort: str) -> list[str]:
    """`--effort <level>` for a known level, else nothing (nothing unvalidated reaches a command line)."""
    return ["--effort", effort] if effort in EFFORTS else []


def parse_model_pref(raw: str) -> tuple[str, str]:
    """"<model>[,<effort>]" -> (model, effort). Unknown model -> opus, unknown effort -> default."""
    m, _, e = raw.strip().partition(",")
    m, e = m.strip(), e.strip().lower()
    return (m if m in MODELS else DEFAULT_MODEL, e if e in EFFORTS else DEFAULT_EFFORT)


def format_model_pref(model: str, effort: str) -> str:
    return f"{model},{effort}" if effort in EFFORTS else model


def reconcile(model: str, tool: str, changed: str) -> tuple[str, str]:
    """Fable only runs in Claude Code. `changed` ("model"|"tool") is the side
    the user just set, so the other side gives way."""
    if model == "fable" and tool != DEFAULT_TOOL:
        return (model, DEFAULT_TOOL) if changed == "model" else ("opus", tool)
    return model, tool


def _open_other_tool(tool: str, model: str, fresh: bool) -> None:
    cmd = tool_command(tool, model, fresh)
    env = _child_env(tools=True)  # raw clauderc exports only
    if IS_MAC:  # no installers on macOS yet: say how to get the tool
        exe = plat_tool(cmd[0])
        if not exe:
            input(f"{tool} is not installed. Install it first (Homebrew or the vendor's installer), "
                  "then try again. Press Enter to go back. ")
            return
        cmd = [exe, *cmd[1:]]
    elif not shutil.which(cmd[0]):
        input(f"{tool} is not installed; run 'maude update'. Press Enter to go back. ")
        return
    if tool == "Kiro CLI" and subprocess.run([cmd[0], "whoami"], capture_output=True, env=env).returncode != 0:
        # Interop is off in the sandbox, so a browser cannot open: use the device code flow.
        subprocess.run([cmd[0], "login", "--use-device-flow"], check=False, env=env)
    ret = subprocess.run(cmd, check=False, env=env).returncode
    if ret != 0 and tool == "Kiro CLI" and not fresh:
        subprocess.run([cmd[0], *tool_command(tool, model, True)[1:]], check=False, env=env)  # nothing to resume


def _open_project_win(path: Path, model: str, fresh: bool, tool: str, effort: str = "") -> None:
    """Windows: run the tool natively in the project folder, OUTSIDE the sandbox, with normal
    permission prompts (no bypass flag anywhere). Children go through mx._run so Ctrl+C
    never kills them."""
    mx.offer_git()
    mx.offer_gh()
    if tool == DEFAULT_TOOL:
        if not mx.find_claude(os.environ["USERPROFILE"]):  # offer the install; run_windows finds it after
            if not mx.ensure_tool(tool, mx.find_tool, refresh=mx.refresh_path):
                input("Press Enter to go back. ")
                return
        sync_perplexity()
        rc = mx.run_windows(str(path), model, fresh=fresh, effort=effort)
        if rc == 3:  # claude missing: run_windows printed the install hint
            input("Press Enter to go back. ")
        return
    cmd = tool_command(tool, model, fresh)
    exe = mx.ensure_tool(tool, mx.find_tool, refresh=mx.refresh_path)
    if not exe:  # ensure_tool said why (declined, failed, not found)
        input("Press Enter to go back. ")
        return
    print(mx.banner_text(str(path), HAS_SANDBOX))
    env = _child_env(tools=True)  # raw clauderc exports (None = inherit)
    if mx._run([exe, *cmd[1:]], cwd=str(path), env=env) and tool == "Kiro CLI" and not fresh:
        mx._run([exe, *tool_command(tool, model, True)[1:]], cwd=str(path), env=env)  # nothing to resume


def open_project(project_path: Path, model: str, *, fresh: bool = False,
                 tool: str = DEFAULT_TOOL, effort: str = "") -> None:
    """Launch the selected AI tool for a project (Claude Code by default).

    For Claude Code:

    By default tries `--continue` first (resume the previous session),
    then, only if the project has saved sessions, `--resume` (Claude's
    interactive session picker, which `--continue` may ask for when
    several sessions exist), and finally a fresh launch.
    When `fresh=True`, skip `--continue` entirely so the conversation
    starts with no history.
    """
    if IS_WIN:  # no chdir: the TUI must not hold the folder (delete would fail); children get cwd=
        _open_project_win(project_path, model, fresh, tool, effort)
        return
    os.chdir(project_path)
    if tool != DEFAULT_TOOL:
        _open_other_tool(tool, model, fresh)
        return
    head, env = ["claude", model], _child_env()  # sandbox: the ~/bin/claude wrapper takes the model as a word
    if IS_MAC:  # no wrapper outside the sandbox: the real claude (--model) with Maude's env
        real = plat_real_claude()
        if real == "claude":  # not found anywhere
            input("Claude Code is not installed. Install it with: curl -fsSL https://claude.ai/install.sh | bash"
                  "  then try again. Press Enter to go back. ")
            return
        head = [real, "--model", model]
    sync_perplexity()
    eff = effort_args(effort)
    if fresh:
        subprocess.run([*head, *eff], check=False, env=env)
        return
    ret = subprocess.run([*head, *eff, "--continue"], check=False, env=env).returncode
    if ret != 0 and _has_claude_sessions(project_path):
        ret = subprocess.run([*head, *eff, "--resume"], check=False, env=env).returncode
    if ret != 0:
        subprocess.run([*head, *eff], check=False, env=env)


def maude_dir() -> Path:
    """~/.maude (Path.home(): %USERPROFILE% on Windows), created with mode 0700 if missing.
    Shared by the model/tool state; credentials can move here too. Same on Linux, macOS and Windows."""
    d = STATE_DIR
    d.mkdir(mode=0o700, parents=True, exist_ok=True)  # the mode has no effect on Windows
    return d


def _decode_state(raw: bytes) -> str:
    """Tolerant text decode for state files: a UTF-16 BOM (PowerShell 5.1 `>`) is honoured, a UTF-8
    BOM is dropped, and undecodable bytes are replaced (the value then simply fails validation)."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def _migrate_state(new: Path, old: Path) -> None:
    """Move the legacy ~/.maude-model|tool into ~/.maude/ (only if the new file is missing)."""
    try:
        if not new.exists() and old.exists():
            val = _decode_state(old.read_bytes())
            maude_dir()
            new.write_text(val, encoding="utf-8")
            old.unlink()
    except (OSError, ValueError):
        pass


def _read_state(path: Path, old: Path) -> str:
    _migrate_state(path, old)
    try:
        return _decode_state(path.read_bytes())
    except (OSError, ValueError):
        return ""


def _write_state(path: Path, text: str) -> None:
    try:
        maude_dir()
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass


def read_model_effort() -> tuple[str, str]:
    """Saved (model, effort) from ~/.maude/maude-model ("<model>[,<effort>]")."""
    return parse_model_pref(_read_state(MODEL_FILE, OLD_MODEL_FILE))


def _save_model_effort(model: str, effort: str) -> None:
    _write_state(MODEL_FILE, format_model_pref(model, effort))


def read_model() -> str:
    """Return the user's saved Claude model alias, or the default."""
    return read_model_effort()[0]


def read_effort() -> str:
    return read_model_effort()[1]


def save_model(name: str) -> None:
    if name in MODELS:
        _save_model_effort(name, read_effort())


def save_effort(effort: str) -> None:
    if effort == "" or effort in EFFORTS:
        _save_model_effort(read_model(), effort)


def read_tool() -> str:
    """Return the user's saved AI tool, or the default."""
    t = _read_state(TOOL_FILE, OLD_TOOL_FILE).strip()
    return t if t in AI_TOOLS else DEFAULT_TOOL


def save_tool(name: str) -> None:
    if name in AI_TOOLS:
        _write_state(TOOL_FILE, name)


def features(github: bool, outside: bool) -> str:
    return mx.features(github, outside and HAS_SANDBOX) if IS_WIN else "GitHub" if github else ""


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

AWS_CREDS_PATH  = Path.home() / ".aws"    / "credentials"
AWS_CONFIG_PATH  = Path.home() / ".aws"    / "config"
GEMINI_ENV_PATH  = Path.home() / ".gemini" / ".env"

# Recognised credential-related env vars, for parsing pasted exports.
CRED_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "AWS_", "AZURE_", "OPENAI_",
                 "GEMINI_", "GOOGLE_")
# Vars that belong to the Gemini CLI: routed to ~/.gemini/.env (where the
# CLI looks by default) instead of ~/.azure/clauderc, which is Claude's
# Azure-vs-AWS routing file and unrelated to Gemini.
GEMINI_PREFIXES = ("GEMINI_", "GOOGLE_")
# Default Vertex AI region when a user enables Vertex without naming one.
# us-west1 is The Dalles, Oregon — the closest region (mirrors the Bedrock
# tab defaulting to us-west-2, also Oregon).
DEFAULT_VERTEX_LOCATION = "us-west1"
CRED_KEY_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")


def _unquote_rc_value(v: str) -> str:
    """Value of an `export K=V` line. Reads the old form (`"V"`, taken raw between the quotes) and
    the shell-safe form write_clauderc writes (single quotes; a quote inside V becomes
    quote, backslash, quote, quote)."""
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1]
    if v.startswith("'"):
        out, i, n = [], 0, len(v)
        while i < n:
            if v[i] == "'":
                j = v.find("'", i + 1)
                if j < 0:
                    break
                out.append(v[i + 1:j])
                i = j + 1
            elif v.startswith("\\'", i):
                out.append("'")
                i += 2
            else:
                break
        else:
            return "".join(out)
        return v[1:-1] if v.endswith("'") and len(v) >= 2 else v
    return v


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
        key, val = m.group(1), _unquote_rc_value(m.group(2).strip())
        # Skip empty values — a bare `FOO=` is junk that would only mask
        # the real "not authenticated" error downstream.
        if key.startswith(CRED_PREFIXES) and val:
            out[key] = val
    return out


def parse_clauderc(text: str) -> dict[str, str]:
    """Parse the lines of a clauderc (`export K="V"`, any prefix) into a dict. Same quote rules
    as parse_creds_text but keeps every key and empty values."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        m = CRED_KEY_RE.match(line)
        if m:
            k, v = m.group(1), _unquote_rc_value(m.group(2).strip())
            out[k] = v
    return out


def _sh_quote(v: str) -> str:
    """Single-quote v for a bash `source` (the wrapper and the reviewer skills source clauderc):
    nothing inside is expanded. A quote becomes quote-backslash-quote-quote. CR/LF cannot be stored
    on one line and are dropped."""
    return "'" + re.sub(r"[\r\n]", "", v).replace("'", "'\\''") + "'"


def _write_private(path: Path, data: bytes) -> None:
    """Write bytes to a file that is created mode 0600 (no window where it is group/world readable).
    The mode has no effect on Windows, where the user profile ACL protects the file."""
    flags = os.O_WRONLY
    flags |= os.O_CREAT
    flags |= os.O_TRUNC
    flags |= getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    try:
        path.chmod(0o600)  # a pre-existing file keeps its old mode otherwise
    except OSError:
        pass


def write_clauderc(values: dict[str, str]) -> None:
    """Write env vars to clauderc_path() as `export KEY='VALUE'` lines (mode 0600 from creation)."""
    path = clauderc_path(create=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"export {k}={_sh_quote(str(v))}" for k, v in values.items() if CRED_KEY_RE.match(f"{k}=x")]
    _write_private(path, ("\n".join(lines) + "\n").encode("utf-8"))  # LF on Windows too (also read by bash)


def merge_clauderc(values: dict[str, str]) -> None:
    """Merge `values` into clauderc_path(), preserving any existing
    exports that aren't in `values`. Values from `values` win on collision.
    An existing file that cannot be decoded (not UTF-8/UTF-16) is never overwritten: OSError.
    """
    existing: dict[str, str] = {}
    path = clauderc_path()
    if path.exists():
        try:
            existing = parse_clauderc(_decode_text_strict(path.read_bytes()))
        except ValueError:
            raise OSError(f"{path} is not valid UTF-8/UTF-16 text; it was left untouched") from None
    existing.update(values)
    write_clauderc(existing)

def merge_gemini_env(values: dict[str, str], remove=None) -> None:
    """Merge `values` into ~/.gemini/.env (dotenv format), the file the
    Gemini CLI auto-loads. Preserves existing keys; `values` win on collision.
    Any key in `remove` is deleted from the result.
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
    for k in (remove or ()):
        existing.pop(k, None)
    GEMINI_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    # dotenv format — plain KEY="VALUE", no `export` (the CLI's loader
    # does not strip it).
    lines = [f'{k}="{v}"' for k, v in existing.items()]
    GEMINI_ENV_PATH.write_text("\n".join(lines) + "\n")
    try:
        GEMINI_ENV_PATH.chmod(0o600)
    except OSError:
        pass


def _env_file_value(text: str, key: str) -> str:
    """Return the value of `key` in a dotenv/export blob, or '' if absent."""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        m = CRED_KEY_RE.match(line)
        if m and m.group(1) == key:
            v = m.group(2).strip()
            if (v.startswith('"') and v.endswith('"')) or \
               (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            return v
    return ""


def finalize_gemini_vals(gemini_vals: dict[str, str]):
    """Pick a Gemini-CLI auth mode that will actually authenticate.

    The Gemini CLI accepts exactly:
      - GEMINI_API_KEY                       → AI Studio (API keys WORK here)
      - GOOGLE_GENAI_USE_VERTEXAI=true + ADC → Vertex (API keys are REJECTED;
                                               needs OAuth / service account)
      - GOOGLE_GENAI_USE_GCA=true            → OAuth / Code Assist

    A bare API key must therefore go to AI Studio, never Vertex. We switch to
    Vertex only on real intent: the flag is in *this* paste, or a service
    account / ADC file is configured. Otherwise an API key (pasted as either
    GEMINI_API_KEY or GOOGLE_API_KEY) is routed to AI Studio — and any stale
    GOOGLE_GENAI_USE_VERTEXAI from a previous paste is turned back off so the
    user isn't trapped on the Vertex path.

    Returns (values_to_set, keys_to_remove).
    """
    out = dict(gemini_vals)
    drop: set[str] = set()
    existing = ""
    if GEMINI_ENV_PATH.exists():
        try:
            existing = GEMINI_ENV_PATH.read_text()
        except OSError:
            pass

    def present(key: str) -> bool:
        return key in out or _env_file_value(existing, key) != ""

    flag_in_paste = str(gemini_vals.get("GOOGLE_GENAI_USE_VERTEXAI", "")) \
        .strip().lower() in ("1", "true", "yes")
    vertex_intent = flag_in_paste or present("GOOGLE_APPLICATION_CREDENTIALS")

    if vertex_intent:
        out["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        if not present("GOOGLE_CLOUD_LOCATION"):
            out["GOOGLE_CLOUD_LOCATION"] = DEFAULT_VERTEX_LOCATION
    elif present("GOOGLE_GENAI_USE_GCA"):
        pass  # OAuth / Code Assist — nothing to inject
    else:
        # API-key path → AI Studio. The CLI reads AI Studio keys from
        # GEMINI_API_KEY, so map GOOGLE_API_KEY onto it, and make sure Vertex
        # isn't (still) forced on.
        gkey = out.get("GOOGLE_API_KEY") or _env_file_value(existing, "GOOGLE_API_KEY")
        if not present("GEMINI_API_KEY") and gkey:
            out["GEMINI_API_KEY"] = gkey
        if present("GEMINI_API_KEY") or gkey:
            out["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
            # Standardise on GEMINI_API_KEY: drop GOOGLE_API_KEY so the CLI
            # doesn't warn "Both ... set. Using GOOGLE_API_KEY" and pick the
            # wrong one.
            out.pop("GOOGLE_API_KEY", None)
            drop.add("GOOGLE_API_KEY")
    return out, drop


_INI_HEADER_RE = re.compile(r"^\s*\[([^\]\r\n]*)\]")


def replace_ini_section(text: str, header: str, body: str) -> str:
    """Replace the section `[header]` of an ini text with `body` (the complete new section including
    its header line) and keep every other byte. Trailing blank/comment lines of the old section stay
    (they usually introduce the next section). Further duplicates of the section are removed. A
    missing section is appended. Line ending style follows the file."""
    eol = "\r\n" if "\r\n" in text else "\n"
    body = body.replace("\n", eol)
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i, placed = 0, False
    while i < len(lines):
        m = _INI_HEADER_RE.match(lines[i].lstrip("\ufeff"))
        if m and m.group(1).strip() == header:
            j = i + 1
            while j < len(lines) and not _INI_HEADER_RE.match(lines[j]):
                j += 1
            k = j
            while k > i + 1 and (not lines[k - 1].strip() or lines[k - 1].lstrip().startswith(("#", ";"))):
                k -= 1
            if not placed:
                out.append(lines[i][:1] if lines[i].startswith("\ufeff") else "")  # keep a BOM
                out.append(body)
                placed = True
            out.extend(lines[k:j])
            i = j
        else:
            out.append(lines[i])
            i += 1
    joined = "".join(out)
    if not placed:
        if joined and not joined.endswith(("\n", "\r")):
            joined += eol
        if joined.strip() and not joined.endswith(eol + eol):
            joined += eol  # blank line before the new section
        joined += body
    return joined


def _merge_ini_file(path: Path, header: str, body: str) -> None:
    """replace_ini_section on a file: other sections stay byte for byte, a one-time `.maude-bak`
    copy is taken before the first change, undecodable files are refused (OSError)."""
    raw = path.read_bytes() if path.exists() else b""
    try:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            raise ValueError("utf-16")
        text = raw.decode("utf-8")
    except ValueError:
        raise OSError(f"{path} is not UTF-8 text; it was left untouched") from None
    new = replace_ini_section(text, header, body)
    if new == text:
        return
    bak = path.with_name(path.name + ".maude-bak")
    if raw and not bak.exists():
        shutil.copy2(path, bak)
    _write_private(path, new.encode("utf-8"))


def write_aws_credentials(access_key: str, secret_key: str, region: str) -> None:
    """Persist AWS creds to ~/.aws/credentials and ~/.aws/config so that
    boto3 / claude-wrapper / kanna can pick them up.

    Uses the [bedrock] profile to match the convention in the maude
    welcome banner (which suggests `aws --profile bedrock configure`).
    Only that profile is replaced: the user's other profiles, SSO config and comments stay
    byte for byte (a `.maude-bak` copy is made before the first change).
    """
    one = lambda v: re.sub(r"[\r\n]", "", v)
    AWS_CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _merge_ini_file(AWS_CREDS_PATH, "bedrock",
                    f"[bedrock]\naws_access_key_id = {one(access_key)}\n"
                    f"aws_secret_access_key = {one(secret_key)}\n")
    _merge_ini_file(AWS_CONFIG_PATH, "profile bedrock",
                    f"[profile bedrock]\nregion = {one(region)}\n")

def have_bedrock_creds() -> bool:
    return AWS_CREDS_PATH.exists() and AWS_CREDS_PATH.stat().st_size > 0


def have_foundry_creds() -> bool:
    if os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"):
        return True
    # Check both the active clauderc and the parked .inactive sibling
    # (the latter exists when Bedrock is currently the default).
    for p in (clauderc_path(), clauderc_inactive_path()):
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
    Plus a fourth tab-row button that is an action, not a mode:
      • Claude Login — suspends the TUI and runs the unwrapped
        ~/.local/bin/claude /login for native claude.ai OAuth; dismisses
        the modal as saved when a valid token lands in
        ~/.claude/.credentials.json.
    All modes save to clauderc_path() (merged; ~/.azure/clauderc in the
    sandbox, ~/.maude/clauderc on Windows/macOS) so existing creds
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
                yield Button("Claude Login", id="btn-creds-login",
                             variant="default")
            with Container(id="creds-body"):
                yield from self._compose_body()
            yield Label("", id="creds-status")
            yield Checkbox("Default to AWS Bedrock (uncheck → Foundry)",
                           id="creds-default-bedrock", value=False)
            with Horizontal(id="creds-buttons"):
                yield Button("Save",   variant="success", id="btn-creds-save")
                yield Button("Cancel", variant="primary", id="btn-creds-cancel")

    def on_mount(self) -> None:
        # compose() already shows the paste pane; defer the rest until the children exist (on a slow
        # runner the mount event can run before they are mounted).
        self.call_after_refresh(self._init_ui)

    def _init_ui(self) -> None:
        try:
            self._switch_mode(self.MODE_PASTE)
            self._update_default_checkbox()
        except NoMatches:  # screen already closed
            return
        if is_native() and have_claude_login():
            self._set_status("[yellow]A Claude login is active and takes precedence over saved cloud "
                             "credentials. Run `claude /logout` to use them.[/]")

    # ── Mode switcher ──────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-creds-mode-paste")
    def mode_paste(self) -> None:
        self._switch_mode(self.MODE_PASTE)

    @on(Button.Pressed, "#btn-creds-mode-bedrock")
    def mode_bedrock(self) -> None:
        self._switch_mode(self.MODE_BEDROCK)

    @on(Button.Pressed, "#btn-creds-mode-foundry")
    def mode_foundry(self) -> None:
        self._switch_mode(self.MODE_FOUNDRY)

    @on(Button.Pressed, "#btn-creds-login")
    def claude_login(self) -> None:
        """Native claude.ai OAuth login (subscription, no API key).

        Runs the UNWRAPPED binary: with no cloud creds configured, the
        ~/bin/claude wrapper exits with "No valid configuration found"
        before Claude Code ever starts, so /login can't complete through
        it. Interop is disabled in the sandbox, so Claude Code prints the
        OAuth URL for the user to open in a Windows browser manually.
        """
        if is_native():  # Windows/macOS: the real claude opens the browser itself
            exe = plat_real_claude()
            if exe == "claude" and not plat_tool("claude"):
                self._set_status("[bold red]Claude Code was not found. Install it first, then try again.[/]")
                return
            with self.app.suspend():
                print("Starting Claude Code for /login: your browser opens, finish signing in "
                      "there.\nExit Claude Code when done.", flush=True)
                try:
                    subprocess.run([exe, "/login"])
                except KeyboardInterrupt:
                    pass  # user Ctrl+C'd out of claude: not an error
                except OSError as err:
                    print(f"Could not run claude: {err}")
            if have_claude_login() or claude_login_status() is True:
                self.dismiss(True)
            else:
                self._set_status(
                    "[bold yellow]Login not completed: Claude reports no login. "
                    "Try again, or use one of the other methods.[/]")
            return
        claude_bin = Path.home() / ".local" / "bin" / "claude"
        if not claude_bin.exists():
            self._set_status(
                "[bold red]~/.local/bin/claude not found — "
                "is Claude Code installed?[/]")
            return
        with self.app.suspend():
            print("Starting Claude Code for /login — copy the OAuth URL "
                  "into a Windows browser, then paste the code back here.\n"
                  "Exit Claude Code when done.", flush=True)
            try:
                subprocess.run([str(claude_bin), "/login"])
            except KeyboardInterrupt:
                pass  # user Ctrl+C'd out of claude — not an error
            except OSError as err:
                print(f"Could not run claude: {err}")
        if have_claude_login():
            self.dismiss(True)
        else:
            self._set_status(
                "[bold yellow]Login not completed — no valid token found. "
                "Try again, or use one of the other methods.[/]")

    def _switch_mode(self, mode: str) -> None:
        # All three mode bodies stay mounted from compose(); switching only toggles `display` and the
        # button variants. Nothing is removed or re-mounted, so a rapid click or a click right after the
        # screen opens cannot race a TextArea that is still rendering (textual KeyError text-area--gutter,
        # NoMatches). save() reads only the pane of self.mode, so hidden fields never count.
        self.mode = mode
        try:
            self._apply_mode(mode)
        except NoMatches:  # children not mounted yet, or the screen is closing
            pass

    def _apply_mode(self, mode: str) -> None:
        for btn_id, btn_mode in (
            ("#btn-creds-mode-paste",   self.MODE_PASTE),
            ("#btn-creds-mode-bedrock", self.MODE_BEDROCK),
            ("#btn-creds-mode-foundry", self.MODE_FOUNDRY),
        ):
            self.query_one(btn_id, Button).variant = "primary" if mode == btn_mode else "default"
        for pane_mode, pane_id in self._PANES.items():
            self.query_one(pane_id).display = (pane_mode == mode)
        self._focus_later(self._FOCUS[mode])

    _PANES = {"paste": "#creds-pane-paste", "bedrock": "#creds-pane-bedrock", "foundry": "#creds-pane-foundry"}
    _FOCUS = {"paste": "#creds-text", "bedrock": "#creds-aws-key", "foundry": "#creds-foundry-url"}

    def _focus_later(self, selector: str) -> None:
        def go() -> None:
            try:
                if self.query_one(selector).display and self.is_attached:
                    self.query_one(selector).focus()
            except Exception:  # the widget is gone (screen closed): nothing to focus
                pass
        self.call_after_refresh(go)

    def _compose_body(self) -> ComposeResult:
        with Vertical(id="creds-pane-paste", classes="creds-pane"):
            yield Label(
                "Paste shell `export` lines or KEY=VALUE pairs. Recognised "
                "prefixes: ANTHROPIC_*, CLAUDE_*, AWS_*, AZURE_*, OPENAI_*. "
                "For Gemini, paste an API key (GEMINI_API_KEY or GOOGLE_API_KEY "
                "from aistudio.google.com/apikey) — it's routed to AI Studio, no "
                "OAuth. Vertex (needs a service account) is used only if you paste "
                "GOOGLE_GENAI_USE_VERTEXAI=true / GOOGLE_APPLICATION_CREDENTIALS. "
                "Saved to ~/.gemini/.env."
            )
            yield TextArea("", id="creds-text", language=None, show_line_numbers=False)
        with Vertical(id="creds-pane-bedrock", classes="creds-pane"):
            yield Label("AWS Bedrock — region defaults to us-west-2.")
            yield Label("AWS_ACCESS_KEY_ID:")
            yield Input(placeholder="AKIA…", id="creds-aws-key")
            yield Label("AWS_SECRET_ACCESS_KEY:")
            yield Input(placeholder="secret", id="creds-aws-secret", password=True)
            yield Label("AWS_REGION:")
            yield Input(value="us-west-2", id="creds-aws-region")
        with Vertical(id="creds-pane-foundry", classes="creds-pane"):
            yield Label("Azure AI Foundry — Anthropic-compatible endpoint.")
            yield Label("ANTHROPIC_FOUNDRY_BASE_URL:")
            yield Input(placeholder="https://<resource>.services.ai.azure.com/anthropic",
                        id="creds-foundry-url")
            yield Label("ANTHROPIC_FOUNDRY_API_KEY:")
            yield Input(placeholder="api key", id="creds-foundry-key", password=True)
    def _update_default_checkbox(self) -> None:
        """Show the Bedrock-vs-Foundry default toggle only when both
        credential families are present on disk (active or parked)."""
        cb = self.query_one("#creds-default-bedrock", Checkbox)
        if have_bedrock_creds() and have_foundry_creds():
            cb.display = True
            # If clauderc is parked as .inactive, Bedrock is the
            # currently-default provider; check the box accordingly.
            parked = clauderc_inactive_path().exists() and not clauderc_path().exists()
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
            # Infer the right auth mode (AI Studio vs Vertex) and standardise
            # on GEMINI_API_KEY so a partial paste actually authenticates.
            gemini_vals, drop = finalize_gemini_vals(gemini_vals)
            merge_gemini_env(gemini_vals, remove=drop)
            # Reflect into this session too: set the kept vars, clear dropped.
            values = {**values, **gemini_vals}
            for k in drop:
                values.pop(k, None)
                os.environ.pop(k, None)
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
        active   = clauderc_path()
        inactive = clauderc_inactive_path()
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


SEP_KEY = "__sep__"  # key of the rule row between no-sandbox and share projects (Windows)


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Ask the user to confirm deletion."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def action_cancel(self) -> None:
        self.dismiss(False)

    def __init__(self, project_name: str, outside: bool = False) -> None:
        super().__init__()
        self.project_name = project_name
        self.outside = outside

    def compose(self) -> ComposeResult:
        with Container(id="confirm-box"):
            yield Label(f"Delete project '{self.project_name}'?", id="confirm-title")
            yield Label("Removed from the Maude list only. The folder is not deleted."
                        if self.outside else
                        "It will be moved to .deleted/  and can be recovered manually.",
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


class NewProjectScreen(ModalScreen[Optional[str]]):  # not `str | None`: evaluated at runtime, Python 3.9 fails
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


# ── Update modal ───────────────────────────────────────────────────────────

# Update Maude on Windows and macOS (the sandbox runs `maude update`, see UpdateMaudeScreen) ──

def native_update_steps(platform: str, has_sandbox: bool, claude: str | None) -> list:
    """Steps of Update Maude outside the sandbox, as (kind, label, argv). kind: "exe" (replace
    maude.exe from the latest release), "macpy" (validated fetch of maude.py), "cmd" (run argv)."""
    steps = [("exe", "Updating maude.exe...", None)] if platform == "windows" \
        else [("macpy", "Updating maude.py...", None)]
    if claude:
        steps.append(("cmd", "Updating Claude Code...", [claude, "update"]))
    if platform == "windows" and has_sandbox:
        steps.append(("cmd", "Updating the Maude sandbox...", ["wsl.exe", "-d", "Maude", "--", "bash", "-lc", "maude update"]))
    return steps


def stream_cmd(cmd: list, say, env=None) -> int:
    """Run cmd and say() each output line (ANSI removed, stderr merged). Exit code, -1 if it cannot start."""
    ansi = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
    try:
        # CREATE_NO_WINDOW (Windows only): output is piped, so no console window must pop up
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=env,
                             creationflags=0x08000000 if sys.platform == "win32" else 0)
    except OSError as err:
        say(f"✗ could not start {cmd[0]}: {err}")
        return -1
    for raw in p.stdout:
        say(ansi.sub("", raw.decode("utf-8", "replace")).rstrip())
    return p.wait()


def fetch_maude_py(target: Path, say, urlopen=urllib.request.urlopen, validate=None) -> str:
    """macOS Update Maude: download maude.py and replace target, with the checks of maybe_self_update
    (static validation, then a subprocess import). Returns "updated", "current", "skipped" (inside a
    git checkout: git pull is the way there) or "failed"."""
    validate = validate or import_check_update
    try:
        for parent in target.resolve().parents:
            if (parent / ".git").exists():
                say(f"maude.py is inside a git checkout ({parent}): not replacing it. Use git pull there.")
                return "skipped"
        with urlopen(f"{UPDATE_URL}?cache={int(time.time())}", timeout=20) as resp:
            data = resp.read()
        if not valid_update(data, str(target)):
            say("✗ downloaded maude.py failed validation; keeping the current version")
            return "failed"
        if target.is_file() and target.read_bytes() == data:
            say("maude.py is up to date.")
            return "current"
        tmp = target.with_name(target.name + ".new")
        try:
            tmp.write_bytes(data)
            why = validate(str(tmp))
            if why:
                say(f"✗ downloaded maude.py failed validation; keeping the current version ({why})")
                return "failed"
            tmp.replace(target)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        say("✓ maude.py updated.")
        return "updated"
    except Exception as err:
        say(f"✗ maude.py update failed: {err}")
        return "failed"


def native_update(say, stream=stream_cmd) -> tuple:
    """Run Update Maude outside the sandbox, saying every line. Returns (failed steps, reload) where
    reload is "must" (a new maude.exe is staged: Close and Escape reload too),
    "ok" (maude.py was replaced: Reload offered) or "" (nothing to reload)."""
    errors, reload = 0, ""
    steps = native_update_steps(PLATFORM, HAS_SANDBOX, plat_tool("claude"))
    for kind, label, cmd in steps:
        say(label)
        if kind == "exe":
            st = mx.update_exe(say)
            errors += st == "failed"
            if st == "staged" or mx.staged_tag():
                reload = "must"
        elif kind == "macpy":
            st = fetch_maude_py(Path(__file__).resolve(), say)
            errors += st == "failed"
            if st == "updated":
                reload = "ok"
        else:
            env = _child_env()
            if cmd[0] == "wsl.exe":  # UTF-8 messages from wsl.exe itself (default is UTF-16)
                env = {**(env if env is not None else os.environ), "WSL_UTF8": "1"}
            rc = stream(cmd, say, env)
            if rc != 0:
                say(f"⚠ {cmd[0]} exited {rc}")
                errors += 1
    return errors, reload


class UpdateMaudeScreen(ModalScreen[None]):
    """Modal that runs 'maude update' and streams all output with backscroll."""

    BINDINGS = [Binding("escape", "close", "Close", show=False)]
    _busy = False         # native update running: ignore Escape
    _must_reload = False  # a new maude.exe is staged: closing means reloading
    _reload_ok = False    # native: something to reload (the sandbox always offers Reload)

    DEFAULT_CSS = """
    UpdateMaudeScreen {
        align: center middle;
    }
    #update-box {
        width: 90%;
        height: 85%;
        background: #1a1a1a;
        border: solid #b87878;
        padding: 1 2;
    }
    #update-title {
        color: #d4a0a0;
        text-style: bold;
        margin-bottom: 1;
    }
    #update-log {
        height: 1fr;
        background: #0e0e0e;
        border: solid #3a3a3a;
        padding: 0 1;
    }
    #update-status {
        color: #a09090;
        margin-top: 1;
        margin-bottom: 1;
    }
    #update-buttons {
        height: auto;
        margin-top: 1;
    }

    #update-buttons Button {
        margin-right: 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="update-box"):
            yield Label("Update Maude", id="update-title")
            yield RichLog(id="update-log", auto_scroll=True, markup=True)
            yield Label("Running...", id="update-status")
            with Horizontal(id="update-buttons"):
                yield Button("Reload UI", id="btn-update-reload",
                             variant="success", disabled=True)
                yield Button("Close",     id="btn-update-close",
                             variant="primary", disabled=True)

    def on_mount(self) -> None:
        self.run_worker(self._run_update(), exclusive=True)

    @staticmethod
    def _colorize(line: str) -> Text:
        """Apply truecolor styling to a stripped update output line."""
        stripped = line.strip()
        if stripped.startswith("✓") or stripped.startswith("✓"):
            return Text(line, style="#72c09a")          # green — success
        if stripped.startswith("✗") or stripped.startswith("✗"):
            return Text(line, style="#e07070")          # red — failure
        if stripped.startswith("⚠") or stripped.startswith("WARNING"):
            return Text(line, style="#e8a040")          # amber — warning
        if stripped.startswith("maude version:"):
            return Text.assemble(
                (line.split(":")[0] + ": ", "#a09090"),
                (line.split(":", 1)[1].strip(), "bold #c8f0e0"),
            )
        # Section headers end with "..." and are bold
        if stripped.endswith("...") and len(stripped) < 60:
            return Text(line, style="bold #d4a0a0")
        # Note/info lines
        if stripped.startswith("Note:") or stripped.startswith("Running weekly"):
            return Text(line, style="#7090b0")
        return Text(line, style="#a8a8a8")

    async def _run_update(self) -> None:
        log = self.query_one("#update-log", RichLog)
        status = self.query_one("#update-status", Label)
        close_btn = self.query_one("#btn-update-close", Button)

        maude_bin = shutil.which("maude") or str(Path.home() / "bin" / "maude")
        ansi_re = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

        try:
            if NATIVE:  # Windows / macOS: steps run in Python, see native_update()
                def say(line: str) -> None:
                    self.app.call_from_thread(log.write, self._colorize(line))
                self._busy = True
                try:
                    errors, reload = await asyncio.to_thread(native_update, say)
                finally:
                    self._busy = False
                self._reload_ok, self._must_reload = bool(reload), reload == "must"
                tail = " Press Reload UI to switch to the new version." if self._must_reload \
                    else " Reload to run the new version." if reload else ""
                if errors:
                    status.update(f"[#e07070]⚠ Update finished with {errors} error(s).{tail}[/#e07070]")
                else:
                    status.update(f"[#72c09a]✓ Update complete.{tail}[/#72c09a]")
                return
            proc = await asyncio.create_subprocess_exec(
                maude_bin, "update",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "TERM": "dumb"},
            )
            async for raw in proc.stdout:
                line = ansi_re.sub("", raw.decode(errors="replace")).rstrip()
                log.write(self._colorize(line))
            await proc.wait()
            rc = proc.returncode
            if rc == 0:
                status.update("[#72c09a]✓ Update complete. Reload to run the new version.[/#72c09a]")
            else:
                status.update(f"[#e07070]⚠ Update finished with errors (exit {rc}).[/#e07070]")
        except Exception as exc:
            log.write(Text(f"Error: {exc}", style="#e07070"))
            status.update("[#e07070]✗ Update failed.[/#e07070]")
        finally:
            reload_btn = self.query_one("#btn-update-reload", Button)
            close_btn.disabled = self._must_reload
            reload_btn.disabled = NATIVE and not self._reload_ok
            (close_btn if reload_btn.disabled else reload_btn).focus()

    def action_close(self) -> None:
        if not self._busy:
            self.dismiss(self._must_reload)  # a staged maude.exe: Escape reloads, there is no way back

    @on(Button.Pressed, "#btn-update-close")
    def close_pressed(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#btn-update-reload")
    def reload_pressed(self) -> None:
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
        width: 41;
        padding: 0 1;
        background: #242424;
        border-right: solid #b87878;
    }

    #logo {
        height: 5;
        color: #72c09a;
        text-style: bold;
        margin-bottom: 0;
    }

    #divider {
        color: #6a5058;
        height: 1;
        margin-bottom: 0;
    }

    #pick-row {
        height: auto;
    }

    .opt-col {
        width: 1fr;
        height: auto;
    }

    /* three pickers share the sidebar width: model | AI tool | thinking */
    #pick-row #model-col { width: 11; }
    #pick-row #tool-col  { width: 16; }
    #pick-row #effort-col { width: 11; }

    /* Compact one-line toggles: box left of the label, no border/padding, all in one column. */
    #sidebar Checkbox {
        border: none;
        padding: 0;
        margin: 0;
        height: 1;
        width: 100%;
    }

    #sidebar Checkbox:focus {
        border: none;
    }

    #maude-version {
        color: #6a5058;
        margin-top: 0;
    }

    #divider2 {
        color: #6a5058;
        height: 1;
        margin-top: 1;
        margin-bottom: 0;
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
        margin-bottom: 0;
    }

    #model-label, #tool-label, #effort-label {
        color: #d4a0a0;
        text-style: bold;
        margin-bottom: 0;
    }

    #model-select, #tool-select, #effort-select {
        background: #242424;
        border: none;
        padding: 0;
        height: auto;
    }

    #model-select:focus-within, #tool-select:focus-within, #effort-select:focus-within {
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

    Checkbox.-on > .toggle--button {
        color: #72c09a;
    }

    RadioButton.-on > .toggle--button {
        color: #e8a040;
    }

    RadioButton.-on {
        color: #f8d090;
        text-style: bold;
    }

    /* Radio cursor: no Textual blue/green block, and none at all when the set is
       not focused (otherwise a stale cursor row looks like a second selection). */
    RadioSet:blur > RadioButton.-selected > .toggle--label,
    RadioButton:blur:hover > .toggle--label {
        background: transparent;
    }

    RadioSet:focus > RadioButton.-selected > .toggle--label {
        background: #3a3030;
        color: #f8d090;
        text-style: bold;
    }

    ScrollBar {
        color: #b87878;
        background: #1a1a1a;
    }

    ScrollBar:hover {
        color: #d4a0a0;
    }

    Input:focus, TextArea:focus {
        border: double #b87878;
    }

    #main {
        padding: 1 2;
    }

    #win-warning {
        height: auto;
        margin-bottom: 1;
        padding: 0 1;
        color: #ffffff;
        background: #a82a2a;
        text-style: bold;
    }

    #main-header {
        height: auto;
        margin-bottom: 1;
        align: left middle;
    }

    #section-title {
        color: #d4a0a0;
        text-style: bold;
        width: auto;
    }

    #projects-table {
        height: 1fr;
        border: solid #b87878;
    }

    DataTable > .datatable--header {
        color: #d4a0a0;
    }

    DataTable > .datatable--cursor {
        background: #3a2a10;
        color: #f8e8c0;
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

    #btn-open   { color: #7de8b0; border: tall #3a6a50; }
    #btn-new    { color: #70c8e8; border: tall #305870; }
    #btn-web    { color: #c8b070; border: tall #605030; }
    #btn-update { color: #e8a040; border: tall #704818; }
    #btn-cli    { color: #908080; border: tall #504040; }
    #btn-github { color: #d0b8e8; border: tall #604878; }
    #btn-togithub { color: #b8d0e8; border: tall #486078; }
    #btn-creds  { color: #e8c8b0; border: tall #785840; }

    #kanna-url {
        color: #72c09a;
        width: 1fr;
        content-align: right middle;
        height: 1;
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

    #creds-body .creds-pane {
        height: auto;
    }

    #creds-pane-bedrock, #creds-pane-foundry {
        display: none;  /* paste is the initial mode; _switch_mode toggles these */
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

    def __init__(self, select: str = "", model: str = "") -> None:
        """select: Windows/macOS only. Folder (outside the share) preselected in the list, with the
        outside-the-sandbox warning shown; it is listed even if it is not in the registry.
        model: optional model alias to preselect (ignored unless it is one of MODELS)."""
        super().__init__()
        self._select = os.path.realpath(select) if ((IS_WIN or IS_MAC) and select) else ""
        self._extra: tuple = (self._select,) if self._select else ()
        self._bar_on = False  # the warning bar (#win-warning) is active
        self._tool = read_tool()
        want = model if model in MODELS else ""
        saved_model, self._effort = read_model_effort()
        model = want or saved_model
        self._model, self._tool = reconcile(model, self._tool, "model" if want else "tool")
        if want:
            save_model(self._model)
            save_tool(self._tool)
        elif self._model != model:
            save_model(self._model)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Static(make_gradient_logo(), id="logo")
                yield Static("─" * 28, id="divider")
                yield Label("Tips", id="tips-title")
                yield Static(
                    "Screen split:  Alt+Shift+Plus | Minus\n"
                    + ("Paste image:   Alt+V\n" if IS_WIN else "Paste image:   Ctrl+V\n" if IS_MAC
                       else "Share image:   drop into ~/Maude\n") +
                    ("Voice dictate: Fn twice (macOS Dictation)" if IS_MAC else "Voice dictate: Win+H (Windows mic)"),
                    id="tips",
                )
                yield Static("─" * 28, id="divider2")
                with Horizontal(id="pick-row"):
                    with Vertical(classes="opt-col", id="model-col"):
                        yield Label("Model", id="model-label")
                        with RadioSet(id="model-select"):
                            for m in MODELS:
                                yield RadioButton(m, value=(m == self._model))
                    with Vertical(classes="opt-col", id="tool-col"):
                        yield Label("AI tool", id="tool-label")
                        with RadioSet(id="tool-select"):
                            for t in AI_TOOLS:
                                yield RadioButton(t, value=(t == self._tool))
                    with Vertical(classes="opt-col", id="effort-col"):
                        yield Label("Thinking", id="effort-label")
                        with RadioSet(id="effort-select"):
                            for e in EFFORT_LABELS:
                                yield RadioButton(e, value=(e == (self._effort or "auto")))
                yield Static("─" * 28, id="divider3")
                # One column of one-line checkboxes (box to the left of the label), boxes aligned.
                yield Checkbox("Clear History", value=False, id="fresh-context")
                yield Checkbox("Perplexity MCP", value=PERPLEXITY_FLAG.exists(), id="perplexity")
                yield Checkbox("Autostart", value=not DISABLE_FLAG.exists(), id="autostart")
                yield Static(
                    Text.assemble(
                        ("  maude ", "#505050"),
                        (f" {get_maude_version()} ", "bold #c8f0e0 on #1a3a2a"),
                    ),
                    id="maude-version",
                )
            with Vertical(id="main"):
                yield Static("", id="win-warning")  # shown only when started on a folder (Explorer menu)
                with Horizontal(id="main-header"):
                    yield Label("Projects", id="section-title")
                    yield Static("", id="kanna-url")
                yield DataTable(id="projects-table", cursor_type="row",
                                zebra_stripes=True)
        with Horizontal(id="bottom-bar"):
            yield Button("Open Project",    id="btn-open")
            yield Button("+ New",           id="btn-new")
            yield Button("Web UI",          id="btn-web")
            yield Button("To Github",       id="btn-togithub")
            yield Button("Setup Git(hub)",  id="btn-github")
            yield Button("Set Creds",       id="btn-creds")
            yield Button("Update Maude",   id="btn-update")
            yield Button("Command Line",    id="btn-cli")
        yield Footer()

    def on_mount(self) -> None:
        self._kanna_proc: subprocess.Popen | None = None
        for i in hidden_widgets():  # per-platform feature map (FEATURES)
            self.query_one(i).display = False
        if IS_WIN:
            if HAS_SANDBOX:
                self.query_one("#logo").styles.height = 6  # room for the caption line
        warn = self.query_one("#win-warning", Static)
        warn.display = False
        self._refresh_table()
        if self._select:
            self._bar_on = True
            warn.display = True
            found = self._select_project(Path(self._select))  # Enter sentence only if that row has the cursor
            self._update_bar(self._select if found else None)
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
        self._sync_cursor("#model-select", MODELS, self._model)
        self._sync_cursor("#tool-select", AI_TOOLS, self._tool)
        self._sync_cursor("#effort-select", EFFORT_LABELS, self._effort or "auto")

    def _sync_cursor(self, sel: str, items: tuple, value: str) -> None:
        try:
            radioset = self.query_one(sel, RadioSet)
        except Exception:
            return
        try:
            idx = items.index(value)
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
        if IS_WIN or IS_MAC:  # not yet supported natively (SSH/Git setup package)
            return
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
        table.add_columns("  Project", "Last modified", "Features")
        now = time.time()
        projs = list_projects(self._extra)
        # Windows: one rule row between the no-sandbox and the share group.
        sep_at = next((i for i in range(1, len(projs))
                       if projs[i - 1]["outside"] and not projs[i]["outside"]), None)
        for n, proj in enumerate(projs):
            if n == sep_at:
                w = [max(len(c) for c in col) for col in zip(
                    ("  Project", "Last modified", "Features"),
                    *((f"  {q['label']}", q["modified"], features(q["github"], q["outside"]) or "·") for q in projs))]
                table.add_row(*(Text("─" * x, style="dim") for x in w), key=SEP_KEY)
            age = now - proj["mtime"] if proj["mtime"] else float("inf")
            if age < 86400:            # today
                name_style  = "bold #f0f0f0"
                time_style  = "#72c09a"
            elif age < 7 * 86400:      # this week
                name_style  = "#d8d0d0"
                time_style  = "#a0b898"
            elif age < 30 * 86400:     # this month
                name_style  = "#b0a8a8"
                time_style  = "#808880"
            else:                      # older — use dim+italic so text stays readable
                name_style  = "dim #a09090"
                time_style  = "italic #888080"
            feats = features(proj["github"], proj["outside"])
            table.add_row(
                Text(f"  {proj['label']}", style=name_style),
                Text(proj["modified"],    style=time_style),
                Text(feats or "·", style="#72c09a" if feats else "#504040"),
                key=str(proj["path"]),
            )

    def _selected_project(self) -> Path | None:
        table = self.query_one("#projects-table", DataTable)
        if table.row_count == 0 or table.cursor_row < 0:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        if key == SEP_KEY:
            return None
        path = Path(key)
        return path if path.exists() else None

    @on(events.MouseDown, "#projects-table")
    def _table_mouse_down(self) -> None:
        self._clicked = True

    @on(DataTable.RowHighlighted, "#projects-table")
    def _skip_separator(self, event: DataTable.RowHighlighted) -> None:
        """The rule row is not selectable: step over it in the direction of travel."""
        row, last = event.cursor_row, getattr(self, "_last_row", -1)
        if event.row_key is None or event.row_key.value != SEP_KEY:
            self._last_row = row
            self._clicked = False  # a click on a normal row must not steer the next key press
            self._update_bar(event.row_key.value if event.row_key is not None else None)
            return
        down = getattr(self, "_clicked", False) or row >= last
        self._clicked = False
        self._last_row = row + 1 if down else row - 1
        event.data_table.move_cursor(row=self._last_row)

    def _is_outside(self, path: Path) -> bool:
        return (IS_WIN or IS_MAC) and path.parent != PROJECTS_DIR

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
            self.push_screen(ConfirmDeleteScreen(path.name, self._is_outside(path)), self._on_confirm_delete)

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
        src = '. "$HOME/.local/lib/maude/kanna.sh" 2>/dev/null && '
        try:
            needs_fix = subprocess.run(
                ["bash", "-c", src + "kanna_needs_fix"], check=False, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode == 0
        except Exception:
            needs_fix = False
        try:
            if needs_fix:
                # Reinstall can take ~2 minutes: show the message and bun output.
                with self.suspend():
                    try:
                        subprocess.run(["bash", "-c", src + "ensure_kanna_ready"], check=False)
                    except KeyboardInterrupt:
                        pass  # user Ctrl+C'd the reinstall; launch kanna anyway
            else:
                subprocess.run(
                    ["bash", "-c", src + "ensure_kanna_ready"],
                    check=False, timeout=10,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass
        # Make sure the port isn't held by a stale instance before launching.
        kill_port(KANNA_PORT)
        env = {**os.environ, **kanna_env()}
        # Send kanna's output to a temp log so we can surface errors if it
        # dies on startup. start_new_session detaches kanna from the TUI's
        # controlling terminal: kanna 0.88 touches the tty, and as a background
        # process group of that tty the kernel stopped it (state T, never
        # listening). Its pgid equals its pid, so stop_kanna still kills the tree.
        fd, log_path = tempfile.mkstemp(prefix="maude-kanna-", suffix=".log")
        self._kanna_log = Path(log_path)
        try:
            self._kanna_proc = subprocess.Popen(
                [KANNA_CMD, "--no-open"], env=env,
                stdin=subprocess.DEVNULL,
                stdout=fd, stderr=subprocess.STDOUT,
                start_new_session=True,
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
        url = f"http://127.0.0.1:{KANNA_PORT}"
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

    @on(Button.Pressed, "#btn-update")
    def btn_update(self) -> None:
        def _after_update(reload: bool) -> None:
            if reload:
                self.exit(42)  # 42 = reload signal; launch_tui() in bash re-execs
        self.push_screen(UpdateMaudeScreen(), _after_update)

    @on(Button.Pressed, "#btn-cli")
    def btn_cli(self) -> None:
        self.exit()  # back to the shell that started the TUI (bash, or PowerShell on Windows)

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        """Double-click / Enter on a row opens the project."""
        if event.row_key.value == SEP_KEY:
            return
        path = Path(event.row_key.value)
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

    @on(Checkbox.Changed, "#perplexity")
    def perplexity_toggled(self, event: Checkbox.Changed) -> None:
        try:
            if event.value:
                PERPLEXITY_FLAG.touch()
                self.notify("Perplexity connector will be set up when you open a project")
            else:
                PERPLEXITY_FLAG.unlink(missing_ok=True)
                remove_perplexity()
                self.notify("Perplexity connector removed")
        except OSError as e:
            self.notify(f"Could not save Perplexity setting: {e}", severity="error")

    @on(RadioSet.Changed, "#model-select")
    def model_changed(self, event: RadioSet.Changed) -> None:
        new = str(event.pressed.label)
        if new == self._model:  # echo of a programmatic update
            return
        self._model, tool = reconcile(new, self._tool, "model")
        save_model(self._model)
        if tool != self._tool:
            self._tool = tool
            save_tool(tool)
            self._press("#tool-select", AI_TOOLS, tool)
        self.query_one("#projects-table", DataTable).focus()

    @on(RadioSet.Changed, "#effort-select")
    def effort_changed(self, event: RadioSet.Changed) -> None:
        label = str(event.pressed.label)
        new = "" if label == "auto" else label
        if new == self._effort:
            return
        self._effort = new
        save_effort(new)
        self.query_one("#projects-table", DataTable).focus()

    @on(RadioSet.Changed, "#tool-select")
    def tool_changed(self, event: RadioSet.Changed) -> None:
        new = str(event.pressed.label)
        if new == self._tool:
            return
        self._tool = new
        save_tool(new)
        model, _ = reconcile(self._model, new, "tool")
        if model != self._model:
            self._model = model
            save_model(model)
            self._press("#model-select", MODELS, model)
            self.notify("Fable is only available in Claude Code; switched model to opus")
        self.query_one("#projects-table", DataTable).focus()

    def _press(self, sel: str, items: tuple, value: str) -> None:
        """Select a radio button in code. Set every button and the set's pressed button
        directly: this runs inside the other RadioSet's prevent(RadioButton.Changed), so the
        set never sees the change and would leave the old button on (two selected)."""
        rs = self.query_one(sel, RadioSet)
        buttons = list(rs.query(RadioButton))
        idx = items.index(value)
        with rs.prevent(RadioButton.Changed):
            for i, b in enumerate(buttons):
                b.value = i == idx
        rs._pressed_button = buttons[idx]  # type: ignore[attr-defined]
        self._sync_cursor(sel, items, value)

    # ── Callbacks ─────────────────────────────────────────────────────

    def _launch_project(self, path: Path, *, fresh: bool | None = None) -> None:
        # Gate on credentials so we don't drop the user into a Claude Code
        # session that immediately fails on first turn.
        # `fresh=None` means "read the sidebar checkbox"; callers that know
        # there is no history (e.g. a brand-new project) pass fresh=True so we
        # skip `claude --continue` — otherwise it errors ("No conversation to
        # continue"), flashes on screen, and only then falls back to a fresh
        # launch.
        pre = creds_gate_precheck()
        if is_native() and self._tool != DEFAULT_TOOL:
            pre = False  # the claude login says nothing about Kiro/OpenCode/Crush
        if pre is None:  # Windows/macOS: `claude auth status` can take seconds, so ask it off the UI thread
            if getattr(self, "_checking_login", False):
                return
            self._checking_login = True
            self.notify("Checking sign-in...", timeout=3)

            def ask() -> None:
                try:
                    status = claude_login_status()
                except Exception:
                    status = None
                self.call_from_thread(self._after_login_check, path, fresh, status)
            self.run_worker(ask, thread=True)
            return
        self._gate_or_launch(path, fresh, bool(pre))

    def _after_login_check(self, path: Path, fresh: bool | None, status) -> None:
        self._checking_login = False
        self._gate_or_launch(path, fresh, creds_gate_decision(True, False, False, status))

    def _gate_or_launch(self, path: Path, fresh: bool | None, gate: bool) -> None:
        if gate:  # Windows/macOS: only when claude itself says "not logged in" (see creds_gate_decision)
            self._pending_project = path
            self._pending_fresh = fresh
            self.push_screen(CredsEntryScreen(), self._on_creds_for_project)
            return
        self._launch_project_now(path, fresh=fresh)

    def _on_creds_for_project(self, saved: bool) -> None:
        path = getattr(self, "_pending_project", None)
        fresh = getattr(self, "_pending_fresh", None)
        self._pending_project = None
        self._pending_fresh = None
        # Sandbox: no credentials, no launch. Windows/macOS: Cancel still opens (claude can sign in itself).
        if path is not None and (saved or is_native()):
            self._launch_project_now(path, fresh=fresh)

    def _launch_project_now(self, path: Path, *, fresh: bool | None = None) -> None:
        # Honour the sidebar checkbox when the caller didn't force a value:
        # when checked, skip --continue so Claude starts with a clean
        # conversation.
        if fresh is None:
            try:
                fresh = self.query_one("#fresh-context", Checkbox).value
            except Exception:
                fresh = False
        self._register_selected(path)
        with self.suspend():
            open_project(path, self._model, fresh=fresh, tool=self._tool, effort=self._effort)
        # Auto-clear the checkbox after use — fresh-context is a one-shot
        # exception; the default is always to resume with history.
        if fresh:
            try:
                self.query_one("#fresh-context", Checkbox).value = False
            except Exception:
                pass
        self._refresh_table()
        self._select_project(path)

    def _register_selected(self, path: Path) -> None:
        """Windows: the folder the app was started on (select=) is written to the registry only when
        it is really opened, not when the TUI starts. A registry error is ignored (_extra keeps the
        row listed for this session). A folder inside the share is never written to the Projects key
        (the share projects are listed anyway)."""
        if ((IS_WIN or IS_MAC) and self._select and _pkey(str(path)) == _pkey(self._select)
                and _outside_share(self._select, os.path.realpath(PROJECTS_DIR.parent))):
            try:
                plat_add_outside(self._select)
            except OSError:
                pass

    def _update_bar(self, key: str | None) -> None:
        """The "Press Enter" sentence only while the cursor is on the folder the bar is about."""
        if not self._select or not self._bar_on:
            return
        on_row = key is not None and key != SEP_KEY and _pkey(key) == _pkey(self._select)
        # A RowHighlighted can arrive before the bar is mounted or after the screen is torn down
        # (slow machine, app exit): then there is nothing to update.
        try:
            bar = self.query_one("#win-warning", Static)
        except NoMatches:
            return
        bar.update(Text(
            plat_banner_text(self._select)
            + ("\nPress Enter to open it, or pick another project, model or tool first." if on_row else "")))

    def _select_project(self, path: Path) -> bool:
        """Move the table cursor to the row of `path`. Returns whether the row exists."""
        name = str(path)
        table = self.query_one("#projects-table", DataTable)
        try:
            index = table.get_row_index(name)
        except Exception:
            # DataTable raises its own RowDoesNotExist (a plain Exception,
            # NOT a KeyError) when the row is missing — e.g. the project was
            # deleted (by this session or another) while it was open in
            # Claude. Best-effort cursor restore; just leave it unmoved.
            index = -1
        if index >= 0:
            table.move_cursor(row=index)
        table.focus()
        return index >= 0

    def _on_new_project(self, name: str | None) -> None:
        if not name:
            return
        path = PROJECTS_DIR / name
        path.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["git", "-C", str(path), "init", "--quiet"], check=False)
        except FileNotFoundError:  # Git for Windows missing
            self.notify("git not found: project created without git init", severity="warning")
        self._refresh_table()
        # A just-created project has no prior session, so launch fresh and
        # skip `claude --continue` (which would error + flash on screen).
        self._launch_project(path, fresh=True)

    def _on_confirm_delete(self, confirmed: bool) -> None:
        if not confirmed:
            return
        path = self._selected_project()
        if path and self._is_outside(path):  # registry entry only: never touch the folder
            try:
                plat_remove_outside(REG_ORIG.get(str(path), str(path)))
            except OSError as e:  # the row stays, so keep it in _extra and keep the bar
                self.notify(f"Could not remove '{path.name}': {e}", severity="error")
                return
            self._extra = tuple(e for e in self._extra if _pkey(e) != _pkey(str(path)))
            if self._select and _pkey(self._select) == _pkey(str(path)):
                self._bar_on = False  # the folder the bar is about is gone from the list
                self.query_one("#win-warning", Static).display = False
            self.notify(f"'{path.name}' was removed from the Maude list. The folder still exists: {REG_ORIG.get(str(path), str(path))}. "
                        "Delete it manually if you no longer need it.", timeout=10)
            self._refresh_table()
        elif path:
            try:
                soft_delete(path)
            except OSError as e:  # e.g. folder in use by a shell opened via Command Line
                self.notify(f"Could not delete '{path.name}': {e}", severity="error")
                return
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
    if not (IS_WIN or IS_MAC):  # sandbox only: maude.exe is replaced as a whole, macOS comes with its package
        maybe_self_update()
    while True:
        app = MaudeApp()
        rv = app.run()           # app.run() returns the value passed to self.exit()
        # 42 = "Reload UI" clicked after update. Re-exec this script so the
        # fresh maude.py takes over without touching the shell.
        if rv == 42:
            script = Path(__file__).resolve()
            os.execv(sys.executable, [sys.executable, str(script)] + sys.argv[1:])
        break
