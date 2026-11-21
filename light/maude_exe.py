#!/usr/bin/env python3
"""maude.exe: Windows launcher for Maude Light. Stdlib only.

It copies itself to %USERPROFILE%\\.local\\bin, adds the "Open in Maude"
Explorer entries (HKCU, no admin) and starts the Maude WSL sandbox.
The Textual TUI (maude.py) stays inside WSL. See docs/windows-exe.md.
"""
import ntpath
import os
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


def share_root(localappdata: str) -> str:
    return str(W(localappdata) / "Maude" / "Data" / "Maude")


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
    return ["wsl.exe", "-d", DISTRO, "--cd", d, "--", "bash", "-lc", OPEN_SH, "maude", model]


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
            return f.read().strip() or "opus"
    except OSError:
        return "opus"


BANNER = ("Maude: running OUTSIDE the sandbox in {}. "
          "Claude Code has your full Windows user rights here.")


def run_windows(folder: str, model: str) -> int:
    env = os.environ
    claude = find_claude(env["USERPROFILE"])
    if not claude:
        print("Maude: Claude Code for Windows was not found.\n" + INSTALL_HINT, file=sys.stderr)
        return 3
    print(BANNER.format(folder))
    model = model or windows_model(env["USERPROFILE"])
    rc = subprocess.run(windows_cmd(claude, model, True), cwd=folder, check=False).returncode
    if rc:
        rc = subprocess.run(windows_cmd(claude, model, False), cwd=folder, check=False).returncode
    return rc


def self_install(env=os.environ) -> str:
    import shutil
    import winreg
    cur = os.path.abspath(sys.executable)
    target = install_target(env["USERPROFILE"])
    exe = target
    if os.path.normcase(cur) != os.path.normcase(target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".new"
        try:
            shutil.copy2(cur, tmp)
            os.replace(tmp, target)  # fails with PermissionError while the old copy runs
        except OSError as err:
            print(f"maude: could not update {target}: {err}", file=sys.stderr)
            if not os.path.exists(target):
                exe = cur
    for k in KEYS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, k) as h:
            winreg.SetValueEx(h, "MUIVerb", 0, winreg.REG_SZ, "Open in Maude")
            winreg.SetValueEx(h, "Icon", 0, winreg.REG_SZ, exe)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, k + r"\command") as h:
            winreg.SetValueEx(h, None, 0, winreg.REG_SZ, context_cmd(exe))
    return exe


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
    if "--no-install" not in argv and not os.environ.get("MAUDE_NO_INSTALL"):
        exe = self_install()
        print(f"Maude: installed {exe} and the 'Open in Maude' folder menu.")
        print(r"Hint: add %USERPROFILE%\.local\bin to your user PATH to type 'maude' in PowerShell.")
    if not args:
        return subprocess.run(["wsl.exe", "-d", DISTRO, "--cd", "~", "--", "bash", "-lc", "maude tui"],
                              check=False).returncode
    folder = os.path.realpath(args[0])
    share = os.path.realpath(share_root(os.environ["LOCALAPPDATA"]))
    answer = None
    while True:
        what = decide(folder, share, flag, answer)
        if what != "ask":
            break
        print(BANNER.format(folder))
        answer = ""
        if sys.stdin.isatty():  # no terminal means No
            answer = input("Run Claude Code on Windows outside the sandbox? [y/N] ")
    if what == "windows":
        return run_windows(folder, model)
    if what in ("deny", "refuse"):
        print(f"Maude: not started. To use the sandbox, move the folder into {share}.", file=sys.stderr)
        if sys.stdin.isatty() and not sys.stdout.closed:
            input("Press Enter to close.")
        return 2
    return subprocess.run(sandbox_cmd(wsl_dir(folder, share), model), check=False).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
