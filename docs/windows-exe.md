# maude.exe (Windows launcher)

## What it is

`maude.exe` is a native Windows program built from `light/maude_exe.py` (launcher) and `light/maude.py` (the Textual TUI), packed with PyInstaller (one file, Textual bundled).

With no argument it runs the Maude TUI natively in the current console. Start it from PowerShell and the TUI runs inside that PowerShell. A double-click gives it its own console window. `--tui` does the same as no argument.

This is Windows mode of the TUI. It lists the projects of the Maude share (the same `Projects` folder the sandbox uses; share found as described below, then `\Projects`). The Linux behaviour of `maude.py` is unchanged.

| TUI feature | On Windows (maude.exe) |
|---|---|
| Project list, New, Delete | Works on the share's `Projects` folder (Delete = soft delete to `Projects\.deleted`) plus the outside-sandbox projects (see next section). New always creates the folder in the share |
| Model picker, AI tool picker, Clear context, Perplexity checkbox | Work. State files are in `%USERPROFILE%` (`.maude-model`, `.maude-tool`, `.maude-perplexity`) |
| Open Project | Runs the selected AI tool natively on Windows in the project folder, OUTSIDE the sandbox, with normal permission prompts (see below) |
| Command Line | Exits the TUI and returns to the PowerShell that started `maude.exe` (same as on Linux). Run `maude.exe` again to come back. |
| Web UI (kanna), To Github, Setup Git(hub), Set Creds, Update Maude, Autostart | Hidden. They need the sandbox (bash helpers, ssh, gpg, `mom`, `.bashrc`, `fuser`, process groups, the `claude` wrapper). Use the sandbox for them |
| Daily self-update of `maude.py`, credential gate | Off. The exe is updated by downloading a new release. Native `claude.exe` has its own login |

Open Project runs these commands (working directory is the project folder):

| Tool | Command |
|---|---|
| Claude Code | `claude --model <alias> --continue`; if that exits non-zero, `claude --model <alias>`. With Clear context only `claude --model <alias>` |
| Kiro CLI | `kiro-cli chat --resume` (then `kiro-cli chat` if that fails); fresh: `kiro-cli chat` |
| OpenCode, Crush | `opencode --continue`, `crush --continue` (fresh: no flag) |

Each tool is found on PATH, else in `%USERPROFILE%\.local\bin` (claude), `%USERPROFILE%\.bun\bin` (bun and its global tools) or `%ProgramFiles%\Kiro-Cli` (Kiro CLI). If it is missing, the TUI is suspended and asks (see "Tool install offers" below). A one-line OUTSIDE-the-sandbox banner is printed before every launch. No bypass flag (`--dangerously-skip-permissions` or similar) is ever passed on Windows. Ctrl+C belongs to the child tool and never kills it. With the Perplexity box ticked, the connector is set up with the real `claude` binary before Claude Code starts.

The sandbox and the `claude` wrapper (with its positional model alias) do not exist on Windows. Folder arguments and the Explorer menu keep their behaviour (next sections).

## Tool install offers (first run on a new PC)

Goal: start `maude.exe` on a new PC and let it set up what is missing. In order:

1. Start: it copies itself to `%USERPROFILE%\.local\bin`, adds the Explorer menu entry and adds that folder to the user PATH (see "First run").
2. The TUI starts without WSL. With no WSL, no `Maude` distro or no cached share, it tries `wsl.exe` once (no console input, 15 s limit), then uses the setup folder rule. `Projects` is created if missing.
3. Open Project with a tool that is not installed: the TUI is suspended and Maude prints what is missing and the exact command, then asks `Install <Tool> now? [Y/n]`. Enter or `y` runs it with `powershell -NoProfile -ExecutionPolicy Bypass -Command "<command>"` in the same console. `n` prints a message and waits for Enter.

| Tool | Command |
|---|---|
| Claude Code | `irm https://claude.ai/install.ps1 \| iex` |
| Kiro CLI | `irm 'https://cli.kiro.dev/install.ps1' \| iex` (MSI, installs to `C:\Program Files\Kiro-Cli`, may ask for admin) |
| OpenCode | `bun install -g --trust @opencode/cli` |
| Crush | `bun install -g --trust @charmland/crush` |
| Bun (first, only if OpenCode or Crush is chosen and bun is missing) | `irm bun.sh/install.ps1 \| iex` |

After a successful install Maude adds `%USERPROFILE%\.local\bin` and `%USERPROFILE%\.bun\bin` (when they exist) to the user PATH and to its own PATH, looks the tool up again and starts it. If the install fails or the tool is still not found, Maude prints why and waits for Enter. Other PowerShell windows need a restart to see the new PATH.

`--trust` is a documented `bun add` flag; its effect together with `-g` is not verified.

### Git for Windows (optional, once per Maude run)

On the first Open Project in a Maude run, before the tool check, Maude looks for `git` (PATH, `%ProgramFiles%\Git\cmd`, `%LOCALAPPDATA%\Programs\Git\cmd`). If it is missing and `winget --version` works, Maude prints the exact command and asks `Install Git for Windows now? [Y/n]`. It runs `winget` directly (not through PowerShell):

- Elevated window (Run as administrator): `winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements`
- Normal window (also for members of Administrators): the same command plus `--scope user` (per-user install, no elevation).

If winget is missing or blocked by policy (App Installer disabled), Maude does not offer an install. It prints `Git for Windows is not installed. Get it from https://git-scm.com/download/win (optional).` and continues. A "no", a failed install or Ctrl+C never stops the project from opening. After a successful install Maude adds the Git `cmd` folder to its own PATH (the installer sets the saved PATH).

### GitHub CLI (optional, once per Maude run)

On the first Open Project in a Maude run, right after the Git offer, Maude looks for `gh` (PATH, then `%ProgramFiles%\GitHub CLI`). If it is missing and `winget --version` works, Maude prints the exact command and asks `Install GitHub CLI now? [Y/n]`. It runs `winget` directly:

`winget install --id GitHub.cli -e --source winget --accept-package-agreements --accept-source-agreements`

There is no `--scope` option: the package is a machine MSI, so Windows may ask for admin approval (UAC prompt). The prompt text says so.

If winget is missing or blocked by policy, Maude does not offer an install. It prints `GitHub CLI is not installed. Get it from https://cli.github.com (optional).` and continues. A "no", a failed install or Ctrl+C never stops the project from opening. After a successful install Maude adds the `GitHub CLI` folder to its own PATH (the installer sets the saved PATH).

## Outside-sandbox projects

The Windows TUI shows two groups of projects. The red logo with the caption "running outside the sandbox" tells you that you are in this mode.

- Outside-sandbox projects come first. They are folders that you opened with "Open in Maude" (or `maude.exe <folder>`) outside the share and then confirmed Windows mode (`y`, `yes` or `--windows`). A folder inside the share is not added, because it is already listed.
- Then come the share projects. The outside-sandbox projects are not shown when the TUI runs in the sandbox.

Each group is sorted by last change, newest first. Last change = the newest modification time of the folder and its direct children (no deep scan, `.git` is skipped). A folder that no longer exists is hidden, but its registry value stays. If two projects have the same folder name, the list shows `name (parent folder)`.

The third column is "Features". It lists `GitHub` (the project has a GitHub remote) and `No Sandbox` (outside-sandbox project), joined with `, `. In the sandbox only `GitHub` can appear.

Registry (current user, REG_SZ values):

```
HKCU\Software\Maude\Projects   <normalised folder path (lower case)> = <full folder path>
```

Delete on an outside-sandbox project removes only its registry value. The folder is never moved or deleted. Delete on a share project is still a soft delete. A dim line separates the outside-sandbox projects from the share projects, and after Delete on an outside project a message shows the folder path so you can delete the folder by hand. To remove an entry by hand, delete the value in `regedit`. `maude.exe --uninstall` does not remove this key (it removes only the `Share` value of the parent key, see below).

## Two modes

The sandbox is the default. Windows mode is a deliberate choice.

| Folder | Flags | What happens |
|---|---|---|
| none (`maude.exe`, or `--tui`) | | The Windows TUI described above (no WSL needed to start it) |
| inside `%LOCALAPPDATA%\Maude\Data\Maude` | | Sandbox: `wsl.exe -d Maude --cd ~/Maude/<rel> -e /usr/bin/env MAUDE_WELCOMED=1 bash -lc <script> maude <model>`. `-e` runs argv without a shell, so the model stays `$1`. `MAUDE_WELCOMED=1` stops the welcome TUI. The script runs `claude <model> --continue`, else `claude <model>` |
| outside the share | none | Prints the OUTSIDE warning and asks `Run Maude on Windows outside the sandbox? [y/N]`. Only `y` or `yes` continues. No terminal means No |
| outside the share | `--windows` | Windows mode without the question |
| outside the share | `--sandbox` | Not started. Prints that the folder must be moved into the share, exit code 2 |
| inside the share | `--windows` | Windows mode too: an explicit choice is honoured even for a folder inside the share |
| any | `--model <alias>` | Model override (`opus`, `sonnet`, `haiku`, `fable` or a full model name; letters, digits, `.`, `_`, `-` only; default: sandbox `~/.maude-model`, or `%USERPROFILE%\.maude-model` in Windows mode, else `opus`) |

Windows mode runs `claude --model <alias> --continue` in the folder. If that exits non-zero it runs `claude --model <alias>`. It looks for `claude` on PATH, then `%USERPROFILE%\.local\bin\claude.exe`. It never passes `--dangerously-skip-permissions` or any bypass mode. If Claude Code is not found it prints `irm https://claude.ai/install.ps1 | iex` and exits with code 3. Folder mode installs nothing; only the TUI offers installs.

Share detection order (TUI and folder mode):

1. Registry cache: `HKCU\Software\Maude`, REG_SZ value `Share`. It is used if the folder exists and has a `Projects` folder. No WSL call, no delay.
2. Else the drvfs line for `/home/<user>/Maude` in the distro's `/etc/fstab` (`wsl.exe -d Maude -e cat /etc/fstab`; `\040` decoded to a space). Maude prints "Maude: locating the Maude share (WSL)..." and stores the result in `Share`.
3. Else the setup rule: `%LOCALAPPDATA%\Maude\Data\Maude` if it has a `Projects` folder, else the first OneDrive `Maude` folder that has one, else the local one. This guess is never stored, because it can be wrong. The forced setup flags `-OneDrive` and `-NoOneDrive` cannot be seen at run time.

A failed registry read or write is ignored. To reset the cache, delete the `Share` value in `regedit` or run `maude.exe --uninstall` (it removes `Share` and leaves `HKCU\Software\Maude\Projects` alone).

Security statement: only exe mode, on a folder outside the share, after `y` or `--windows`, runs outside the sandbox. There Maude's AI agents have your full Windows user rights (all your files, SSH keys, browser data), with normal permission prompts. Everything inside the share stays in the isolated WSL distro. The context menu runs `"<exe>" "%V"` without `--windows`, so it always asks first.

## First run (no admin)

1. Copies itself to `%USERPROFILE%\.local\bin\maude.exe` (copy to `maude.exe.new`, then `os.replace`). Skipped if it already runs from there. If the old copy is in use, it prints a warning and keeps the old copy.
2. Writes these HKCU keys (idempotent):

```
HKCU\Software\Classes\Directory\shell\OpenInMaude             MUIVerb = Open in Maude, Icon = <exe>
HKCU\Software\Classes\Directory\shell\OpenInMaude\command     (default) = "<exe>" "%V"
HKCU\Software\Classes\Directory\Background\shell\OpenInMaude  MUIVerb = Open in Maude, Icon = <exe>
HKCU\Software\Classes\Directory\Background\shell\OpenInMaude\command  (default) = "<exe>" "%V"
```

3. Adds `%USERPROFILE%\.local\bin` to the user PATH (`HKCU\Environment`, value `Path`, appended, nothing reordered, the system PATH is never touched) if it is not there, and broadcasts a settings change. This runs on every start, so it also repairs a PATH entry that a user removed. Open a new PowerShell window to type `maude`.
4. When the exe or the menu was newly installed, prints: Shift+right-click any folder in Explorer and select "Open in Maude". When PATH changed, it also says so.

Windows 11 shows these entries under "Show more options".

Flags: `--version`, `--uninstall` (removes the keys; then delete `%USERPROFILE%\.local\bin\maude.exe` by hand), `--no-install` or env `MAUDE_NO_INSTALL=1` (skip self-install; used in CI).

## Install

Download `maude.exe` (and `maude.exe.sha256`) from the latest GitHub Release and run it once, also from the Downloads folder: it copies itself to `%USERPROFILE%\.local\bin`. Check the hash with `Get-FileHash maude.exe`. The exe is unsigned, so SmartScreen shows "Windows protected your PC": choose More info, then Run anyway.

## Build

`.github/workflows/build-maude-exe.yml` builds on `windows-latest` with `pyinstaller==6.11.1` and `textual==8.2.8`: `pyinstaller --onefile --console --name maude --paths light --hidden-import maude --hidden-import maude_exe --collect-all textual --collect-all rich light/maude_exe.py`. It injects the tag as version (`dev` outside tags). Checks in CI:

1. `python tests/win_tui_smoke.py` from source: headless Textual run with a fake `USERPROFILE`, share and registry. It checks the project list and its order, the Features cells, that Delete on an outside project removes only the registry entry, the logo caption, the hidden buttons, that Command Line exits the TUI and the Open Project commands (spawns are patched, nothing opens).
2. `maude.exe --version`.
3. `maude.exe --selftest`: starts the TUI headless from the frozen exe. This proves that Textual and its data files are bundled.

It uploads the artifact `maude-exe`. `release.yml` calls it on `v*.*.*` tags and attaches `maude.exe` and `maude.exe.sha256` to the release. The real console behaviour (colours, suspend while a tool runs, new PowerShell window) is not tested in CI.

The Windows setup script `setup-wsl-maude.ps1` removes the cached `HKCU\Software\Maude\Share` value, so a reinstall makes maude.exe look up the share root again.

## Notes from field tests

- A tool is found on PATH first (also entries written as `%VAR%`, which `shutil.which` does not expand), then in `%USERPROFILE%\.local\bin`, `%USERPROFILE%\.bun\bin`, `%ProgramFiles%\Kiro-Cli`, `%LOCALAPPDATA%\Programs\Kiro-Cli` and `%LOCALAPPDATA%\Kiro-Cli`. A user reported that Maude offered to install Kiro CLI although `kiro-cli` was on PATH. Cause found on the developer PC: `kiro-cli.exe` was in `%LOCALAPPDATA%\Kiro-Cli` (a user PATH entry), and that folder was only added to `tool_dirs()` by `9babfcf`, so a maude.exe whose PATH predates the Kiro install missed it. Fixed in v0.3.3. If it happens again after v0.3.3, run `where.exe kiro-cli` in the same window and add that folder to `tool_dirs()` in `light/maude_exe.py`.
- The Kiro CLI MSI can fail with exit code 1603 (installer kept in `%TEMP%\kiro-cli-installer-<version>.msi`). Maude only reports "The Kiro CLI install failed." and goes back to the menu.
- The docs site needs Pages source "GitHub Actions" and the v4 `upload-pages-artifact` and `deploy-pages` actions. The v5 pair did not upload the artifact, and Python 3.15 is not used.
- `ensure_user_path` and the `WM_SETTINGCHANGE` broadcast are untested on Windows.


## Status and next steps (v0.3.3)

Released: v0.3.3 (exe, WSL tarball). It includes the Kiro lookup fix (`9babfcf`) and the GitHub CLI offer.

Open items:

1. Partly checked on the developer PC: `%USERPROFILE%\.local\bin\maude.exe` exists, the user PATH has the entry and `HKCU\Environment\Path` kept its type REG_EXPAND_SZ. Still open: run `maude.exe` from Downloads on a clean PC, check the printed messages and the new-window `maude` command.
2. Kiro CLI offered although installed: on the developer PC `kiro-cli.exe` was in `%LOCALAPPDATA%\Kiro-Cli` (a user PATH entry). That folder was only added to `tool_dirs()` by `9babfcf`, so a maude.exe whose PATH predates the Kiro install missed it. Likely fixed in v0.3.3. Kiro CLI and the Kiro IDE were then uninstalled on the developer PC (2026-11-22). Next check: Open Project with Kiro CLI must offer the install, and after installing it must be found without restarting Maude.
3. GitHub CLI offer (`gh_plan`, `find_gh`, `offer_gh` in `light/maude_exe.py`): done in v0.3.3. Next: check on a real PC that the UAC prompt appears and `gh` is found right after the install.
4. The Kiro MSI exit code 1603: find the cause (try `msiexec /i <msi> /l*v kiro.log`).

How to work on it:

- `python3 -m py_compile light/maude_exe.py` and `bash tests/test-maude-exe.sh` are quick. `make test-fast` takes about 3 minutes.
- The pure helpers (`path_with`, `find_tool`, `git_plan`, `install_plan`) are tested on Linux. Registry, `ctypes` and `winget` code only runs on Windows. `tests/win_tui_smoke.py` runs in CI on `windows-latest`.
- Release: `make tag VERSION=vX.Y.Z` runs `release.yml`, which builds `maude.exe` and the WSL tarball. A run takes a few minutes. Check with `gh run list`.
- Docs site (https://dirkpetersen.github.io/maude/): the Documentation workflow runs only when `docs/**`, `zensical.toml` or the workflow file change. Pages was enabled with `gh api -X POST repos/dirkpetersen/maude/pages -f build_type=workflow`.
- `setup-wsl-maude.ps1` must keep CRLF line endings (check with `file`).
