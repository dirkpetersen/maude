#!/usr/bin/env python3
"""maude.exe: Windows launcher for Maude Light. Stdlib only.

It copies itself to %USERPROFILE%\\.local\\bin, adds the "Open in Maude"
Explorer entries (HKCU, no admin) and starts the Maude WSL sandbox.
With no argument it runs the Textual TUI (maude.py, Windows mode) in the
current console. See docs/windows-exe.md.
"""
import ntpath
import contextlib
import os
import re
import shutil
import subprocess
import sys
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


def decide(folder: str, share: str, flag: str | None, answer: str | None) -> str:
    """Return "sandbox", "windows", "ask" (need a y/N answer), "deny" (declined) or "refuse".
    flag is "windows", "sandbox" or None; answer is the typed reply or None if not asked yet."""
    if flag == "windows":
        return "windows"
    if folder_mode(folder, share) == "sandbox":
        return "sandbox"
    if flag == "sandbox":
        return "refuse"
    if answer is None:
        return "ask"
    return "windows" if answer.strip().lower() in ("y", "yes") else "deny"


def sandbox_cmd(d: str, model: str) -> list[str]:
    # -e runs argv without a shell (so model stays $1); MAUDE_WELCOMED=1 stops the welcome TUI.
    return ["wsl.exe", "-d", DISTRO, "--cd", d, "-e", "/usr/bin/env", "MAUDE_WELCOMED=1",
            "bash", "-lc", OPEN_SH, "maude", model]


def windows_cmd(claude: str, model: str, cont: bool) -> list[str]:
    # No bypass or yolo flag here: default permission mode only.
    return [claude, "--model", model] + (["--continue"] if cont else [])


BUN_CMD = "irm bun.sh/install.ps1 | iex"
# Tool label -> (binary, PowerShell install command). OpenCode and Crush come from bun.
TOOL_INSTALL = {
    "Claude Code": ("claude", "irm https://claude.ai/install.ps1 | iex"),
    "Kiro CLI": ("kiro-cli", "irm 'https://cli.kiro.dev/install.ps1' | iex"),
    "OpenCode": ("opencode", "bun install -g --trust @opencode/cli"),  # --trust with -g: unverified
    "Crush": ("crush", "bun install -g --trust @charmland/crush"),
}


def user_dirs(env) -> list[str]:
    """Per-user install folders: claude (.local\\bin), bun and bun globals (.bun\\bin)."""
    up = env["USERPROFILE"]
    return [str(W(up) / ".local" / "bin"), str(W(up) / ".bun" / "bin")]


def tool_dirs(env) -> list[str]:
    """user_dirs plus the Kiro MSI folder (a system install that sets its own PATH)."""
    return user_dirs(env) + ([str(W(env["ProgramFiles"]) / "Kiro-Cli")] if env.get("ProgramFiles") else [])


def find_tool(binary: str, env=os.environ, which=shutil.which) -> str | None:
    """PATH first, then the installer folders (a fresh install is not on this process's PATH yet)."""
    return which(binary) or next((r for d in tool_dirs(env) if (r := which(binary, path=d))), None)


def find_claude(userprofile: str) -> str | None:
    return find_tool("claude", {**os.environ, "USERPROFILE": userprofile})


def install_plan(tool: str, have_tool: bool, have_bun: bool) -> list[tuple[str, str]]:
    """(label, PowerShell command) steps needed before tool can start. Bun comes first if needed."""
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


def ensure_tool(tool: str, find, ask=lambda q: input(q), run=None, say=print, refresh=lambda: None) -> str | None:
    """Return the tool's path, offering to install it (and bun) when missing. None = not available
    (declined, install failed or still not found); the reason has been printed."""
    exe = find(TOOL_INSTALL[tool][0])
    if exe:
        return exe
    run = run or _run
    for label, cmd in install_plan(tool, False, find("bun") is not None):
        say(f"{label} is not installed on Windows.\nInstall command: {cmd}")
        try:
            yes = ask(f"Install {label} now? [Y/n] ").strip().lower() in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            yes = False
        if not yes:
            return None
        if run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd]):
            say(f"The {label} install failed.")
            return None
        refresh()  # bun must be on PATH for the next step
    exe = find(TOOL_INSTALL[tool][0])
    if not exe:
        say(f"{tool} was installed but is not found yet. Open a new PowerShell window and try again.")
    return exe


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


def projects_dir(env=os.environ) -> str:
    return str(W(os.path.realpath(resolve_share(env))) / "Projects")


def run_windows(folder: str, model: str, banner: bool = True, fresh: bool = False) -> int:
    env = os.environ
    claude = find_claude(env["USERPROFILE"])
    if not claude:
        print("Maude: Claude Code for Windows was not found.\n" + INSTALL_HINT, file=sys.stderr)
        return 3
    if banner:
        print(BANNER.format(folder))
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
        import maude  # the Textual TUI, Windows mode (light/maude.py)
        maude.MaudeApp().run()
        return 0
    folder = os.path.realpath(args[0])
    env = os.environ
    share = os.path.realpath(resolve_share(env))
    answer, asked = None, False
    while True:
        what = decide(folder, share, flag, answer)
        if what != "ask":
            break
        print(BANNER.format(folder))
        answer, asked = "", True
        if sys.stdin.isatty():  # no terminal means No
            answer = input("Run Maude on Windows outside the sandbox? [y/N] ")
    if what == "windows":
        if folder_mode(folder, share) == "windows":  # outside the share: list it in the TUI
            with contextlib.suppress(OSError):
                add_project(folder)
        return run_windows(folder, model, banner=not asked)
    if what in ("deny", "refuse"):
        print(f"Maude: not started. To use the sandbox, move the folder into {share}.", file=sys.stderr)
        if sys.stdin.isatty() and not sys.stdout.closed:
            input("Press Enter to close.")
        return 2
    return _run(sandbox_cmd(wsl_dir(folder, share), model))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
