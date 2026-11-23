#!/usr/bin/env python3
"""maude.exe: Windows launcher for Maude Light. Stdlib only.

It copies itself to %USERPROFILE%\\.local\\bin, adds the "Open in Maude"
Explorer entries (HKCU, no admin) and starts the Maude WSL sandbox.
With no argument it runs the Textual TUI (maude.py, Windows mode) in the
current console. See docs/windows-exe.md.
"""
import ntpath
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import PureWindowsPath as W

VERSION = "dev"  # CI replaces this line with the release tag
DISTRO = "Maude"
KEYS = (r"Software\Classes\Directory\shell\OpenInMaude",
        r"Software\Classes\Directory\Background\shell\OpenInMaude")
# Claude Code in the sandbox: continue the last session, else start a new one.
# $1 = model override (may be empty); else the sandbox model file; else opus.
OPEN_SH = ('m="${1:-$(cat ~/.maude-model 2>/dev/null)}"; '
           'claude "${m:-opus}" --continue || claude "${m:-opus}"')
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


def resolve_share(env=os.environ) -> str:
    return pick_share(read_share(), os.path.isdir, fstab_share,
                      lambda: share_root(env["LOCALAPPDATA"], onedrive_candidates(env)), store_share)


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


def windows_cmd(claude: str, model: str, cont: bool) -> list[str]:
    # No bypass or yolo flag here: default permission mode only.
    return [claude, "--model", model] + (["--continue"] if cont else [])


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
}


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


def download_verified(url: str, dest: str, sha256: str, urlopen=urllib.request.urlopen, timeout: int = 60) -> None:
    """Stream url to dest and check its SHA-256. On a mismatch dest is removed and ValueError raised."""
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "maude.exe"})
    with urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != sha256.lower():
        with contextlib.suppress(OSError):
            os.remove(dest)
        raise ValueError(f"checksum mismatch for {url} (expected {sha256.lower()}, got {h.hexdigest()})")


_SHA_RE = re.compile(r"[0-9a-fA-F]{64}")
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


NATIVE_INSTALL = {"Kiro CLI": install_kiro}  # tool label -> Python installer taking say=, returning an exit code


def user_dirs(env) -> list[str]:
    """Per-user install folders: claude (.local\\bin), bun and bun globals (.bun\\bin)."""
    up = env["USERPROFILE"]
    return [str(W(up) / ".local" / "bin"), str(W(up) / ".bun" / "bin")]


def tool_dirs(env) -> list[str]:
    """user_dirs plus the Kiro MSI folder (a system install that sets its own PATH)."""
    extra = [str(W(env[v]) / sub) for v, sub in (("ProgramFiles", "Kiro-Cli"), ("LOCALAPPDATA", r"Programs\Kiro-Cli"),
                                                  ("LOCALAPPDATA", "Kiro-Cli")) if env.get(v)]
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
_git_done = False  # offer Git once per Maude process


def git_plan(have_git: bool, have_winget: bool):
    """None = nothing to do, "link" = cannot install here (print the download link), else the winget
    argument list (fixed strings). Always --scope user (no UAC, even for an admin) and no installer UI."""
    if have_git:
        return None
    if not have_winget:
        return "link"
    return ["winget", "install", "--id", "Git.Git", "-e", "--source", "winget",
            "--accept-package-agreements", "--accept-source-agreements",
            "--scope", "user", "--silent", "--disable-interactivity"]


def git_dirs(env=os.environ) -> list[str]:
    return [str(W(env[k]) / sub) for k, sub in (("ProgramFiles", "Git/cmd"), ("LOCALAPPDATA", "Programs/Git/cmd"))
            if env.get(k)]


def find_git(env=os.environ, which=shutil.which) -> str | None:
    return which("git") or next((r for d in git_dirs(env) if (r := which("git", path=d))), None)


def winget_works(which=shutil.which, run=subprocess.run) -> str | None:
    """Full path of a working winget (App Installer can be disabled by policy), else None."""
    w = which("winget")
    try:
        if w and run([w, "--version"], stdin=subprocess.DEVNULL, capture_output=True, timeout=5).returncode == 0:
            return w
    except (OSError, subprocess.SubprocessError):
        pass
    return None


# APPINSTALLER_CLI_ERROR_INVALID_CL_ARGUMENTS = 0x8A150002 (winget-cli doc/windows/package-manager/winget/returnCodes.md);
# Popen reports it unsigned (2316632066), other callers see it signed (-1978335230).
WINGET_INVALID_ARGS = (0x8A150002, 0x8A150002 - 2**32)


def _offer_winget(label, link, intro, find, winget, plan_for, dirs, env, ask, run, say) -> None:
    """Body of offer_git: nothing if found, link text without winget, else show the command, ask Y/n,
    run winget directly (one retry without --disable-interactivity if winget rejects that flag),
    then put the install dir on this process's PATH."""
    if find(env):
        return
    w = winget()
    plan = plan_for(w is not None)
    if plan == "link":
        say(f"{label} is not installed. Get it from {link} (optional).")
        return
    plan[0] = w
    say(f"{label} is not installed ({intro}).\nInstall command: {subprocess.list2cmdline(plan)}")
    try:
        yes = ask(f"Install {label} now? [Y/n] ").strip().lower() in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        yes = False
    if not yes:
        return
    try:
        rc = (run or _run)(plan)
        if rc in WINGET_INVALID_ARGS and "--disable-interactivity" in plan:  # winget older than 1.4 rejects that flag
            say("Retrying without --disable-interactivity (older winget)...")
            rc = (run or _run)([a for a in plan if a != "--disable-interactivity"])
    except OSError:
        rc = 1
    if rc:
        say(f"The {label} install failed.")
        return
    d = next((d for d in dirs(env) if os.path.isdir(d)), None)  # the installer sets the persisted PATH itself
    if d:
        env["PATH"] = path_prepend(env.get("PATH", ""), [d])


def offer_git(env=os.environ, find=find_git, winget=winget_works,
              ask=lambda q: input(q), run=None, say=print) -> None:
    """Offer Git for Windows via winget, once per process. Never blocks opening a project."""
    global _git_done
    if _git_done:
        return
    _git_done = True
    _offer_winget("Git for Windows", GIT_LINK, "optional; the AI tools work better with it", find, winget,
                  lambda have_w: git_plan(False, have_w), git_dirs, env, ask, run, say)


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
    if find(env):
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


def windows_model(userprofile: str) -> str:
    try:
        with open(W(userprofile) / ".maude-model", encoding="utf-8") as f:
            m = f.read().strip()
    except OSError:
        m = ""
    return m if valid_model(m) else "opus"


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


def run_windows(folder: str, model: str, banner: bool = True, fresh: bool = False) -> int:
    env = os.environ
    claude = find_claude(env["USERPROFILE"])
    if not claude:
        print("Maude: Claude Code for Windows was not found.\n" + INSTALL_HINT, file=sys.stderr)
        return 3
    if banner:
        print(banner_text(folder, has_sandbox()))
    model = model or windows_model(env["USERPROFILE"])
    rc = _run(windows_cmd(claude, model, not fresh), cwd=folder)
    if rc and not fresh:
        rc = _run(windows_cmd(claude, model, False), cwd=folder)
    return rc


def _run(cmd: list[str], **kw) -> int:
    """Run cmd and wait. Ctrl+C belongs to the child (Claude Code cancels a turn with it):
    subprocess.run would kill the child on KeyboardInterrupt, so keep waiting instead."""
    p = subprocess.Popen(cmd, **kw)
    while True:
        try:
            return p.wait()
        except KeyboardInterrupt:
            continue


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


def self_install(env=os.environ) -> tuple[str, bool, bool]:
    """Return (exe path used by the menu, whether the exe or menu changed, whether PATH changed)."""
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
            if not (os.path.isfile(target) and os.path.getsize(target) == os.path.getsize(cur)
                    and open(target, "rb").read() == open(cur, "rb").read()):
                shutil.copy2(cur, tmp)
                os.replace(tmp, target)  # fails with PermissionError while the old copy runs
                changed = True
        except OSError as err:
            print(f"maude: could not update {target}: {err}", file=sys.stderr)
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
    import maude
    async def go():
        async with maude.MaudeApp().run_test() as pilot:
            await pilot.pause()
            assert pilot.app.query_one("#projects-table")
    asyncio.run(go())
    print("selftest ok")
    return 0


def fix_folder_arg(arg: str) -> str:
    """Explorer's "%V" on a drive root is "C:\\" and the closing quote is eaten by argv parsing (\\"),
    which gives C:" . Strip trailing quotes and turn a bare drive back into its root: C: -> C:\\."""
    arg = arg.rstrip('"')
    return arg + "\\" if re.fullmatch(r"[A-Za-z]:", arg) else arg


def run_tui(folder: str = "", model: str = "") -> int:
    """Run the Windows TUI (light/maude.py). With a folder (outside the share): list it, preselect
    its row and show the OUTSIDE warning. The folder is registered only when it is opened from the
    TUI (MaudeApp._register_selected), never just by starting. A normal quit (q, Command Line) just
    returns. Textual's App.run() does not re-raise errors in handlers: it prints the traceback and
    sets return_code = 1, so check that too. On a failure the window waits for Enter on a terminal,
    so the window of the Explorer menu does not close over the error."""
    def wait(msg):
        if sys.stdin.isatty() and not sys.stdout.closed:
            with contextlib.suppress(EOFError, OSError):
                input(msg)
    if folder and not os.path.isdir(folder):
        print(f"Maude: not started. This is not a folder: {folder}", file=sys.stderr)
        wait("Press Enter to close.")
        return 2
    try:
        import maude  # the Textual TUI, Windows mode
        app = maude.MaudeApp(select=folder, model=model)
        app.run()
        rc = getattr(app, "return_code", 0)
    except Exception:
        import traceback
        traceback.print_exc()
        rc = 1
    if rc not in (0, None):
        wait("Maude failed to start or crashed. Press Enter to close.")
        return 1
    return 0


def main(argv: list[str]) -> int:
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
    if "--no-install" not in argv and not os.environ.get("MAUDE_NO_INSTALL"):
        exe, changed, path_changed = self_install()
        if changed:
            print(f'Maude is installed as {exe}. Shift+right-click any folder in Explorer and select "Open in Maude".')
        if path_changed:
            print(r"Added %USERPROFILE%\.local\bin to your user PATH. Open a new PowerShell window to type 'maude' there.")
    if not args:
        return run_tui()
    folder = os.path.realpath(fix_folder_arg(args[0]))
    env = os.environ
    share = os.path.realpath(resolve_share(env))
    what = decide(folder, share, flag)
    if what == "tui":  # Explorer menu on a folder outside the share: no question, TUI with a warning
        return run_tui(folder, model)
    if what == "windows":
        if folder_mode(folder, share) == "windows":  # outside the share: list it in the TUI
            with contextlib.suppress(OSError):
                add_project(folder)
        return run_windows(folder, model)
    sandbox = has_sandbox()
    if what == "refuse" or not sandbox:
        if not sandbox:
            print("Maude: not started. No Maude sandbox (WSL distro 'Maude') is installed, so there is "
                  "nothing to run this folder in. Install it with setup-wsl-maude.ps1, or use --windows "
                  "to run on Windows with your full user rights.", file=sys.stderr)
        else:
            print(f"Maude: not started. To use the sandbox, move the folder into {share}.", file=sys.stderr)
        if sys.stdin.isatty() and not sys.stdout.closed:
            input("Press Enter to close.")
        return 2
    return _run(sandbox_cmd(wsl_dir(folder, share), model))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
