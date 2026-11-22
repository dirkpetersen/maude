# Command line

Run these commands in a Maude shell (the sandbox). Type `maude` or `maude help` to see the list.

| Command | What it does |
|---------|--------------|
| `maude <project>` | Opens a project. Creates it if it does not exist. |
| `maude list` (or `ls`) | Shows your projects. |
| `maude delete <project>` (or `rm`) | Asks for confirmation, then moves the project to `Projects/.deleted/`. It is not a permanent delete. |
| `maude tui` | Starts the [TUI](tui.md). |
| `maude web` | Starts the kanna web UI. It prints `http://127.0.0.1:3210`. Data is in `~/Maude/.kanna/data`. |
| `maude github` | Starts the Git, SSH and GPG wizard. |
| `maude update` | Updates Claude Code, the Maude TUI and kanna. Installs Kiro CLI and Crush if they are missing. |
| `maude help` | Shows the help. |

## Projects

A project name can use letters, digits, dots, dashes and underscores. Projects are folders in `~/Maude/Projects`. On Windows this is the shared folder. Press Tab to complete a project name.

`maude <project>` creates the folder and runs `git init` for a new project. Then it starts Claude Code. If Claude Code is not installed, Maude asks to install it. A warning shows when a project has a `.claude/` folder or a `CLAUDE.md` file, because these can change the sandbox rules.

Maude updates Claude Code about once a week.

## Other commands

| Command | What it does |
|---------|--------------|
| `menu` | Opens the TUI again. |
| `mom install -y <package>` | Installs a system package without `sudo`. See [mom](https://github.com/dirkpetersen/mom). |

## Windows

`maude.exe` is a different program with its own options. See [maude.exe on Windows](windows-exe.md).
