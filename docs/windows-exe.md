# maude.exe (Windows launcher)

## What it is

`maude.exe` is a small native Windows program built from `light/maude_exe.py` (stdlib only, PyInstaller one-file). It is a launcher. It is not a port of `maude.py`.

Reason: every feature of `maude.py` (Textual TUI, `claude` wrapper, `~/Maude/Projects`, kanna Web UI, `fuser`, `os.killpg`, bash helpers in `~/.local/lib/maude`, `.bashrc` edits, ssh-agent, gpg, `mom`) needs Linux. Maude Light already runs all of this inside the WSL sandbox. So the exe only starts the sandbox. `maude.py` is not changed, and Textual is not bundled.

| maude.py feature | On Windows (maude.exe) |
|---|---|
| Textual TUI, project list, model picker, git wizard, kanna Web UI, creds, update | Runs unchanged inside WSL. `maude.exe` with no argument starts `maude tui` in the WSL distro |
| Open project in Claude Code | `maude.exe "<folder>"` runs `claude <model> --continue` in the WSL distro (see below) |
| `fuser`, `killpg`, bash helpers, `.bashrc`, `mom`, `/tmp` | Not used by the exe. Stay inside WSL |
| Native Windows `claude.exe` | Used only in Windows mode (see below) |

## Two modes

The sandbox is the default. Windows mode is a deliberate choice.

| Folder | Flags | What happens |
|---|---|---|
| none (`maude.exe`) | | `wsl.exe -d Maude --cd ~` (normal login; the welcome script starts the TUI) |
| inside `%LOCALAPPDATA%\Maude\Data\Maude` | | Sandbox: `wsl.exe -d Maude --cd ~/Maude/<rel> -e /usr/bin/env MAUDE_WELCOMED=1 bash -lc <script> maude <model>`. `-e` runs argv without a shell, so the model stays `$1`. `MAUDE_WELCOMED=1` stops the welcome TUI. The script runs `claude <model> --continue`, else `claude <model>` |
| outside the share | none | Prints the OUTSIDE warning and asks `Run Claude Code on Windows outside the sandbox? [y/N]`. Only `y` or `yes` continues. No terminal means No |
| outside the share | `--windows` | Windows mode without the question |
| outside the share | `--sandbox` | Not started. Prints that the folder must be moved into the share, exit code 2 |
| inside the share | `--windows` | Windows mode too: an explicit choice is honoured even for a folder inside the share |
| any | `--model <alias>` | Model override (`opus`, `sonnet`, `haiku`, `fable` or a full model name; letters, digits, `.`, `_`, `-` only; default: sandbox `~/.maude-model`, or `%USERPROFILE%\.maude-model` in Windows mode, else `opus`) |

Windows mode runs `claude --model <alias> --continue` in the folder. If that exits non-zero it runs `claude --model <alias>`. It looks for `claude` on PATH, then `%USERPROFILE%\.local\bin\claude.exe`. It never passes `--dangerously-skip-permissions` or any bypass mode. If Claude Code is not found it prints `irm https://claude.ai/install.ps1 | iex` and exits with code 3. It installs nothing.

The share is found like `setup-wsl-maude.ps1` does: `%LOCALAPPDATA%\Maude\Data\Maude` if it has a `Projects` folder, else the first OneDrive `Maude` folder that has one, else the local one. The forced setup flags `-OneDrive` and `-NoOneDrive` cannot be seen at run time.

Security statement: only exe mode, on a folder outside the share, after `y` or `--windows`, runs outside the sandbox. There Claude Code has your full Windows user rights (all your files, SSH keys, browser data), with normal permission prompts. Everything inside the share stays in the isolated WSL distro. The context menu runs `"<exe>" "%V"` without `--windows`, so it always asks first.

## First run (no admin)

1. Copies itself to `%USERPROFILE%\.local\bin\maude.exe` (copy to `maude.exe.new`, then `os.replace`). Skipped if it already runs from there. If the old copy is in use, it prints a warning and keeps the old copy.
2. Writes these HKCU keys (idempotent):

```
HKCU\Software\Classes\Directory\shell\OpenInMaude             MUIVerb = Open in Maude, Icon = <exe>
HKCU\Software\Classes\Directory\shell\OpenInMaude\command     (default) = "<exe>" "%V"
HKCU\Software\Classes\Directory\Background\shell\OpenInMaude  MUIVerb = Open in Maude, Icon = <exe>
HKCU\Software\Classes\Directory\Background\shell\OpenInMaude\command  (default) = "<exe>" "%V"
```

3. Prints a hint to add `%USERPROFILE%\.local\bin` to the user PATH. It does not change PATH.

Windows 11 shows these entries under "Show more options".

Flags: `--version`, `--uninstall` (removes the keys; then delete `%USERPROFILE%\.local\bin\maude.exe` by hand), `--no-install` or env `MAUDE_NO_INSTALL=1` (skip self-install; used in CI).

## Install

Download `maude.exe` (and `maude.exe.sha256`) from the latest GitHub Release and run it once. Check the hash with `Get-FileHash maude.exe`. The exe is unsigned, so SmartScreen shows "Windows protected your PC": choose More info, then Run anyway.

## Build

`.github/workflows/build-maude-exe.yml` builds on `windows-latest` (`pyinstaller --onefile --console`), injects the tag as version (`dev` outside tags), smoke-tests `--version`, and uploads the artifact `maude-exe`. `release.yml` calls it on `v*.*.*` tags and attaches `maude.exe` and `maude.exe.sha256` to the release. It needs no Textual data files.
