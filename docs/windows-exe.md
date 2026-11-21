# maude.exe (Windows launcher)

## What it is

`maude.exe` is a small native Windows program built from `light/maude_exe.py` (stdlib only, PyInstaller one-file). It is a launcher. It is not a port of `maude.py`.

Reason: every feature of `maude.py` (Textual TUI, `claude` wrapper, `~/Maude/Projects`, kanna Web UI, `fuser`, `os.killpg`, bash helpers in `~/.local/lib/maude`, `.bashrc` edits, ssh-agent, gpg, `mom`) needs Linux. Maude Light already runs all of this inside the WSL sandbox. So the exe only starts the sandbox. `maude.py` is not changed, and Textual is not bundled.

| maude.py feature | On Windows (maude.exe) |
|---|---|
| Textual TUI, project list, model picker, git wizard, kanna Web UI, creds, update | Runs unchanged inside WSL. `maude.exe` with no argument starts `maude tui` in the WSL distro |
| Open project in Claude Code | `maude.exe "<folder>"` runs `claude <model> --continue` in the WSL distro (see below) |
| `fuser`, `killpg`, bash helpers, `.bashrc`, `mom`, `/tmp` | Not used by the exe. Stay inside WSL |
| Native Windows `claude.exe` | Not used (disabled) |

## Open question: what does "Open in Maude" do?

Decision: option (b). The folder goes to the WSL Maude sandbox.

- The sandbox only sees `%LOCALAPPDATA%\Maude\Data\Maude` (mounted as `~/Maude`). A folder inside it opens in `~/Maude/<relative path>`. A folder outside it gets a clear message and is not opened.
- Security trade-off: the sandbox exists so the agent cannot touch the rest of the Windows disk. Option (b) keeps this. The cost: you must move or copy a project into the share first.
- Alternative (a): run Windows-native `claude.exe` directly in any folder. It works everywhere, but the agent then runs in bypass mode with your full Windows user rights (all your files, SSH keys, browser data). Not implemented. It could be added later as an explicit opt-in.

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

## Build

`.github/workflows/build-maude-exe.yml` builds on `windows-latest` (`pyinstaller --onefile --console`) and uploads the artifact `maude.exe`. It needs no Textual data files. The exe is not signed: Windows SmartScreen or antivirus can warn.
