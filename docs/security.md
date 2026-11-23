# Security

## Sandbox mode (WSL2)

Maude runs the AI tool in a separate WSL2 distro named `Maude`.

- **No automount.** The Windows drives (`/mnt/c`, `/mnt/d`) are not mounted.
- **One shared folder.** Only `~/Maude` is mounted from the host. It maps to `%LOCALAPPDATA%\Maude\Data\Maude`, or to a OneDrive folder if you chose that at install. Projects, `.claude` and kanna data are stored there, so they survive a reinstall.
- **No generic `sudo`.** The user and the AI agent can run only tools that are installed. New system packages come through [mom](https://github.com/dirkpetersen/mom). It can install, update and refresh, but cannot add repositories or run unvetted code.
- **Yolo mode.** Claude Code runs without permission prompts. This is safe only because of the limits above. The marker file is `~/.claude/yolo-mode`. Delete it to turn this off.
- **DANGER-ZONE.txt.** A copy is in `~/Maude/DANGER-ZONE.txt`. It tells you that the AI can read, change and delete anything in the shared folder without asking. Maude restores it at startup if it is missing.
- **Sandbox rules.** `~/.claude/MAUDE.md` is replaced at each `maude` start. It tells the agent to write only to the current project folder. It is not meant for you to edit. Your own settings go in `~/.claude/CLAUDE.md`.

Put into the `Maude` folder only the files that the AI may see and change.

## Weaker modes

These options remove part of the isolation. They are opt-in.

- **Dev mode.** Windows drives, Windows programs and the Windows PATH come back. The Maude user gets `sudo` with a password. The agent runs as the same user, so treat this as root for the agent.
- **Second user.** Adds your own Linux user with `sudo` and the Windows drives for that user only. Do not type its password in a Maude terminal.

The commands are in the [Maude Light README](https://github.com/dirkpetersen/maude/blob/main/light/README.md).

## Windows mode (no sandbox)

`maude.exe` can open a folder outside the sandbox. This is different in these ways:

- The AI tool runs natively on Windows with your full user rights. It can reach all your files, SSH keys and browser data.
- Normal permission prompts stay on. Maude never passes a bypass flag such as `--dangerously-skip-permissions`.
- A folder outside the shared folder never opens in the sandbox. With the Explorer menu (or `maude.exe <folder>`) it opens the red Windows TUI with that folder preselected and a red warning that the AI agents have your full Windows user rights; you start it with Enter. `--windows` starts Claude Code there directly.
- A folder inside the shared folder opens in the sandbox, unless you use `--windows`. If no Maude sandbox is installed, it does not start (exit code 2) unless you use `--windows`.
- Web UI, Git setup, credentials and update are hidden. They need the sandbox.
- The red logo shows that you are in this mode. With a sandbox installed it also says "running outside the sandbox"; without one the caption is left out, because there is no sandbox to be outside of.

See [maude.exe on Windows](windows-exe.md) for details.
