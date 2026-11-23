# Maude

Maude is a ready-to-run place for agentic coding. You can run it in two ways. Pick one.

## Run Maude on Windows (no sandbox)

In this mode, `maude.exe` runs on Windows. The AI tool runs on your Windows account. WSL is not needed.

!!! warning "No sandbox"
    AI agents run with your full Windows user rights. They can reach all your files, SSH keys and browser data. The normal permission prompts of the AI tool stay on. Maude never turns them off.

1. Download `maude.exe` from the [latest GitHub release](https://github.com/dirkpetersen/maude/releases/latest). The exe is not signed. If Windows shows "Windows protected your PC", select **More info**, then **Run anyway**.
2. Open PowerShell. Run the file once. Go to the folder that has the file first, for example Downloads.

    ```powershell
    cd $env:USERPROFILE\Downloads
    .\maude.exe
    ```

3. On the first run, Maude does these steps. No admin rights are needed.
    - It copies itself to `%USERPROFILE%\.local\bin`.
    - It adds that folder to your user PATH.
    - It adds "Open in Maude" to the Explorer folder menu. Shift+right-click a folder to see it.
4. Open a new PowerShell window and type:

    ```powershell
    maude
    ```

The Maude TUI opens. Select a project and press **Open Project**. If the AI tool is not installed (Claude Code, Kiro CLI, OpenCode or Crush), Maude offers to install it on first open. It also offers Git for Windows (winget) and the GitHub CLI (official zip), both optional, per user, with no admin prompt.

For all details, see [maude.exe on Windows](windows-exe.md).

## Run Maude in the sandbox (WSL2)

In this mode, the AI tool runs in an isolated Ubuntu distro called `Maude`. It can see only one shared folder. WSL2 must be installed. If it is not, see [Windows setup](windows-setup.md) and the admin step in the [Maude Light README](https://github.com/dirkpetersen/maude/blob/main/light/README.md#install).

Open a normal PowerShell (not as administrator) and run this one command:

```powershell
curl.exe --ssl-no-revoke -fSLo $env:TEMP\setup-wsl-maude.ps1 https://raw.githubusercontent.com/dirkpetersen/maude/main/light/setup-wsl-maude.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\setup-wsl-maude.ps1
```

The first run takes 3 to 5 minutes. A later reinstall takes about 30 seconds.

Options to add at the end of the command:

| Option | Effect |
|--------|--------|
| `-Noble` | Use Ubuntu 24.04 instead of 26.04 |
| `-OneDrive` | Put the shared folder in OneDrive |
| `-NoOneDrive` | Put the shared folder in `%LOCALAPPDATA%\Maude\Data\Maude` |
| `-Release <tag>` | Use a tagged release and check the SHA-256 of each file |
| `-Admin` | First-time WSL2 install (needs an elevated PowerShell) |
| `-NoDefenderExclusion` | With `-Admin`: do not add the Windows Defender exclusion |

To start Maude:

- Open the **Maude** profile in Windows Terminal, or use the desktop shortcut. The TUI starts by itself.
- In a Maude shell, type `maude` to see the help, or `maude <project-name>` to open a project.
- Type `menu` to open the TUI again.

See [TUI](tui.md), [Command line](cli.md) and [Security](security.md).

## What is Maude

Maude Light is a WSL2 sandbox for agentic AI coding. A normal WSL distro mounts all your Windows drives. Maude turns that off. It shares one folder only, `%LOCALAPPDATA%\Maude\Data\Maude`. The AI tool can read and change only that folder and the tools that are already installed.

Maude has a full-screen TUI and a simple command line. The same TUI also runs on Windows as `maude.exe`.

## Pages

- [maude.exe on Windows](windows-exe.md): the Windows launcher, outside-sandbox projects, registry keys.
- [Windows setup](windows-setup.md): install Windows Terminal and WSL.
- [TUI](tui.md): layout, buttons and settings.
- [Command line](cli.md): the `maude` commands.
- [Security](security.md): the sandbox model and the Windows mode.
- [Multi-CLI reviewers](multi-cli-reviewers.md): second opinions from other AI tools.
