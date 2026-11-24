# maude.exe (Windows launcher)

## What it is

`maude.exe` is a native Windows program built from `light/maude_exe.py` (launcher) and `light/maude.py` (the Textual TUI), packed with PyInstaller (one file, Textual bundled).

With no argument it runs the Maude TUI natively in the current console. Start it from PowerShell and the TUI runs inside that PowerShell. A double-click gives it its own console window. `--tui` does the same as no argument.

This is Windows mode of the TUI. It lists the projects of the Maude share (the same `Projects` folder the sandbox uses; share found as described below, then `\Projects`). The Linux behaviour of `maude.py` is unchanged.

| TUI feature | On Windows (maude.exe) |
|---|---|
| Project list, New, Delete | Works on the share's `Projects` folder (Delete = soft delete to `Projects\.deleted`) plus the outside-sandbox projects (see next section). New always creates the folder in the share |
| Model picker, Thinking picker, AI tool picker, Clear History, Perplexity MCP | Work. Model and tool are stored in `%USERPROFILE%\.maude\maude-model` (`<model>[,<thinking-level>]`, for example `opus,high`) and `%USERPROFILE%\.maude\maude-tool`, the same files as on Linux and macOS. An old `%USERPROFILE%\.maude-model` / .maude-tool is moved there once. A bad value falls back to `opus` / no level / `Claude Code`. The Perplexity flag is the file `%USERPROFILE%\.maude-perplexity`. Image paste into Claude Code works with Alt+V (the tip in the TUI says so) |
| Open Project | Runs the selected AI tool natively on Windows in the project folder, OUTSIDE the sandbox, with normal permission prompts (see below) |
| PowerShell | Suspends the TUI and opens `powershell.exe -NoLogo` in the same window, in the highlighted project folder (else your home folder), with your saved credentials from `~/.maude/clauderc` exported as plain environment variables (the same raw exports Kiro CLI, OpenCode and Crush get; the Claude-only wrapper rules are not applied, so a `claude` started by hand behaves like a normal `claude` with those variables, and `kiro-cli` works too). Ctrl+C inside that PowerShell does not end Maude. Type `exit` to come back to the TUI. It no longer exits `maude.exe` (there may be no PowerShell behind it when started from Explorer); quit with `q`, shown in the footer. In the Linux sandbox the button is still "Command Line" and exits to bash. |
| Set Creds | Works. Opens the same screen as in the sandbox (Paste exports, AWS Bedrock, Azure Foundry, Claude Login). Credentials go to `%USERPROFILE%\.maude\clauderc` in the sandbox format (`export K='V'` lines, same parser and merge code; the file mode 0600 has no effect on Windows, the user profile ACL protects it). The "Default to AWS Bedrock" toggle parks the file as `clauderc.inactive`, as in the sandbox. Bedrock keys also go to `~\.aws\credentials` + `config` (`[bedrock]`), Gemini keys to `~\.gemini\.env`. See "Credentials" below |
| Web UI (kanna), Autostart | Hidden. They need the sandbox (bash helpers, `mom`, `.bashrc`, `fuser`, process groups). Use the sandbox for them. To Github and Setup Git(hub) work natively, see "Git, SSH and commit signing" below |
| Update Maude | Shown. See "Update Maude" below |
| Daily self-update of `maude.py` | Off. The exe is updated by the Update Maude button |
| Credential gate | On, but it never locks anyone out (see "Credentials" below) |

Open Project runs these commands (working directory is the project folder):

| Tool | Command |
|---|---|
| Claude Code | `claude --model <alias> --continue`; if that exits non-zero, `claude --model <alias>`. With a thinking level, `--effort <level>` follows the model. With Clear History only `claude --model <alias>` (plus `--effort`) |
| Kiro CLI | `kiro-cli chat --resume` (then `kiro-cli chat` if that fails); fresh: `kiro-cli chat` |
| OpenCode, Crush | `opencode --continue`, `crush --continue` (fresh: no flag) |

Each tool is found on PATH, else in `%USERPROFILE%\.local\bin` (claude), `%USERPROFILE%\.bun\bin` (bun and its global tools) or `%ProgramFiles%\Kiro-Cli` (Kiro CLI). If it is missing, the TUI is suspended and asks (see "Tool install offers" below). A one-line OUTSIDE-the-sandbox banner is printed before every launch. No bypass flag (`--dangerously-skip-permissions` or similar) is ever passed on Windows. Ctrl+C belongs to the child tool and never kills it. With the Perplexity box ticked, the connector is set up with the real `claude` binary before Claude Code starts.

The sandbox and the `claude` wrapper (with its positional model alias) do not exist on Windows. Folder arguments and the Explorer menu keep their behaviour (next sections).

## Tool install offers (first run on a new PC)

Goal: start `maude.exe` on a new PC and let it set up what is missing. In order:

1. Start: it copies itself to `%USERPROFILE%\.local\bin`, adds the Explorer menu entry and adds that folder to the user PATH (see "First run").
2. The TUI starts without WSL. With no WSL, no `Maude` distro or no cached share, it tries `wsl.exe` once (no console input, 15 s limit), then uses the setup folder rule. `Projects` is created if missing.
3. Open Project with a tool that is not installed: the TUI is suspended and Maude prints what is missing and the exact command, then asks `Install <Tool> now? [Y/n]`. Enter or `y` runs it unattended in the same console: no installer window, no further prompt and no UAC, always per user. Python installers (Kiro CLI, GitHub CLI) check a SHA-256 for integrity only (corruption, truncation); the hash comes from the same vendor origin, so HTTPS is the trust root, not the hash. Ctrl+C while a Python install downloads prints "cancelled", removes the temp files and returns to the menu (once `msiexec` has started, Ctrl+C is ignored and `msiexec` runs to completion; a PowerShell install may stop on Ctrl+C and is then reported as failed); any other error is printed and never crashes the TUI. PowerShell commands run as `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "<command>"` (`-NonInteractive` makes a stray prompt fail instead of hang). The Claude Code and Bun `irm | iex` scripts contain no prompts, but they have not been run under `-NonInteractive` on a real PC yet. `n` prints a message and waits for Enter.

| Tool | Command |
|---|---|
| Claude Code | `irm https://claude.ai/install.ps1 \| iex` |
| Kiro CLI | Python in `maude.exe` (`install_kiro`), not the vendor script: reads `https://prod.download.cli.kiro.dev/stable/latest/manifest.json`, downloads the Windows x86_64 MSI named there, checks its SHA-256, then runs `msiexec /i <msi> /qn /norestart /l*v %TEMP%\maude-kiro-install.log ALLUSERS=2 MSIINSTALLPERUSER=1`. The install lands in `%LOCALAPPDATA%\Kiro-Cli` (user PATH) and needs no admin rights. The MSI ignores `MSIINSTALLPERUSER`: its log says "MSIINSTALLPERUSER property is not valid for UAC compliant package". It still installs per user (its `INSTALLFOLDER` is under `LocalAppDataFolder`), but Windows registers its Programs and Features entry under HKLM. On a non-zero msiexec exit code Maude prints the code and the log path |
| OpenCode | `bun install -g --trust opencode-ai` (same npm package as the WSL side, `light/lib/opencode.sh`) |
| Crush | `bun install -g --trust @charmland/crush` |
| Bun (first, only if OpenCode or Crush is chosen and bun is missing) | `irm bun.sh/install.ps1 \| iex` |

After a successful install Maude adds `%USERPROFILE%\.local\bin` and `%USERPROFILE%\.bun\bin` (when they exist) to the user PATH and to its own PATH, looks the tool up again and starts it. If the install fails or the tool is still not found, Maude prints why and waits for Enter. Other PowerShell windows need a restart to see the new PATH.

`--trust` is a documented `bun add`/`bun install` flag (it lets the packages' postinstall scripts run). Checked with bun 1.4.5: `bun install -g --trust opencode-ai` and `bun install -g --trust @charmland/crush` both install a working `opencode.exe` and `crush.exe` into the global bin folder.

### Git (MinGit, optional, once per Maude run)

On the first Open Project in a Maude run, before the tool check, Maude looks for `git` (PATH, then `%LOCALAPPDATA%\Programs\MinGit\cmd`, `%ProgramFiles%\Git\cmd`, `%LOCALAPPDATA%\Programs\Git\cmd`, so an existing Git for Windows is still used) and asks `Install Git now? [Y/n]`. winget and the big Git for Windows installer are not used. `install_mingit` in `light/maude_exe.py` (same flow as the GitHub CLI):

1. Reads `https://api.github.com/repos/git-for-windows/git/releases/latest`. The tag looks like `v2.56.1.windows.1`. Exactly one asset must match `MinGit-<version>-64-bit.zip` (`-arm64.zip` on ARM). The `-busybox-*` zips never match.
2. Gets the SHA-256 from the asset's `digest` field (`sha256:<hex>`) and from the release notes: Git for Windows publishes its hashes as `<asset name> | <hex>` table rows in the release body, not as a file. If both are present they must agree, else nothing is installed. The zip is downloaded from `https://github.com/git-for-windows/git/releases/download/<tag>/<asset>` and checked. This catches corruption and truncation; the hash comes from the same release, so HTTPS is the trust root.
3. Refuses a zip with an absolute path, a drive or stream (`:`), a `..` part, a symbolic link or no `cmd/git.exe`, unpacks it into a staging folder next to the target and renames that to `%LOCALAPPDATA%\Programs\MinGit`. An existing target is only replaced when it holds nothing but MinGit's own top-level folders (an old or partial install); it is renamed aside first, deleted after the swap and renamed back if the swap fails. Anything else is left alone.
4. Adds `...\MinGit\cmd` to the user PATH (HKCU) and to Maude's own PATH, so `git` works at once in Maude and in the AI tools it starts.

Why not just `git.exe` in `%USERPROFILE%\.local\bin`: `cmd\git.exe` is a launcher. Copied alone it fails with `error launching git: The system cannot find the path specified.` (checked with v2.56.1). It needs the whole tree (`etc\`, `usr\`, `ucrt64\` or `clangarm64\`), so the tree lives in `Programs\MinGit` (the same per-user place as the GitHub CLI) and only its `cmd` folder goes on PATH. A `.cmd` shim in `.local\bin` would not work, because `CreateProcess("git")` and Node's `spawn` do not resolve `.cmd` files.

Size: the 64-bit zip is about 38 MB and unpacks to about 96 MB (not 30-60 MB; it bundles Git Credential Manager). Checked on v2.56.1: `cmd\git.exe --version` works, `git ls-remote https://github.com/git-for-windows/git HEAD` works, and so does `git ls-remote git@github.com:...` (git calls the Windows `C:\Windows\System32\OpenSSH\ssh.exe`, not MinGit's own).

What MinGit does not contain (checked on the 64-bit and arm64 zips): `ssh-keygen.exe`, `gpg.exe`, `scp.exe`, `sftp.exe`. It has `usr\bin\ssh.exe`, `ssh-add.exe`, `ssh-agent.exe` (not on PATH, only `cmd` is) and `git-credential-manager.exe` (in `ucrt64\bin`). A Windows "Setup Git" feature must therefore take `ssh-keygen` and `ssh-agent` from Windows OpenSSH (an optional Windows feature, normally present) and has no `gpg` (use SSH signing or install Gpg4win).

If this PC has no zip (not AMD64 or ARM64), the release has no matching asset, there is no network or the API rate limit (60 requests per hour per IP, unauthenticated) is hit, Maude prints the failure and `https://git-scm.com/download/win` (optional) and continues. A "no", a failed install or Ctrl+C never stops the project from opening. No admin rights, no UAC, no installer window.

### GitHub CLI (optional, once per Maude run)

On the first Open Project in a Maude run, right after the Git offer, Maude looks for `gh` (PATH, `%LOCALAPPDATA%\Programs\GitHub CLI\bin`, `%LOCALAPPDATA%\Programs\GitHub CLI`, `%ProgramFiles%\GitHub CLI`) and asks `Install GitHub CLI now? [Y/n]`. winget is not used (its GitHub.cli package is a machine-wide MSI that raises UAC). `install_gh` in `light/maude_exe.py`:

1. Reads the latest tag from `https://api.github.com/repos/cli/cli/releases/latest`.
2. Downloads `gh_<ver>_windows_amd64.zip` (`arm64` on ARM) and `gh_<ver>_checksums.txt` from `https://github.com/cli/cli/releases/download/<tag>/` into a temp folder and checks the zip's SHA-256. This catches corruption and truncation. It is not proof of origin: the checksum comes from the same release, so HTTPS (the certificate chain) is the trust root.
3. Refuses a zip that has an absolute path, a drive or stream (`:`), a `..` part, a symbolic link or no `bin/gh.exe`, then unpacks it into a staging folder next to the target and renames that to `%LOCALAPPDATA%\Programs\GitHub CLI` (`bin\gh.exe` inside). An existing target is only replaced when it is empty or holds nothing but `LICENSE` and `bin\gh.exe` (an old or partial install): it is renamed aside first, deleted after the swap and renamed back if the swap fails (if that also fails, the message names the `.old-...` folder so you can find it). `LICENSE` and `bin\gh.exe` must be regular files, not folders or links. A target with any other content is left alone and Maude says so. The temp folders are always removed.
4. Adds `...\GitHub CLI\bin` to the user PATH (HKCU) and to Maude's own PATH.

No admin rights, no UAC, no installer window. If this PC has no zip (not AMD64 or ARM64) Maude does not offer an install and prints `GitHub CLI is not installed. Get it from https://cli.github.com (optional).` A "no", a failed install or Ctrl+C never stops the project from opening. Limits: the GitHub API allows 60 unauthenticated requests per hour per IP, so behind a shared NAT the offer can fail with a message; remove the folder to reinstall.

## Outside-sandbox projects

The Windows TUI shows two groups of projects. The red logo tells you that you are in Windows mode. There are three modes in total:

| Mode | Logo | Caption |
|---|---|---|
| Linux sandbox (`maude tui` in WSL) | green | none |
| Windows mode, Maude sandbox installed | red | "running outside the sandbox" |
| Windows mode, no sandbox installed | red | none (there is no sandbox to be outside of) |

"Sandbox installed" means the WSL distro `Maude` is registered. `maude.exe` reads the distro names from `HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss` (value `DistributionName` of each subkey), without starting `wsl.exe`. If WSL was never used (no `Lxss` key) there is no sandbox. If the registry cannot be read, Maude assumes a sandbox exists (the caption stays). With no sandbox, the Features column no longer shows `No Sandbox`, the warning reads "running on Windows in <folder>" instead of "OUTSIDE the sandbox", all projects (also those in the share folder) open natively as before, and `maude.exe <folder-in-share>` prints that no sandbox is installed and exits with code 2 (use `--windows` to run on Windows anyway).

- Outside-sandbox projects come first. They are folders that you opened with "Open in Maude" (or `maude.exe <folder>`) outside the share. They are registered when you open the folder from the TUI (or start it with `--windows`), not when the TUI merely starts on it. A folder inside the share is not added, because it is already listed.
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
| outside the share | none (what the Explorer menu passes) | No question. If the argument is not an existing folder, Maude prints that, waits for Enter on a terminal, exits with code 2 and registers nothing. Otherwise the Windows TUI starts with that folder's row selected and focused, so Enter opens it with the model and AI tool shown in the TUI (change them first if you like). Nothing is written to the registry at start: the folder is registered only when it is opened from the TUI (a registry error is ignored, the row stays listed for the session), so quitting with `q` leaves the registry as it was. A red warning bar above the list says: "Maude: running OUTSIDE the sandbox in <folder>. Maude's AI agents have your full Windows user rights here." (without a sandbox: "running on Windows in <folder>"). The sentence "Press Enter to open it, ..." is shown only while the cursor is on that folder's row; the bar disappears when that row is deleted. A folder that is already listed is selected, not duplicated. `--model <alias>` preselects (and saves) that model in the picker; only the aliases of the picker (`opus`, `fable`, `sonnet`, `haiku`) are used, any other name is ignored in TUI mode. `--model fable` also switches the tool to Claude Code and saves that too, because Fable only exists there. A drive root from Explorer arrives as `C:"` (argv eats the backslash-quote); Maude strips the quote and turns it back into `C:\` (a plain `C:` without a quote keeps its Windows meaning, the current directory of that drive). An empty argument or a lone `"` is an error (exit code 2), never the current folder. `maude.exe --model <alias>` without a folder preselects the model in the TUI too. A folder inside the share is never written to the `Projects` key. Quitting the TUI (`q`) ends `maude.exe`, so the window of the Explorer menu closes with no "Press Enter". If the TUI fails to start, or Textual reports an error (it prints the traceback and `App.run()` returns with `return_code` 1), the window waits for Enter on a terminal and `maude.exe` exits with code 1 |
| outside the share | `--windows` | Launches Claude Code directly in the folder (and registers it in the project list), no TUI |
| outside the share | `--sandbox` | Not started. Prints that the folder must be moved into the share, exit code 2 |
| inside the share | `--windows` | Windows mode too: an explicit choice is honoured even for a folder inside the share |
| any | `--model <alias>` | Model override (`opus`, `sonnet`, `haiku`, `fable` or a full model name; letters, digits, `.`, `_`, `-` only; default: sandbox `~/.maude/maude-model` (`<model>[,<level>]`; the level becomes `--effort`), or `%USERPROFILE%\.maude\maude-model` in Windows mode, else `opus`) |

Windows mode runs `claude --model <alias> --continue` in the folder. If that exits non-zero it runs `claude --model <alias>`. It looks for `claude` on PATH, then `%USERPROFILE%\.local\bin\claude.exe`. It never passes `--dangerously-skip-permissions` or any bypass mode. If Claude Code is not found it prints `irm https://claude.ai/install.ps1 | iex` and exits with code 3. Folder mode installs nothing; only the TUI offers installs.

Share detection order (TUI and folder mode):

1. Registry cache: `HKCU\Software\Maude`, REG_SZ value `Share`. It is used if the folder exists and has a `Projects` folder. No WSL call, no delay.
2. Else the drvfs line for `/home/<user>/Maude` in the distro's `/etc/fstab` (`wsl.exe -d Maude -e cat /etc/fstab`; `\040` decoded to a space). Maude prints "Maude: locating the Maude share (WSL)..." and stores the result in `Share`.
3. Else the setup rule: `%LOCALAPPDATA%\Maude\Data\Maude` if it has a `Projects` folder, else the first OneDrive `Maude` folder that has one, else the local one. This guess is never stored, because it can be wrong. The forced setup flags `-OneDrive` and `-NoOneDrive` cannot be seen at run time.

A failed registry read or write is ignored. To reset the cache, delete the `Share` value in `regedit` or run `maude.exe --uninstall` (it removes `Share` and leaves `HKCU\Software\Maude\Projects` alone).

Security statement: only exe mode, on a folder outside the share, runs outside the sandbox. There Maude's AI agents have your full Windows user rights (all your files, SSH keys, browser data), with normal permission prompts. Everything inside the share stays in the isolated WSL distro. The context menu runs `"<exe>" "%V"` without `--windows`: it shows the red TUI with the warning bar and nothing starts until you press Enter on the selected folder (or pick another row).

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

[Download maude.exe (v0.3.8)](https://github.com/dirkpetersen/maude/releases/download/v0.3.8/maude.exe) (checksum: [maude.exe.sha256](https://github.com/dirkpetersen/maude/releases/download/v0.3.8/maude.exe.sha256)) and run it once, also from the Downloads folder: it copies itself to `%USERPROFILE%\.local\bin`. Check the hash with `Get-FileHash maude.exe`. The exe is unsigned, so SmartScreen shows "Windows protected your PC": choose More info, then Run anyway.

## Build

`.github/workflows/build-maude-exe.yml` builds on `windows-latest` with `pyinstaller==6.11.1` and `textual==8.2.8`: `pyinstaller --onefile --console --name maude --paths light --hidden-import maude --hidden-import maude_exe --collect-submodules textual --collect-data textual --collect-submodules rich --collect-data rich --exclude-module textual.demo --exclude-module tkinter --exclude-module unittest --exclude-module pydoc light/maude_exe.py` (no `--collect-all`: it also packed the `.py` sources of Textual and Rich as loose data files, about 350 extra files that the onefile bootloader has to unpack and the virus scanner has to check on every start). It injects the tag as version (`dev` outside tags). Checks in CI:

1. `python tests/win_tui_smoke.py` from source: headless Textual run with a fake `USERPROFILE`, share and registry. It checks the project list and its order, the Features cells, that Delete on an outside project removes only the registry entry, the logo caption (shown with a sandbox, absent without, red logo in both), `MaudeApp(select=...)` (row selected and focused, warning shown, a folder missing from the registry still listed, `model=` preselected), the hidden buttons, that the PowerShell button launches `powershell.exe -NoLogo` in the right folder and keeps the TUI running, and the Open Project commands (spawns are patched, nothing opens).
2. `maude.exe --version`.
3. `maude.exe --selftest`: starts the TUI headless from the frozen exe. This proves that Textual and its data files are bundled. It also resolves every name in `textual.widgets.__all__` (the slim build has no loose `.py` files), calls `rich.cells.cell_len` on a wide character and pushes and pops the credentials, Git setup, new-project and delete-confirm modals (about 6 s).

It uploads the artifact `maude-exe`. `release.yml` calls it on `v*.*.*` tags and attaches `maude.exe` and `maude.exe.sha256` to the release. The real console behaviour (colours, suspend while a tool runs, new PowerShell window) is not tested in CI.

The Windows setup script `setup-wsl-maude.ps1` removes the cached `HKCU\Software\Maude\Share` value, so a reinstall makes maude.exe look up the share root again.

## Startup time and the "Starting Maude" line

A start shows a black window for a moment. Where the time goes (developer PC, Defender real-time protection on, warm cache; a first start of a new exe is much slower, 6 to 7 s, because the scanner checks the new file):

| Phase | Time |
|---|---|
| 1. Onefile bootloader unpacks to `%TEMP%\_MEI...` and starts Python. No Python code runs yet, so nothing can be printed | about 1.5 s with `--collect-all` (410 files), about 0.5 s now (58 files; a hello-world onefile exe needs about 0.6 s on the same PC) |
| 2. Import of `maude_exe` (stdlib) | about 0.1 s |
| 3. `import maude` (Textual, Rich) | about 0.35 s |
| 4. Table fill in `on_mount` until the first paint | about 2.4 s with 31 projects: `maude.py` `list_projects()` starts one `git config` process per project (`git_remote_origin_url`, about 100 ms each on Windows) |

From the first Python line on, `maude.exe` prints `Starting Maude, please wait ...` (red, plain text if the console has no virtual terminal support) before it imports Textual, for the TUI modes only: no argument, `--tui`, `--model`, and a folder without `--windows` or `--sandbox`. That includes a folder inside the share, which is routed to the sandbox (the line is shown while the share is looked up and then erased). It is not printed for `--version`, `--help`, `--selftest`, `--uninstall`, `--windows`, `--sandbox`, an empty folder argument or when stdout is not a console. The line is erased in place (cursor up, clear line) right before the next thing that writes to the screen: before Textual takes over, before the sandbox session starts, and before any message or prompt (install notices, share lookup, errors). When `main()` ends it never writes, so nothing of a tool's output is erased. A plain-text banner (no virtual terminal support) stays on screen. Phase 1 cannot be covered by text: the bootloader runs before Python. PyInstaller's `--splash` is a Tk image splash (bundles Tcl/Tk, not text) and was not used; `--onedir` avoids the unpacking but is not one file, so it was not used.

`$env:MAUDE_TIMING=1; maude.exe` prints the phase times to stderr and quits at the first paint (opt-in debugging aid; `0` or empty is off). The first figure is the Python child process only: the onefile bootloader parent unpacks before that process exists, so phase 1 is not in it (measure it as wall time of `maude.exe --version` minus that figure).

## Notes from field tests

- A tool is found on PATH first (also entries written as `%VAR%`, which `shutil.which` does not expand), then in `%USERPROFILE%\.local\bin`, `%USERPROFILE%\.bun\bin`, `%ProgramFiles%\Kiro-Cli`, `%LOCALAPPDATA%\Programs\Kiro-Cli` and `%LOCALAPPDATA%\Kiro-Cli`. A user reported that Maude offered to install Kiro CLI although `kiro-cli` was on PATH. Cause confirmed on the developer PC (2026-11-22): `kiro-cli.exe` was in `%LOCALAPPDATA%\Kiro-Cli` (a user PATH entry), and that folder was only added to `tool_dirs()` by `9babfcf`, so a maude.exe whose PATH predates the Kiro install missed it. The code before `9babfcf` returned None from `find_tool('kiro-cli')` with a PATH that had no Kiro entry. The current code finds the exe. Fixed in v0.3.3. If it happens again after v0.3.3, run `where.exe kiro-cli` in the same window and add that folder to `tool_dirs()` in `light/maude_exe.py`.
- Kiro CLI install without admin works (developer PC, 2026-11-22, Windows 11, non-admin user). Maude's command downloads the MSI to `%TEMP%`, checks its SHA-256 from the manifest and runs `msiexec /i <msi> /quiet /norestart`. Result: exit 0, Kiro CLI 3.5.0. The script prints "installed to C:\Program Files\Kiro-Cli\", but the MSI installed per user to `%LOCALAPPDATA%\Kiro-Cli\kiro-cli.exe` and added that folder to the user PATH. Nothing went under Program Files and nothing to the machine PATH.
- The Kiro CLI MSI can fail with exit code 1603 (installer kept in `%TEMP%\kiro-cli-installer-<version>.msi`). At the time Maude only reported "The Kiro CLI install failed." and went back to the menu (now it prints the msiexec code and the `%TEMP%\maude-kiro-install.log` path). Not reproduced on the developer PC. The hypothesis that `/quiet` suppresses UAC, so that on a PC where policy forces a per-machine install a non-admin quiet install fails with 1603, did not apply on the developer PC: `/qn` worked as a non-admin user there. The cause of 1603 elsewhere is still unknown.
- Field test 2026-11-23 (clean PC, non-admin): `install_kiro()` took 17 s, exited 0 with no UAC prompt and installed kiro-cli 3.5.0. `find_tool` found it right away and the user PATH got `%LOCALAPPDATA%\Kiro-Cli`.
- Uninstalling Kiro CLI leaves `%LOCALAPPDATA%\Kiro-Cli` behind (`bun.exe`, `node.exe`, `tui.js`, `data.sqlite3`, about 880 MB). `run\chat-cli-<version>.exe` there is ACL-protected (Everyone: ReadAndExecute), but a normal user can still delete it: `Remove-Item -Force` fails because it first tries to change the file's attributes, while `[IO.File]::Delete('<path>')` works, because the user has FullControl (including FILE_DELETE_CHILD) on the parent folder. After that the folder can be removed.
- The docs site needs Pages source "GitHub Actions" and the v4 `upload-pages-artifact` and `deploy-pages` actions. The v5 pair did not upload the artifact, and Python 3.15 is not used.
- `ensure_user_path` and the `WM_SETTINGCHANGE` broadcast are untested on Windows.


## Credentials (Windows and macOS)

There is no `~/bin/claude` wrapper outside the sandbox, so Maude does what the wrapper does. One helper, `clauderc_path()`, names the file: `~/.azure/clauderc` in the sandbox, `~/.maude/clauderc` on Windows and macOS (created through `maude_dir()`). `CredsEntryScreen`, `merge_clauderc`, `check_credentials` and the Bedrock/Foundry default toggle all go through it.

The file is written as `export K='V'` lines (single quotes, a quote in a value as `'\''`), so bash can `source` it (the sandbox wrapper and the reviewer skills do) without running anything in a value; the reader also accepts the old `"V"` form. The file is created with mode 0600 from the start. An existing file that is not UTF-8/UTF-16 text is never overwritten (the save shows an error); UTF-16 and BOM files are read and rewritten as UTF-8.

`plat_claude_env()` parses that file (never `clauderc.inactive`) and adds the wrapper rules, in the wrapper order, **only when a clauderc exists** (or is parked with the Bedrock flag): a custom `ANTHROPIC_BASE_URL` (dummy `ANTHROPIC_API_KEY`, cloud flags off); a native claude.ai login (cloud variables cleared; `have_claude_login()` follows the wrapper: an expired access token with a valid refresh token still counts); Foundry (`CLAUDE_CODE_USE_FOUNDRY=1` + URL and key give `ANTHROPIC_BASE_URL`, a dummy key and Bedrock off); Bedrock (only when saved through Set Creds, i.e. `CLAUDE_CODE_USE_BEDROCK=1` in the clauderc or parked, plus a `[bedrock]` / `[profile bedrock]` header in `~/.aws/config`: `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_PROFILE=bedrock`, `AWS_DEFAULT_REGION=us-west-2`). With no clauderc the child environment is exactly the inherited one: no dummy key, nothing stripped, and an `~/.aws/config` alone never selects Bedrock. Model ids are not pinned (the wrapper pins ids that change per release; Claude Code uses its own defaults). The result goes to Claude Code and the `claude mcp` calls (`_child_env()`); Kiro CLI, OpenCode and Crush get only the raw clauderc exports (`_child_env(tools=True)`), without the wrapper rewrites. `maude.exe` started from Explorer or with `--windows` loads it too (`maude_exe._creds_env()`, only when a clauderc or clauderc.inactive exists; a load failure prints one warning line).

Saving AWS Bedrock keys replaces only the `[bedrock]` section of `~/.aws/credentials` and the `[profile bedrock]` section of `~/.aws/config`; every other profile, SSO section and comment stays byte for byte, and a one-time `credentials.maude-bak` / `config.maude-bak` copy is made before the first change (on every platform).

The gate (`creds_gate_precheck()` / `creds_gate_decision()`): a project opens without a question when `~/.claude/.credentials.json` shows a login, when credentials are configured, or when `claude auth status` (JSON, 10 s timeout) says `loggedIn: true` (macOS keeps the token in the Keychain, so the file is not enough there). That call runs in a worker thread with a "Checking sign-in..." notice, and a True answer is remembered for the session. If nothing can be confirmed (claude missing, timeout, no answer) the gate does not show. It shows only when claude itself answers "not logged in" and nothing is configured, and even then Cancel still opens the project. The gate only applies to Claude Code, not to the other tools. The Set Creds button is always available; when a Claude login exists the screen says it takes precedence over saved cloud credentials and that `claude /logout` switches to them.
Claude Login runs the real claude with `/login`; the browser opens natively and the sandbox-only "copy the URL into a Windows browser" text is not shown.

## Git, SSH and commit signing (Windows and macOS)

Setup Git(hub) and To Github run natively with plain `git`/`gh` subprocesses (`plat_tool("git")`, `plat_tool("gh")`; on Windows Maude offers MinGit and the GitHub CLI per user, no admin, when the wizard opens and one is missing).

One SSH stack per platform, never mixed: the operating system's own OpenSSH.

- **Windows**: `C:\Windows\System32\OpenSSH` (`ssh`, `ssh-keygen`, `ssh-add`). It is an optional Windows feature that is normally present. MinGit has no `ssh-keygen` and its `usr\bin\ssh-agent` is an MSYS agent (`SSH_AUTH_SOCK`) that Windows OpenSSH cannot talk to, so MinGit's ssh tools are not used. Signing sets `gpg.ssh.program` to the Windows `ssh-keygen.exe`. Independently of signing, `core.sshCommand` is set to the Windows `ssh.exe` only when git is MinGit (Git for Windows brings its own ssh) and you have no `core.sshCommand`, `GIT_SSH` or `GIT_SSH_COMMAND`. If System32 OpenSSH is missing, Maude says "Install the OpenSSH Client optional feature (Settings > System > Optional features)" and never falls back to a bare `ssh-keygen` from PATH (that could be an MSYS one). Pushes from Maude use `GIT_SSH_COMMAND` with the same `ssh.exe` and batch mode.
- **Agent**: the Windows `ssh-agent` service is disabled by default and enabling it needs an administrator, so Maude only detects it (`ssh-add -l`), never starts it. Running: Maude never asks for the passphrase at start; the service already manages the key (it keeps loaded keys across reboots until `ssh-add -d`). A passphrase key that is not loaded yet is unlocked on demand when To Github needs it, or loaded by Setup Git(hub); an unencrypted key is never added silently on Windows. Not running: an unencrypted key (the wizard's default: leave the passphrase blank) needs no agent and just works; a passphrase key cannot be unlocked and To Github says so.
- **macOS**: `/usr/bin` OpenSSH with Apple's agent. `ssh-add --apple-use-keychain`, and `AddKeysToAgent yes` / `UseKeychain yes` (with `IgnoreUnknown UseKeychain`) are appended to `~/.ssh/config` only if the file mentions neither option.
- **Passphrase**: ssh-add and `ssh -T` get it through `SSH_ASKPASS` + `SSH_ASKPASS_REQUIRE=force` and a static helper (`.cmd` with delayed expansion on Windows, `.sh` on macOS) that prints `%MAUDE_ASKPASS_PW%` / `$MAUDE_ASKPASS_PW` from the child's environment. The passphrase is never written to disk.
- **Signing is only switched on after a test**: before writing `commit.gpgsign true` the wizard signs a temp file exactly as git will (`ssh-keygen -Y sign -n git -f <pub>`, stdin closed, empty-passphrase askpass, 8 s timeout, process tree killed on timeout). If that fails (passphrase key that no agent holds, no OpenSSH, OpenSSH too old) signing stays off and the wizard says why, because a broken setup would hang every `git commit`. An existing signing setup (another `user.signingkey`, `gpg.format` other than ssh, `commit.gpgsign` with your default key) is never overwritten. A passphrase key passes the test only while an agent holds it: if the Windows ssh-agent service is not running after a reboot, commits fail to sign until you start the ssh-agent service and run `ssh-add` (or set `commit.gpgsign false`); on macOS Maude reloads the key from the keychain at TUI start. The test kills the whole process tree on timeout (POSIX: its process group). OpenSSH older than 8.4 is refused for passphrase entry (`SSH_ASKPASS_REQUIRE`).
- **Signing**: SSH signing, no gpg at all: `gpg.format ssh`, `user.signingkey <...>/.ssh/id_ed25519.pub`, `commit.gpgsign true`, and the public key is registered with `gh ssh-key add - --type signing`. `gh auth login --web --skip-ssh-key --scopes admin:ssh_signing_key` supplies the scope, or `gh auth refresh -h github.com -s admin:ssh_signing_key` when gh is already logged in (the sandbox keeps `write:gpg_key` and gpg). `admin:` rather than `write:` because that is the scope gh itself names for `gh ssh-key add --type signing`; the narrower `write:ssh_signing_key` was not verified with gh. The Re-authenticate button always does a full `gh auth login` (so it can switch accounts); `gh auth refresh` is used only after an upload failed for the missing scope. Pushes keep your `GIT_SSH`, `GIT_SSH_COMMAND` or `core.sshCommand` (read as git sees it for the project: local, global, system) if you set one; the plain System32 `ssh.exe` value that Setup Git writes itself does not count, so pushes still get BatchMode. A key GitHub reports as "already in use" (another account) is an error, not a success. With the `.pub` path, OpenSSH 9 signs with the agent key or, without an agent, with the matching private key file (checked on Windows with the agent running and with the key only on disk).
- The wizard does not run `mom install keychain` and does not touch `~/.bashrc` here (`fix_bashrc_orphan_fi` stays sandbox-only). `gh auth login --web` opens the browser itself, so the xdg-open filter and the "copy the URL" text are sandbox-only.

Tested by `tests/test-git-native.sh` (pure helpers, temp HOME and `GIT_CONFIG_GLOBAL`, a real SSH-signed commit when git and ssh-keygen exist) and `tests/win_tui_smoke.py` (opens the wizard and the push screen). Not verified: a real `gh auth login`, a real key upload or push to GitHub, and anything on macOS.

## Platform layer and macOS (prepared, untested)

`light/maude.py` is the single source for the TUI on every platform. It derives `PLATFORM` (`windows`, `macos` or `linux-sandbox`) from `sys.platform`; the buttons that depend on the platform come from the `FEATURES` map (one line per feature). Today Windows and macOS hide two widgets: Web UI and Autostart (Set Creds, Update Maude, To Github and Setup Git(hub) are on). Web UI and Autostart stay hidden on both platforms for good; the later packages flip the other lines.

macOS is prepared and has never been run on a Mac. What exists: a red logo without the caption (no sandbox), projects in `~/Maude/Projects`, outside projects in `~/.maude/projects.json` (a JSON list of folder paths, shown first), and Open Project with the real `claude` (`--model <model>`, found in `~/.local/bin`, PATH, `/opt/homebrew/bin` or `/usr/local/bin`) instead of the sandbox wrapper. A missing tool is offered, like on Windows but with the macOS installers. Everything is user level: no sudo anywhere, Maude never installs Homebrew, and each install shows the exact command and asks `[y/N]` (default no) on the console while the TUI is suspended. The plan is the pure `mac_install_plan(tool, have_brew)`; the one runner is `mac_ensure_tool`, which re-resolves the tool with `plat_tool` afterwards.

| Tool | Without Homebrew | With Homebrew |
|------|------------------|---------------|
| Claude Code | `set -o pipefail; curl -fsSL https://claude.ai/install.sh \| bash` (binary in `~/.local/bin`) | same |
| Kiro CLI | `set -o pipefail; curl -fsSL https://cli.kiro.dev/install \| bash -s -- --no-modify-path` (on macOS the script first installs a DMG: it copies `Kiro CLI.app` to `/Applications` and opens it, and does not create `~/.local/bin/kiro-cli` itself; only its zip fallback does. If the command is not found afterwards Maude says to open Kiro CLI.app once; not tried on a Mac) | same |
| Git | `xcode-select --install`: opens an Apple dialog (Command Line Tools, about 1 GB); Maude waits for Enter after you finished it | `brew install git` |
| GitHub CLI, OpenCode, Crush | only a hint with the command and the vendor URL (cli.github.com, opencode.ai, github.com/charmbracelet/crush) | `brew install gh` / `brew install opencode` / `brew install charmbracelet/tap/crush` |

Where it fires: Open Project (Claude Code or the selected tool; git and gh once per Maude run, silently when there is no installer), and the Setup Git(hub) wizard (git and gh, asked again even after a decline). Apple's `/usr/bin/git` stub, which only opens the install dialog, does not count as an installed git until `xcode-select -p` succeeds: `plat_tool("git")` returns None for it (only a positive result is cached per process, so a Command Line Tools install done outside Maude is noticed), and `git_exe()` then gives a path that does not exist instead of the bare name `git`, so Maude's own git calls skip the stub (`gh` itself runs git from `PATH` and can still hit it). The curl installers run with `pipefail`, so a failed download is reported as a failed install. Tested by `tests/test-mac-install.sh` (pure plan and runner with every side effect injected; no installer is ever run). Not verified: any of these installers on a real Mac. `from __future__ import annotations` keeps the file importable by the 3.9 system Python on macOS.

## Status and next steps (v0.3.4)

Released: v0.3.4 (exe, WSL tarball). It adds validated `maude.py` self-updates and `checksums.txt` for pinned installs. v0.3.3 had the Kiro lookup fix (`9babfcf`) and the GitHub CLI offer.

Open items:

1. Partly checked on the developer PC: `%USERPROFILE%\.local\bin\maude.exe` exists, the user PATH has the entry and `HKCU\Environment\Path` kept its type REG_EXPAND_SZ. Still open: run `maude.exe` from Downloads on a clean PC, check the printed messages and the new-window `maude` command.
2. Kiro CLI offered although installed: done, confirmed fixed in v0.3.3 (developer PC, 2026-11-22). `kiro-cli.exe` was in `%LOCALAPPDATA%\Kiro-Cli` (a user PATH entry). With a PATH that has no Kiro entry, the code before `9babfcf` returns None from `find_tool('kiro-cli')`, and the current code finds the exe. The non-admin install through Maude's command also worked (exit 0, Kiro CLI 3.5.0).
3. GitHub CLI offer (`gh_plan`, `find_gh`, `install_gh`, `offer_gh` in `light/maude_exe.py`): v0.3.3 used winget (machine MSI, UAC). Now a per-user zip install, verified against the real GitHub endpoints (v2.105.0 unpacks to `bin\gh.exe` and runs). Next: check on a non-admin PC that the offer installs without any prompt and `gh` is found right after.
4. The Kiro MSI exit code 1603: not reproduced on the developer PC (the non-admin quiet install succeeded). The MSI (3.5.0) was inspected: its Property table has no `ALLUSERS`, its summary says no elevation is required and `INSTALLFOLDER` is `LocalAppDataFolder\Kiro-Cli`, so the package itself is per user; a forced per-machine install (policy, or the vendor script run elevated) is the likely cause. `install_kiro` now passes `ALLUSERS=2 MSIINSTALLPERUSER=1`, `/qn` and `/l*v %TEMP%\maude-kiro-install.log` and prints the code and the log path on failure. Not yet run on an affected PC: if 1603 still happens there, read that log.

How to work on it:

- `python3 -m py_compile light/maude_exe.py` and `bash tests/test-maude-exe.sh` are quick. `make test-fast` takes about 3 minutes.
- The pure helpers (`path_with`, `find_tool`, `mingit_plan`, `mingit_release`, `install_plan`, `kiro_package`, `msiexec_cmd`, `gh_release`, `checksum_for`, `unsafe_zip_names`) and the Kiro/GitHub CLI/MinGit download flows (fake network, fake `msiexec`) are tested on Linux. Registry, `ctypes` and real `msiexec` calls only run on Windows. `tests/win_tui_smoke.py` runs in CI on `windows-latest`.
- Release: `make tag VERSION=vX.Y.Z` runs `release.yml`, which builds `maude.exe` and the WSL tarball. A run takes a few minutes. Check with `gh run list`.
- Docs site (https://dirkpetersen.github.io/maude/): the Documentation workflow runs only when `docs/**`, `zensical.toml` or the workflow file change. Pages was enabled with `gh api -X POST repos/dirkpetersen/maude/pages -f build_type=workflow`.
- `setup-wsl-maude.ps1` must keep CRLF line endings (check with `file`).

## Update Maude (Windows and macOS)

The **Update Maude** button opens the same log screen as in the sandbox, but the steps run in Python (`native_update()` in `maude.py`, helpers in `maude_exe.py`). No network access in the tests: every download goes through an injectable `urlopen` (`tests/test-update-native.sh`).

Windows, in this order, all streamed into the log:

1. **maude.exe.** `stage_exe()` (alias `update_exe`) reads `https://api.github.com/repos/dirkpetersen/maude/releases/latest`. The release must list the assets `maude.exe` and `maude.exe.sha256` (`release.yml` publishes both; the checksum file is `sha256sum` format). The download URLs are rebuilt from the tag, never taken from the JSON. If the tag (`vX.Y.Z`) is newer than `VERSION`, the exe is downloaded to `maude.exe.update-<pid>` next to the installed exe (`%USERPROFILE%\.local\bin\maude.exe`), SHA-256 checked (`download_verified`) and must start with `MZ`. **Nothing is renamed at this point**: a PyInstaller onefile exe reopens itself by path for lazy imports, so the running TUI must not be swapped under it. The staged tag is remembered in the process (a second Update says "already updated, press Reload UI"; a lock refuses two runs at once). Pre-release tags are ignored, and a build whose `VERSION` is not a plain `vX.Y.Z` (`dev`, `v1.2.3-rc1`) counts as a development build: it is never replaced, the log says so and nothing is downloaded. A same or older release changes nothing, so a newer build is never downgraded. HTTP 403/429 from GitHub prints "GitHub rate limit, try later." Python (not a frozen exe) is skipped. With `--no-install` (a portable exe) the exe that runs is the update target, not the installed one, so the version compared is the one that is replaced.
2. **Claude Code**: `claude update`, if `claude` is found.
3. **Sandbox**: if the `Maude` WSL distro exists, `wsl.exe -d Maude -- bash -lc "maude update"` (with `WSL_UTF8=1`; child processes run with `CREATE_NO_WINDOW`).

A failed step is shown with a warning and counted; the next step still runs. Once an exe is staged, Close is disabled and Escape reloads too (so the TUI is not left running on a swapped-out exe path). If the TUI ends any other way (`q`, Ctrl+Q), the staged file is deleted and nothing changes; if the console window is closed, the leftover file is removed at a later start (after one day). Reload exits the TUI with code 42; `run_tui()` then calls `apply_staged()`: it re-hashes the staged file, renames the exe to `maude.exe.old-<pid>` (`.old-<pid>-<n>` if that name is taken and cannot be removed) and moves the new one in (the old one is put back if that fails; then it says so, keeps the current exe and does not restart). Then it starts the exe again with the same folder, model and `--no-install` arguments and waits for it, so the console stays owned by one running process; there is no `execv` on Windows. Reload is only offered when something was staged (or, on macOS, `maude.py` was replaced).

At every frozen start `maude.exe.old-*` next to the running exe and next to the installed exe is removed best effort (exact prefix; one that still runs stays for later), plus staged downloads older than a day. `self_install` never copies an older running exe over a newer installed one, and a `dev` build never over a release: it runs `<installed> --version` (short timeout) to read the installed version. That was chosen over a stamp file, which goes stale when the exe is copied by hand; the extra start only happens when the running exe is not the installed one and differs from it.

macOS: `fetch_maude_py()` downloads `maude.py` from `main` with the same checks as the daily sandbox self-update (`valid_update()`, then `import_check_update()` on a temp file) and replaces the running copy, then `claude update` runs. **Reload UI** exits with 42. Run as a script (`python3 maude.py`), the `__main__` block of `maude.py` handles 42 itself with `os.execv`. A macOS launcher that imports `maude` and calls `MaudeApp().run()` itself must loop on 42 (start the app again, in a fresh process so the new `maude.py` is loaded). There is no such launcher yet, and macOS is untested.
