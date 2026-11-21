#!/usr/bin/env python3
"""maude.exe: Windows launcher for Maude Light. Stdlib only.

It copies itself to %USERPROFILE%\\.local\\bin, adds the "Open in Maude"
Explorer entries (HKCU, no admin) and starts the Maude WSL sandbox.
The Textual TUI (maude.py) stays inside WSL. See docs/windows-exe.md.
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
                           capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_fstab_share(r.stdout.decode("utf-8", "replace")) if r.returncode == 0 else None


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


def tui_cmd() -> list[str]:
    return ["wsl.exe", "-d", DISTRO, "--cd", "~"]  # normal login: welcome script starts the TUI


def windows_cmd(claude: str, model: str, cont: bool) -> list[str]:
    # No bypass or yolo flag here: default permission mode only.
    return [claude, "--model", model] + (["--continue"] if cont else [])


def find_claude(userprofile: str) -> str | None:
    return (shutil.which("claude")
            or next((p for p in [str(W(userprofile) / ".local" / "bin" / "claude.exe")]
                     if os.path.isfile(p)), None))


def windows_model(userprofile: str) -> str:
    try:
        with open(W(userprofile) / ".maude-model", encoding="utf-8") as f:
            m = f.read().strip()
    except OSError:
        m = ""
    return m if valid_model(m) else "opus"


BANNER = ("Maude: running OUTSIDE the sandbox in {}. "
          "Claude Code has your full Windows user rights here.")


def run_windows(folder: str, model: str, banner: bool = True) -> int:
    env = os.environ
    claude = find_claude(env["USERPROFILE"])
    if not claude:
        print("Maude: Claude Code for Windows was not found.\n" + INSTALL_HINT, file=sys.stderr)
        return 3
    if banner:
        print(BANNER.format(folder))
    model = model or windows_model(env["USERPROFILE"])
    rc = _run(windows_cmd(claude, model, True), cwd=folder)
    if rc:
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


def self_install(env=os.environ) -> tuple[str, bool]:
    """Return (exe path used by the menu, whether anything changed)."""
    if not getattr(sys, "frozen", False):  # plain `python maude_exe.py`: never install python.exe
        return os.path.abspath(sys.executable), False
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
    return exe, changed


def uninstall() -> None:
    import winreg
    for k in KEYS:
        for sub in (k + r"\command", k):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except FileNotFoundError:
                pass
    print("Context menu removed. To finish, delete "
          + install_target(os.environ["USERPROFILE"]) + " (after this program has ended).")


def main(argv: list[str]) -> int:
    if argv[:1] in (["--version"], ["--help"], ["-h"]):
        print(f"maude.exe {VERSION}\nUsage: maude.exe [--model ALIAS] [--windows|--sandbox] [folder] | --uninstall | --no-install")
        return 0
    if argv[:1] == ["--uninstall"]:
        uninstall()
        return 0
    if os.name != "nt":
        print("maude.exe runs on Windows only.", file=sys.stderr)
        return 1
    model, flag, args, it = "", None, [], iter(argv)
    for a in it:
        if a == "--model":
            model = next(it, "")
        elif a in ("--windows", "--sandbox"):
            flag = a[2:]
        elif a != "--no-install":
            args.append(a)
    if model and not valid_model(model):
        print(f"maude: bad model name: {model}", file=sys.stderr)
        return 2
    if "--no-install" not in argv and not os.environ.get("MAUDE_NO_INSTALL"):
        exe, changed = self_install()
        if changed:
            print(f"Maude: installed {exe} and the 'Open in Maude' folder menu.")
            print(r"Hint: add %USERPROFILE%\.local\bin to your user PATH to type 'maude' in PowerShell.")
    if not args:
        return _run(tui_cmd())
    folder = os.path.realpath(args[0])
    env = os.environ
    share = os.path.realpath(fstab_share() or share_root(env["LOCALAPPDATA"], onedrive_candidates(env)))
    answer, asked = None, False
    while True:
        what = decide(folder, share, flag, answer)
        if what != "ask":
            break
        print(BANNER.format(folder))
        answer, asked = "", True
        if sys.stdin.isatty():  # no terminal means No
            answer = input("Run Claude Code on Windows outside the sandbox? [y/N] ")
    if what == "windows":
        return run_windows(folder, model, banner=not asked)
    if what in ("deny", "refuse"):
        print(f"Maude: not started. To use the sandbox, move the folder into {share}.", file=sys.stderr)
        if sys.stdin.isatty() and not sys.stdout.closed:
            input("Press Enter to close.")
        return 2
    return _run(sandbox_cmd(wsl_dir(folder, share), model))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
