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
import urllib.parse
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
    "togithub":  {"linux-sandbox": True, "windows": True,  "macos": True},
    "github":    {"linux-sandbox": True, "windows": True,  "macos": True},   # Setup Git(hub)
    "creds":     {"linux-sandbox": True, "windows": True,  "macos": True},   # Settings -> ~/.maude/clauderc
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


def replace_retry(src, dst) -> None:
    """os.replace that, on Windows only, retries a transient PermissionError (an antivirus or indexer holding the
    file for a moment): 5 tries, 50 ms apart. Any other error, and the last PermissionError, propagate."""
    tries = 5 if IS_WIN else 1
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05)


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
MAC_GIT_STUB = "/usr/bin/git"  # Apple's stub: without the Command Line Tools it only pops the install dialog
_mac_clt_cache: list = [None]  # per process: does `xcode-select -p` succeed (None = not checked yet)


def mac_clt_ready(run=None) -> bool:
    """macOS: are the Command Line Tools installed (so /usr/bin/git is real git, not Apple's stub)?
    Only a True answer is cached (per process): while the tools are missing it is re-checked, so an
    install done outside Maude is noticed. A timeout counts as not ready. mac_ensure_tool also resets
    the cache after an install attempt. An injected `run` (tests) is never cached."""
    if run is None and _mac_clt_cache[0]:
        return True
    try:
        ok = (run or subprocess.run)(["xcode-select", "-p"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if run is None and ok:
        _mac_clt_cache[0] = True
    return ok


def plat_tool(name: str) -> str | None:
    """Full path of an executable, or None. Windows delegates to maude_exe; macOS also looks in the
    Homebrew and per-user bin dirs (a GUI-launched or fresh shell may not have them on PATH)."""
    if IS_WIN:
        return mx.find_git() if name == "git" else mx.find_gh() if name == "gh" else mx.find_tool(name)
    found = shutil.which(name)
    if not IS_MAC:
        return found
    if found and not (name == "git" and found == MAC_GIT_STUB and not mac_clt_ready()):
        return found
    home = Path.home()
    for d in (*MAC_TOOL_DIRS, str(home / ".local" / "bin"), str(home / ".bun" / "bin")):
        cand = shutil.which(name, path=d)
        if cand and not (name == "git" and cand == MAC_GIT_STUB and not mac_clt_ready()):
            return cand
    return None


def plat_real_claude() -> str:
    """The real claude binary (never the ~/bin/claude sandbox wrapper). Falls back to plain "claude"."""
    if IS_WIN:
        return mx.find_claude(os.environ["USERPROFILE"]) or "claude"
    real = Path.home() / ".local" / "bin" / "claude"
    if os.access(real, os.X_OK):
        return str(real)
    return (plat_tool("claude") or "claude") if IS_MAC else "claude"


# ── macOS: offer to install a missing tool (never sudo, always asks, user level) ──
# Windows has maude_exe.ensure_tool/offer_git/offer_gh; this is the macOS counterpart. The plan is
# pure (mac_install_plan); mac_ensure_tool is the one runner and must be called while the TUI is
# suspended (it asks on the console). Nothing here installs Homebrew.

# Binary name -> the name shown to the user.
MAC_TOOL_LABELS = {"claude": "Claude Code", "git": "Git", "gh": "GitHub CLI",
                   "kiro-cli": "Kiro CLI", "opencode": "OpenCode", "crush": "Crush",
                   "codex": "Codex", "agy": "Antigravity", "grok": "Grok", "copilot": "Copilot CLI"}
# Agent key -> the command (binary) each agent installs, for the TUI picker to launch. Same table in
# maude_exe.py (AGENT_BINARIES); tests/test-maude-exe.sh checks the two stay equal.
AGENT_BINARIES = {"codex": "codex", "antigravity": "agy", "grok": "grok", "copilot": "copilot"}
MAC_BREW_URL = "https://brew.sh"
# gh / opencode / crush: (brew formula, manual-install URL)
_MAC_BREW_TOOLS = {
    "gh": ("gh", "https://cli.github.com"),
    "opencode": ("opencode", "https://opencode.ai"),
    "crush": ("charmbracelet/tap/crush", "https://github.com/charmbracelet/crush"),
}
# codex / agy / copilot: Homebrew casks (formulae.brew.sh/cask/codex, /cask/antigravity-cli,
# /cask/copilot-cli; docs.github.com "Install Copilot CLI"). Without brew the vendor's own script is used
# (_MAC_CURL_INSTALL). Binary name -> (cask, manual-install URL).
_MAC_BREW_CASKS = {
    "codex": ("codex", "https://github.com/openai/codex"),
    "agy": ("antigravity-cli", "https://antigravity.google/docs/cli/install"),
    "copilot": ("copilot-cli", "https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli"),
}
# Official installers, run through bash with pipefail so a failed download is an error, not an empty
# script that "succeeds". claude puts its binary in ~/.local/bin. The Kiro script, on macOS, normally
# copies "Kiro CLI.app" to /Applications and opens it (the app is expected to set up the command);
# only its zip fallback installs ~/.local/bin/kiro-cli itself. The script creates no symlink.
_MAC_CURL_INSTALL = {
    "claude": "set -o pipefail; curl -fsSL https://claude.ai/install.sh | bash",
    "kiro-cli": "set -o pipefail; curl -fsSL https://cli.kiro.dev/install | bash -s -- --no-modify-path",
    # no Homebrew package for grok: its official script only (x.ai/cli/install.sh, installs ~/.grok/bin + a link in ~/.local/bin)
    "grok": "set -o pipefail; curl -fsSL https://x.ai/cli/install.sh | bash",
}
# Used only when brew is missing (see _MAC_BREW_CASKS). The scripts install in the user's home, never sudo.
_MAC_CURL_FALLBACK = {
    "codex": "set -o pipefail; curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=true sh",
    "agy": "set -o pipefail; curl -fsSL https://antigravity.google/cli/install.sh | bash",
    "copilot": "set -o pipefail; curl -fsSL https://gh.io/copilot-install | bash",
}
MAC_NOT_FOUND_HINT = {
    "kiro-cli": "The Kiro installer opens \"Kiro CLI.app\" (in /Applications) to finish setup. Wait for "
                "that to complete, or open the app once yourself, then try again.",
}


def mac_install_plan(tool: str, have_brew: bool) -> tuple[list[str] | None, str, str]:
    """Pure. How to install `tool` (a binary name) on macOS without sudo.
    Returns (argv, shown, note): argv = the command to run (None = Maude cannot install it here),
    shown = the command line, or the hint text when argv is None, note = an extra line for the user
    ("" if none). A "brew" argv[0] is a bare name, the runner substitutes the resolved path."""
    if tool in _MAC_CURL_INSTALL:
        cmd = _MAC_CURL_INSTALL[tool]
        return ["/bin/bash", "-c", cmd], cmd, ""
    if tool == "git":
        if have_brew:
            return ["brew", "install", "git"], "brew install git", ""
        return (["xcode-select", "--install"], "xcode-select --install",
                "This opens a macOS dialog (Apple Command Line Tools, about 1 GB): click Install there, "
                "wait until it finishes, then come back here.")
    if tool in _MAC_BREW_CASKS:
        cask = _MAC_BREW_CASKS[tool][0]
        if have_brew:
            return ["brew", "install", "--cask", cask], f"brew install --cask {cask}", ""
        cmd = _MAC_CURL_FALLBACK[tool]
        return ["/bin/bash", "-c", cmd], cmd, ""
    if tool in _MAC_BREW_TOOLS:
        formula, url = _MAC_BREW_TOOLS[tool]
        if have_brew:
            return ["brew", "install", formula], f"brew install {formula}", ""
        return (None, f"Install Homebrew first ({MAC_BREW_URL}), then run: brew install {formula}\n"
                      f"Or see {url} for other ways to install it. Maude does not install Homebrew.", "")
    return None, f"No automatic install is known for {tool}.", ""


def _mac_find(name: str, which=None, run=None) -> str | None:
    """plat_tool, except that Apple's /usr/bin/git stub (present without the Command Line Tools,
    it only pops the install dialog) does not count as an installed git."""
    found = (which or plat_tool)(name)
    if name == "git" and found == MAC_GIT_STUB and not mac_clt_ready(run):
        return None
    return found


def mac_ensure_tool(tool: str, find=None, ask=None, run=None, say=print, quiet_hint: bool = False) -> str | None:
    """Return the path of `tool` (a binary name) on macOS, offering to install it when missing.
    None = not available (declined, no installer, failed or not found yet; the reason was printed,
    except the no-installer hint with quiet_hint=True). Asks y/N and shows the command first.
    Never runs an installer without a yes, never uses sudo."""
    find = find or _mac_find
    ask = ask or input
    run = run or subprocess.run
    exe = find(tool)
    if exe:
        return exe
    label = MAC_TOOL_LABELS.get(tool, tool)
    brew = find("brew")
    argv, shown, note = mac_install_plan(tool, brew is not None)
    if argv is None:
        if not quiet_hint:
            say(f"{label} is not installed.\n{shown}")
        return None
    if argv[0] == "brew":
        argv = [brew, *argv[1:]]
    say(f"{label} is not installed.\nCommand that will run: {shown}")
    if note:
        say(note)
    try:
        yes = ask(f"Install {label} now? [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        yes = False
    if not yes:
        return None
    try:
        rc = run(argv, check=False).returncode
    except KeyboardInterrupt:
        say(f"{label} install cancelled.")
        return None
    except OSError as err:
        say(f"Could not run the {label} installer: {err}")
        return None
    xcode = tool == "git" and argv[0] == "xcode-select"
    _mac_clt_cache[0] = None  # an install was attempted: re-check the Command Line Tools
    if xcode:  # the dialog installs in the background
        try:
            ask("Press Enter when the Command Line Tools installation has finished. ")
        except (EOFError, KeyboardInterrupt):
            pass
    elif rc:
        say(f"The {label} install failed (exit code {rc}).")
        return None
    exe = find(tool)  # re-resolve: ~/.local/bin, /opt/homebrew/bin are searched by plat_tool
    if not exe:
        if xcode:
            say("Git is not installed yet: the Command Line Tools are not finished (Apple's dialog may "
                "have been cancelled or is still running). Try again when it has finished.")
        else:
            say(MAC_NOT_FOUND_HINT.get(tool) or
                f"{label} was installed but is not found yet. Open a new terminal window and try again.")
    return exe


_mac_offered = set()  # binary names already offered in this process


def mac_offer(tool: str) -> None:
    """Once per Maude process: offer to install git or gh when missing (the macOS counterpart of
    mx.offer_git / mx.offer_gh). Silent when Maude has no installer for it. Declined stays declined."""
    if tool in _mac_offered:
        return
    _mac_offered.add(tool)
    mac_ensure_tool(tool, quiet_hint=True)


def clauderc_path(create: bool = False) -> Path:
    """The one place Claude credentials (`export K='V'` lines, single-quoted, see _sh_quote) live.
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
    only when the user saved it through Settings (CLAUDE_CODE_USE_BEDROCK=1 in the clauderc, or parked).
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
    env: dict = {}
    if rc or parked:
        try:
            aws = AWS_CONFIG_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            aws = ""
        env = claude_env_from(rc, os.environ, have_claude_login(), aws, parked)
    key = active_anthropic_key()  # Settings > Anthropic > "Use this API key": wins over a login, Bedrock, Foundry
    return with_active_anthropic_key(env, key, os.environ) if key else env


KEY_AGENTS = ("OpenCode", "Crush")  # the agents that may use the Anthropic API key (Settings > Anthropic)


def plat_tool_env(tool: str | None = None) -> dict:
    """Extra environment for the non-Claude tools (Kiro, OpenCode, Crush): the raw clauderc exports
    only, no wrapper rewrites (no dummy key, no login stripping). OpenCode and Crush (KEY_AGENTS) also get the
    active Anthropic API key of Settings > Anthropic; the other agents (Kiro, Copilot, ...) never do. Sandbox: nothing."""
    if current_platform() == "linux-sandbox":
        return {}
    env = read_clauderc()
    key = active_anthropic_key() if tool in KEY_AGENTS else ""
    return {**env, "ANTHROPIC_API_KEY": key} if key else env


def find_powershell() -> str:
    """Windows PowerShell 5.1 (System32 path, then PATH), or "" if it cannot be found."""
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(ps):
        return ps
    return shutil.which("powershell.exe") or shutil.which("powershell") or ""


def _child_env(tools: bool = False, tool: str | None = None) -> dict | None:
    """env= for child processes: None (inherit) unless there is something to add.
    tools=True: for Kiro/OpenCode/Crush (raw clauderc exports), else for Claude Code. `tool` (an agent name) lets
    plat_tool_env add what only that agent gets."""
    extra = plat_tool_env(tool) if tools else plat_claude_env()
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

MODEL_AUTO    = "auto"          # built-in first model of every agent: pass no model flag at all
# Default model when nothing is saved: Claude Code opus (what it always was; "auto" there means the
# ~/bin/claude wrapper's own default, which is haiku), every other agent auto.
DEFAULT_MODEL = "opus"
CLAUDE_ALIASES = ("fable", "opus", "sonnet", "haiku")  # what the sandbox claude wrapper takes as model word
STATE_DIR     = Path.home() / ".maude"
# ~/.maude/maude-model is CLAUDE CODE ONLY: "<claude model>[,<effort>]" ("opus", "opus,high"; "" or no file = opus;
# an EMPTY model field, "," or ",high", is Claude Code "auto": no model word, the wrapper's own default. An old
# maude.exe (OPEN_SH) rejects the empty field and runs opus, so it never gets "claude auto". The effort is shared by
# all agents that have one. Other agents keep their model in agent-models.json.
MODEL_FILE    = STATE_DIR / "maude-model"
OLD_MODEL_FILE = Path.home() / ".maude-model"  # previous location, migrated on first read
# The Agent column, in display order. AI_TOOLS is the old name of the same tuple.
AGENTS        = ("Claude Code", "Codex", "Antigravity", "Grok CLI", "Kiro CLI",
                 "GitHub Copilot CLI", "OpenCode", "Crush")
AI_TOOLS      = AGENTS
DEFAULT_TOOL  = "Claude Code"
# Agent -> the command it runs (the vendors' names come from AGENT_BINARIES, shared with the installers).
AGENT_BIN = {"Claude Code": "claude", "Codex": AGENT_BINARIES["codex"], "Antigravity": AGENT_BINARIES["antigravity"],
             "Grok CLI": AGENT_BINARIES["grok"], "Kiro CLI": "kiro-cli", "GitHub Copilot CLI": AGENT_BINARIES["copilot"],
             "OpenCode": "opencode", "Crush": "crush"}
# Agent -> its label in maude_exe.TOOL_INSTALL (the Windows installers), where that differs from the agent name.
INSTALL_LABEL = {"Grok CLI": "Grok", "GitHub Copilot CLI": "Copilot CLI"}
TOOL_FILE     = STATE_DIR / "maude-tool"      # the saved agent (file name kept for compatibility)
OLD_TOOL_FILE = Path.home() / ".maude-tool"
AGENT_MODELS_FILE = STATE_DIR / "agent-models.json"  # {"<agent>": "<model id>"}: the model last used per agent
# Outside the sandbox (NATIVE) and with settings["native_only_approved"], only these agents may run.
NATIVE_APPROVED_AGENTS = ("Kiro CLI", "GitHub Copilot CLI")
FALLBACK_AGENT = "Kiro CLI"
SETTINGS_FILE = STATE_DIR / "settings.json"
SETTINGS_DEFAULTS = {"native_only_approved": True}
# Reasoning effort: Claude Code `--effort <level>` (`claude --help`), Codex `-c model_reasoning_effort=<level>`
# (max -> xhigh: Codex has no max), Antigravity `agy --effort <level>`, Kiro CLI `kiro-cli chat --effort <level>`
# (kiro-cli 3.5.0 chat --help). "" = the agent's own default. Other agents have no such flag.
EFFORTS       = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = ""
EFFORT_LABELS = ("auto",) + EFFORTS  # "auto" = the agent's own default (passes nothing)
EFFORT_AGENTS = ("Claude Code", "Codex", "Antigravity", "Kiro CLI")
# Agents whose resume flag fails when there is nothing to resume: retry once without it.
RESUME_FALLBACK_AGENTS = ("Codex", "Antigravity", "Grok CLI", "Kiro CLI", "GitHub Copilot CLI")
MODEL_ID_RE   = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+#-]{0,79}")  # used with fullmatch: nothing odd reaches a command line
CLAUDE_ID_RE  = re.compile(r"claude-[A-Za-z0-9][A-Za-z0-9._-]{0,70}")   # a full Claude model id (fullmatch)
LABEL_MAX     = 9               # label width that fits the Model column (longer labels are cut)
CONFIG_VERSION = 1              # "version" of maude-models.json this code understands
MODEL_SLOTS   = 12              # radio buttons in the Model column (auto + up to 11 configured models)
MODELS_URL    = UPDATE_URL.rsplit("/", 1)[0] + "/maude-models.json"
MODELS_DEFAULT_FILE = STATE_DIR / "models-default.json"  # daily copy of light/maude-models.json
MODELS_USER_FILE    = STATE_DIR / "models.json"          # optional user overrides / additions
MODELS_STAMP  = STATE_DIR / "models-last-update"          # YYYY-MM-DD of the last successful refresh
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
            [git_exe(), "-C", str(Path(__file__).parent), "describe", "--tags", "--always"],
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
    return bool(active_anthropic_key())  # Windows/macOS: Settings > Anthropic keeps the key in ~/.maude/anthropic-key


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
            [git_exe(), "-C", str(project_path), "config", "--get", "remote.origin.url"],
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
    gh = gh_exe()
    if not gh:
        return ""
    try:
        r = subprocess.run(
            [gh, "api", "user", "--jq", ".login"],
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


def valid_model_id(model: str) -> bool:
    """Syntactically safe model id (fullmatch: "opus\\n" is rejected)."""
    return isinstance(model, str) and bool(MODEL_ID_RE.fullmatch(model))


def claude_model_ok(model: str) -> bool:
    """What Claude Code may be given as model word: an alias or a full claude-* id, so that a config entry
    like "update" or "default" can never become a subcommand of the ~/bin/claude wrapper. "auto" is not a
    model (it means no word at all)."""
    return isinstance(model, str) and (model in CLAUDE_ALIASES or bool(CLAUDE_ID_RE.fullmatch(model)))


def model_ok(agent: str, model: str) -> bool:
    """Is `model` an acceptable id for this agent (auto always is)?"""
    if model == MODEL_AUTO:
        return True
    return claude_model_ok(model) if agent == DEFAULT_TOOL else valid_model_id(model)


def default_model(agent: str) -> str:
    """The model of an agent when nothing is saved: opus for Claude Code, auto for the rest."""
    return DEFAULT_MODEL if agent == DEFAULT_TOOL else MODEL_AUTO


def effort_for(tool: str, effort: str) -> str:
    """The effort that really applies: "" unless the agent has a reasoning-effort setting."""
    return effort if (tool in EFFORT_AGENTS and effort in EFFORTS) else ""


def effort_args(effort: str, tool: str = DEFAULT_TOOL) -> list[str]:
    """The agent's own effort flag for a known level, else nothing (nothing unvalidated reaches a command
    line). Claude Code, Antigravity and Kiro CLI `--effort <level>`; Codex `-c model_reasoning_effort=<level>`
    (a bare string is taken literally by its TOML parser; Codex has no max, so max is sent as xhigh). Other
    agents have none."""
    e = effort_for(tool, effort)
    if not e:
        return []
    if tool == "Codex":
        return ["-c", f"model_reasoning_effort={'xhigh' if e == 'max' else e}"]
    return ["--effort", e]


def tool_command(tool: str, model: str, fresh: bool, effort: str = "") -> list[str]:
    """Command line of an agent. model "auto" (or anything not acceptable for the agent) passes no model, so the
    agent starts on its own default. `fresh` starts without resuming the previous session. The binary names come
    from AGENT_BIN. Flags: Claude Code `claude [model] --effort --continue`; Codex `codex -m M -c .. [resume
    --last]`; Antigravity `agy --model M --effort E -c`; Grok `grok -m M -c`; Kiro `kiro-cli chat --model M
    --effort E --resume`; Copilot `copilot --model M --continue`; OpenCode `opencode --model P/M --continue`;
    Crush `crush --model P/M --continue`."""
    m = model if (model != MODEL_AUTO and model_ok(tool, model)) else ""
    eff = effort_args(effort, tool)
    cont = [] if fresh else ["--continue"]
    exe = AGENT_BIN[tool]
    def opt(flag: str) -> list[str]:
        return [flag, m] if m else []
    if tool == "Claude Code":
        return [exe, *([m] if m else []), *eff, *cont]
    if tool == "Codex":
        return [exe, *opt("-m"), *eff, *([] if fresh else ["resume", "--last"])]
    if tool == "Antigravity":
        return [exe, *opt("--model"), *eff, *([] if fresh else ["-c"])]
    if tool == "Grok CLI":
        return [exe, *opt("-m"), *([] if fresh else ["-c"])]
    if tool == "Kiro CLI":
        return [exe, "chat", *opt("--model"), *eff, *([] if fresh else ["--resume"])]
    return [exe, *opt("--model"), *cont]  # GitHub Copilot CLI, OpenCode, Crush


# Exit codes that are a deliberate quit, not a failure: 0, 130 (128+SIGINT), a negative signal number for SIGINT or
# SIGTERM (POSIX subprocess), and Windows STATUS_CONTROL_C_EXIT 0xC000013A (unsigned and signed).
NORMAL_QUIT_CODES = frozenset({0, 130, -2, -15, 0xC000013A, 0xC000013A - 2**32})


def agent_quit_normally(ret: int) -> bool:
    return ret in NORMAL_QUIT_CODES


def agent_retry_fresh(tool: str, fresh: bool, ret: int) -> bool:
    """After an agent exited with `ret`: start it once more without the resume flag? Only when it was started
    with a resume flag (not fresh), did not just quit normally (agent_quit_normally), and resume can fail for
    want of a session (RESUME_FALLBACK_AGENTS). Whether a model was passed does not matter: the fresh run keeps
    it, and only if that fails too is the error shown."""
    return not agent_quit_normally(ret) and not fresh and tool in RESUME_FALLBACK_AGENTS


def tool_model_passed(tool: str, model: str) -> bool:
    return model != MODEL_AUTO and model_ok(tool, model)


def agent_exit_note(tool: str, model: str, ret: int, effort: str = "") -> str:
    """Text to show (and wait on) when an agent failed (also after the retry without resume), so a wrong model id
    or thinking level is not repainted over. Names what was passed: the model flag and/or --effort."""
    s = f"{tool} exited with code {ret}."
    passed, tries = [], []
    if tool_model_passed(tool, model):
        passed.append(f"A model ({model})")
        tries.append("Model: auto")
    if effort_for(tool, effort):
        passed.append(f"Thinking ({effort_for(tool, effort)})")
        tries.append("Thinking: auto")
    if passed:
        s += f" {' and '.join(passed)} {'was' if len(passed) == 1 else 'were'} passed: if that is the problem, try {' / '.join(tries)}."
    return s + " Press Enter to go back. "


def parse_model_pref(raw: str) -> tuple[str, str]:
    """Content of maude-model -> (Claude model, effort). Nothing saved ("") = opus. An EMPTY model field ("," or
    ",high") = "auto" (Claude Code without a model word). A legacy "auto" also means auto. A model that Claude
    Code may not get (not an alias or claude-* id) falls back to opus; an unknown effort to the default."""
    raw = raw.strip()
    m, comma, e = raw.partition(",")
    m, e = m.strip(), e.strip().lower()
    e = e if e in EFFORTS else DEFAULT_EFFORT
    if not raw:
        return DEFAULT_MODEL, DEFAULT_EFFORT
    if comma and not m:
        return MODEL_AUTO, e
    return (m if (m == MODEL_AUTO or claude_model_ok(m)) else DEFAULT_MODEL), e


def format_model_pref(model: str, effort: str) -> str:
    """(Claude model, effort) -> maude-model content. auto is written as an empty model field (",high")."""
    eff = effort if effort in EFFORTS else ""
    if model == MODEL_AUTO:
        return "," + eff
    return f"{model},{eff}" if eff else model


# ── Model lists per agent (maude-models.json) ──────────────────────────────

# Built-in fallback: identical to light/maude-models.json (tests/test-models-config.sh checks that). Used
# when neither ~/.maude/models-default.json nor ~/.maude/models.json is usable, e.g. maude.exe offline.
MODELS_FALLBACK_JSON = r'''{
  "version": 1,
  "agents": {
    "Claude Code": {"models": [
      {"label": "fable", "id": "fable"},
      {"label": "opus", "id": "opus"},
      {"label": "sonnet", "id": "sonnet"},
      {"label": "haiku", "id": "haiku"}
    ]},
    "Codex": {"models": [
      {"label": "6.2 astra", "id": "gpt-6.2-astra"},
      {"label": "6.2 sol", "id": "gpt-6.2-sol"},
      {"label": "6.1 sol", "id": "gpt-6.1-sol"},
      {"label": "6.1 terra", "id": "gpt-6.1-terra"},
      {"label": "6 luna", "id": "gpt-6-luna"}
    ]},
    "Antigravity": {"models": []},
    "Grok CLI": {"models": [
      {"label": "grok 4.7", "id": "grok-4.7"},
      {"label": "grok 4.6", "id": "grok-4.6"},
      {"label": "grok 4.5", "id": "grok-4.5"}
    ]},
    "Kiro CLI": {"models": [
      {"label": "fable", "id": "claude-fable-5.1"},
      {"label": "opus", "id": "claude-opus-5.5"},
      {"label": "sonnet", "id": "claude-sonnet-5.5"},
      {"label": "haiku", "id": "claude-haiku-5.5"},
      {"label": "5.6 sol", "id": "gpt-5.6-sol"},
      {"label": "5.6 terra", "id": "gpt-5.6-terra"},
      {"label": "5.6 luna", "id": "gpt-5.6-luna"}
    ]},
    "GitHub Copilot CLI": {"models": [
      {"label": "fable", "id": "claude-fable-5.2"},
      {"label": "opus", "id": "claude-opus-5.5"},
      {"label": "sonnet", "id": "claude-sonnet-5.5"},
      {"label": "haiku", "id": "claude-haiku-5.5"},
      {"label": "6.2 sol", "id": "gpt-6.2-sol"},
      {"label": "6.2 astra", "id": "gpt-6.2-astra"},
      {"label": "6.1 terra", "id": "gpt-6.1-terra"}
    ]},
    "OpenCode": {"models": [
      {"label": "fable", "id": "anthropic/claude-fable-5-2"},
      {"label": "opus", "id": "anthropic/claude-opus-5-5"},
      {"label": "sonnet", "id": "anthropic/claude-sonnet-5-5"},
      {"label": "haiku", "id": "anthropic/claude-haiku-5-5"}
    ]},
    "Crush": {"models": []}
  }
}'''


def _clean_label(label) -> str:
    """A label that is safe to show: printable, no markup brackets, cut to LABEL_MAX. "" = rejected."""
    if not isinstance(label, str):
        return ""
    label = label.strip()
    if not label or not label.isprintable() or "[" in label or "]" in label:
        return ""
    return label[:LABEL_MAX]


def _model_entries(raw, agent: str = "") -> list[tuple[str, str]]:
    """Valid (label, id) pairs of a JSON list: a clean label (_clean_label), an id that the agent accepts
    (model_ok), no duplicates, and never "auto" (that one is built in)."""
    out, seen = [], {MODEL_AUTO}
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        label, mid = _clean_label(e.get("label")), e.get("id")
        if not label or not isinstance(mid, str):
            continue
        mid = mid.strip()
        if mid in seen or not model_ok(agent, mid):
            continue
        seen.add(mid)
        out.append((label, mid))
    return out


def parse_models_config(obj) -> dict | None:
    """Validate one config document: {"version": 1, "agents": {"<agent>": {"models": [...], "extra": [...]}}}.
    "models" replaces an agent's list ([] = the agent has auto only), "extra" appends to it. Returns
    {agent: {"models": list | None, "extra": list}} (models None = the key is absent) for the known agents, or
    None when the schema is not met: not an object, no "agents" object, a "version" that is not CONFIG_VERSION,
    or no known agent at all."""
    if not isinstance(obj, dict) or not isinstance(obj.get("agents"), dict):
        return None
    if "version" in obj and (type(obj["version"]) is not int or obj["version"] != CONFIG_VERSION):
        return None
    out = {}
    for agent, d in obj["agents"].items():
        if agent not in AGENTS or not isinstance(d, dict):
            continue
        models = _model_entries(d["models"], agent) if isinstance(d.get("models"), list) else None
        out[agent] = {"models": models, "extra": _model_entries(d.get("extra"), agent)}
    return out or None


def validate_models_bytes(data: bytes) -> bool:
    """A downloaded maude-models.json is acceptable: parses, schema and version ok, and EVERY known agent has a
    "models" list (which may be empty: auto only), so a truncated or half-edited file can never replace a good
    one and a bad id can be removed by the daily refresh."""
    try:
        parsed = parse_models_config(json.loads(_decode_state(data)))
    except (ValueError, TypeError):
        return False
    return bool(parsed) and all(a in parsed and parsed[a]["models"] is not None for a in AGENTS)


def _read_json(path: Path):
    try:
        return json.loads(_decode_state(path.read_bytes()))
    except (OSError, ValueError, TypeError):
        return None


def load_models_config() -> dict:
    """{agent: [(label, id), ...]} without the built-in auto entry: embedded fallback, then the daily
    ~/.maude/models-default.json, then the user's ~/.maude/models.json (a corrupt or invalid file is skipped)."""
    cfg = {a: d["models"] or [] for a, d in (parse_models_config(json.loads(MODELS_FALLBACK_JSON)) or {}).items()}
    for path in (MODELS_DEFAULT_FILE, MODELS_USER_FILE):
        parsed = parse_models_config(_read_json(path))
        for agent, d in (parsed or {}).items():
            if d["models"] is not None:
                cfg[agent] = d["models"]
            if d["extra"]:
                have = {i for _, i in cfg.get(agent, [])}
                cfg[agent] = cfg.get(agent, []) + [e for e in d["extra"] if e[1] not in have]
    return cfg


def agent_models(agent: str, cfg: dict | None = None) -> list[tuple[str, str]]:
    """The Model column of an agent: auto first (always, once), then its configured models."""
    cfg = load_models_config() if cfg is None else cfg
    return [(MODEL_AUTO, MODEL_AUTO)] + list(cfg.get(agent, []))[:MODEL_SLOTS - 1]


def resolve_model(agent: str, model: str, remembered: str = "", cfg: dict | None = None) -> str:
    """The model to select for an agent: `remembered` (the one used last with this agent) if it is still in
    the list, else `model` (the current one) if it is, else the agent's default (opus for Claude Code, auto
    for the rest), else the first entry."""
    ids = [i for _, i in agent_models(agent, cfg)]
    for cand in (remembered, model, default_model(agent)):
        if cand in ids:
            return cand
    return ids[0]


def initial_selection(saved_tool: str, claude_model: str, remembered: dict, settings: dict | None = None,
                      native: bool | None = None, cfg: dict | None = None) -> tuple[str, str]:
    """(agent, model) to start with: the saved agent unless the restriction forbids it (then Kiro CLI, for this
    session only: nothing is written), and that agent's model. Claude Code's model is `claude_model` (from
    maude-model), every other agent's is in `remembered` (agent-models.json); anything not in the agent's
    list becomes its default."""
    tool = restrict_agent(saved_tool if saved_tool in AGENTS else DEFAULT_TOOL, settings, native)
    cand = claude_model if tool == DEFAULT_TOOL else remembered.get(tool, "")
    return tool, resolve_model(tool, cand, cfg=cfg)


def maybe_update_models(fetch=None, now=None) -> bool:
    """Refresh ~/.maude/models-default.json from GitHub once per day, at or after noon local time (like
    maybe_self_update). Downloads are validated (validate_models_bytes), written to a temp file in the same
    folder and moved into place; the stamp is only set on success. Never raises. Runs on a worker thread,
    never at UI mount. True if a new file was written."""
    try:
        now = now or datetime.now()
        if now.hour < UPDATE_HOUR:
            return False
        today = now.date().isoformat()
        try:
            if MODELS_STAMP.read_text().strip() == today:
                return False
        except OSError:
            pass
        if fetch is None:
            def fetch() -> bytes:
                with urllib.request.urlopen(f"{MODELS_URL}?cache={int(time.time())}", timeout=10) as resp:
                    return resp.read(1_000_000)
        data = fetch()
        if not validate_models_bytes(data):
            return False
        maude_dir()
        fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), prefix=".models-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, MODELS_DEFAULT_FILE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        MODELS_STAMP.write_text(today)
        return True
    except Exception:
        return False


def start_models_refresh(on_done=None) -> None:
    """Fire-and-forget daily refresh of the model lists on a daemon thread (no network on the UI thread).
    on_done() is called on that thread after a new file was written. MAUDE_MODELS_REFRESH=0 turns it off."""
    if os.environ.get("MAUDE_MODELS_REFRESH") == "0":
        return
    import threading

    def work() -> None:
        if maybe_update_models() and on_done is not None:
            try:
                on_done()
            except Exception:
                pass
    threading.Thread(target=work, name="maude-models", daemon=True).start()


# ── Settings (~/.maude/settings.json) and the restriction outside the sandbox ───

def _parse_settings(raw: bytes) -> dict | None:
    try:
        obj = json.loads(_decode_state(raw))
    except Exception:  # not JSON, wrong type, or nested so deep that json raises RecursionError: load_settings never raises
        return None
    return obj if isinstance(obj, dict) else None


def load_settings() -> dict:
    """~/.maude/settings.json merged over SETTINGS_DEFAULTS. A missing or corrupt file (not JSON, or not an
    object) gives the defaults; reading never changes the file (save_settings backs a corrupt one up before it
    writes). A value of the wrong type for a known key falls back to its default. Unknown keys are kept. Never
    raises."""
    d = dict(SETTINGS_DEFAULTS)
    try:
        obj = _parse_settings(SETTINGS_FILE.read_bytes())
    except OSError:
        return d
    for k, v in (obj or {}).items():
        if k in SETTINGS_DEFAULTS and not isinstance(v, type(SETTINGS_DEFAULTS[k])):
            continue
        d[k] = v
    return d


def save_settings(d: dict) -> bool:
    """Write the settings atomically (temp file in ~/.maude, mode 0600, os.replace). A corrupt existing file is
    first kept as settings.json.bak. True on success."""
    try:
        maude_dir()
        try:
            if _parse_settings(SETTINGS_FILE.read_bytes()) is None:
                replace_retry(SETTINGS_FILE, SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".bak"))
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), prefix=".settings-", suffix=".tmp")  # created 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2, sort_keys=True)
                f.write("\n")
            replace_retry(tmp, SETTINGS_FILE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False


def agent_allowed(agent: str, settings: dict | None = None, native: bool | None = None) -> bool:
    """Outside the sandbox (native) with native_only_approved on, only NATIVE_APPROVED_AGENTS may run.
    Inside the sandbox the setting has no effect."""
    settings = load_settings() if settings is None else settings
    native = NATIVE if native is None else native
    return not (native and settings.get("native_only_approved", True)) or agent in NATIVE_APPROVED_AGENTS


def restrict_agent(agent: str, settings: dict | None = None, native: bool | None = None) -> str:
    """The agent itself if allowed, else FALLBACK_AGENT (Kiro CLI)."""
    return agent if agent_allowed(agent, settings, native) else FALLBACK_AGENT


def _pause(msg: str) -> None:
    """Wait for Enter with a message (the TUI is suspended). Never raises."""
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        pass


def _after_agent(tool: str, model: str, fresh: bool, effort: str, ret: int, rerun) -> None:
    """The agent exited with `ret`. A normal quit (0, Ctrl+C: agent_quit_normally) is done. Otherwise start it once
    more without the resume flag if one was used (agent_retry_fresh), and if that also fails, stop and show the exit
    code (with "try Model: auto / Thinking: auto" for what was passed), so the error stays readable instead of being
    painted over by the TUI. rerun(cmd) runs a command line and returns its exit code."""
    if agent_quit_normally(ret):
        return
    if agent_retry_fresh(tool, fresh, ret):
        ret = rerun(tool_command(tool, model, True, effort))
        if agent_quit_normally(ret):
            return
    _pause(agent_exit_note(tool, model, ret, effort))


def _open_other_tool(tool: str, model: str, fresh: bool, effort: str = "") -> None:
    cmd = tool_command(tool, model, fresh, effort)
    env = _child_env(tools=True, tool=tool)  # raw clauderc exports only
    if IS_MAC:  # offer the install (y/N, never sudo); the TUI is suspended here
        exe = mac_ensure_tool(cmd[0])
        if not exe:  # mac_ensure_tool said why (declined, no installer, failed)
            input("Press Enter to go back. ")
            return
        cmd = [exe, *cmd[1:]]
    elif not shutil.which(cmd[0]):
        input(f"{tool} is not installed; run 'maude update'. Press Enter to go back. ")
        return
    if tool == "Kiro CLI" and subprocess.run([cmd[0], "whoami"], capture_output=True, env=env).returncode != 0:
        # Interop is off in the sandbox, so a browser cannot open: use the device code flow.
        subprocess.run([cmd[0], "login", "--use-device-flow"], check=False, env=env)
    ret = subprocess.run(cmd, check=False, env=env).returncode
    _after_agent(tool, model, fresh, effort, ret,
                 lambda c: subprocess.run([cmd[0], *c[1:]], check=False, env=env).returncode)


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
    label, binary = INSTALL_LABEL.get(tool, tool), AGENT_BIN[tool]
    if label in getattr(mx, "TOOL_INSTALL", {}):  # found on the PATH or the installer folders, else offered
        exe = mx.ensure_tool(label, mx.find_tool, refresh=mx.refresh_path)
    else:  # no installer known for it: only a hint, when it is truly missing
        exe = mx.find_tool(binary)
        if not exe:
            print(f"{tool} is not installed and Maude cannot install it on Windows; install it yourself and make "
                  f"sure '{binary}' is on the PATH.")
    if not exe:  # ensure_tool said why (declined, failed, not found)
        input("Press Enter to go back. ")
        return
    cmd = tool_command(tool, model, fresh, effort)
    print(mx.banner_text(str(path), HAS_SANDBOX))
    env = _child_env(tools=True, tool=tool)  # raw clauderc exports (None = inherit)
    ret = mx._run([exe, *cmd[1:]], cwd=str(path), env=env)
    _after_agent(tool, model, fresh, effort, ret, lambda c: mx._run([exe, *c[1:]], cwd=str(path), env=env))


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
        _open_other_tool(tool, model, fresh, effort)
        return
    mw = [model] if (model != MODEL_AUTO and claude_model_ok(model)) else []  # auto: no model argument at all
    head, env = ["claude", *mw], _child_env()  # sandbox: the ~/bin/claude wrapper takes the model as a word
    if IS_MAC:  # no wrapper outside the sandbox: the real claude (--model) with Maude's env
        mac_offer("git")
        mac_offer("gh")
        real = plat_real_claude()
        if real == "claude":  # not found anywhere: offer the official per-user installer
            if not mac_ensure_tool("claude"):
                input("Press Enter to go back. ")
                return
            real = plat_real_claude()
        head = [real, *(["--model", model] if mw else [])]
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
    """Claude Code's saved model (maude-model): opus when nothing is saved, "auto" for an empty model field."""
    return read_model_effort()[0]


def read_agent_models() -> dict:
    """{agent: model id} last used per agent EXCEPT Claude Code (its model is in maude-model), from
    ~/.maude/agent-models.json. Corrupt file or entries -> skipped."""
    obj = _read_json(AGENT_MODELS_FILE)
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items() if k in AGENTS and k != DEFAULT_TOOL and isinstance(v, str) and model_ok(k, v)}


def save_agent_model(agent: str, model: str) -> None:
    """Remember the model of a non-Claude agent in agent-models.json (atomic, best effort)."""
    if agent not in AGENTS or agent == DEFAULT_TOOL or not isinstance(model, str) or not model_ok(agent, model):
        return
    d = read_agent_models()
    d[agent] = model
    try:
        maude_dir()
        fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), prefix=".agent-models-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2, sort_keys=True)
            replace_retry(tmp, AGENT_MODELS_FILE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass


def recall_model(agent: str) -> str:
    """The model last chosen for an agent ("" = none): Claude Code's from maude-model, the others' from
    agent-models.json."""
    return read_model() if agent == DEFAULT_TOOL else read_agent_models().get(agent, "")


def remember_model(agent: str, model: str) -> None:
    """Persist the user's model choice for an agent (the right file for it)."""
    if agent == DEFAULT_TOOL:
        save_model(model)
    else:
        save_agent_model(agent, model)


def read_effort() -> str:
    return read_model_effort()[1]


def save_model(name: str) -> None:
    """Save Claude Code's model (maude-model): an alias, a claude-* id or auto. Other agents: save_agent_model."""
    if name == MODEL_AUTO or claude_model_ok(name):
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

# ── Native (Windows/macOS) Git, SSH and commit-signing helpers ─────────────
# ONE SSH stack per native platform, never mixed: the OS's own OpenSSH. Windows: C:\Windows\System32\
# OpenSSH (ssh, ssh-keygen, ssh-add; its ssh-agent is a Windows service we only detect, never
# start: enabling it needs admin). macOS: /usr/bin (Apple's agent + keychain). MinGit ships no
# ssh-keygen and its MSYS ssh-agent cannot talk to Windows OpenSSH, so it is not used for keys.
# Commit signing is SSH signing (gpg.format ssh), never gpg, on Windows/macOS; the sandbox keeps gpg.

MAC_NO_GIT = "/nonexistent/git"  # subprocess raises FileNotFoundError, like a missing git on Windows


def git_exe() -> str:
    """git binary for subprocess calls: the offered/installed one natively, plain "git" in the sandbox."""
    if not is_native():
        return "git"
    found = plat_tool("git")
    if IS_MAC and not found:  # never the bare name: that would run Apple's stub (install dialog)
        return MAC_NO_GIT
    return found or "git"


def gh_exe() -> str | None:
    """gh binary, or None when it is not installed."""
    return plat_tool("gh") if is_native() else shutil.which("gh")


def ssh_tool(name: str, platform: str | None = None, env=None, exists=os.path.exists) -> str:
    """Path of an OpenSSH tool ("ssh", "ssh-keygen", "ssh-add", "ssh-agent") for the platform's
    one SSH stack. Sandbox: the bare name (unchanged behaviour)."""
    import ntpath
    platform = platform or current_platform()
    env = os.environ if env is None else env
    if platform == "windows":
        root = env.get("SystemRoot") or env.get("windir") or "C:\\Windows"
        p = ntpath.join(root, "System32", "OpenSSH", name + ".exe")
        return p  # always the System32 path: never fall back to a bare name that PATH could resolve to an MSYS tool
    if platform == "macos":
        p = "/usr/bin/" + name
        return p if exists(p) else name
    return name


def _fwd(p: str) -> str:
    """Forward-slash form: git runs GIT_SSH_COMMAND through sh and reads config values itself."""
    return p.replace("\\", "/")


def git_ssh_command(platform: str | None = None, ssh: str | None = None) -> str:
    """GIT_SSH_COMMAND for pushes: batch mode, so a bad SSH state fails fast instead of prompting."""
    platform = platform or current_platform()
    opts = "-o BatchMode=yes -o StrictHostKeyChecking=accept-new"
    if platform == "linux-sandbox":
        return "ssh " + opts
    exe = _fwd(ssh or ssh_tool("ssh", platform))
    if " " in exe:
        exe = f'"{exe}"'
    return f"{exe} {opts}"


ASKPASS_ENV_VAR = "MAUDE_ASKPASS_PW"


def askpass_script(platform: str | None = None) -> tuple[str, str]:
    """(file suffix, text) of a static SSH_ASKPASS helper that prints $MAUDE_ASKPASS_PW. The
    passphrase travels in the child's environment only, never on disk. Windows: a .cmd with delayed
    expansion, so &, |, %, ^ in the passphrase are not interpreted by cmd."""
    if (platform or current_platform()) == "windows":
        return ".cmd", "@echo off\r\nsetlocal EnableDelayedExpansion\r\necho(!" + ASKPASS_ENV_VAR + "!\r\n"
    return ".sh", "#!/bin/sh\nprintf '%s' \"$" + ASKPASS_ENV_VAR + "\"\n"


def askpass_env(script_path: str, passphrase: str, base_env=None) -> dict:
    """Environment that makes ssh/ssh-add read the passphrase from the askpass helper."""
    env = dict(os.environ if base_env is None else base_env)
    env["SSH_ASKPASS"] = script_path
    env["SSH_ASKPASS_REQUIRE"] = "force"     # OpenSSH 8.4+
    env.setdefault("DISPLAY", ":0")          # older OpenSSH still wants it
    env[ASKPASS_ENV_VAR] = passphrase
    return env


def make_askpass(platform: str | None = None) -> str:
    """Write the static askpass helper to a temp file and return its path (caller unlinks it)."""
    suffix, text = askpass_script(platform)
    fd, path = tempfile.mkstemp(prefix="maude-askpass-", suffix=suffix)
    with os.fdopen(fd, "w", newline="") as f:
        f.write(text)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def ssh_add_args(key_path: str, platform: str | None = None, ssh_add: str | None = None) -> list:
    """ssh-add command line. macOS also stores the passphrase in the login keychain."""
    platform = platform or current_platform()
    cmd = [ssh_add or ssh_tool("ssh-add", platform)]
    if platform == "macos":
        cmd.append("--apple-use-keychain")
    return cmd + [str(key_path)]


def ssh_signing_config(pub_path: str, platform: str | None = None, env=None, exists=os.path.exists) -> list:
    """(key, value) pairs for `git config --global`: SSH commit signing with the key's .pub file
    (ssh-keygen falls back to the matching private key file when the agent does not have it).
    gpg.ssh.program pins ssh-keygen to the platform's one stack: git would otherwise pick
    whatever ssh-keygen is first on PATH (MinGit has none, Git for Windows has an MSYS one)."""
    platform = platform or current_platform()
    pairs = [("gpg.format", "ssh"), ("user.signingkey", _fwd(str(pub_path))), ("commit.gpgsign", "true")]
    keygen = ssh_tool("ssh-keygen", platform, env, exists)
    if keygen != "ssh-keygen":
        pairs.append(("gpg.ssh.program", _fwd(keygen)))
    return pairs


def signing_conflict(pub_path: str, get=None) -> str:
    """Reason why an existing signing setup must be left alone, or "". Never overwrite another
    key, another format, or signing with a default GPG key."""
    get = get or git_config_get
    ours = _fwd(str(pub_path))
    fmt, key, gs = get("gpg.format"), get("user.signingkey"), get("commit.gpgsign").lower()
    if key and fmt not in ("", "ssh"):
        return f"user.signingkey is already set ({key}) with gpg.format {fmt}"
    if key and key != ours:
        return f"user.signingkey is already set to another key ({key})"
    if not key and gs == "true":
        return "commit.gpgsign is already on with your default signing key"
    return ""


def apply_ssh_signing(pub_path: str, get=None, set_=None, platform: str | None = None, env=None,
                      exists=os.path.exists) -> list:
    """Set the SSH signing config unless the user already has a different signing setup (then
    nothing is touched and [] is returned; see signing_conflict). Returns the pairs that were set."""
    get = get or git_config_get
    set_ = set_ or git_config_set
    if signing_conflict(pub_path, get):
        return []
    pairs = ssh_signing_config(pub_path, platform, env, exists)
    for k, v in pairs:
        set_(k, v)
    return pairs


def ssh_command_pair(get=None, platform: str | None = None, env=None, git_path: str = "", exists=os.path.exists):
    """(core.sshCommand, value) for Windows or None. Needed only with MinGit (it would use its own
    MSYS ssh, which cannot reach the Windows agent). Skipped when the user has core.sshCommand, when
    GIT_SSH / GIT_SSH_COMMAND is set, or when git is not MinGit (Git for Windows brings its own ssh)."""
    get = get or git_config_get
    platform = platform or current_platform()
    env = os.environ if env is None else env
    if platform != "windows" or get("core.sshCommand") or env.get("GIT_SSH") or env.get("GIT_SSH_COMMAND"):
        return None
    if "mingit" not in git_path.lower():
        return None
    ssh = _fwd(ssh_tool("ssh", platform, env, exists))
    return ("core.sshCommand", f'"{ssh}"' if " " in ssh else ssh)


def apply_ssh_command(get=None, set_=None, platform: str | None = None, env=None, git_path: str = "",
                      exists=os.path.exists):
    """Set core.sshCommand when ssh_command_pair says so. Independent of commit signing."""
    pair = ssh_command_pair(get, platform, env, git_path, exists)
    if pair:
        (set_ or git_config_set)(*pair)
    return pair


def parse_ssh_version(text: str):
    """(major, minor) from `ssh -V` output ("OpenSSH_for_Windows_9.5p2, ..." / "OpenSSH_9.6p1 ..."), or None."""
    m = re.search(r"OpenSSH_(?:for_Windows_)?(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


OPENSSH_MISSING_MSG = ("The OpenSSH client is not installed. Install the OpenSSH Client optional feature "
                       "(Settings > System > Optional features), then try again.")


def openssh_available(platform: str | None = None, env=None, exists=os.path.exists) -> bool:
    """True when the platform's one SSH stack (ssh-keygen, ssh, ssh-add) is there. Sandbox: always."""
    platform = platform or current_platform()
    if platform == "linux-sandbox":
        return True
    return all(exists(ssh_tool(n, platform, env, exists)) for n in ("ssh-keygen", "ssh", "ssh-add"))


def askpass_unsupported_reason(version) -> str:
    """Message when this OpenSSH cannot take a passphrase through SSH_ASKPASS_REQUIRE (needs 8.4+), else ""."""
    if version and version < (8, 4):
        return (f"OpenSSH {version[0]}.{version[1]} is older than 8.4 and cannot take a passphrase "
                "non-interactively. Update OpenSSH (Windows: Settings > System > Optional features) "
                "or use a key without a passphrase.")
    return ""


def ssh_version_now() -> tuple | None:
    """Installed OpenSSH (major, minor) via `ssh -V`, or None when unknown."""
    try:
        r = subprocess.run([ssh_tool("ssh"), "-V"], capture_output=True, text=True, timeout=5,
                           stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return parse_ssh_version(r.stderr + r.stdout)


def kill_proc_tree(p) -> None:
    """Kill a process and its children (Windows: taskkill /T from System32, as maude_exe does; POSIX:
    the whole process group, so the child must have been started with start_new_session=True)."""
    if os.name != "nt":
        try:
            os.killpg(p.pid, 9)
        except Exception:
            pass
    else:
        try:
            tk = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
            subprocess.run([tk, "/T", "/F", "/PID", str(p.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass
    try:
        p.kill()
    except Exception:
        pass


def run_capture(cmd: list, timeout: float, env=None):
    """Run cmd with stdin closed and stdout/stderr in temp files (a pipe held by a grandchild would
    block us). On timeout the whole process tree is killed. Returns (returncode or None, output)."""
    with tempfile.TemporaryFile() as out:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env,
                             start_new_session=(os.name != "nt"))
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_proc_tree(p)
            try:
                p.wait(timeout=5)
            except Exception:
                pass
            rc = None
        out.seek(0)
        return rc, out.read().decode("utf-8", "replace").strip()


def signing_roundtrip(pub_path: str, timeout: float = 8, platform: str | None = None) -> tuple[bool, str]:
    """Sign a temp file exactly as git will (`ssh-keygen -Y sign -n git -f <pub>`), without any prompt:
    stdin closed, an askpass that answers with an empty passphrase (so an encrypted key that no agent
    holds fails at once instead of hanging every `git commit`), a short timeout that kills the tree."""
    platform = platform or current_platform()
    keygen = ssh_tool("ssh-keygen", platform)
    if not os.path.exists(keygen):
        return False, OPENSSH_MISSING_MSG if platform == "windows" else "ssh-keygen was not found."
    d = tempfile.mkdtemp(prefix="maude-sign-")
    askpass = None
    try:
        data = os.path.join(d, "data.txt")
        with open(data, "w") as f:
            f.write("maude signing test\n")
        askpass = make_askpass(platform)
        env = askpass_env(askpass, "")
        rc, out = run_capture([keygen, "-Y", "sign", "-n", "git", "-f", str(pub_path), data], timeout, env)
        if rc is None:
            return False, "signing did not finish (the key probably needs a passphrase that no agent holds)"
        if rc == 0 and os.path.exists(data + ".sig"):
            return True, "signing works"
        low = out.lower()
        if "passphrase" in low or "incorrect" in low or "agent" in low:
            return False, "the key has a passphrase and no ssh-agent holds it"
        return False, (out.splitlines()[-1] if out else f"ssh-keygen exited with {rc}")
    except OSError as err:
        return False, str(err)
    finally:
        if askpass:
            try:
                os.unlink(askpass)
            except OSError:
                pass
        shutil.rmtree(d, ignore_errors=True)


def active_ssh_key() -> Path:
    """The key actually in use: ~/.ssh/id_ed25519, else id_rsa when only that exists."""
    found = existing_ssh_key()
    return Path(found) if found else SSH_KEY_PATH


def push_guard_key() -> Path:
    """Key the push guard checks: the sandbox keeps id_ed25519 exactly as before (its unlock screen
    only handles that file); natively the key actually in use, id_rsa included."""
    return active_ssh_key() if is_native() else SSH_KEY_PATH


def _norm_cmd(s: str) -> str:
    """Comparable form of a command path: no quotes, forward slashes, lower case."""
    return s.strip().strip('"').replace("\\", "/").lower()


def push_ssh_env(env=None, core_ssh_command: str = "", platform: str | None = None, exists=os.path.exists) -> dict:
    """GIT_SSH_COMMAND to force for a push. Sandbox: always (unchanged). Native: not when the user set
    GIT_SSH / GIT_SSH_COMMAND or has a core.sshCommand of their own. A core.sshCommand that is just
    our own (the plain System32 ssh written by apply_ssh_command) does not count as the user's: it
    would drop BatchMode/accept-new and an unknown host key would hang the push, so it is still forced."""
    platform = platform or current_platform()
    env = os.environ if env is None else env
    if platform != "linux-sandbox":
        own = _norm_cmd(ssh_tool("ssh", platform, env, exists))
        theirs = core_ssh_command and _norm_cmd(core_ssh_command) != own
        if env.get("GIT_SSH") or env.get("GIT_SSH_COMMAND") or theirs:
            return {}
    return {"GIT_SSH_COMMAND": git_ssh_command(platform)}


def git_config_effective(project: str, key: str) -> str:
    """`git -C <project> config --get <key>`: the value git really uses (local, global, system)."""
    try:
        r = subprocess.run([git_exe(), "-C", str(project), "config", "--get", key],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""

def mac_load_keychain() -> None:
    """macOS: load keys stored in the login keychain into the agent (after a restart the agent is empty)."""
    if not IS_MAC:
        return
    try:
        subprocess.run([ssh_tool("ssh-add"), "--apple-load-keychain"], capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        pass

GH_SIGNING_SCOPE = "admin:ssh_signing_key"   # needed by `gh ssh-key add --type signing`


def gh_login_scopes(platform: str | None = None) -> str:
    """Extra OAuth scope the wizard asks gh for: GPG key upload in the sandbox, SSH signing key natively."""
    return "write:gpg_key" if (platform or current_platform()) == "linux-sandbox" else GH_SIGNING_SCOPE


def gh_login_cmd(gh: str, platform: str | None = None) -> list:
    platform = platform or current_platform()
    cmd = [gh, "auth", "login", "--hostname", "github.com", "--git-protocol", "ssh", "--web",
           "--scopes", gh_login_scopes(platform)]
    if platform != "linux-sandbox":
        cmd.append("--skip-ssh-key")  # step 2 handles the auth key; no pub-file shuffling needed
    return cmd


def gh_refresh_cmd(gh: str) -> list:
    """Add the signing-key scope to an already authenticated gh (no new login needed)."""
    return [gh, "auth", "refresh", "-h", "github.com", "-s", GH_SIGNING_SCOPE]


MAC_SSH_CONFIG_BLOCK = (
    "\n# Added by Maude: keep the key in the macOS agent and keychain\n"
    "Host *\n"
    "    IgnoreUnknown UseKeychain\n"
    "    AddKeysToAgent yes\n"
    "    UseKeychain yes\n"
)


def ensure_mac_ssh_config(config_path: Path) -> bool:
    """Append AddKeysToAgent/UseKeychain to ~/.ssh/config unless the file already sets either.
    Returns True when the file was changed."""
    try:
        text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    except (OSError, UnicodeDecodeError):
        return False
    low = text.lower()
    if "usekeychain" in low or "addkeystoagent" in low:
        return False
    try:
        config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(config_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(MAC_SSH_CONFIG_BLOCK if text else MAC_SSH_CONFIG_BLOCK.lstrip("\n"))
        os.chmod(config_path, 0o600)
    except OSError:
        return False
    return True


def git_config_get(key: str) -> str:
    """Return `git config --global <key>` or empty string."""
    try:
        r = subprocess.run(
            [git_exe(), "config", "--global", key],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def git_config_set(key: str, value: str) -> None:
    """Set `git config --global <key> <value>`. Silent on failure."""
    try:
        subprocess.run(
            [git_exe(), "config", "--global", key, value],
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
    if not openssh_available():
        return False, OPENSSH_MISSING_MSG
    SSH_KEY_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    if SSH_KEY_PATH.exists():
        return False, f"{SSH_KEY_PATH} already exists; refusing to overwrite."
    try:
        subprocess.run(
            [ssh_tool("ssh-keygen"), "-t", "ed25519", "-C", comment,
             "-f", str(SSH_KEY_PATH), "-N", passphrase],
            check=True, capture_output=True, text=True, timeout=30,
        )
        try:  # no-op on Windows (ssh-keygen sets the ACL itself)
            SSH_KEY_PATH.chmod(0o600)
            SSH_KEY_PATH.with_suffix(".pub").chmod(0o644)
        except OSError:
            pass
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

    if passphrase and is_native():
        why = askpass_unsupported_reason(ssh_version_now())
        if why:
            return False, why
        # Static helper script; the passphrase goes through the environment, never to disk.
        askpass_path = make_askpass()
        env = askpass_env(askpass_path, passphrase, env)
    elif passphrase:
        # Write a tiny shell script that prints the passphrase to stdout.
        fd, askpass_path = tempfile.mkstemp(prefix="maude-askpass-", suffix=".sh")
        with os.fdopen(fd, "w") as f:
            f.write("#!/bin/sh\n")
            f.write(f"printf '%s' {shlex.quote(passphrase)}\n")
        os.chmod(askpass_path, 0o700)
        env["SSH_ASKPASS"] = askpass_path
        env["SSH_ASKPASS_REQUIRE"] = "force"     # OpenSSH 8.4+
        env.setdefault("DISPLAY", ":0")          # older OpenSSH still needs this

    cmd = [ssh_tool("ssh"), "-T",
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
            [ssh_tool("ssh-keygen"), "-y", "-f", str(key_path), "-P", ""],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return is_native()  # native: no ssh-keygen = cannot use the key; never report "unencrypted"
    except subprocess.TimeoutExpired:
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
            [ssh_tool("ssh-add"), "-L"], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return r.returncode == 0 and key_blob in r.stdout


def ssh_agent_running() -> bool:
    """True if SSH_AUTH_SOCK points at a usable agent (Windows: if the OpenSSH ssh-agent service answers)."""
    win = current_platform() == "windows"
    if not win and not os.environ.get("SSH_AUTH_SOCK"):
        return False  # Windows has no SSH_AUTH_SOCK: its agent is a named pipe
    try:
        r = subprocess.run(
            [ssh_tool("ssh-add"), "-l"], capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if win:  # the service is disabled by default; the error text differs between builds
        low = (r.stdout + r.stderr).lower()
        if "error connecting" in low or "could not open a connection" in low:
            return False
    # Exit codes: 0 = keys loaded, 1 = no keys, 2 = no agent.
    return r.returncode != 2


def ensure_ssh_agent() -> bool:
    """Start an ssh-agent if none is running and import SSH_AUTH_SOCK /
    SSH_AGENT_PID into os.environ so subsequent ssh-add / git push
    can use it. No-op if an agent is already reachable. Returns True
    on success. Windows: only detects the OpenSSH ssh-agent service (starting it needs admin)."""
    if ssh_agent_running():
        return True
    if current_platform() == "windows":
        return False
    agent = ssh_tool("ssh-agent")
    if not (shutil.which(agent) or (os.path.isabs(agent) and os.path.exists(agent))):
        return False
    try:
        r = subprocess.run(
            [agent, "-s"], capture_output=True, text=True, timeout=5,
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
    if is_native():
        if not openssh_available():
            return False, OPENSSH_MISSING_MSG
        if passphrase:
            why = askpass_unsupported_reason(ssh_version_now())
            if why:
                return False, why
        askpass = make_askpass()  # static helper; the passphrase is in the child's environment only
        try:
            env = askpass_env(askpass, passphrase)
            cmd = ssh_add_args(str(key_path))
            rc, out = run_capture(cmd, 5, env)  # 5 s: a wrong passphrase must not freeze the UI; kills the tree
            if rc not in (None, 0) and "--apple-use-keychain" in cmd and any(s in out.lower() for s in ("apple-use-keychain", "illegal option", "unknown option")):
                rc, out = run_capture([cmd[0], str(key_path)], 5, env)  # old macOS: no such flag
            if rc is None:
                return False, "ssh-add did not finish: wrong passphrase?"
            return rc == 0, out
        except OSError as err:
            return False, str(err)
        finally:
            try:
                os.unlink(askpass)
            except OSError:
                pass
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
    gh = gh_exe()
    if not gh:
        return False
    try:
        r = subprocess.run(
            [gh, "auth", "status"], capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return r.returncode == 0


def classify_gh_key_failure(kind: str, out: str) -> tuple[bool, str]:
    """(ok, message) for a failed `gh <kind> add`. GPG keys: unchanged ("already"/"duplicate" = ok).
    SSH keys: gh itself prints "already exists on your account" with exit 0, so a failure is never ok; GitHub's "key is already in use" means
    ANOTHER account holds the key, which is not a success."""
    low = out.lower()
    if kind == "gpg-key":
        if "already" in low or "duplicate" in low:
            return True, "already on GitHub"
        return False, out or "upload failed"
    if "in use" in low:
        return False, ("This SSH key is already registered on another GitHub account (a key can belong "
                       "to one account only). Generate a new key or remove it there first.")
    return False, out or "upload failed"


def gh_upload_key(kind: str, armored: str, title: str) -> tuple[bool, str]:
    """Upload an SSH or GPG public key via gh CLI.

    `kind` is "ssh-key", "gpg-key" or "ssh-signing-key" (an SSH key
    registered for commit signing; needs the admin:ssh_signing_key scope).
    Returns (ok, message). If the key is already on the account, that's
    treated as success.
    """
    gh = gh_exe()
    if not gh:
        return False, "gh is not installed"
    if kind == "ssh-signing-key":
        cmd = [gh, "ssh-key", "add", "-", "--type", "signing", "--title", title]
    else:
        cmd = [gh, kind, "add", "-", "--title", title]
    try:
        r = subprocess.run(
            cmd, input=armored, text=True, capture_output=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as err:
        return False, str(err)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        return True, out or "uploaded"
    return classify_gh_key_failure(kind, out)


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
    as parse_creds_text but keeps every key and empty values. Lines end at "\n" only (a trailing
    "\r" is stripped), the same characters _sh_quote refuses to store."""
    out: dict[str, str] = {}
    for raw in text.split("\n"):
        line = raw.rstrip("\r").strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        m = CRED_KEY_RE.match(line)
        if m:
            k, v = m.group(1), _unquote_rc_value(m.group(2).strip())
            out[k] = v
    return out


def _sh_quote(v: str) -> str:
    """Single-quote v for a bash `source` (the wrapper and the reviewer skills source clauderc):
    nothing inside is expanded. A quote becomes quote-backslash-quote-quote. Line breaks cannot be
    stored on one line and are dropped: CR, LF, VT, FF, FS/GS/RS, NEL, U+2028/2029 (what
    str.splitlines() splits on) and NUL."""
    return "'" + re.sub(r"[\x00\r\n\x0b\x0c\x1c-\x1e\x85\u2028\u2029]", "", v).replace("'", "'\\''") + "'"


def _write_private(path: Path, data: bytes) -> None:
    """Write bytes to a file atomically and mode 0600 (temp file in the same folder, created 0600 so there is no
    window where it is group/world readable, then replace_retry). A symlinked target is written through (the link
    stays). The mode has no effect on Windows, where the user profile ACL protects the file."""
    real = Path(os.path.realpath(str(path)))
    fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix=".tmp-", suffix=real.suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        replace_retry(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_clauderc(values: dict[str, str], path: Path | None = None) -> None:
    """Write env vars to clauderc_path() (or `path`) as `export KEY='VALUE'` lines (mode 0600 from creation)."""
    path = path or clauderc_path(create=True)
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


def _parse_rc_strict(path: Path) -> dict:
    try:
        return parse_clauderc(_decode_text_strict(path.read_bytes()))
    except ValueError:
        raise OSError(f"{path} is not valid UTF-8/UTF-16 text; it was left untouched") from None


def park_clauderc(park: bool) -> None:
    """Move clauderc to clauderc.inactive (park=True: Bedrock is the default) or back (park=False: Foundry is).
    When BOTH files exist (a save re-created the active one while the other was parked) nothing is ever deleted
    unmerged: the values of both are combined (the active file wins on a collision) into the destination and only
    then the source is removed. An undecodable file makes it refuse (OSError)."""
    active, inactive = clauderc_path(), clauderc_inactive_path()
    src, dst = (active, inactive) if park else (inactive, active)
    if not src.exists():
        return
    if not dst.exists():
        src.rename(dst)
        return
    a, b = _parse_rc_strict(src), _parse_rc_strict(dst)
    merged = {**b, **a} if park else {**a, **b}  # the active file's values win either way
    write_clauderc(merged, dst)
    src.unlink()

def remove_clauderc_keys(keys) -> None:
    """Delete `keys` from clauderc_path() (other exports stay). A clauderc left empty is removed, so it no
    longer counts as configured credentials. An undecodable file is left untouched (OSError)."""
    path = clauderc_path()
    if not path.exists():
        return
    try:
        existing = parse_clauderc(_decode_text_strict(path.read_bytes()))
    except ValueError:
        raise OSError(f"{path} is not valid UTF-8/UTF-16 text; it was left untouched") from None
    if not any(k in existing for k in keys):
        return
    for k in keys:
        existing.pop(k, None)
    if existing:
        write_clauderc(existing)
    else:
        path.unlink()


# ── Settings: secrets kept in their own 0600 files (never in clauderc, so "Default to AWS Bedrock" parking and
#    clauderc rewrites cannot strand them). The Anthropic API key mode ("Use this API key") is Windows/macOS only for
#    now: the sandbox's ~/bin/claude wrapper only reads clauderc and has no plain-key branch, so the sandbox uses
#    Claude Login or a pasted `export ANTHROPIC_API_KEY=...` line. ──────────────────────────────────────────────
ANTHROPIC_ACTIVE_SETTING = "anthropic_key_active"  # settings.json: is the key in ~/.maude/anthropic-key in use?
_ACTIVE_OFF_VARS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_VERTEX")
_ACTIVE_UNSET_VARS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")


def anthropic_key_file() -> Path:
    return STATE_DIR / "anthropic-key"


def opencode_key_file() -> Path:
    return STATE_DIR / "opencode-key"


def _one_line(v: str) -> str:
    return re.sub(r"[\x00\r\n\x0b\x0c\x1c-\x1e\x85\u2028\u2029]", "", v).strip()


def read_secret(path: Path) -> str:
    """The one-line secret in `path` ("" when missing/unreadable)."""
    try:
        return _one_line(_decode_text_strict(path.read_bytes()))
    except (OSError, ValueError):
        return ""


def write_secret(path: Path, value: str) -> None:
    """Write a secret file atomically (temp file in the same folder, mode 0600 from creation, os.replace); no
    trailing newline: OpenCode's {file:} reads it as is."""
    maude_dir()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".secret-", suffix=".tmp")  # created 0600
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(_one_line(value).encode("utf-8"))
        replace_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def remove_secret(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def active_anthropic_key() -> str:
    """The Anthropic API key when "Use this API key" is on (settings.json flag + ~/.maude/anthropic-key), else "".
    Windows/macOS only: in the sandbox a flag in settings.json is ignored."""
    if is_native() and load_settings().get(ANTHROPIC_ACTIVE_SETTING) is True:
        return read_secret(anthropic_key_file())
    return ""


def with_active_anthropic_key(env: dict, key: str, ambient) -> dict:
    """Pure (Windows/macOS): `env` (what claude_env_from returned) with the active API key forced on, even when a
    claude login exists. Bedrock/Foundry/Vertex flags that are set anywhere are turned off and a custom base URL /
    auth token is removed (a None value = remove), for Claude Code's child environment only; no file is edited."""
    out = dict(env)
    merged = {**ambient, **env}
    out["ANTHROPIC_API_KEY"] = key
    for k in _ACTIVE_OFF_VARS:
        if k in merged:
            out[k] = "0"
    for k in _ACTIVE_UNSET_VARS:
        if k in merged:
            out[k] = None
    return out


def apply_anthropic_key(new_key: str, active: bool) -> None:
    """Settings > Anthropic (Windows/macOS only; a no-op in the sandbox). Stores the key in ~/.maude/anthropic-key
    (0600) and sets the flag in settings.json; plat_claude_env() injects the key when the flag is on (it wins over a
    login, Bedrock and Foundry), nothing is added to clauderc. A clauderc ANTHROPIC_API_KEY equal to the key is moved
    into the key file (a different key of the user's is never removed). new_key "" = keep the stored key file,
    else take the one exported in clauderc (a stored key is never overwritten by a clauderc key). ValueError (message for the user) when activating with no key; OSError when a file
    cannot be written."""
    if not is_native():
        return
    rc = read_clauderc()
    key = _one_line(new_key) or read_secret(anthropic_key_file()) or rc.get("ANTHROPIC_API_KEY", "")
    if not key:
        if active:
            raise ValueError("Enter an ANTHROPIC_API_KEY first.")
        return
    write_secret(anthropic_key_file(), key)  # the key is parked first: never lost between the writes below
    settings = load_settings()
    settings[ANTHROPIC_ACTIVE_SETTING] = bool(active)
    if not save_settings(settings):
        raise OSError("could not write ~/.maude/settings.json")
    if rc.get("ANTHROPIC_API_KEY") == key:
        remove_clauderc_keys(["ANTHROPIC_API_KEY"])
    if os.environ.get("ANTHROPIC_API_KEY") == key:
        os.environ.pop("ANTHROPIC_API_KEY", None)

# ── Settings > OpenCode: a custom OpenAI-compatible provider in ~/.config/opencode/opencode.json ───────
# Format (opencode.ai/docs/providers "Custom provider", /docs/config): `provider.<id>.npm` =
# "@ai-sdk/openai-compatible", `options.baseURL`, `options.apiKey`, `models.<model-id>`; the top-level `model`
# ("<provider>/<model>") is what `opencode` starts on when no --model is given, i.e. the picker's Model: auto.
# The key is referenced as {file:~/.maude/opencode-key} (OpenCode substitutes {file:...} and {env:...} in the config;
# `~/` is accepted), so it is not duplicated in the JSON, needs no environment variable and also works when
# `opencode` is started by hand.
OPENCODE_PROVIDER = "maude-custom"
OPENCODE_NPM = "@ai-sdk/openai-compatible"
OPENCODE_KEY_REF = "{file:~/.maude/opencode-key}"
OPENCODE_PREV_MODEL_SETTING = "opencode_prev_model"  # settings.json: the top-level `model` Maude overwrote


def opencode_config_path() -> Path:
    return Path.home() / ".config" / "opencode" / "opencode.json"


def opencode_url_error(url: str) -> str:
    """"" when `url` is acceptable as the OpenCode model URL, else a message. https anywhere; http only for
    localhost, 127.0.0.1 and ::1; no braces (OpenCode would substitute them), no user:password@, no whitespace."""
    if not url or re.search(r"[\s{}\"'\\]", url):
        return "OpenCode model URL: a plain URL like https://host/v1 is needed (no spaces, braces or quotes)."
    try:
        u = urllib.parse.urlsplit(url)
        host = u.hostname
        u.port  # raises ValueError for a bad port
    except ValueError:
        return "OpenCode model URL is not a valid URL."
    if u.scheme not in ("http", "https") or not host:
        return "OpenCode model URL must start with https:// (or http:// for localhost)."
    if "@" in u.netloc:
        return "OpenCode model URL must not contain user:password@ (use the api_key field)."
    if u.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
        return "http:// is only allowed for localhost, 127.0.0.1 and [::1]; use https://."
    return ""


def _opencode_load() -> tuple[Path, bytes, dict]:
    """(real path, raw bytes, parsed object) of opencode.json. A symlink is followed (the target is edited, the
    link stays). Comments / not an object: OSError, nothing touched."""
    path = Path(os.path.realpath(opencode_config_path()))
    obj: dict = {}
    raw = b""
    if path.exists():
        raw = path.read_bytes()
        try:
            obj = json.loads(_decode_text_strict(raw))
        except ValueError:
            raise OSError(f"{path} has comments or is not valid JSON, so Maude left it untouched "
                          "(remove the comments or edit it by hand)") from None
        if not isinstance(obj, dict):
            raise OSError(f"{path} is not a JSON object; it was left untouched")
    return path, raw, obj


def _opencode_store(path: Path, raw: bytes, obj: dict) -> None:
    """Atomic write (temp file beside the target) with a one-time `.maude-bak` copy (0600) of the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    bak = path.with_name(path.name + ".maude-bak")
    if raw and not bak.exists():
        shutil.copy2(path, bak)
        try:
            bak.chmod(0o600)
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".opencode-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
            f.write("\n")
        replace_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_opencode_custom() -> tuple[str, str]:
    """(base URL, model id) of the maude-custom provider in the user's opencode.json, "" when absent or unreadable."""
    try:
        obj = json.loads(_decode_text_strict(opencode_config_path().read_bytes()))
    except (OSError, ValueError):
        return "", ""
    if not isinstance(obj, dict):
        return "", ""
    prov = obj.get("provider")
    entry = prov.get(OPENCODE_PROVIDER) if isinstance(prov, dict) else None
    opts = entry.get("options") if isinstance(entry, dict) else None
    url = opts.get("baseURL") if isinstance(opts, dict) else ""
    model = obj.get("model")
    pre = OPENCODE_PROVIDER + "/"
    return (url if isinstance(url, str) else ""), (model[len(pre):] if isinstance(model, str) and model.startswith(pre) else "")


def merge_opencode_config(url: str, model: str, with_key: bool) -> None:
    """Add/refresh the maude-custom provider in opencode.json without touching anything else (other providers,
    other keys). with_key adds apiKey = {file:~/.maude/opencode-key} (the file itself is written by the caller
    AFTER this succeeds). Drops the `//` "managed by maude" marker (the file is the user's now, `maude update`
    no longer regenerates it). Sets the top-level `model`. See _opencode_load for the refusals."""
    path, raw, obj = _opencode_load()
    obj.setdefault("$schema", "https://opencode.ai/config.json")
    if isinstance(obj.get("//"), str) and "managed by maude" in obj["//"]:
        del obj["//"]
    prov = obj.get("provider") if isinstance(obj.get("provider"), dict) else {}
    entry = prov.get(OPENCODE_PROVIDER) if isinstance(prov.get(OPENCODE_PROVIDER), dict) else {}
    opts = entry.get("options") if isinstance(entry.get("options"), dict) else {}
    opts["baseURL"] = url
    if with_key:
        opts["apiKey"] = OPENCODE_KEY_REF
    models = entry.get("models") if isinstance(entry.get("models"), dict) else {}
    models.setdefault(model, {"name": model})
    entry.update({"npm": OPENCODE_NPM, "name": "Custom (Maude)", "options": opts, "models": models})
    prov[OPENCODE_PROVIDER] = entry
    obj["provider"] = prov
    pre = OPENCODE_PROVIDER + "/"
    s = load_settings()
    prev = obj.get("model")
    if isinstance(prev, str) and prev and not prev.startswith(pre):  # remember what we overwrite, for Remove
        s[OPENCODE_PREV_MODEL_SETTING] = prev
        save_settings(s)  # best effort: a failure only means Remove cannot restore it
    elif not prev:
        s.pop(OPENCODE_PREV_MODEL_SETTING, None)
        save_settings(s)
    obj["model"] = f"{OPENCODE_PROVIDER}/{model}"
    _opencode_store(path, raw, obj)


def remove_opencode_custom() -> bool:
    """Undo merge_opencode_config: delete provider.maude-custom and the top-level `model` when it points at it,
    then delete ~/.maude/opencode-key. True when the config changed. Same refusals as the merge."""
    path, raw, obj = _opencode_load()
    changed = False
    prov = obj.get("provider")
    if isinstance(prov, dict) and OPENCODE_PROVIDER in prov:
        del prov[OPENCODE_PROVIDER]
        changed = True
    if isinstance(obj.get("model"), str) and obj["model"].startswith(OPENCODE_PROVIDER + "/"):
        s = load_settings()
        prev = s.get(OPENCODE_PREV_MODEL_SETTING)
        if isinstance(prev, str) and prev:
            obj["model"] = prev  # the model that was set before the custom provider
        else:
            del obj["model"]
        if OPENCODE_PREV_MODEL_SETTING in s:
            s.pop(OPENCODE_PREV_MODEL_SETTING)
            save_settings(s)
        changed = True
    if changed:
        _opencode_store(path, raw, obj)
    remove_secret(opencode_key_file())
    return changed


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
        try:
            bak.chmod(0o600)  # the copy holds the same secrets as the original
        except OSError:
            pass  # no POSIX modes on Windows
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
    """The Settings modal (button "Settings", formerly "Set Creds"; also the credential gate).

    Above the mode buttons (outside the sandbox only): the checkbox "Only allow Kiro.dev and Github Copilot
    outside the Sandbox" (settings.json native_only_approved; Save persists it and calls
    app.apply_agent_restrictions()). Five input modes selectable via tabs:
      • Paste        — original `export FOO=bar` paste flow
      • Anthropic    — "Claude Login" button (suspends the TUI and runs the unwrapped
        ~/.local/bin/claude /login for native claude.ai OAuth; dismisses the modal as saved when a valid
        token lands in ~/.claude/.credentials.json), the ANTHROPIC_API_KEY field and the checkbox
        "Use this API key" (Windows/macOS only for now; in the sandbox both are disabled with a note and this pane
        saves nothing: use Claude Login or Paste). The key is kept in ~/.maude/anthropic-key (0600). Checked:
        plat_claude_env() injects it for Claude Code (it wins over a login). Unchecked: the key is kept but not used.
      • Bedrock      — three named fields (access key / secret / region)
      • Foundry      — base URL + API key
      • OpenCode     — model URL, model name, api_key of an OpenAI-compatible endpoint
        (merge_opencode_config; key in ~/.maude/opencode-key, "Remove" button)
    Paste, Bedrock and Foundry save to clauderc_path() (merged; ~/.azure/clauderc in the
    sandbox, ~/.maude/clauderc on Windows/macOS) so existing creds
    survive across modes; Bedrock additionally writes
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
    MODE_ANTHROPIC = "anthropic"
    MODE_OPENCODE = "opencode"

    def __init__(self) -> None:
        super().__init__()
        self.mode = self.MODE_PASTE
        self._anth_initial_use = False
        self._default_bedrock_initial = False

    def action_cancel(self) -> None:
        self.dismiss(False)

    def compose(self) -> ComposeResult:
        with Container(id="creds-box"):
            yield Label("Settings", id="creds-title")
            if is_native():  # no meaning in the sandbox
                yield Checkbox("Only allow Kiro.dev and Github Copilot outside the Sandbox",
                               id="creds-native-only",
                               value=bool(load_settings().get("native_only_approved", True)))
            with Horizontal(id="creds-tabs"):
                yield Button("Paste",     id="btn-creds-mode-paste",
                             variant="primary")
                yield Button("Anthropic", id="btn-creds-mode-anthropic",
                             variant="default")
                yield Button("Bedrock",   id="btn-creds-mode-bedrock",
                             variant="default")
                yield Button("Foundry",   id="btn-creds-mode-foundry",
                             variant="default")
                yield Button("OpenCode",  id="btn-creds-mode-opencode",
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
            self._switch_mode(self.mode)
            self._update_default_checkbox()
        except NoMatches:  # screen already closed
            return
        if is_native() and have_claude_login():
            if active_anthropic_key():
                self._set_status("[yellow]A Claude login exists, but the API key of Settings > Anthropic is in use "
                                 "(it wins). Uncheck \"Use this API key\" to use the login.[/]")
            else:
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

    @on(Button.Pressed, "#btn-creds-mode-anthropic")
    def mode_anthropic(self) -> None:
        self._switch_mode(self.MODE_ANTHROPIC)

    @on(Button.Pressed, "#btn-creds-mode-opencode")
    def mode_opencode(self) -> None:
        self._switch_mode(self.MODE_OPENCODE)

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
        # All mode bodies stay mounted from compose(); switching only toggles `display` and the
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
            ("#btn-creds-mode-paste",     self.MODE_PASTE),
            ("#btn-creds-mode-anthropic", self.MODE_ANTHROPIC),
            ("#btn-creds-mode-bedrock",   self.MODE_BEDROCK),
            ("#btn-creds-mode-foundry",   self.MODE_FOUNDRY),
            ("#btn-creds-mode-opencode",  self.MODE_OPENCODE),
        ):
            self.query_one(btn_id, Button).variant = "primary" if mode == btn_mode else "default"
        for pane_mode, pane_id in self._PANES.items():
            self.query_one(pane_id).display = (pane_mode == mode)
        self._focus_later(self._FOCUS[mode])

    _PANES = {"paste": "#creds-pane-paste", "anthropic": "#creds-pane-anthropic", "bedrock": "#creds-pane-bedrock",
              "foundry": "#creds-pane-foundry", "opencode": "#creds-pane-opencode"}
    _FOCUS = {"paste": "#creds-text", "anthropic": "#creds-anthropic-key", "bedrock": "#creds-aws-key",
              "foundry": "#creds-foundry-url", "opencode": "#creds-oc-url"}

    def _focus_later(self, selector: str) -> None:
        def go() -> None:
            try:
                if self._FOCUS[self.mode] == selector and self.is_attached:  # only the active pane's field
                    w = self.query_one(selector)
                    (self.query_one("#btn-creds-login") if w.disabled else w).focus()  # sandbox: the key field is disabled
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
        with Vertical(id="creds-pane-anthropic", classes="creds-pane"):
            yield Button("Claude Login", id="btn-creds-login", variant="default")
            yield Label("Claude subscription: sign in with your claude.ai account (no API key).")
            yield Label("ANTHROPIC_API_KEY:")
            has_key = bool(read_secret(anthropic_key_file()) or read_clauderc().get("ANTHROPIC_API_KEY"))
            # State when the screen opened: Save changes nothing unless the field or the checkbox differs from it.
            native = is_native()  # API key mode is Windows/macOS only for now: in the sandbox both are disabled
            self._anth_initial_use = native and bool(active_anthropic_key())  # the flag + key file only, not a pasted key
            yield Input(placeholder="saved (leave empty to keep it)" if has_key else "sk-ant-...",
                        id="creds-anthropic-key", password=True, disabled=not native)
            yield Checkbox("Use this API key", id="creds-anthropic-use", value=self._anth_initial_use, disabled=not native)
            if native:
                yield Label("Checked: Maude starts Claude with this key, even if you are logged in. "
                            "Unchecked: Claude Login / Bedrock / Foundry are used.")
            else:
                yield Label("API key mode is not available in the sandbox yet. Use Claude Login, or Paste an "
                            "`export ANTHROPIC_API_KEY=...` line.", id="creds-anthropic-note")
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
        with Vertical(id="creds-pane-opencode", classes="creds-pane"):
            oc_url, oc_model = read_opencode_custom()
            yield Label("OpenCode: any OpenAI-compatible endpoint. OpenCode's model 'auto' then uses it.")
            yield Label("OpenCode model URL:")
            yield Input(value=oc_url, placeholder="https://host/v1", id="creds-oc-url")
            yield Label("Model name (as the endpoint calls it):")
            yield Input(value=oc_model, placeholder="e.g. qwen3-coder", id="creds-oc-model")
            yield Label("api_key (empty keeps the saved one; kept in ~/.maude/opencode-key):")
            yield Input(placeholder="saved (leave empty to keep it)" if read_secret(opencode_key_file())
                        else "api key", id="creds-oc-key", password=True)
            yield Button("Remove OpenCode custom provider", id="btn-oc-remove", variant="default")

    def _update_default_checkbox(self) -> None:
        """Show the Bedrock-vs-Foundry default toggle only when both
        credential families are present on disk (active or parked)."""
        cb = self.query_one("#creds-default-bedrock", Checkbox)
        self._default_bedrock_initial = False
        if have_bedrock_creds() and have_foundry_creds():
            cb.display = True
            # If clauderc is parked as .inactive, Bedrock is the
            # currently-default provider; check the box accordingly.
            # Only the parked file present = Bedrock is the default. When BOTH exist (a save re-created the active
            # file while parked, or a stale .inactive is left next to an active Foundry setup) the active file
            # decides: any credential in it (CLAUDE_CODE_USE_FOUNDRY=1 included) means Foundry, unchecked.
            parked = clauderc_inactive_path().exists() and not read_clauderc()
            cb.value = parked or (
                bool(os.environ.get("CLAUDE_CODE_USE_BEDROCK"))
                and not bool(os.environ.get("CLAUDE_CODE_USE_FOUNDRY"))
            )
            self._default_bedrock_initial = cb.value
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
            elif self.mode == self.MODE_ANTHROPIC:
                self._save_anthropic()
            elif self.mode == self.MODE_BEDROCK:
                self._save_bedrock()
            elif self.mode == self.MODE_OPENCODE:
                self._save_opencode()
            else:
                self._save_foundry()
            self._save_native_only()
        except _CredsValidation:
            # The handler already populated the status label.
            return
        except OSError as err:
            self._set_status(f"[bold red]Could not save: {err}[/]")
            return
        # Apply the routing checkbox if it's visible (it concerns Bedrock vs Foundry only; the Anthropic key
        # and OpenCode panes have their own routing).
        if self.mode not in (self.MODE_ANTHROPIC, self.MODE_OPENCODE):
            try:
                self._apply_default_provider()
            except OSError as err:
                self._set_status(f"[bold red]Could not switch the default provider: {err}[/]")
                return
        self.dismiss(True)

    def _save_native_only(self) -> None:
        """Persist "Only allow Kiro.dev and Github Copilot outside the Sandbox" (outside the sandbox only) and
        re-grey the Agent column. Runs after the active pane saved, so a failed validation saves nothing."""
        try:
            cb = self.query_one("#creds-native-only", Checkbox)
        except NoMatches:
            return
        s = load_settings()
        s["native_only_approved"] = bool(cb.value)
        if not save_settings(s):
            raise OSError("could not write ~/.maude/settings.json")
        apply = getattr(self.app, "apply_agent_restrictions", None)
        if callable(apply):
            apply()

    def _save_anthropic(self) -> None:
        if not is_native():
            return  # sandbox: the key field and the checkbox are disabled, this pane saves nothing
        key = self.query_one("#creds-anthropic-key", Input).value.strip()
        use = self.query_one("#creds-anthropic-use", Checkbox).value
        if not key and use == self._anth_initial_use:
            return  # nothing changed in this pane: touch no file
        try:
            apply_anthropic_key(key, use)
        except ValueError as err:
            self._set_status(f"[bold red]{err}[/]")
            raise _CredsValidation() from None

    def _save_opencode(self) -> None:
        url = self.query_one("#creds-oc-url", Input).value.strip()
        model = self.query_one("#creds-oc-model", Input).value.strip()
        key = self.query_one("#creds-oc-key", Input).value.strip()
        if not url and not model and not key:
            return  # nothing entered: only the checkbox above may have changed
        err = opencode_url_error(url)
        if err:
            self._set_status(f"[bold red]{err}[/]")
            raise _CredsValidation()
        if not MODEL_ID_RE.fullmatch(model) or "#" in model:
            self._set_status("[bold red]OpenCode needs the model name the endpoint expects "
                             "(letters, digits and . _ : / @ + -).[/]")
            raise _CredsValidation()
        # Config first: a refused file (comments, not an object) leaves nothing half-saved. The key file follows;
        # a key left empty keeps the saved one. opencode.json refers to it as {file:~/.maude/opencode-key}.
        try:
            merge_opencode_config(url, model, with_key=bool(key) or bool(read_secret(opencode_key_file())))
        except OSError as err:
            self._set_status(f"[bold red]OpenCode config not saved: {err}[/]")
            raise _CredsValidation() from None
        if key:
            write_secret(opencode_key_file(), key)
        if not is_native():
            self.app.notify("maude update no longer manages ~/.config/opencode/opencode.json now that it holds your "
                            "custom provider.", timeout=8)

    @on(Button.Pressed, "#btn-oc-remove")
    def oc_remove(self) -> None:
        try:
            changed = remove_opencode_custom()
        except OSError as err:
            self._set_status(f"[bold red]Could not remove: {err}[/]")
            return
        for i in ("#creds-oc-url", "#creds-oc-model", "#creds-oc-key"):
            self.query_one(i, Input).value = ""
        self._set_status("OpenCode custom provider removed." if changed else "No OpenCode custom provider was set up.")

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
        if cb.value == self._default_bedrock_initial:
            return  # not flipped in this session: change nothing (no parking, no env or clauderc rewrite)
        if cb.value:
            # Bedrock is the default now: a plain API key must not win over it, and clauderc is parked so the
            # wrapper doesn't source Foundry creds. Both files existing is merged, never deleted (park_clauderc).
            if cb.value != self._default_bedrock_initial and active_anthropic_key():  # only when flipped just now
                apply_anthropic_key("", False)
            park_clauderc(True)
            os.environ.pop("CLAUDE_CODE_USE_FOUNDRY", None)
            os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
        else:
            # Foundry active → restore clauderc if it was parked (merging when a save re-created it).
            park_clauderc(False)
            os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)
            os.environ["CLAUDE_CODE_USE_FOUNDRY"] = "1"
            if clauderc_path().exists():
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

    Triggered at TUI startup (sandbox; natively only when Maude started its own agent) or by To Github
    when ~/.ssh/id_ed25519 is encrypted but the running ssh-agent doesn't have it yet.
    """

    BINDINGS = [Binding("escape", "skip", show=False)]

    def action_skip(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Container(id="unlock-box"):
            yield Label("Unlock SSH key", id="unlock-title")
            yield Label(
                f"Your ~/.ssh/{active_ssh_key().name if is_native() else 'id_ed25519'} is passphrase-protected. Enter the\n"
                "passphrase to load it into ssh-agent so git push, gh, etc.\n"
                "work without prompting later."
                + ("\nIt is also stored in the macOS keychain." if IS_MAC else "")
                + ("\nThe Windows ssh-agent keeps the key loaded, even after a restart, until you run ssh-add -d." if IS_WIN else ""),
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
        ok, msg = ssh_add_with_passphrase(active_ssh_key() if is_native() else SSH_KEY_PATH, passphrase)
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
    # Windows/macOS: SSH commit signing instead of GPG, no keychain package/.bashrc block.
    NATIVE_STEP_TITLES = (
        STEP_TITLES[0], STEP_TITLES[1], STEP_TITLES[2],
        "Step 4 of 4 — Git Config & Signing",
    )

    def _title(self, step: int) -> str:
        return (self.NATIVE_STEP_TITLES if is_native() else self.STEP_TITLES)[step]

    def __init__(self) -> None:
        super().__init__()
        self.step = 0
        self.signing_uploaded = False   # native: SSH signing key is registered on GitHub
        self._use_refresh = False       # native, already logged in: only add the signing scope
        self._need_scope = False        # the signing key upload failed for a missing scope
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
            yield Label(self._title(0), id="wiz-title")
            yield Container(id="wiz-body")
            yield Log(id="wiz-log", auto_scroll=True, max_lines=200)
            with Horizontal(id="wiz-buttons"):
                yield Button("Back",   id="wiz-back",   variant="default")
                yield Button("Skip",   id="wiz-skip",   variant="warning")
                yield Button("Next",   id="wiz-next",   variant="success")
                yield Button("Cancel", id="wiz-cancel", variant="error")

    async def on_mount(self) -> None:
        await self._render_step()
        if IS_WIN or IS_MAC:
            self.call_after_refresh(self._offer_missing_tools)

    def _offer_missing_tools(self) -> None:
        """Windows: offer MinGit / GitHub CLI (per-user, no admin) when missing. macOS: git via the
        Command Line Tools or Homebrew, gh via Homebrew (or the hint). The offers ask on the
        console, so the TUI is suspended; the user asked for Git setup, so ask again even if declined."""
        if IS_MAC:
            if _mac_find("git") and _mac_find("gh"):
                return
        elif plat_tool("git") and plat_tool("gh"):
            return
        try:
            with self.app.suspend():
                if IS_MAC:
                    mac_ensure_tool("git")
                    mac_ensure_tool("gh")
                    if not (_mac_find("git") and _mac_find("gh")):  # let the user read why
                        input("Press Enter to go back to Maude. ")
                else:
                    mx.reset_offers()
                    mx.offer_git()
                    mx.offer_gh()
                    if not (plat_tool("git") and plat_tool("gh")):  # let the user read why
                        input("Press Enter to go back to Maude. ")
        except Exception as err:  # never take the wizard down
            self._log(f"Could not run the installer offer: {err}")
        self.app.call_after_refresh(self._rerender_current_step)

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
        self.query_one("#wiz-title", Label).update(self._title(self.step))
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
        if is_native() and not openssh_available():
            body.mount(Label(Text(OPENSSH_MISSING_MSG + "\nOr click Skip.")))
            return
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
            if is_native():
                body.mount(Label(Text(
                    "No SSH key found at ~/.ssh/id_ed25519.\n"
                    + ("Leave the passphrase blank for a key without one (recommended on Windows: the "
                       "OpenSSH ssh-agent service is off by default and enabling it needs an "
                       "administrator). With a passphrase, git asks for it unless that service runs."
                       if IS_WIN else
                       "A passphrase is stored in the macOS keychain, so you type it only once.")
                    + "\nThen click Generate ed25519 key."
                )))
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
        if not gh_exe():
            if IS_WIN:
                hint = ("Close this wizard and open it again: Maude offers to install it for you "
                        "(no administrator needed), or get it from https://cli.github.com.")
            elif IS_MAC:
                hint = "Run [bold]brew install gh[/] in another terminal, then come back."
            else:
                hint = "Run [bold]mom install -y gh[/] in another terminal, then come back."
            body.mount(Label(Text.from_markup(
                "[yellow]gh CLI is not installed.[/]\n\n" + hint
                + " Or click [bold]Skip[/] to skip GitHub auth."
            )))
            return
        if already:
            body.mount(Label(Text.from_markup(
                "[green]✓ gh CLI is already authenticated.[/]\n\n"
                "Click [bold]Next[/] to move on. Use "
                "[bold]Re-authenticate[/] to switch accounts."
            )))
        elif is_native():
            body.mount(Label(Text(
                "We'll run `gh auth login --web`. Your browser opens by itself: "
                "enter the one-time code shown below and authorize GitHub CLI.\n\n"
                "Afterwards the wizard registers your SSH key on GitHub as a commit "
                "signing key (SSH signing, no GPG), so your commits show as Verified."
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
        if is_native():
            extra = "  • Sign commits with your SSH key (gpg.format = ssh)\n"
            if IS_MAC:
                extra += "  • Keep the key in the macOS agent and keychain (~/.ssh/config)\n"
            body.mount(Label(
                "Finalise the setup:\n"
                f"  • git config user.name = [bold]{self.full_name}[/]\n"
                f"  • git config user.email = [bold]{self.email}[/]\n"
                "  • init.defaultBranch = [bold]main[/]\n"
                + extra + "\n"
                "Click [bold]Finish[/] to apply, or [bold]Back[/] to revisit a step."
            ))
            return
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
        self._log(f"[skipped] {self._title(self.step)}")
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
        if not gh_exe():
            self._log("gh CLI is not installed (Windows: reopen this wizard to install it; "
                      "macOS: brew install gh; sandbox: mom install -y gh)."
                      if is_native() else "gh CLI is not installed (try `mom install -y gh`).")
            return
        # Run gh in a worker thread and stream its stdout into the Log
        # widget. Suspending the TUI doesn't work reliably here — gh's
        # interactive "Press Enter" prompt closes immediately on EOF —
        # so we keep the TUI up, feed Enter on stdin ourselves, and let
        # the user Ctrl+click the URL straight from the wizard log.
        self.query_one("#wiz-gh-auth", Button).disabled = True
        # Only a missing scope is fixed with `gh auth refresh -s`; an explicit Re-authenticate is a full login
        # (refresh cannot switch accounts).
        self._use_refresh = is_native() and self._need_scope and gh_is_authed()
        self._need_scope = False
        self._log("─────────────── How this works ───────────────")
        if is_native():
            self._log("1. gh prints a one-time code below (e.g. ABCD-1234)")
            self._log("2. Your browser opens by itself (else open https://github.com/login/device)")
            self._log("3. Enter the code, then click 'Authorize GitHub CLI'")
            self._log("4. Come back here — gh detects the auth automatically")
        else:
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
        native   = is_native()  # Windows/macOS: gh is recent (--skip-ssh-key) and opens a real browser
        if pub.exists() and not native:
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
                    # Scope needed to upload the signing key after auth completes
                    # (GPG key in the sandbox, SSH signing key natively).
                    (gh_refresh_cmd(gh_exe() or "gh") if self._use_refresh else gh_login_cmd(gh_exe() or "gh"))
                    if native else
                    ["gh", "auth", "login",
                     "--hostname", "github.com",
                     "--git-protocol", "ssh",
                     "--web",
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
                    if not native and any(s in line for s in noise_substrings):
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
            self._provision_signing_now()
        else:
            self._log(f"gh auth did not complete (exit code: {returncode}).")
        # Refresh the step so the button label flips to "Re-authenticate".
        self.app.call_after_refresh(self._rerender_current_step)

    def _rerender_current_step(self) -> None:
        # Schedule the async re-render from sync context.
        self.run_worker(self._render_step, exclusive=False)

    def _provision_signing_now(self) -> None:
        """Sandbox: silent GPG signing key. Windows/macOS: register the SSH key as a signing key."""
        if is_native():
            self._provision_ssh_signing_now()
        else:
            self._provision_gpg_now()

    def _provision_ssh_signing_now(self) -> None:
        """Windows/macOS: upload the SSH public key to GitHub as a signing key (no GPG)."""
        if not self.ssh_key_path:
            self._log("No SSH key chosen in step 2; skipping the signing key.")
            return
        pub = read_ssh_pubkey(self.ssh_key_path)
        if not pub:
            self._log(f"Could not read {self.ssh_key_path}.pub; skipping the signing key.")
            return
        self._log("Registering your SSH key on GitHub as a signing key…")
        ok, msg = gh_upload_key("ssh-signing-key", pub, "Maude (TUI) signing")
        if ok:
            self._log("✓ " + msg)
            self.signing_uploaded = True
        else:
            self._log("✗ " + msg)
            if "scope" in msg.lower():
                self._need_scope = True  # the next press only adds the scope (gh auth refresh)
                self._log("Click 'Re-authenticate' above — this adds the "
                          f"{GH_SIGNING_SCOPE} scope, which lets the upload succeed.")

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
        done = self.signing_uploaded if is_native() else (self.gpg_key_id and self.gpg_uploaded)
        if gh_is_authed() and not done:
            self._provision_signing_now()
        await self._advance()

    # ── Step 4: finalise ────────────────────────────────────────────

    def _finish(self) -> None:
        self._log("Applying git config…")
        if self.full_name: git_config_set("user.name",          self.full_name)
        if self.email:     git_config_set("user.email",         self.email)
        git_config_set("init.defaultBranch", "main")
        if is_native():
            self._finish_native()
            return
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

    def _finish_native(self) -> None:
        """Windows/macOS tail of step 4. The slow parts (ssh-add, the signing test) run in a worker
        thread so the UI never freezes."""
        for sel in ("#wiz-next", "#wiz-back", "#wiz-cancel", "#wiz-skip"):
            try:
                self.query_one(sel).disabled = True
            except Exception:
                pass
        self._log("Checking SSH and signing…")
        self.run_worker(self._finish_native_work, thread=True)

    def _finish_native_work(self) -> None:
        def log(m: str) -> None:
            self.app.call_from_thread(self._log, m)
        try:
            self._finish_native_steps(log)
        except Exception as err:  # never leave the wizard stuck with disabled buttons
            log(f"  ! unexpected error: {err}")
        self.app.call_from_thread(self._finish_native_done)

    def _finish_native_done(self) -> None:
        self._log("[green]✓ Setup complete.[/]")
        self._dismissed = True
        self.dismiss(True)

    def _finish_native_steps(self, log) -> None:
        git = plat_tool("git")
        if not git:
            log("! git is not installed, so nothing could be written to your git config. "
                "Reopen this wizard to install it.")
        key = Path(self.ssh_key_path) if self.ssh_key_path else None
        pub = key.with_suffix(".pub") if key else None
        ssh_ok = openssh_available()
        if not ssh_ok:
            log("! " + OPENSSH_MISSING_MSG)
        if git:
            pair = apply_ssh_command(git_path=git)  # MinGit only; independent of signing
            if pair:
                log(f"  {pair[0]} = {pair[1]}")
        if IS_MAC and ensure_mac_ssh_config(Path.home() / ".ssh" / "config"):
            log("  ✓ ~/.ssh/config: AddKeysToAgent + UseKeychain.")
        if ssh_ok and key and key.exists():
            if ensure_ssh_agent():
                mac_load_keychain()
                if not ssh_key_in_agent(key):
                    enc = ssh_key_has_passphrase(key)
                    # Encrypted: needs the passphrase from step 2. Unencrypted: not on Windows, where
                    # the agent keeps keys across reboots (registry) and the key needs no agent.
                    if (enc and self.ssh_passphrase) or (not enc and not IS_WIN):
                        ok, msg = ssh_add_with_passphrase(key, self.ssh_passphrase)
                        log("  ✓ key loaded into ssh-agent." if ok else
                            f"  ! ssh-add: {msg.splitlines()[-1] if msg else 'failed'}")
            elif IS_WIN:
                log("  (the Windows ssh-agent service is not running; an unencrypted key needs no agent)")
        if not (git and pub and pub.exists()):
            return
        if not self.signing_uploaded:
            log("  (commit signing not configured: the signing key is not on GitHub yet; "
                "run Setup Git(hub) again after re-authenticating)")
            return
        why = signing_conflict(str(pub))
        if why:
            log(f"  Your existing signing setup was left untouched: {why}.")
            return
        ok, msg = signing_roundtrip(str(pub))
        if not ok:
            log(f"  ! Commit signing was NOT enabled: {msg}.")
            log("    Use a key without a passphrase, or run the ssh-agent with the key loaded, "
                "then run Setup Git(hub) again. (A broken signing setup would hang every git commit.)")
            return
        for k, v in apply_ssh_signing(str(pub)):
            log(f"  {k} = {v}")
        if key and ssh_key_has_passphrase(key):  # the test passed only because an agent holds the key
            if IS_WIN:
                log("  Note: this key has a passphrase and signs through the ssh-agent service. If that service "
                    "is not running after a reboot, commits will fail to sign; start the ssh-agent service, then run ssh-add, or set "
                    "commit.gpgsign false.")
            else:
                log("  Note: this key has a passphrase and signs through the ssh-agent. Maude reloads it "
                    "from the macOS keychain at start; outside Maude run ssh-add if a commit fails to sign.")

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
        git, gh = git_exe(), gh_exe() or "gh"
        if is_native() and not plat_tool("git"):
            return False, "Git is not installed. Open Setup Git(hub): Maude offers to install it."
        # Guard against the SSH-key passphrase trap. If the user has an
        # encrypted SSH key but no agent has it loaded, both
        # `gh repo create --push` and `git push` will block forever
        # waiting for a passphrase prompt that no one can type. We
        # short-circuit with a clear error so the caller can pop the
        # unlock modal instead.
        key = push_guard_key()
        if is_native() and key.exists() and not openssh_available():
            return False, OPENSSH_MISSING_MSG
        if (key.exists()
                and (not ssh_agent_running()
                     or not ssh_key_in_agent(key))
                and ssh_key_has_passphrase(key)):
            if IS_WIN and not ssh_agent_running():
                # Nothing the unlock screen could load the key into (starting the service needs admin).
                return False, ("your SSH key has a passphrase and the Windows ssh-agent service is "
                               "not running. Enable that service (administrator) or create a key "
                               "without a passphrase in Setup Git(hub).")
            return False, ("ssh key needs to be unlocked first — "
                           "exit the TUI and run `menu` again, or "
                           "click Setup Git(hub) to load the key.")
        # Force ssh into batch mode for *this* push so a misconfigured
        # state errors out fast (5–10s) instead of blocking on a
        # passphrase prompt for 60+s. Natively it is the platform's one OpenSSH.
        push_env = {
            **os.environ,
            **push_ssh_env(os.environ, git_config_effective(cwd, "core.sshCommand") if is_native() else ""),
        }
        if is_native():  # gh runs git from PATH: make sure the one we found is there
            push_env["PATH"] = os.path.dirname(git) + os.pathsep + push_env.get("PATH", "") \
                if os.path.isabs(git) else push_env.get("PATH", "")
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
            [git, "-C", cwd, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if log.returncode != 0:
            # No commits yet — try to make an initial one from existing files.
            subprocess.run([git, "-C", cwd, "add", "-A"], capture_output=True)
            commit = subprocess.run(
                [git, "-C", cwd, "commit", "-m", "Initial commit",
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
                [git, "-C", cwd, "add", "README.md"],
                capture_output=True, text=True, timeout=5,
            )
            subprocess.run(
                [git, "-C", cwd, "commit", "-m", "Add README.md"],
                capture_output=True, text=True, timeout=15,
            )

        # Does the remote already exist on GitHub?
        check = subprocess.run(
            [gh, "repo", "view", f"{owner}/{repo}"],
            capture_output=True, text=True, timeout=15,
        )
        if check.returncode != 0:
            create = subprocess.run(
                [gh, "repo", "create", f"{owner}/{repo}",
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
                [git, "-C", cwd, "remote", "add", "origin", url],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return False, (r.stderr or r.stdout).strip()
        elif existing != url:
            r = subprocess.run(
                [git, "-C", cwd, "remote", "set-url", "origin", url],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return False, (r.stderr or r.stdout).strip()
        push = subprocess.run(
            [git, "-C", cwd, "push", "-u", "origin", "HEAD"],
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
        if IS_MAC and not plat_tool("git"):
            self.query_one("#detach-status", Label).update(
                "[bold red]Git is not installed. Open Setup Git(hub): Maude offers to install it.[/]"
            )
            return
        try:
            r = subprocess.run(
                [git_exe(), "-C", cwd, "remote", "remove", "origin"],
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
        width: 49;
        overflow-y: auto;            /* at 80x24 or 120x30 the pickers do not all fit: scroll, never clip */
        scrollbar-size-vertical: 1;
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

    /* three pickers share the sidebar width: agent | model | thinking (label width + 4 each) */
    #pick-row #agent-col  { width: 22; }
    #pick-row #model-col  { width: 13; }
    #pick-row #effort-col { width: 10; }

    /* greyed-out choices: agents not allowed outside the sandbox, Thinking when the agent has none */
    RadioButton:disabled, RadioSet:disabled > RadioButton {
        color: #5a5050;
        text-style: none;
    }
    RadioButton:disabled > .toggle--button {
        color: #4a4040;
    }

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
        margin-top: 0;
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
        margin-top: 0;
        margin-bottom: 0;
    }

    #agent-label, #model-label, #effort-label {
        color: #d4a0a0;
        text-style: bold;
        margin-bottom: 0;
    }

    #agent-select, #model-select, #effort-select {
        background: #242424;
        border: none;
        padding: 0;
        height: auto;
    }

    #agent-select:focus-within, #model-select:focus-within, #effort-select:focus-within {
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
        padding: 1 3;
        width: 90;
        max-width: 100%;
        height: 42;
        max-height: 100%;
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
        min-width: 0;
    }

    #creds-body {
        height: 1fr;
        overflow-y: auto;
    }

    #creds-body .creds-pane {
        height: auto;
    }

    #creds-pane-anthropic, #creds-pane-bedrock, #creds-pane-foundry, #creds-pane-opencode {
        display: none;  /* paste is the initial mode; _switch_mode toggles these */
    }

    #creds-box Checkbox {
        border: none;
        padding: 0;
        margin: 0;
        height: 1;
        width: 100%;
    }

    #creds-native-only {
        margin-bottom: 1;
    }

    #creds-pane-anthropic #btn-creds-login {
        margin-bottom: 1;
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
        height: auto;
        max-height: 3;
        width: 100%;
        color: #c09898;
        margin-top: 0;
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
        model: optional Claude Code model (alias, claude-* id or auto) to preselect with Claude Code (ignored when
        Claude Code is not allowed here or the model is not acceptable for it)."""
        super().__init__()
        self._select = os.path.realpath(select) if ((IS_WIN or IS_MAC) and select) else ""
        self._extra: tuple = (self._select,) if self._select else ()
        self._bar_on = False  # the warning bar (#win-warning) is active
        self._settings = load_settings()
        self._cfg = load_models_config()  # read once; again only when the daily refresh has written a new file
        saved_tool = read_tool()
        claude_model, self._effort = read_model_effort()
        if model and agent_allowed(DEFAULT_TOOL, self._settings) \
                and model in [i for _, i in agent_models(DEFAULT_TOOL, self._cfg)]:
            # `maude.exe --model X`: Claude Code with that model, remembered like a click
            self._tool, self._model = DEFAULT_TOOL, resolve_model(DEFAULT_TOOL, model, cfg=self._cfg)
            if self._model == model:
                save_tool(self._tool)
                save_model(self._model)
        else:  # the restriction may put us on Kiro CLI for THIS session only: nothing is written
            self._tool, self._model = initial_selection(saved_tool, claude_model, read_agent_models(),
                                                        self._settings, cfg=self._cfg)
        self._forced = self._tool != read_tool()

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
                    with Vertical(classes="opt-col", id="agent-col"):
                        yield Label("Agent", id="agent-label")
                        with RadioSet(id="agent-select"):
                            for t in AGENTS:
                                yield RadioButton(t, value=(t == self._tool),
                                                  disabled=not agent_allowed(t, self._settings))
                    with Vertical(classes="opt-col", id="model-col"):
                        yield Label("Model", id="model-label")
                        # A fixed pool of buttons: relabelled (and hidden when unused) whenever the agent changes,
                        # so nothing is removed or mounted at runtime.
                        self._model_entries = agent_models(self._tool, self._cfg)
                        with RadioSet(id="model-select"):
                            for n in range(MODEL_SLOTS):
                                if n < len(self._model_entries):
                                    lab, mid = self._model_entries[n]
                                    yield RadioButton(Text(lab), value=(mid == self._model))
                                else:
                                    yield RadioButton("-", disabled=True)
                    with Vertical(classes="opt-col", id="effort-col"):
                        yield Label("Thinking", id="effort-label")
                        with RadioSet(id="effort-select", disabled=self._tool not in EFFORT_AGENTS):
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
            yield Button("Settings",        id="btn-creds")
            yield Button("Update Maude",   id="btn-update")
            yield Button("PowerShell" if IS_WIN else "Command Line", id="btn-cli")
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
        self._fill_models()  # hides the unused buttons of the Model pool
        start_models_refresh(lambda: self.call_from_thread(self._reload_models))  # daily list refresh, on a thread
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
        self._sync_cursor("#model-select", tuple(i for _, i in self._model_entries), self._model)
        self._sync_cursor("#agent-select", AGENTS, self._tool)
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
        key = active_ssh_key() if is_native() else SSH_KEY_PATH  # native: id_rsa counts too
        if not key.exists():
            return
        if is_native() and not openssh_available():
            return  # Windows without the OpenSSH Client feature: nothing can load or use the key
        # Windows/macOS: ssh_tool() picks the one OpenSSH stack. No reachable agent (Windows: the
        # ssh-agent service is off by default) -> nothing to unlock into; skip cleanly.
        # Start an agent if one isn't already exposed in the env. On a
        # fresh terminal `keychain` may not have run yet (the .bashrc
        # block executes before `maude tui`, but the wizard's keychain
        # block was just appended and won't take effect until the next
        # login). Spinning up our own ssh-agent is cheap and keeps git
        # push from blocking on a passphrase prompt.
        if is_native() and ssh_agent_running():
            # Windows/macOS: the user's own agent (Windows ssh-agent service, macOS launchd agent)
            # is already in charge of the key; never prompt for its passphrase at startup.
            mac_load_keychain()  # macOS: no-op elsewhere; loads keychain-stored keys silently
            return
        if not ensure_ssh_agent():
            return
        mac_load_keychain()  # macOS: after a restart the agent is empty until the keychain is loaded
        if ssh_key_in_agent(key):
            return  # already loaded
        if not ssh_key_has_passphrase(key):
            # Unencrypted key — load silently with empty passphrase. Not on Windows: its agent
            # keeps keys across reboots (registry) and an unencrypted key needs no agent.
            if not IS_WIN:
                ssh_add_with_passphrase(key, "")
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
        if not IS_WIN:
            self.exit()  # back to the shell that started the TUI (bash)
            return
        # Windows: maude.exe may have been started from Explorer/a shortcut (no shell behind it),
        # so open PowerShell here and come back to the TUI when it exits.
        ps = find_powershell()
        if not ps:
            self.notify("Could not start PowerShell: powershell.exe not found.", severity="error")
            return
        sel = self._selected_project()
        cwd = str(sel) if sel is not None and sel.is_dir() else os.path.expanduser("~")
        err = ""
        # App.suspend() has no try/finally: nothing may raise inside the with block, or the
        # TUI driver is never resumed. So catch everything there and notify afterwards.
        with self.suspend():
            print("Type exit to return to Maude.", flush=True)
            # Textual runs under asyncio.run(), whose SIGINT handler cancels the main task, so
            # Ctrl+C inside PowerShell would end Maude: ignore it here, the child still gets it.
            try:
                old = signal.signal(signal.SIGINT, signal.SIG_IGN)
            except (ValueError, OSError):
                old = None
            try:
                # raw clauderc exports (like Kiro/OpenCode/Crush): claude and kiro-cli both work
                subprocess.run([ps, "-NoLogo"], cwd=cwd, env=_child_env(tools=True), check=False)
            except OSError as e:
                err = str(e)
            finally:
                if old is not None:
                    signal.signal(signal.SIGINT, old)
        if err:
            self.notify(f"Could not start PowerShell: {err}", severity="error")
        self._refresh_table()

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
        idx = event.radio_set.pressed_index
        if not 0 <= idx < len(self._model_entries):
            return
        new = self._model_entries[idx][1]
        if new == self._model:  # echo of a programmatic update
            return
        self._model = new
        remember_model(self._tool, new)  # Claude Code: maude-model; the other agents: agent-models.json
        self.query_one("#projects-table", DataTable).focus()

    def _reload_models(self) -> None:
        """The daily refresh wrote a new models-default.json: use it (the selection stays if it is still listed)."""
        self._cfg = load_models_config()
        self._model = resolve_model(self._tool, self._model, cfg=self._cfg)
        self._fill_models()

    def _fill_models(self) -> None:
        """Show the model list of the current agent (auto first) in the fixed pool of Model buttons and select
        self._model (which must be in the list; else the agent's default). Nothing is saved here."""
        entries = agent_models(self._tool, self._cfg)
        self._model_entries = entries
        ids = [i for _, i in entries]
        if self._model not in ids:
            self._model = resolve_model(self._tool, self._model, cfg=self._cfg)
        idx = ids.index(self._model)
        rs = self.query_one("#model-select", RadioSet)
        buttons = list(rs.query(RadioButton))
        with rs.prevent(RadioButton.Changed):
            for i, b in enumerate(buttons):
                if i < len(entries):
                    b.label = Text(entries[i][0])  # Text: a label like "[/x]" is never parsed as markup
                    b.display, b.disabled, b.value = True, False, i == idx
                else:
                    b.label = Text("-")
                    b.display, b.disabled, b.value = False, True, False
        rs._pressed_button = buttons[idx]  # type: ignore[attr-defined]
        self._sync_cursor("#model-select", tuple(ids), self._model)
        self.query_one("#effort-select", RadioSet).disabled = self._tool not in EFFORT_AGENTS

    def _switch_agent(self, new: str, press: bool = True, persist: bool = True) -> None:
        """Select an agent and show its models. The model is the one used last with it (Claude Code: maude-model),
        else the current one if the new agent lists it too, else its default. persist=False (the restriction
        moved us) writes nothing, so the user's own choice comes back when the restriction is lifted."""
        self._tool = new
        if persist:
            save_tool(new)
        self._model = resolve_model(new, self._model, recall_model(new), cfg=self._cfg)
        if press:
            self._press("#agent-select", AGENTS, new)
        self._fill_models()

    def apply_agent_restrictions(self) -> None:
        """Public: re-read ~/.maude/settings.json (native_only_approved) and refresh the Agent column. Outside the
        sandbox with native_only_approved on, every agent except Kiro CLI and GitHub Copilot CLI is greyed out
        (disabled, not hidden); if the selected agent is one of them the selection moves to Kiro CLI for this
        session (the saved choice is not overwritten). Turning it off enables everything and goes back to the
        saved agent. Inside the sandbox it changes nothing. The Settings screen calls this after save_settings()."""
        self._settings = load_settings()
        for b in self.query_one("#agent-select", RadioSet).query(RadioButton):
            b.disabled = not agent_allowed(str(b.label), self._settings)
        saved = read_tool()
        want = restrict_agent(saved, self._settings)  # the user's own choice, unless forbidden now
        self._forced = want != saved
        if want != self._tool:
            self._switch_agent(want, persist=False)
            if self._forced:
                self.notify(f"Only {' and '.join(NATIVE_APPROVED_AGENTS)} may run outside the sandbox; using {want}")

    @on(RadioSet.Changed, "#effort-select")
    def effort_changed(self, event: RadioSet.Changed) -> None:
        label = str(event.pressed.label)
        new = "" if label == "auto" else label
        if new == self._effort:
            return
        self._effort = new
        save_effort(new)
        self.query_one("#projects-table", DataTable).focus()

    @on(RadioSet.Changed, "#agent-select")
    def agent_changed(self, event: RadioSet.Changed) -> None:
        new = str(event.pressed.label)
        if new == self._tool:
            return
        if not agent_allowed(new, self._settings):  # greyed out: cannot normally be clicked, never accept it
            self._press("#agent-select", AGENTS, self._tool)
            return
        self._forced = False
        self._switch_agent(new, press=False)
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
            subprocess.run([git_exe() if IS_MAC else "git", "-C", str(path), "init", "--quiet"], check=False)
        except FileNotFoundError:  # Git for Windows / Command Line Tools missing
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
