#!/usr/bin/env python3
"""maude.exe: Windows launcher for Maude Light. Stdlib only.

It copies itself to %USERPROFILE%\\.local\\bin, adds the "Open in Maude"
Explorer entries (HKCU, no admin) and starts the Maude WSL sandbox.
With no argument it runs the Textual TUI (maude.py, Windows mode) in the
current console. See docs/windows-exe.md.
"""
import time
_T0 = time.perf_counter()  # first line of Maude code that runs (after the onefile unpack)
import ntpath
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PureWindowsPath as W

VERSION = "dev"  # CI replaces this line with the release tag
DISTRO = "Maude"
KEYS = (r"Software\Classes\Directory\shell\OpenInMaude",
        r"Software\Classes\Directory\Background\shell\OpenInMaude")
# Claude Code in the sandbox: continue the last session, else start a new one.
# $1 = model override (may be empty); else the model in ~/.maude/maude-model ("<model>[,<effort>]",
# old ~/.maude-model as fallback); else opus. The effort (low|medium|high|xhigh|max) becomes --effort.
OPEN_SH = ('v="$(cat ~/.maude/maude-model 2>/dev/null || cat ~/.maude-model 2>/dev/null)"; '
           'v="${v//[[:space:]]/}"; '
           'm="${1:-${v%%,*}}"; e=""; [[ "$v" == *,* ]] && e="${v#*,}"; '
           '[[ "$m" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || m=opus; '
           'case "$e" in low|medium|high|xhigh|max) x=(--effort "$e");; *) x=();; esac; '
           'claude "$m" "${x[@]}" --continue || claude "$m" "${x[@]}"')
INSTALL_HINT = "Install Claude Code for Windows: irm https://claude.ai/install.ps1 | iex   (run in PowerShell)"


def install_target(userprofile: str) -> str:
    return str(W(userprofile) / ".local" / "bin" / "maude.exe")


def context_cmd(exe: str) -> str:
    return f'"{exe}" "%V"'


def onedrive_candidates(env, listdir=os.listdir, isdir=os.path.isdir) -> list[str]:
    """OneDrive\\Maude candidates in the order of Find-OneDriveMaudeFolder (setup-wsl-maude.ps1)."""
    c = []
    if env.get("OneDriveCommercial"):
        c.append(str(W(env["OneDriveCommercial"]) / "Maude"))
    if env.get("OneDriveConsumer"):
        c.append(str(W(env["OneDriveConsumer"]) / "Maude"))
    if env.get("OneDrive") and not env.get("OneDriveCommercial") and not env.get("OneDriveConsumer"):
        c.append(str(W(env["OneDrive"]) / "Maude"))
    up = env.get("USERPROFILE", "")
    try:
        biz = next((d for d in sorted(listdir(up)) if d.lower().startswith("onedrive - ")), None)
    except OSError:
        biz = None
    for base in ([str(W(up) / biz)] if biz else []) + ([str(W(up) / "OneDrive")] if isdir(str(W(up) / "OneDrive")) else []):
        if str(W(base) / "Maude") not in c:
            c.append(str(W(base) / "Maude"))
    return c


def share_root(localappdata: str, od_candidates=(), isdir=os.path.isdir) -> str:
    """Same default rule as setup: local share if it has Projects, else the first OneDrive one
    that has Projects, else local. (-OneDrive/-NoOneDrive forced choices are not visible here.)"""
    local = str(W(localappdata) / "Maude" / "Data" / "Maude")
    if isdir(str(W(local) / "Projects")):
        return local
    return next((c for c in od_candidates if isdir(str(W(c) / "Projects"))), local)


def parse_fstab_share(text: str) -> str | None:
    """Windows source of the drvfs line mounted at /home/<user>/Maude (root-bootstrap.sh writes it)."""
    for line in text.splitlines():
        f = line.split()
        if (len(f) >= 3 and not f[0].startswith("#") and f[2] == "drvfs"
                and re.fullmatch(r"/home/[^/]+/Maude", f[1]) and re.match(r"[A-Za-z]:\\", f[0])):
            return f[0].replace("\\040", " ")  # fstab escapes spaces as \040
    return None


def fstab_share() -> str | None:
    try:
        r = subprocess.run(["wsl.exe", "-d", DISTRO, "-e", "cat", "/etc/fstab"],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_fstab_share(r.stdout.decode("utf-8", "replace")) if r.returncode == 0 else None


REG_MAUDE = r"Software\Maude"  # HKCU; value "Share" caches the share root (REG_SZ)


def read_share() -> str | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_MAUDE) as h:
            v, t = winreg.QueryValueEx(h, "Share")
        return v if t == winreg.REG_SZ and v else None
    except (ImportError, OSError):
        return None


def store_share(share: str) -> None:
    """Non-fatal: a failed write only means the next start looks it up again."""
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_MAUDE) as h:
            winreg.SetValueEx(h, "Share", 0, winreg.REG_SZ, share)
    except (ImportError, OSError):
        pass


def pick_share(cached, isdir, lookup, heuristic, store, say=print) -> str:
    """Share root: a cached value that still has Projects (no WSL call); else the fstab lookup
    (cached when it works); else the heuristic, which is never cached because it may be wrong."""
    if cached and isdir(str(W(cached) / "Projects")):
        return cached
    say("Maude: locating the Maude share (WSL)...")
    found = lookup()
    if found:
        store(found)
        return found
    return heuristic()


def _say_clearing(msg) -> None:
    clear_banner()  # a line is about to be printed under the banner: the banner can no longer be erased in place
    print(msg)


def resolve_share(env=os.environ) -> str:
    return pick_share(read_share(), os.path.isdir, fstab_share,
                      lambda: share_root(env["LOCALAPPDATA"], onedrive_candidates(env)), store_share, _say_clearing)


def valid_model(m: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", m))


def wsl_dir(folder: str, share: str) -> str | None:
    """Return the ~/Maude/... path of a Windows folder, or None if outside the share."""
    try:
        rel = W(folder).relative_to(W(share))
    except ValueError:
        return None
    return "~/Maude" + "".join("/" + p for p in rel.parts)


def folder_mode(folder: str, share: str) -> str:
    """"sandbox" if folder is the share or inside it, else "windows". Pass realpath()ed paths."""
    f = ntpath.normcase(ntpath.normpath(folder))
    r = ntpath.normcase(ntpath.normpath(share))
    return "sandbox" if f == r or f.startswith(r.rstrip("\\") + "\\") else "windows"


def decide(folder: str, share: str, flag: str | None) -> str:
    """Return "sandbox", "windows" (explicit --windows, launch Claude directly), "tui" (outside
    the share without a flag: the Windows TUI with the folder preselected and a warning) or "refuse".
    flag is "windows", "sandbox" or None."""
    if flag == "windows":
        return "windows"
    if folder_mode(folder, share) == "sandbox":
        return "sandbox"
    if flag == "sandbox":
        return "refuse"
    return "tui"


def sandbox_cmd(d: str, model: str) -> list[str]:
    # -e runs argv without a shell (so model stays $1); MAUDE_WELCOMED=1 stops the welcome TUI.
    return ["wsl.exe", "-d", DISTRO, "--cd", d, "-e", "/usr/bin/env", "MAUDE_WELCOMED=1",
            "bash", "-lc", OPEN_SH, "maude", model]


EFFORTS = ("low", "medium", "high", "xhigh", "max")  # Claude Code --effort; "" = its own default


def windows_cmd(claude: str, model: str, cont: bool, effort: str = "") -> list[str]:
    # No bypass or yolo flag here: default permission mode only.
    return ([claude, "--model", model] + (["--effort", effort] if effort in EFFORTS else [])
            + (["--continue"] if cont else []))


BUN_CMD = "irm bun.sh/install.ps1 | iex"
KIRO_BASE = "https://prod.download.cli.kiro.dev/stable"
KIRO_MANIFEST = KIRO_BASE + "/latest/manifest.json"
# Tool label -> (binary, install command shown to the user). All are per-user installs without
# admin rights or installer windows. OpenCode and Crush come from bun (--trust lets their
# postinstall scripts run). Kiro CLI is not a PowerShell command: install_kiro() does it in Python
# (see NATIVE_INSTALL), because the vendor's install.ps1 runs a plain msiexec that can go per-machine.
TOOL_INSTALL = {
    "Claude Code": ("claude", "irm https://claude.ai/install.ps1 | iex"),
    "Kiro CLI": ("kiro-cli", "download the Kiro CLI MSI from prod.download.cli.kiro.dev, check its "
                             "SHA-256 and install it for this user (msiexec /qn, no admin)"),
    "OpenCode": ("opencode", "bun install -g --trust opencode-ai"),
    "Crush": ("crush", "bun install -g --trust @charmland/crush"),
    # Codex and Grok: the vendors' own per-user PowerShell installers (no admin). Codex's script checks the release SHA-256; the x.ai script does not checksum grok.exe (HTTPS only).
    "Codex": ("codex", "$env:CODEX_NON_INTERACTIVE='true'; irm https://chatgpt.com/codex/install.ps1 | iex"),
    "Grok": ("grok", "irm https://x.ai/cli/install.ps1 | iex"),
    # Antigravity and Copilot CLI: install_antigravity()/install_copilot() do it in Python (see NATIVE_INSTALL).
    "Antigravity": ("agy", "download agy.exe from Google's Antigravity release manifest (storage.googleapis.com), "
                           "check its SHA-512 and put it in %LOCALAPPDATA%\\agy\\bin (this user only, no admin)"),
    "Copilot CLI": ("copilot", "download copilot-win32-<arch>.zip from github.com/github/copilot-cli, check its "
                               "SHA-256 and unpack it to %LOCALAPPDATA%\\Programs\\Copilot CLI (this user only, no admin)"),
}
# Agent key -> the command (binary) each agent installs, for the TUI picker to launch. Same table in
# maude.py (AGENT_BINARIES); tests/test-maude-exe.sh checks the two stay equal.
AGENT_BINARIES = {"codex": "codex", "antigravity": "agy", "grok": "grok", "copilot": "copilot"}


def ps_cmd(cmd: str) -> list[str]:
    """argv that runs a fixed PowerShell command; -NonInteractive makes a stray prompt fail, not hang."""
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd]


def _http_get(url: str, urlopen=urllib.request.urlopen, limit: int = 8 << 20, timeout: int = 60) -> bytes:
    """Small HTTPS GET (manifests, JSON, checksum files). ValueError when larger than limit."""
    req = urllib.request.Request(url, headers={"User-Agent": "maude.exe"})
    with urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} is larger than {limit} bytes")
    return data


def download_verified(url: str, dest: str, digest: str, urlopen=urllib.request.urlopen, timeout: int = 60) -> None:
    """Stream url to dest and check its digest: SHA-256 (64 hex digits) or, when the vendor publishes
    only that, SHA-512 (128 hex digits). On a mismatch dest is removed and ValueError raised."""
    h = hashlib.sha512() if len(digest) == 128 else hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "maude.exe"})
    with urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != digest.lower():
        with contextlib.suppress(OSError):
            os.remove(dest)
        raise ValueError(f"checksum mismatch for {url} (expected {digest.lower()}, got {h.hexdigest()})")


_SHA_RE = re.compile(r"[0-9a-fA-F]{64}")
_SHA512_RE = re.compile(r"[0-9a-fA-F]{128}")
_KIRO_PATH_RE = re.compile(r"(?!\.)[0-9A-Za-z._-]+/(?!\.)[0-9A-Za-z._-]+\.msi")


def kiro_package(manifest) -> tuple[str, str] | None:
    """(MSI url, sha256) of the Windows x86_64 installer from the Kiro manifest, else None.
    The url is KIRO_BASE plus the manifest's relative path, which must be a plain <ver>/<file>.msi."""
    pkgs = manifest.get("packages") if isinstance(manifest, dict) else None
    for p in pkgs if isinstance(pkgs, list) else []:
        if (isinstance(p, dict) and p.get("os") == "windows" and p.get("architecture") == "x86_64"
                and p.get("kind") == "msi"):
            dl, sha = p.get("download"), p.get("sha256")
            if (isinstance(dl, str) and _KIRO_PATH_RE.fullmatch(dl)
                    and isinstance(sha, str) and _SHA_RE.fullmatch(sha)):
                return f"{KIRO_BASE}/{dl}", sha.lower()
    return None


def msiexec_cmd(msi: str, log: str) -> list[str]:
    """Silent per-user MSI install (ALLUSERS=2 + MSIINSTALLPERUSER=1: per-user even for an admin) with a verbose log."""
    return ["msiexec", "/i", msi, "/qn", "/norestart", "/l*v", log, "ALLUSERS=2", "MSIINSTALLPERUSER=1"]


def install_kiro(say=print, run=None, urlopen=urllib.request.urlopen, tmp_root=None, log_dir=None) -> int:
    """Install Kiro CLI per user: fixed-URL manifest, SHA-256 checked MSI, silent msiexec (no UI, no UAC).
    Returns 0 on success; on failure prints why (msiexec code + verbose log path) and returns non-zero."""
    run = run or _run
    tmp = None
    try:
        try:
            pkg = kiro_package(json.loads(_http_get(KIRO_MANIFEST, urlopen)))
        except Exception as err:  # URLError, HTTPException, bad JSON, ...: never crash the TUI
            say(f"Could not read the Kiro CLI manifest: {err}")
            return 1
        if not pkg:
            say("The Kiro CLI manifest has no Windows x86_64 installer.")
            return 1
        url, sha = pkg
        log = os.path.join(log_dir or tempfile.gettempdir(), "maude-kiro-install.log")
        tmp = tempfile.mkdtemp(prefix="maude-kiro-", dir=tmp_root)
        msi = os.path.join(tmp, "kiro-cli.msi")
        say("Downloading Kiro CLI (about 350 MB)...")
        download_verified(url, msi, sha, urlopen)
        say("Installing Kiro CLI for this user...")
        rc = run(msiexec_cmd(msi, log))
    except KeyboardInterrupt:
        say("Kiro CLI install cancelled.")
        return 1
    except Exception as err:
        say(f"The Kiro CLI download or install failed: {err}")
        return 1
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    if rc in (0, 3010):  # 3010 = success, restart pending (not needed for a per-user install)
        return 0
    say(f"msiexec exited with code {rc}. Verbose log: {log}")
    return rc or 1


AGY_MANIFEST_BASE = "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/"  # as in antigravity.google/cli/install.ps1
_AGY_URL_RE = re.compile(r"https://storage\.googleapis\.com/antigravity-public/[0-9A-Za-z._/-]+\.exe")


def agy_dest(env=os.environ) -> str | None:
    """Per-user folder of agy.exe (the same one the vendor's install.ps1 uses)."""
    return str(W(env["LOCALAPPDATA"]) / "agy" / "bin") if env.get("LOCALAPPDATA") else None


def agy_package(manifest) -> tuple[str, str] | None:
    """(exe url, sha512) from the Antigravity manifest, else None. The url must be an https
    storage.googleapis.com/antigravity-public/... .exe and the digest 128 hex digits."""
    if not isinstance(manifest, dict):
        return None
    url, sha = manifest.get("url"), manifest.get("sha512")
    if (isinstance(url, str) and _AGY_URL_RE.fullmatch(url) and ".." not in url
            and isinstance(sha, str) and _SHA512_RE.fullmatch(sha)):
        return url, sha.lower()
    return None


def _sweep_stale(folder: str, prefix: str, suffix: str, max_age: int = 3600) -> None:
    """Delete leftover staging files (prefix*suffix) older than max_age seconds, e.g. from a killed install. Never raises."""
    try:
        now = time.time()
        for n in os.listdir(folder):
            p = os.path.join(folder, n)
            if n.startswith(prefix) and n.endswith(suffix) and os.path.isfile(p) and now - os.path.getmtime(p) > max_age:
                with contextlib.suppress(OSError):
                    os.remove(p)
    except OSError:
        pass


def install_antigravity(env=os.environ, say=print, urlopen=urllib.request.urlopen, dest=None) -> int:
    """Install the Antigravity CLI (agy.exe) per user: the vendor's manifest, SHA-512 checked download, no admin.
    The vendor script also runs `agy install` (shell setup); Maude skips that and manages PATH itself."""
    arch = {"amd64": "amd64", "arm64": "arm64"}.get(gh_arch(env) or "")
    dest = dest or agy_dest(env)
    if not arch or not dest:
        say("No Antigravity CLI build for this Windows.")
        return 1
    staged = None
    try:
        pkg = agy_package(json.loads(_http_get(f"{AGY_MANIFEST_BASE}windows_{arch}.json", urlopen)))
        if not pkg:
            say("The Antigravity manifest has no usable Windows download.")
            return 1
        os.makedirs(dest, exist_ok=True)
        fd, staged = tempfile.mkstemp(prefix=".agy-new-", suffix=".exe", dir=dest)  # same folder: the move is a rename
        os.close(fd)
        say("Downloading Antigravity CLI (about 240 MB)...")
        download_verified(pkg[0], staged, pkg[1], urlopen, timeout=300)
        os.replace(staged, os.path.join(dest, "agy.exe"))
        staged = None
        _sweep_stale(dest, ".agy-new-", ".exe")
        return 0
    except KeyboardInterrupt:
        say("Antigravity install cancelled.")
        return 1
    except Exception as err:  # URLError, HTTPException, bad JSON, locked file, ...: never crash the TUI
        say(f"The Antigravity download or install failed: {err}")
        return 1
    finally:
        if staged:
            with contextlib.suppress(OSError):
                os.remove(staged)


COPILOT_DL = "https://github.com/github/copilot-cli/releases/latest/download/"


def copilot_dest(env=os.environ) -> str | None:
    """Per-user folder of copilot.exe."""
    return str(W(env["LOCALAPPDATA"]) / "Programs" / "Copilot CLI") if env.get("LOCALAPPDATA") else None


def install_copilot(env=os.environ, say=print, urlopen=urllib.request.urlopen, tmp_root=None, dest=None) -> int:
    """Install GitHub Copilot CLI per user from the official release zip (SHA-256 from the release's
    SHA256SUMS.txt, no admin). Only copilot.exe is taken from the zip. The npm package would need Node.js."""
    arch = {"amd64": "x64", "arm64": "arm64"}.get(gh_arch(env) or "")
    dest = dest or copilot_dest(env)
    if not arch or not dest:
        say("No Copilot CLI release zip for this Windows.")
        return 1
    tmp = staged = None
    try:
        zname = f"copilot-win32-{arch}.zip"
        sha = checksum_for(_http_get(COPILOT_DL + "SHA256SUMS.txt", urlopen).decode("utf-8", "replace"), zname)
        if not sha:
            say(f"No SHA-256 for {zname} in the Copilot CLI release.")
            return 1
        tmp = tempfile.mkdtemp(prefix="maude-copilot-", dir=tmp_root)
        zpath = os.path.join(tmp, zname)
        say("Downloading Copilot CLI (about 70 MB)...")
        download_verified(COPILOT_DL + zname, zpath, sha, urlopen, timeout=300)
        with zipfile.ZipFile(zpath) as zf:
            m = next((i for i in zf.infolist() if i.filename == "copilot.exe"), None)
            if m is None or (m.external_attr >> 16) & 0o170000 == 0o120000:
                say("The Copilot CLI zip looks wrong (no plain copilot.exe); not installing it.")
                return 1
            os.makedirs(dest, exist_ok=True)
            fd, staged = tempfile.mkstemp(prefix=".copilot-new-", suffix=".exe", dir=dest)
            with os.fdopen(fd, "wb") as out, zf.open(m) as src:
                shutil.copyfileobj(src, out)
        os.replace(staged, os.path.join(dest, "copilot.exe"))
        staged = None
        return 0
    except KeyboardInterrupt:
        say("Copilot CLI install cancelled.")
        return 1
    except Exception as err:  # URLError, BadZipFile, locked file, ...: never crash the TUI
        say(f"The Copilot CLI download or install failed: {err}")
        return 1
    finally:
        if staged:
            with contextlib.suppress(OSError):
                os.remove(staged)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


# tool label -> Python installer taking say=, returning an exit code (called as native[label](say=say))
NATIVE_INSTALL = {"Kiro CLI": install_kiro, "Antigravity": install_antigravity, "Copilot CLI": install_copilot}


def user_dirs(env) -> list[str]:
    """Per-user install folders: claude (.local\\bin), bun and bun globals (.bun\\bin), and with LOCALAPPDATA
    the Antigravity (agy\\bin) and Copilot CLI folders, which Maude puts on the user PATH itself."""
    up = env["USERPROFILE"]
    return [str(W(up) / ".local" / "bin"), str(W(up) / ".bun" / "bin")] + [d for d in (
        (str(W(env["LOCALAPPDATA"]) / "agy" / "bin"), str(W(env["LOCALAPPDATA"]) / "Programs" / "Copilot CLI"))
        if env.get("LOCALAPPDATA") else ()) if d]


def tool_dirs(env) -> list[str]:
    """user_dirs plus the Kiro MSI folder (a system install that sets its own PATH) and the folders of the
    Codex and Grok installers (their scripts add themselves to the user PATH)."""
    extra = [str(W(env[v]) / sub) for v, sub in (("ProgramFiles", "Kiro-Cli"), ("LOCALAPPDATA", r"Programs\Kiro-Cli"),
                                                  ("LOCALAPPDATA", "Kiro-Cli")) if env.get(v)]
    extra += [str(W(env[v]) / sub) for v, sub in (("LOCALAPPDATA", r"Programs\OpenAI\Codex\bin"), ("USERPROFILE", r".grok\bin"))
              if env.get(v)]
    return user_dirs(env) + extra


def find_tool(binary: str, env=os.environ, which=shutil.which) -> str | None:
    """PATH first, then the installer folders (a fresh install is not on this process's PATH yet)."""
    # PATH entries written as %VAR% are not expanded by shutil.which, so try them expanded too
    pdirs = [ntpath.expandvars(e) for e in env.get("PATH", "").split(";") if "%" in e]
    return which(binary) or next((r for d in pdirs + tool_dirs(env) if (r := which(binary, path=d))), None)


def find_claude(userprofile: str) -> str | None:
    return find_tool("claude", {**os.environ, "USERPROFILE": userprofile})


def install_plan(tool: str, have_tool: bool, have_bun: bool) -> list[tuple[str, str]]:
    """(label, install command) steps needed before tool can start. Bun comes first if needed.
    The command is a PowerShell command, or for tools in NATIVE_INSTALL a description of the Python install."""
    if have_tool:
        return []
    cmd = TOOL_INSTALL[tool][1]
    return ([("Bun", BUN_CMD)] if cmd.startswith("bun ") and not have_bun else []) + [(tool, cmd)]


def path_prepend(path: str, dirs: list[str]) -> str:
    """PATH value with dirs in front (dirs already in it are not repeated)."""
    norm = lambda p: ntpath.normcase(ntpath.normpath(p.strip()))
    have = {norm(e) for e in path.split(";") if e.strip()}
    return ";".join([d for d in dirs if norm(d) not in have] + ([path] if path else []))


def refresh_path(env=os.environ) -> None:
    """After an install: add the new bin dirs to the user PATH (HKCU) and to this process's PATH."""
    for d in user_dirs(env):
        if os.path.isdir(d):
            ensure_user_path(d)
    env["PATH"] = path_prepend(env.get("PATH", ""), [d for d in tool_dirs(env) if os.path.isdir(d)])


def ensure_tool(tool: str, find, ask=lambda q: input(q), run=None, say=print, refresh=lambda: None,
                native=None) -> str | None:
    """Return the tool's path, offering to install it (and bun) when missing. None = not available
    (declined, install failed or still not found); the reason has been printed.
    native maps a label to a Python installer (default NATIVE_INSTALL); the rest run via PowerShell."""
    exe = find(TOOL_INSTALL[tool][0])
    if exe:
        return exe
    run = run or _run
    native = NATIVE_INSTALL if native is None else native
    for label, cmd in install_plan(tool, False, find("bun") is not None):
        say(f"{label} is not installed on Windows.\nInstall command: {cmd}")
        try:
            yes = ask(f"Install {label} now? [Y/n] ").strip().lower() in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            yes = False
        if not yes:
            return None
        try:
            rc = native[label](say=say) if label in native else run(ps_cmd(cmd))
        except KeyboardInterrupt:
            say(f"{label} install cancelled.")
            return None
        except Exception as err:  # backstop: a failed install never takes the TUI down
            say(f"The {label} install failed: {err}")
            return None
        if rc:
            say(f"The {label} install failed.")
            return None
        refresh()  # bun must be on PATH for the next step
    exe = find(TOOL_INSTALL[tool][0])
    if not exe:
        say(f"{tool} was installed but is not found yet. Open a new PowerShell window and try again.")
    return exe


GIT_LINK = "https://git-scm.com/download/win"
MINGIT_API = "https://api.github.com/repos/git-for-windows/git/releases/latest"
MINGIT_DL = "https://github.com/git-for-windows/git/releases/download/"
_git_done = False  # offer Git once per Maude process


def mingit_dest(env=os.environ) -> str | None:
    """Per-user install folder of MinGit (cmd\\git.exe inside). The whole tree must stay together:
    cmd\\git.exe is a small launcher that finds the real git relative to its own path."""
    return str(W(env["LOCALAPPDATA"]) / "Programs" / "MinGit") if env.get("LOCALAPPDATA") else None


def mingit_arch(env=os.environ) -> str | None:
    """Asset suffix of the MinGit zip for this Windows ("64-bit"/"arm64"), None if there is no such zip."""
    return {"amd64": "64-bit", "arm64": "arm64"}.get(gh_arch(env) or "")


def mingit_plan(have_git: bool, arch: str | None, dest: str | None):
    """None = nothing to do, "link" = cannot install here, else the one-line description of the install."""
    if have_git:
        return None
    if not arch or not dest:
        return "link"
    return (f"download the latest MinGit-<version>-{arch}.zip (about 40 MB) from github.com/git-for-windows/git, "
            f"check its SHA-256 and unpack it to {dest} (about 100 MB, this user only, no admin)")


def git_dirs(env=os.environ) -> list[str]:
    """Where git.exe may be: our per-user MinGit, then an existing Git for Windows (machine or per-user)."""
    d = mingit_dest(env)
    return ([str(W(d) / "cmd")] if d else []) + [
        str(W(env[k]) / sub) for k, sub in (("ProgramFiles", "Git/cmd"), ("LOCALAPPDATA", "Programs/Git/cmd"))
        if env.get(k)]


def find_git(env=os.environ, which=shutil.which) -> str | None:
    return which("git") or next((r for d in git_dirs(env) if (r := which("git", path=d))), None)


def _mingit_body_shas(body, name: str) -> set[str]:
    """All distinct sha256 values listed for name in the release notes table ("<name> | <hex>" rows)."""
    found = set()
    for line in body.splitlines() if isinstance(body, str) else []:
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) == 2 and cells[0] == name and _SHA_RE.fullmatch(cells[1]):
            found.add(cells[1].lower())
    return found


def mingit_body_sha(body, name: str) -> str | None:
    """sha256 of name from the release notes table (Git for Windows publishes its hashes there, not as
    a file). None if there is no row, or if rows for the same name disagree."""
    found = _mingit_body_shas(body, name)
    return next(iter(found)) if len(found) == 1 else None


def _use_found(env, found: str, own: str | None, add_path) -> None:
    """A tool was found outside PATH (or on it): put its folder on this process's PATH, and, if it is our
    own per-user folder (own), also on the user PATH, so later shells and the AI tools see it too."""
    d = ntpath.dirname(found.replace("/", "\\"))
    if not d or not ntpath.isabs(d):  # never put "." or a relative folder on PATH
        return
    env["PATH"] = path_prepend(env.get("PATH", ""), [d])
    norm = lambda p: ntpath.normcase(ntpath.normpath(p))
    if own and norm(d) == norm(own):
        add_path(d)


def mingit_release(rel, arch: str):
    """(tag, zip name, sha256) of the plain MinGit zip for arch from a releases/latest JSON, else None.
    Exactly one asset must match MinGit-<version>-<arch>.zip (the -busybox variants never match). The hash
    is the asset's API digest and/or the release-notes table row; if both exist they must agree.
    Names only: the download URL is rebuilt from MINGIT_DL and the tag, which must look like vX.Y.Z.windows.N."""
    if not isinstance(rel, dict):
        return None
    tag = rel.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+\.windows\.\d+", tag):
        return None
    pat = re.compile(rf"MinGit-\d+(?:\.\d+)+-{re.escape(arch)}\.zip")
    assets = rel.get("assets")
    hits = [a for a in (assets if isinstance(assets, list) else [])
            if isinstance(a, dict) and isinstance(a.get("name"), str) and pat.fullmatch(a["name"])]
    if len(hits) != 1:
        return None
    name, dig = hits[0]["name"], hits[0].get("digest")
    sha_api = (dig[7:].lower() if isinstance(dig, str) and dig.startswith("sha256:") and _SHA_RE.fullmatch(dig[7:])
               else None)
    bodies = _mingit_body_shas(rel.get("body"), name)
    if len(bodies) > 1 or (sha_api and bodies and bodies != {sha_api}):  # ambiguous or disagreeing: refuse
        return None
    sha = sha_api or next(iter(bodies), None)
    return (tag, name, sha) if sha else None


# top-level names of the current 64-bit (ucrt64) and arm64 (clangarm64) MinGit zips; Maude never installed
# an older layout (mingw64), so that is not listed
_MINGIT_TOP = {"LICENSE.txt", "cmd", "etc", "usr", "ucrt64", "clangarm64"}


def mingit_dir_replaceable(dest: str) -> bool:
    """True if dest may be swapped out: missing, empty, or a MinGit tree (an old install or a partial one):
    it has cmd or LICENSE.txt and nothing but what a MinGit zip unpacks to. Anything else is user data."""
    if not os.path.lexists(dest):
        return True
    try:
        if not os.path.isdir(dest) or os.path.islink(dest):
            return False
        names = os.listdir(dest)
        return not names or ({"cmd", "LICENSE.txt"} & set(names) != set() and all(
            n in _MINGIT_TOP and not os.path.islink(os.path.join(dest, n)) for n in names))
    except OSError:
        return False


def install_mingit(env=os.environ, say=print, urlopen=urllib.request.urlopen, tmp_root=None, dest=None) -> int:
    """Install MinGit for this user from the official Git for Windows release zip (SHA-256 checked, no admin).
    0 = installed to mingit_dest(env) (cmd\\git.exe); otherwise the reason has been printed."""
    arch, dest = mingit_arch(env), dest or mingit_dest(env)
    if not arch or not dest:
        say("No MinGit release zip for this Windows.")
        return 1
    tmp = stage = aside = None
    # if the old folder could not be put back (or Ctrl+C landed between the renames), tell the user where it is
    kept = lambda a: f"\nYour previous MinGit folder is kept at {a}" if a and os.path.isdir(a) else ""
    try:
        if not mingit_dir_replaceable(dest):
            say(f"{dest} already exists and holds other files; not touching it. Move it away and try again.")
            return 1
        tmp = tempfile.mkdtemp(prefix="maude-git-", dir=tmp_root)
        rel = mingit_release(json.loads(_http_get(MINGIT_API, urlopen)), arch)
        if not rel:
            say("The latest Git for Windows release has no MinGit zip with a SHA-256 for this PC.")
            return 1
        tag, zname, sha = rel
        say(f"Downloading MinGit {tag} (about 40 MB)...")
        zpath = os.path.join(tmp, zname)
        download_verified(f"{MINGIT_DL}{tag}/{zname}", zpath, sha, urlopen, timeout=120)
        parent = os.path.dirname(dest)
        os.makedirs(parent, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".git-new-", dir=parent)  # same drive as dest: the move is a rename
        with zipfile.ZipFile(zpath) as zf:
            members = zf.infolist()
            bad = unsafe_zip_names(m.filename for m in members) + [
                m.filename for m in members if (m.external_attr >> 16) & 0o170000 == 0o120000]
            if bad or "cmd/git.exe" not in {m.filename for m in members}:
                say(f"The MinGit zip looks wrong ({bad[0] if bad else 'no cmd/git.exe'}); not installing it.")
                return 1
            zf.extractall(stage)
        if os.path.lexists(dest):  # an old or partial MinGit folder (re-checked): move it aside, swap, delete it
            if not mingit_dir_replaceable(dest):
                say(f"{dest} changed while downloading; not touching it.")
                return 1
            aside = f"{dest}.old-{os.getpid()}-{os.urandom(3).hex()}"
            os.replace(dest, aside)
        try:
            os.replace(stage, dest)
        except BaseException:
            if aside:
                os.replace(aside, dest)  # put the old folder back
                aside = None
            raise
        if aside:
            shutil.rmtree(aside, ignore_errors=True)
            aside = None
        return 0
    except KeyboardInterrupt:
        say("Git install cancelled." + kept(aside))
        return 1
    except Exception as err:  # URLError, HTTPException, BadZipFile, bad JSON, ...: never crash the TUI
        say(f"The MinGit download or install failed: {err}" + kept(aside))
        return 1
    finally:
        for d in (tmp, stage):
            if d:
                shutil.rmtree(d, ignore_errors=True)  # stage is gone already after a successful move


def offer_git(env=os.environ, find=find_git, install=install_mingit, add_path=lambda d: ensure_user_path(d),
              ask=lambda q: input(q), say=print) -> None:
    """Offer MinGit (per-user zip install, no winget, no admin), once per process.
    Never blocks opening a project. On success its cmd folder goes on the user PATH and this process's PATH."""
    global _git_done
    if _git_done:
        return
    _git_done = True
    found = find(env)
    if found:  # may be found only through git_dirs: make sure this process (and our own folder's HKCU PATH) has it
        own = mingit_dest(env)
        _use_found(env, found, str(W(own) / "cmd") if own else None, add_path)
        return
    plan = mingit_plan(False, mingit_arch(env), mingit_dest(env))
    if plan == "link":
        say(f"Git is not installed. Get it from {GIT_LINK} (optional).")
        return
    say(f"Git is not installed (optional; the AI tools work better with it).\nInstall plan: {plan}")
    try:
        yes = ask("Install Git now? [Y/n] ").strip().lower() in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        yes = False
    if not yes:
        return
    try:
        rc = install(env, say)
    except KeyboardInterrupt:
        say("Git install cancelled.")
        return
    except Exception as err:  # backstop: an offer never takes the TUI down
        say(f"The Git install failed: {err}")
        return
    if rc:
        say(f"The Git install failed. Get Git from {GIT_LINK} (optional).")
        return
    d = str(W(mingit_dest(env)) / "cmd")
    add_path(d)  # persisted for new shells (HKCU PATH)
    env["PATH"] = path_prepend(env.get("PATH", ""), [d])


def reset_offers() -> None:
    """Forget that Git / the GitHub CLI were already offered in this process (an explicit user action,
    such as opening Setup Git(hub), may ask again)."""
    global _git_done, _gh_done
    _git_done = _gh_done = False


GH_LINK = "https://cli.github.com"
GH_API = "https://api.github.com/repos/cli/cli/releases/latest"
GH_DL = "https://github.com/cli/cli/releases/download/"
_gh_done = False  # offer the GitHub CLI once per Maude process


def gh_dest(env=os.environ) -> str | None:
    """Per-user install folder of the GitHub CLI release zip (bin\\gh.exe inside)."""
    return str(W(env["LOCALAPPDATA"]) / "Programs" / "GitHub CLI") if env.get("LOCALAPPDATA") else None


def gh_arch(env=os.environ) -> str | None:
    """Release asset arch for this Windows ("amd64"/"arm64"), None if there is no such zip."""
    a = (env.get("PROCESSOR_ARCHITEW6432") or env.get("PROCESSOR_ARCHITECTURE") or "").upper()
    return {"AMD64": "amd64", "ARM64": "arm64"}.get(a)


def gh_plan(have_gh: bool, arch: str | None, dest: str | None):
    """None = nothing to do, "link" = cannot install here, else the one-line description of the install."""
    if have_gh:
        return None
    if not arch or not dest:
        return "link"
    return (f"download the latest gh_<version>_windows_{arch}.zip from github.com/cli/cli, check its "
            f"SHA-256 and unpack it to {dest} (this user only, no admin)")


def gh_dirs(env=os.environ) -> list[str]:
    """Where gh.exe may be: our per-user install (bin; the folder itself too, for other per-user
    layouts) and an existing machine install."""
    d = gh_dest(env)
    return ([str(W(d) / "bin"), d] if d else []) + (
        [str(W(env["ProgramFiles"]) / "GitHub CLI")] if env.get("ProgramFiles") else [])


def find_gh(env=os.environ, which=shutil.which) -> str | None:
    return which("gh") or next((r for d in gh_dirs(env) if (r := which("gh", path=d))), None)


def gh_release(rel, arch: str):
    """(tag, zip name, checksums name) for the Windows zip of a releases/latest JSON, else None.
    The tag must look like vX.Y.Z and both assets must be listed (names only: URLs are rebuilt from GH_DL)."""
    tag = rel.get("tag_name") if isinstance(rel, dict) else None
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        return None
    zname, sname = f"gh_{tag[1:]}_windows_{arch}.zip", f"gh_{tag[1:]}_checksums.txt"
    assets = rel.get("assets")
    names = {a["name"] for a in (assets if isinstance(assets, list) else [])
             if isinstance(a, dict) and isinstance(a.get("name"), str)}
    return (tag, zname, sname) if zname in names and sname in names else None


def checksum_for(text: str, name: str) -> str | None:
    """sha256 of name from a sha256sum-style file ("<hex>  <name>" lines), else None."""
    for line in text.splitlines():
        f = line.split()
        if len(f) == 2 and f[1].lstrip("*") == name and _SHA_RE.fullmatch(f[0]):
            return f[0].lower()
    return None


def unsafe_zip_names(names) -> list[str]:
    """Zip member names that could escape the target folder: absolute, drive/stream (":"), or with a ".." part."""
    return [n for n in names if n.startswith(("/", "\\")) or ":" in n or ".." in W(n).parts]


def gh_dir_replaceable(dest: str) -> bool:
    """True if dest may be swapped out: missing, or holding nothing but what a gh zip unpacks to
    (LICENSE, bin, bin/gh.exe), i.e. an old install or a partial one. Anything else is user data."""
    if not os.path.lexists(dest):
        return True
    try:
        if not os.path.isdir(dest) or os.path.islink(dest):
            return False
        top = set(os.listdir(dest))
        if not top <= {"LICENSE", "bin"}:
            return False
        plain = lambda f: os.path.isfile(f) and not os.path.islink(f)  # a regular file, not a dir or link
        if "LICENSE" in top and not plain(os.path.join(dest, "LICENSE")):
            return False
        b = os.path.join(dest, "bin")
        if "bin" not in top:
            return True
        if not os.path.isdir(b) or os.path.islink(b):
            return False
        inner = set(os.listdir(b))
        return inner <= {"gh.exe"} and ("gh.exe" not in inner or plain(os.path.join(b, "gh.exe")))
    except OSError:
        return False


def install_gh(env=os.environ, say=print, urlopen=urllib.request.urlopen, tmp_root=None, dest=None) -> int:
    """Install the GitHub CLI for this user from the official release zip (SHA-256 checked, no admin).
    0 = installed to gh_dest(env); otherwise the reason has been printed."""
    arch, dest = gh_arch(env), dest or gh_dest(env)
    if not arch or not dest:
        say("No GitHub CLI release zip for this Windows.")
        return 1
    tmp = stage = aside = None
    # if the old folder could not be put back (or Ctrl+C landed between the renames), tell the user where it is
    kept = lambda a: f"\nYour previous GitHub CLI folder is kept at {a}" if a and os.path.isdir(a) else ""
    try:
        if not gh_dir_replaceable(dest):
            say(f"{dest} already exists and holds other files; not touching it. Move it away and try again.")
            return 1
        tmp = tempfile.mkdtemp(prefix="maude-gh-", dir=tmp_root)
        rel = gh_release(json.loads(_http_get(GH_API, urlopen)), arch)
        if not rel:
            say("The latest GitHub CLI release has no Windows zip for this PC.")
            return 1
        tag, zname, sname = rel
        sha = checksum_for(_http_get(f"{GH_DL}{tag}/{sname}", urlopen).decode("utf-8", "replace"), zname)
        if not sha:
            say(f"No SHA-256 for {zname} in {sname}.")
            return 1
        say(f"Downloading GitHub CLI {tag}...")
        zpath = os.path.join(tmp, zname)
        download_verified(f"{GH_DL}{tag}/{zname}", zpath, sha, urlopen)
        parent = os.path.dirname(dest)
        os.makedirs(parent, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".gh-new-", dir=parent)  # same drive as dest: the move is a rename
        with zipfile.ZipFile(zpath) as zf:
            members = zf.infolist()
            bad = unsafe_zip_names(m.filename for m in members) + [
                m.filename for m in members if (m.external_attr >> 16) & 0o170000 == 0o120000]
            if bad or "bin/gh.exe" not in {m.filename for m in members}:
                say(f"The GitHub CLI zip looks wrong ({bad[0] if bad else 'no bin/gh.exe'}); not installing it.")
                return 1
            zf.extractall(stage)
        if os.path.lexists(dest):  # an old or partial gh folder (re-checked): move it aside, swap, delete it
            if not gh_dir_replaceable(dest):
                say(f"{dest} changed while downloading; not touching it.")
                return 1
            aside = f"{dest}.old-{os.getpid()}-{os.urandom(3).hex()}"
            os.replace(dest, aside)
        try:
            os.replace(stage, dest)
        except BaseException:
            if aside:
                os.replace(aside, dest)  # put the old folder back
                aside = None
            raise
        if aside:
            shutil.rmtree(aside, ignore_errors=True)
            aside = None
        return 0
    except KeyboardInterrupt:
        say("GitHub CLI install cancelled." + kept(aside))
        return 1
    except Exception as err:  # URLError, HTTPException, BadZipFile, bad JSON, ...: never crash the TUI
        say(f"The GitHub CLI download or install failed: {err}" + kept(aside))
        return 1
    finally:
        for d in (tmp, stage):
            if d:
                shutil.rmtree(d, ignore_errors=True)  # stage is gone already after a successful move


def offer_gh(env=os.environ, find=find_gh, install=install_gh, add_path=lambda d: ensure_user_path(d),
             ask=lambda q: input(q), say=print) -> None:
    """Offer the GitHub CLI (per-user zip install, no winget, no admin), once per process.
    Never blocks opening a project."""
    global _gh_done
    if _gh_done:
        return
    _gh_done = True
    found = find(env)
    if found:  # may be found only through gh_dirs: make sure this process (and our own bin's HKCU PATH) has it
        own = gh_dest(env)
        _use_found(env, found, str(W(own) / "bin") if own else None, add_path)
        return
    plan = gh_plan(False, gh_arch(env), gh_dest(env))
    if plan == "link":
        say(f"GitHub CLI is not installed. Get it from {GH_LINK} (optional).")
        return
    say(f"GitHub CLI is not installed (optional; for GitHub from the AI tools).\nInstall plan: {plan}")
    try:
        yes = ask("Install GitHub CLI now? [Y/n] ").strip().lower() in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        yes = False
    if not yes:
        return
    try:
        rc = install(env, say)
    except KeyboardInterrupt:
        say("GitHub CLI install cancelled.")
        return
    except Exception as err:  # backstop: an offer never takes the TUI down
        say(f"The GitHub CLI install failed: {err}")
        return
    if rc:
        say("The GitHub CLI install failed.")
        return
    d = str(W(gh_dest(env)) / "bin")
    add_path(d)  # persisted for new shells (HKCU PATH)
    env["PATH"] = path_prepend(env.get("PATH", ""), [d])


def maude_dir(home: str | None = None) -> Path:
    """~/.maude (home defaults to Path.home()), created with mode 0700 if missing. Same helper as
    maude.py's maude_dir(); the exe cannot import maude.py (textual) on the --windows path."""
    d = Path(home) / ".maude" if home else Path.home() / ".maude"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)  # the mode has no effect on Windows
    return d


def _decode_state(raw: bytes) -> str:
    """Tolerant text decode for state files: a UTF-16 BOM (PowerShell 5.1 `>`) is honoured, a UTF-8
    BOM is dropped, and undecodable bytes are replaced (the value then simply fails validation)."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def read_state(userprofile: str, name: str, legacy: str) -> str:
    """Contents of <home>/.maude/<name>, or "". A legacy <home>/<legacy> is moved there first
    (value copied into the new file, old file deleted) when the new file is missing. Never raises."""
    new, old = Path(userprofile) / ".maude" / name, Path(userprofile) / legacy
    try:
        if not new.exists() and old.exists():
            val = _decode_state(old.read_bytes())
            maude_dir(userprofile)
            new.write_text(val, encoding="utf-8")
            old.unlink()
    except (OSError, ValueError):
        pass
    try:
        return _decode_state(new.read_bytes())
    except (OSError, ValueError):
        return ""


def parse_model_pref(raw: str) -> tuple[str, str]:
    """"<model>[,<effort>]" -> (model, effort); a bad model gives opus, a bad effort gives ""."""
    m, _, e = raw.strip().partition(",")
    m, e = m.strip(), e.strip().lower()
    return (m if valid_model(m) else "opus", e if e in EFFORTS else "")


def windows_model_effort(userprofile: str) -> tuple[str, str]:
    return parse_model_pref(read_state(userprofile, "maude-model", ".maude-model"))


def windows_model(userprofile: str) -> str:
    return windows_model_effort(userprofile)[0]


REG_PROJECTS = r"Software\Maude\Projects"  # HKCU; value name = project_key(folder), data = folder


def project_key(folder: str) -> str:
    return ntpath.normcase(ntpath.normpath(folder))


def read_projects() -> list[str]:
    """Folders registered by "Open in Maude" (outside the share). Empty off Windows or on error."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PROJECTS) as h:
            n = winreg.QueryInfoKey(h)[1]
            return [v for _, v, t in (winreg.EnumValue(h, i) for i in range(n)) if t == winreg.REG_SZ and v]
    except (ImportError, OSError):
        return []


def add_project(folder: str) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PROJECTS) as h:
        winreg.SetValueEx(h, project_key(folder), 0, winreg.REG_SZ, folder)


def remove_project(folder: str) -> None:
    """Remove the registry value only. The folder itself is never touched."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PROJECTS, 0, winreg.KEY_SET_VALUE) as h:
            winreg.DeleteValue(h, project_key(folder))
    except FileNotFoundError:
        pass  # other OSError (e.g. access denied) goes to the caller


def content_mtime(folder: str) -> float:
    """Newest mtime of the folder and its direct children (no tree walk; .git is skipped). 0.0 if unreadable."""
    try:
        m = os.stat(folder).st_mtime
        with os.scandir(folder) as it:
            for e in it:
                if e.name != ".git":
                    with contextlib.suppress(OSError):
                        m = max(m, e.stat().st_mtime)
        return m
    except OSError:
        return 0.0


def merge_projects(outside: list[dict], share: list[dict]) -> list[dict]:
    """Outside-sandbox projects first, then share projects; each group newest "mtime" first.
    Labels get "name (parent)" when a basename occurs twice. Dicts need "path" (str-like) and "mtime"."""
    rows = ([dict(d, outside=True) for d in sorted(outside, key=lambda d: -d["mtime"])]
            + [dict(d, outside=False) for d in sorted(share, key=lambda d: -d["mtime"])])
    names = [W(str(d["path"])).name or str(d["path"]) for d in rows]
    for d, n in zip(rows, names):
        par = W(str(d["path"])).parent.name
        d["label"] = f"{n} ({par})" if names.count(n) > 1 and par else n
    return rows


def features(github: bool, outside: bool) -> str:
    return ", ".join(f for f, on in (("GitHub", github), ("No Sandbox", outside)) if on)


BANNER = ("Maude: running OUTSIDE the sandbox in {}. "
          "Maude's AI agents have your full Windows user rights here.")
BANNER_NO_SANDBOX = ("Maude: running on Windows in {}. "
                     "Maude's AI agents have your full Windows user rights here.")
REG_LXSS = r"Software\Microsoft\Windows\CurrentVersion\Lxss"  # HKCU; one subkey per WSL distro


def lxss_names() -> list[str] | None:
    """DistributionName of every registered WSL distro (registry only, no wsl.exe). [] if WSL has
    never been used (no Lxss key). None if it cannot be read (unknown)."""
    try:
        import winreg
        names = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_LXSS) as h:
            for i in range(winreg.QueryInfoKey(h)[0]):
                try:
                    with winreg.OpenKey(h, winreg.EnumKey(h, i)) as k:
                        names.append(str(winreg.QueryValueEx(k, "DistributionName")[0]))
                except FileNotFoundError:  # a subkey without DistributionName: not a distro, skip
                    pass
        return names
    except FileNotFoundError:
        return []
    except (ImportError, OSError):  # e.g. access denied: unknown, not "no sandbox"
        return None


def sandbox_present(names: list[str] | None) -> bool:
    """Is the Maude distro registered? Unknown (None) counts as yes: the safe default shows the caption."""
    return names is None or any(n.lower() == DISTRO.lower() for n in names)


def has_sandbox() -> bool:
    return sandbox_present(lxss_names())


def banner_text(folder: str, sandbox: bool = True) -> str:
    return (BANNER if sandbox else BANNER_NO_SANDBOX).format(folder)


def projects_dir(env=os.environ) -> str:
    return str(W(os.path.realpath(resolve_share(env))) / "Projects")


child_env = None  # maude.py (Windows) sets this to its _child_env: callable -> env dict | None


def _creds_env() -> dict | None:
    """Environment for Claude Code with the saved credentials (~/.maude/clauderc), or None to
    inherit. Reached without the TUI (Explorer menu / --windows): maude.py is then loaded only when
    a clauderc (or the parked clauderc.inactive of "Default to AWS Bedrock") exists, so the plain
    start stays fast and an ~/.aws/config alone changes nothing. A load failure prints one line."""
    f = child_env
    if f is None:
        up = os.environ.get("USERPROFILE", "")
        if not (up and (os.path.isfile(os.path.join(up, ".maude", "clauderc"))
                        or os.path.isfile(os.path.join(up, ".maude", "clauderc.inactive")))):
            return None
        try:
            sys.modules.setdefault("maude_exe", sys.modules[__name__])
            import maude
            f = maude._child_env
        except Exception as e:
            print(f"Maude: could not load the saved credentials ({e}); starting without them.",
                  file=sys.stderr)
            return None
    try:
        return f()
    except Exception as e:
        print(f"Maude: could not load the saved credentials ({e}); starting without them.",
              file=sys.stderr)
        return None

def run_windows(folder: str, model: str, banner: bool = True, fresh: bool = False,
                effort: str | None = None) -> int:
    env = os.environ
    claude = find_claude(env["USERPROFILE"])
    if not claude:
        print("Maude: Claude Code for Windows was not found.\n" + INSTALL_HINT, file=sys.stderr)
        return 3
    if banner:
        print(banner_text(folder, has_sandbox()))
    saved_model, saved_effort = windows_model_effort(env["USERPROFILE"])
    model = model or saved_model
    effort = saved_effort if effort is None else effort  # None: take the saved level
    kw = {"cwd": folder}
    cenv = _creds_env()
    if cenv is not None:
        kw["env"] = cenv
    rc = _run(windows_cmd(claude, model, not fresh, effort), **kw)
    if rc and not fresh:
        rc = _run(windows_cmd(claude, model, False, effort), **kw)
    return rc


def _run(cmd: list[str], **kw) -> int:
    """Run cmd and wait. Ctrl+C belongs to the child (Claude Code cancels a turn with it):
    subprocess.run would kill the child on KeyboardInterrupt, so keep waiting instead."""
    p = subprocess.Popen(cmd, **kw)
    # Under the Textual TUI (asyncio.run) SIGINT cancels the main task instead of raising
    # KeyboardInterrupt, which would end Maude when the child exits: ignore it while waiting.
    try:
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):  # not the main thread
        old = None
    try:
        while True:
            try:
                return p.wait()
            except KeyboardInterrupt:
                continue
    finally:
        if old is not None:
            signal.signal(signal.SIGINT, old)


def path_with(value: str, d: str) -> str | None:
    """New user PATH value with d appended, or None if d is already an entry."""
    norm = lambda p: ntpath.normcase(ntpath.normpath(ntpath.expandvars(p.strip()))).rstrip("\\")
    if any(e.strip() and norm(e) == norm(d) for e in value.split(";")):
        return None
    return value.rstrip(";") + (";" if value.rstrip(";") else "") + d


def ensure_user_path(d: str) -> bool:
    """Add d to the user PATH (HKCU only) and tell running shells. True if PATH changed."""
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            try:
                value, kind = winreg.QueryValueEx(h, "Path")
            except FileNotFoundError:
                value, kind = "", winreg.REG_EXPAND_SZ
            new = path_with(value, d)
            if new is None:
                return False
            winreg.SetValueEx(h, "Path", 0, kind, new)
        import ctypes
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, None)
        return True
    except OSError:
        return False


def self_install(env=os.environ, warn=lambda m: print(m, file=sys.stderr)) -> tuple[str, bool, bool]:
    """Return (exe path used by the menu, whether the exe or menu changed, whether PATH changed).
    warn() receives the one warning it may print (so the caller can clear the banner first)."""
    if not getattr(sys, "frozen", False):  # plain `python maude_exe.py`: never install python.exe
        return os.path.abspath(sys.executable), False, False
    import winreg
    cur = os.path.abspath(sys.executable)
    target = install_target(env["USERPROFILE"])
    exe, changed = target, False
    if os.path.normcase(cur) != os.path.normcase(target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".new"
        try:
            same = (os.path.isfile(target) and os.path.getsize(target) == os.path.getsize(cur)
                    and open(target, "rb").read() == open(cur, "rb").read())
            if not same and os.path.isfile(target) and not should_overwrite_installed(VERSION, installed_version(target)):
                warn(f"maude: the installed {target} is newer than this maude.exe ({VERSION}); keeping it.")
            elif not same:
                shutil.copy2(cur, tmp)
                os.replace(tmp, target)  # fails with PermissionError while the old copy runs
                changed = True
        except OSError as err:
            warn(f"maude: could not update {target}: {err}")
            with contextlib.suppress(OSError):
                os.remove(tmp)
            if not os.path.exists(target):
                exe = cur
    for k in KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, k + r"\command") as h:
                if winreg.QueryValueEx(h, None)[0] == context_cmd(exe):
                    continue
        except OSError:
            pass
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, k) as h:
            winreg.SetValueEx(h, "MUIVerb", 0, winreg.REG_SZ, "Open in Maude")
            winreg.SetValueEx(h, "Icon", 0, winreg.REG_SZ, exe)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, k + r"\command") as h:
            winreg.SetValueEx(h, None, 0, winreg.REG_SZ, context_cmd(exe))
        changed = True
    return exe, changed, ensure_user_path(os.path.dirname(target))


def uninstall() -> None:
    import winreg
    for k in KEYS:
        for sub in (k + r"\command", k):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except FileNotFoundError:
                pass
    try:  # the cached share root; the Projects key stays
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_MAUDE, 0, winreg.KEY_SET_VALUE) as h:
            winreg.DeleteValue(h, "Share")
    except OSError:
        pass
    print("Context menu removed. To finish, delete "
          + install_target(os.environ["USERPROFILE"]) + " (after this program has ended).")


def selftest() -> int:
    """Headless TUI start, for CI on the frozen exe: proves Textual and its data files are bundled."""
    import asyncio
    sys.modules.setdefault("maude_exe", sys.modules[__name__])  # maude.py does `import maude_exe`: one module
    import maude
    import textual.widgets
    from rich.cells import cell_len
    # The slim build has no loose .py files: resolve every lazily imported widget and a wide character.
    for name in textual.widgets.__all__:
        getattr(textual.widgets, name)
    assert cell_len("中") == 2
    async def go():
        async with maude.MaudeApp().run_test() as pilot:
            await pilot.pause()
            assert pilot.app.query_one("#projects-table")
            # Modals with widgets that need Textual data files (TextArea, Input, buttons).
            for cls, args in ((maude.CredsEntryScreen, ()), (maude.GitSetupWizard, ()),
                              (maude.NewProjectScreen, ()), (maude.ConfirmDeleteScreen, ("x",))):
                pilot.app.push_screen(cls(*args))
                await pilot.pause()
                assert isinstance(pilot.app.screen, cls), cls.__name__
                pilot.app.pop_screen()
                await pilot.pause()
    asyncio.run(go())
    print("selftest ok")
    return 0


def fix_folder_arg(arg: str) -> str | None:
    """Explorer's "%V" on a drive root is "C:\\" and the closing quote is eaten by argv parsing (\\"),
    which gives C:" . Strip trailing quotes; only if one was stripped is a bare drive turned back
    into its root (C:" -> C:\\). A plain `C:` stays as it is (current directory on that drive).
    Returns None for an empty argument or a lone quote: that is an error, not the current folder."""
    stripped = arg.endswith('"')
    arg = arg.rstrip('"')
    if not arg.strip():
        return None
    return arg + "\\" if stripped and re.fullmatch(r"[A-Za-z]:", arg) else arg


_BANNER = "\x1b[1;38;2;224;82;82m  Starting Maude\x1b[0m\x1b[2m, please wait ...\x1b[0m"
_BANNER_PLAIN = "  Starting Maude, please wait ..."
_banner_on = False


def _enable_vt() -> bool:
    """Turn on virtual terminal processing for stdout (classic conhost needs it; Windows Terminal has it)."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.GetStdHandle.restype = ctypes.c_void_p
        k.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not h or not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(mode.value & 4) or bool(k.SetConsoleMode(h, mode.value | 4))
    except Exception:
        return False


def show_banner(isatty=None, enable_vt=None, out=None) -> bool:
    """Print "Starting Maude, please wait ..." before the slow imports (Textual, Rich). Only on a real
    console; coloured when virtual terminal sequences work, else plain text. Never raises."""
    global _banner_on
    out = out or sys.stdout
    try:
        if not (isatty() if isatty else out.isatty()):
            return False
        vt = (enable_vt or _enable_vt)()
        out.write((_BANNER if vt else _BANNER_PLAIN) + "\n")
        out.flush()
        _banner_on = vt  # plain text cannot be erased in place; it just stays
        return True
    except Exception:
        return False


def forget_banner() -> None:
    """Never writes: just stop treating the banner as erasable (used when main() ends)."""
    global _banner_on
    _banner_on = False


def clear_banner(out=None) -> None:
    """Erase the banner line in place (cursor must be directly below it) so nothing is left on screen
    after the TUI ends. Call it right BEFORE every print, prompt or launch that follows the banner,
    never after something else wrote. Idempotent; a no-op if the banner was not shown or cannot be erased."""
    global _banner_on
    if not _banner_on:
        return
    _banner_on = False
    out = out or sys.stdout
    with contextlib.suppress(Exception):
        out.write("\x1b[1A\r\x1b[2K")
        out.flush()


def timing_report(marks, out=None) -> None:
    """MAUDE_TIMING=1: print phase times (ms) to stderr. marks = [(label, perf_counter), ...]."""
    # GetProcessTimes sees only this (child) process. A onefile exe is a bootloader parent that unpacks
    # to %TEMP%\_MEI... first and then starts this child, so that unpack time is NOT in the first figure.
    out = out or sys.stderr
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
        k.GetProcessTimes(k.GetCurrentProcess(), c, e, kt, ut)
        now = wintypes.FILETIME()
        k.GetSystemTimeAsFileTime(ctypes.byref(now))
        ft = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime
        age_ms = (ft(now) - ft(c)) / 10000.0
        t_now = time.perf_counter()
        print(f"[timing] Python child process start -> first Maude code: {age_ms - (t_now - _T0) * 1000:.0f} ms"
              " (excludes the onefile bootloader unpack, which runs before this process exists)", file=out)
    except Exception:
        pass
    prev = _T0
    for label, t in marks:
        print(f"[timing] {label}: +{(t - prev) * 1000:.0f} ms (total {(t - _T0) * 1000:.0f} ms)", file=out)
        prev = t


# ── Self-update of maude.exe (Update Maude button) ─────────────────────────
# The release workflow publishes maude.exe and maude.exe.sha256 ("sha256sum" format) as release assets.
# Flow: the button only DOWNLOADS and VERIFIES into a staged file (stage_exe). The running onefile exe
# must not be renamed while the TUI still runs (PyInstaller reopens sys.executable for lazy imports and
# would crash), so the swap (apply_staged) happens in run_tui after the TUI exited with 42, then the
# new exe is started. While something is staged, closing the update screen reloads (Close is disabled).
# A staged file that is not switched to (the TUI ended another way) is deleted by run_tui.
# Only plain vX.Y.Z tags count as releases: a build whose VERSION is anything else (dev, v1.2.3-rc1)
# is a development build and is never replaced; pre-release tags on GitHub are ignored.
RELEASE_API = "https://api.github.com/repos/dirkpetersen/maude/releases/latest"
RELEASE_DL = "https://github.com/dirkpetersen/maude/releases/download/"
EXE_ASSET, EXE_SHA_ASSET = "maude.exe", "maude.exe.sha256"
UPDATE_RELOAD = 42  # exit value of the TUI after an update: start again (the new exe)
_VER_RE = re.compile(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)", re.ASCII)
_TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+", re.ASCII)


def parse_version(v) -> tuple[int, int, int] | None:
    """(major, minor, patch) of "v1.2.3" / "1.2.3", else None (dev builds, junk, "v1.2.3-rc1")."""
    m = _VER_RE.fullmatch(v.strip()) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def exe_release(rel) -> tuple[str, str, str] | None:
    """(tag, exe url, sha256 file url) from a releases/latest JSON, else None. The tag must look like
    vX.Y.Z and both assets must be listed; URLs are rebuilt from RELEASE_DL, never taken from the JSON."""
    tag = rel.get("tag_name") if isinstance(rel, dict) else None
    if not isinstance(tag, str) or not _TAG_RE.fullmatch(tag):
        return None
    assets = rel.get("assets")
    names = {a["name"] for a in (assets if isinstance(assets, list) else [])
             if isinstance(a, dict) and isinstance(a.get("name"), str)}
    if EXE_ASSET not in names or EXE_SHA_ASSET not in names:
        return None
    return tag, f"{RELEASE_DL}{tag}/{EXE_ASSET}", f"{RELEASE_DL}{tag}/{EXE_SHA_ASSET}"


def update_decision(current: str, tag: str) -> str:
    """"dev" (a development build is never replaced), "update" (tag is newer), "current" (same or older)."""
    cur, new = parse_version(current), parse_version(tag)
    if cur is None:
        return "dev"
    return "update" if new is not None and new > cur else "current"


def should_overwrite_installed(running: str, installed: str | None) -> bool:
    """self_install: may this (running) exe replace the installed one? Never an older one over a newer
    one, never a dev build over a release. An unknown or unparseable installed version (broken exe,
    dev) is replaced, as before."""
    inst = parse_version(installed) if installed else None
    if inst is None:
        return True
    cur = parse_version(running)
    return cur is not None and cur >= inst


def _kill_tree(p) -> None:
    """Kill a process and its children (the onefile bootloader's child holds the pipes)."""
    if os.name == "nt":
        with contextlib.suppress(Exception):
            subprocess.run([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe"), "/T", "/F", "/PID", str(p.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    with contextlib.suppress(Exception):
        p.kill()


def installed_version(path: str, popen=subprocess.Popen, timeout: float = 30, kill=_kill_tree) -> str | None:
    """Version of an installed exe, from `<exe> --version` ("maude.exe v1.2.3"), None when it cannot be
    told. Chosen over a version stamp file: a stamp goes stale when the exe is copied or replaced by
    hand, and this runs only when the running exe is not the installed one and differs from it
    (rare: first run from Downloads), where one short extra start is cheap.
    Output goes to a temp file, not a pipe: after a timeout, subprocess.run would block in communicate()
    until the onefile child that holds the pipe exits. On a timeout the whole tree is killed."""
    try:
        with tempfile.TemporaryFile() as out:
            p = popen([path, "--version"], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL,
                      env={**os.environ, "MAUDE_NO_INSTALL": "1"})
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill(p)
                with contextlib.suppress(Exception):
                    p.wait(timeout=2)
                return None
            out.seek(0)
            m = re.match(r"maude\.exe (\S+)", out.read(4096).decode("utf-8", "replace"))
            return m.group(1) if m else None
    except Exception:
        return None


def swap_exe(target: str, new: str, pid: int, replace=os.replace) -> str | None:
    """Put new at target: rename target aside to <target>.old-<pid> (or .old-<pid>-<n> when that name
    is taken and cannot be removed), then move new into place. If that fails the old exe is restored.
    Returns the aside path (None when target did not exist). Raises OSError (target intact)."""
    aside, moved = None, False
    if os.path.exists(target):
        for n in range(20):
            cand = f"{target}.old-{pid}" + (f"-{n}" if n else "")
            with contextlib.suppress(OSError):
                os.remove(cand)  # stale file of a recycled pid
            if not os.path.exists(cand):
                aside = cand
                break
        if aside is None:
            raise OSError(f"no free name for {target}.old-{pid}")
        replace(target, aside)
        moved = True
    try:
        replace(new, target)
    except OSError:
        if moved:
            with contextlib.suppress(OSError):
                replace(aside, target)
        raise
    return aside


def cleanup_old(target: str) -> int:
    """Best-effort removal of <target>.old-* (exact prefix) left by earlier updates, in the folder of
    target only; one that is still running cannot be deleted and stays for the next start. Also drops
    staged downloads <target>.update-* older than a day. Returns how many were removed. Never raises."""
    n, d, base = 0, os.path.dirname(target) or ".", os.path.basename(target)
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for name in names:
        full = os.path.join(d, name)
        try:
            low = name.lower()  # Windows names are case-insensitive (callers may pass a lower-cased path)
            if low.startswith(base.lower() + ".old-") or (low.startswith(base.lower() + ".update-")
                                                         and time.time() - os.path.getmtime(full) > 86400):
                os.remove(full)
                n += 1
        except OSError:
            pass
    return n


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


_staged: dict = {}          # {"tag", "path", "target", "sha"} once an update is downloaded and verified
_stage_lock = threading.Lock()


def staged_tag() -> str | None:
    return _staged.get("tag")


def stage_exe(say=print, version: str = None, target: str = None, urlopen=urllib.request.urlopen,
              pid: int = None, running: str = None) -> str:
    """Download and verify the latest release into <target>.update-<pid>. Nothing is renamed here.
    Returns "staged", "current", "dev", "skipped" (not a frozen exe), "busy" (another update runs) or
    "failed". Never raises; reports through say(). target is the installed exe (default: install_target),
    running the exe of this process (frozen only)."""
    version = VERSION if version is None else version
    if running is None:
        running = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else None
    if running is None:
        say("Not running from maude.exe: nothing to update here.")
        return "skipped"
    if parse_version(version) is None:
        say(f"This is a development build ({version}): not replacing it. Install a release to get updates.")
        return "dev"
    if not _stage_lock.acquire(blocking=False):
        say("An update is already running.")
        return "busy"
    tmp = None
    try:
        if _staged:
            say(f"Already updated to {_staged['tag']}: press Reload UI to start it.")
            return "staged"
        if target is None:
            target = install_target(os.environ.get("USERPROFILE", ""))
        if _no_install or not os.path.isfile(target):
            target = running  # --no-install (a portable exe) or not installed: update the exe that runs
        tmp = f"{target}.update-{os.getpid() if pid is None else pid}"
        rel = json.loads(_http_get(RELEASE_API, urlopen))
        found = exe_release(rel)
        if not found:
            say("The latest release has no maude.exe plus maude.exe.sha256: nothing to install.")
            return "failed"
        tag, url, sha_url = found
        if update_decision(version, tag) != "update":
            say(f"maude.exe {version} is up to date (latest release {tag}).")
            return "current"
        say(f"Downloading maude.exe {tag} (installed: {version}) ...")
        sha = checksum_for(_http_get(sha_url, urlopen).decode("utf-8", "replace"), EXE_ASSET)
        if not sha:
            say(f"{EXE_SHA_ASSET} has no checksum for {EXE_ASSET}: not installing.")
            return "failed"
        download_verified(url, tmp, sha, urlopen)
        with open(tmp, "rb") as f:
            if f.read(2) != b"MZ":
                raise ValueError("the download is not a Windows executable")
        _staged.update(tag=tag, path=tmp, target=target, sha=sha)
        say(f"maude.exe {tag} downloaded and verified. Press Reload UI to switch to it.")
        return "staged"
    except urllib.error.HTTPError as err:
        say("GitHub rate limit, try later." if err.code in (403, 429) else f"maude.exe update failed: {err}")
        return "failed"
    except Exception as err:  # network, JSON, checksum: the installed exe stays as it was
        say(f"maude.exe update failed: {err}")
        return "failed"
    finally:
        if tmp and not _staged:
            with contextlib.suppress(OSError):
                os.remove(tmp)
        _stage_lock.release()


update_exe = stage_exe  # name used by maude.py


def apply_staged(say=print, pid: int = None, replace=os.replace) -> str | None:
    """Swap the staged exe in (call after the TUI exited). Returns the installed exe path on success,
    None when nothing was staged or the swap failed (then the old exe stays and say() tells why)."""
    st = dict(_staged)
    _staged.clear()
    if not st:
        return None
    try:
        if _sha256_file(st["path"]) != st["sha"]:
            raise ValueError("the downloaded file changed on disk")
        swap_exe(st["target"], st["path"], os.getpid() if pid is None else pid, replace)
        return st["target"]
    except Exception as err:
        say(f"maude: could not switch to {st['tag']}, keeping the current maude.exe: {err}")
        with contextlib.suppress(OSError):
            os.remove(st["path"])
        return None


def discard_staged() -> None:
    """Forget a staged update and delete its file (the TUI ended without Reload)."""
    st = dict(_staged)
    _staged.clear()
    if st:
        with contextlib.suppress(OSError):
            os.remove(st["path"])


def relaunch_cmd(exe: str, folder: str = "", model: str = "", no_install: bool = False) -> list[str]:
    """argv that starts the (new) exe again with the same folder/model preselection and --no-install."""
    return ([exe] + (["--no-install"] if no_install else []) + (["--model", model] if model and valid_model(model) else [])
            + ([folder] if folder else []))


def should_relaunch(rv, rc=0) -> bool:
    """The TUI asked for a reload (exit value 42) and did not fail."""
    return rv == UPDATE_RELOAD and rc in (0, None)


_no_install = False  # set by _main: relaunch passes --no-install on


def run_tui(folder: str = "", model: str = "") -> int:
    """Run the Windows TUI (light/maude.py). With a folder (outside the share): list it, preselect
    its row and show the OUTSIDE warning. The folder is registered only when it is opened from the
    TUI (MaudeApp._register_selected), never just by starting. A normal quit (q) just
    returns. Textual's App.run() does not re-raise errors in handlers: it prints the traceback and
    sets return_code = 1, so check that too. On a failure the window waits for Enter on a terminal,
    so the window of the Explorer menu does not close over the error."""
    def wait(msg):
        if sys.stdin.isatty() and not sys.stdout.closed:
            with contextlib.suppress(EOFError, OSError):
                input(msg)
    if folder and not os.path.isdir(folder):
        clear_banner()
        print(f"Maude: not started. This is not a folder: {folder}", file=sys.stderr)
        wait("Press Enter to close.")
        return 2
    try:
        mark("before import maude")
        # Run as __main__ (the exe), maude.py's `import maude_exe` would load a SECOND copy of this file
        # with its own banner state: make it the same module. No-op when we were imported normally.
        sys.modules.setdefault("maude_exe", sys.modules[__name__])
        import maude  # the Textual TUI, Windows mode
        mark("import maude (textual, rich) done")
        cls = maude.MaudeApp
        if timing_on():  # opt-in: report the first paint and quit right away
            class cls(maude.MaudeApp):
                def on_ready(self):
                    mark("first paint (Ready)")
                    self.exit()
        app = cls(select=folder, model=model)
        mark("app created")
        clear_banner()  # Textual switches to the alternate screen next; do not leave the line behind
        rv = app.run()
        rc = getattr(app, "return_code", 0)
        if should_relaunch(rv, rc):  # Update Maude replaced the exe: run the new one (no execv on Windows)
            wanted = staged_tag()
            new = apply_staged(lambda msg: print(msg, file=sys.stderr))  # swap only now: the TUI is gone
            if wanted and not new:  # swap failed: the old exe stays, say so and do not restart
                wait("Press Enter to close.")
                return 1
            return _relaunch(folder, model, new)
    except Exception:
        clear_banner()
        import traceback
        traceback.print_exc()
        rc = 1
    finally:
        discard_staged()  # still staged = the TUI ended without Reload (q, Ctrl+Q): drop the file; a killed console leaves it for cleanup_old (1 day)
    if rc not in (0, None):
        wait("Maude failed to start or crashed. Press Enter to close.")
        return 1
    return 0


def _relaunch(folder: str, model: str, exe: str = None, popen=subprocess.Popen) -> int:
    """Start exe (default: this exe) again and wait for it, so the console stays owned by one running
    process (a parent shell would otherwise print its prompt over the new TUI). Keeps --no-install.
    Not frozen: python cannot be restarted meaningfully, return."""
    if not getattr(sys, "frozen", False):
        return 0
    exe = exe or os.path.abspath(sys.executable)
    try:
        return popen(relaunch_cmd(exe, folder, model, _no_install)).wait()
    except OSError as err:
        print(f"maude: could not restart {exe}: {err}", file=sys.stderr)
        return 1


_marks: list = []


def timing_on() -> bool:
    return os.environ.get("MAUDE_TIMING", "").strip() not in ("", "0")


def mark(label: str) -> None:
    if timing_on():
        _marks.append((label, time.perf_counter()))


def main(argv: list[str]) -> int:
    try:
        return _main(argv)
    finally:
        forget_banner()  # never write here: the last screen line may belong to a tool that just ran
        if timing_on():
            mark("exit")
            timing_report(_marks)


def _main(argv: list[str]) -> int:
    # Never resolve programs from the current folder: a cloned repo could plant claude.cmd etc.
    # Read by shutil.which and CreateProcess, and inherited by the AI tools.
    os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"
    if argv[:1] in (["--version"], ["--help"], ["-h"]):
        print(f"maude.exe {VERSION}\nUsage: maude.exe [--tui] | [--model ALIAS] [--windows|--sandbox] folder | --uninstall | --no-install")
        return 0
    if argv[:1] == ["--uninstall"]:
        uninstall()
        return 0
    if os.name != "nt":
        print("maude.exe runs on Windows only.", file=sys.stderr)
        return 1
    if argv[:1] == ["--selftest"]:
        return selftest()
    model, flag, args, it = "", None, [], iter(argv)
    for a in it:
        if a == "--model":
            model = next(it, "")
        elif a in ("--windows", "--sandbox"):
            flag = a[2:]
        elif a not in ("--no-install", "--tui"):
            args.append(a)
    if model and not valid_model(model):
        print(f"maude: bad model name: {model}", file=sys.stderr)
        return 2
    # Say something at once, before the slow part (Textual and Rich imports): for the TUI modes only.
    if not flag and not (args and fix_folder_arg(args[0]) is None):
        shown = show_banner()
        mark(f"banner shown={shown} (erasable={_banner_on})")
    global _no_install
    _no_install = "--no-install" in argv or bool(os.environ.get("MAUDE_NO_INSTALL"))
    if getattr(sys, "frozen", False):  # <exe>.old-<pid> of earlier updates, whatever the install mode
        for e in {os.path.normcase(os.path.abspath(sys.executable)),
                  os.path.normcase(install_target(os.environ.get("USERPROFILE", "")))}:
            cleanup_old(e)
    mark("args parsed")
    if "--no-install" not in argv and not os.environ.get("MAUDE_NO_INSTALL"):
        def _warn(msg):
            clear_banner()
            print(msg, file=sys.stderr)
        exe, changed, path_changed = self_install(warn=_warn)
        if changed or path_changed:
            clear_banner()
        if changed:
            print(f'Maude is installed as {exe}. Shift+right-click any folder in Explorer and select "Open in Maude".')
        if path_changed:
            print(r"Added %USERPROFILE%\.local\bin to your user PATH. Open a new PowerShell window to type 'maude' there.")
    if not args:
        return run_tui("", model)  # --model preselects the model in the picker, as with a folder
    arg = fix_folder_arg(args[0])
    if arg is None:
        clear_banner()
        print("Maude: not started. The folder argument is empty.", file=sys.stderr)
        if sys.stdin.isatty() and not sys.stdout.closed:
            with contextlib.suppress(EOFError, OSError):
                input("Press Enter to close.")
        return 2
    folder = os.path.realpath(arg)
    env = os.environ
    share = os.path.realpath(resolve_share(env))
    what = decide(folder, share, flag)
    if what == "tui":  # Explorer menu on a folder outside the share: no question, TUI with a warning
        return run_tui(folder, model)
    if what == "windows":
        clear_banner()
        if folder_mode(folder, share) == "windows":  # outside the share: list it in the TUI
            with contextlib.suppress(OSError):
                add_project(folder)
        return run_windows(folder, model)
    sandbox = has_sandbox()
    if what == "refuse" or not sandbox:
        clear_banner()
        if not sandbox:
            print("Maude: not started. No Maude sandbox (WSL distro 'Maude') is installed, so there is "
                  "nothing to run this folder in. Install it with setup-wsl-maude.ps1, or use --windows "
                  "to run on Windows with your full user rights.", file=sys.stderr)
        else:
            print(f"Maude: not started. To use the sandbox, move the folder into {share}.", file=sys.stderr)
        if sys.stdin.isatty() and not sys.stdout.closed:
            input("Press Enter to close.")
        return 2
    clear_banner()  # the sandbox session owns the screen from here: erase before it starts, never after
    return _run(sandbox_cmd(wsl_dir(folder, share), model))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
