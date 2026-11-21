#!/usr/bin/env python3
"""maude.exe: Windows launcher for Maude Light. Stdlib only.

It copies itself to %USERPROFILE%\\.local\\bin, adds the "Open in Maude"
Explorer entries (HKCU, no admin) and starts the Maude WSL sandbox.
The Textual TUI (maude.py) stays inside WSL. See docs/windows-exe.md.
"""
import os
import subprocess
import sys
from pathlib import PureWindowsPath as W

VERSION = "0.1.0"
DISTRO = "Maude"
KEYS = (r"Software\Classes\Directory\shell\OpenInMaude",
        r"Software\Classes\Directory\Background\shell\OpenInMaude")
# Claude Code in the sandbox: continue the last session, else start a new one.
OPEN_SH = 'm=$(cat ~/.maude-model 2>/dev/null); claude "${m:-opus}" --continue || claude "${m:-opus}"'


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


def wsl(*args: str) -> int:
    return subprocess.run(["wsl.exe", "-d", DISTRO, *args], check=False).returncode


def main(argv: list[str]) -> int:
    if argv[:1] in (["--version"], ["--help"], ["-h"]):
        print(f"maude.exe {VERSION}\nUsage: maude.exe [folder] | --uninstall | --no-install")
        return 0
    if argv[:1] == ["--uninstall"]:
        uninstall()
        return 0
    if os.name != "nt":
        print("maude.exe runs on Windows only.", file=sys.stderr)
        return 1
    args = [a for a in argv if a != "--no-install"]
    if "--no-install" not in argv and not os.environ.get("MAUDE_NO_INSTALL"):
        exe = self_install()
        print(f"Maude: installed {exe} and the 'Open in Maude' folder menu.")
        print(r"Hint: add %USERPROFILE%\.local\bin to your user PATH to type 'maude' in PowerShell.")
    if not args:
        return wsl("--cd", "~", "--", "bash", "-lc", "maude tui")
    folder = os.path.abspath(args[0])
    d = wsl_dir(folder, share_root(os.environ["LOCALAPPDATA"]))
    if d is None:
        print(f"Maude: {folder} is outside the sandbox share.\n"
              f"Move or copy it into {share_root(os.environ['LOCALAPPDATA'])} first.\n"
              "The sandbox cannot see other Windows folders on purpose.", file=sys.stderr)
        input("Press Enter to close.")
        return 2
    return wsl("--cd", d, "--", "bash", "-lc", OPEN_SH)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
