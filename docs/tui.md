# TUI

The Maude TUI is a full-screen Textual program. In the sandbox it starts by itself when you open a terminal. Type `maude tui` or `menu` to start it again.

![The Maude TUI](screenshot.png)

On Windows, `maude.exe` runs the same TUI. Some buttons are hidden there. See [maude.exe on Windows](windows-exe.md).

## Layout

The left side has the logo and the settings. The right side has the project list. The buttons are at the bottom.

Projects are sorted by last change, newest first. After you close a project, the cursor returns to it.

## Settings (left side)

| Control | What it does |
|---------|--------------|
| Autostart | The TUI starts at every new terminal. Clear the box to opt out. This creates `~/.maude-tui-disabled`, and you get the text banner instead. |
| Clear History | Off by default. When ticked, the next project you open starts Claude Code with no earlier conversation (no `--continue`). It is reset after each launch. |
| Perplexity MCP | Off by default. Saved in `~/.maude-perplexity`. When on, Maude adds the Perplexity Computer connector to Claude Code when you open a project, and runs `claude mcp login`. Clearing the box removes the connector. A connector that you added by hand is not touched while the box was never ticked. |
| Model | `opus` (default), `fable`, `sonnet` or `haiku`. Saved in `~/.maude/maude-model` together with the thinking level (`opus,high`). |
| AI tool | `Claude Code` (default), `Kiro CLI`, `OpenCode` or `Crush`. Saved in `~/.maude/maude-tool`. |
| Thinking | `auto` (default, Claude Code decides), `low`, `medium`, `high`, `xhigh` or `max`. Passed to Claude Code as `--effort`. Saved as the second field of `~/.maude/maude-model`. |

The model and thinking level apply to Claude Code only. The other tools start on their own default model. `fable` exists only in Claude Code. If you pick `fable`, Maude selects Claude Code. If you pick another tool while `fable` is set, Maude changes the model to `opus`.

Kiro CLI asks for a device-code sign-in on first use. A missing tool is installed by `maude update`.

## Buttons

| Button | What it does |
|--------|--------------|
| Open Project | Starts the AI tool in the project folder. For Claude Code, Maude uses `--continue`. |
| + New | Creates a project. Spaces become hyphens. Git is set up for you. |
| Web UI | Starts or stops the [kanna](https://github.com/jakemor/kanna) web UI at `http://127.0.0.1:3210`. |
| Setup Git | A 4-step wizard. See below. Also available as `maude github`. |
| Set Credentials | Opens a window where you paste `export` lines for Anthropic, Foundry, AWS or Azure. Maude saves them to `~/.azure/clauderc` (mode 0600). |
| Command Line | Closes the TUI and returns to the shell. |

## Keys

| Key | Action |
|-----|--------|
| `Enter` | Open the selected project |
| `n` | New project |
| `d` | Delete the selected project. It moves to `Projects/.deleted/`. |
| `q` | Quit to the shell |

## Credentials

If no LLM credentials are set, the TUI shows the credentials window at start. Maude checks `ANTHROPIC_API_KEY`, `ANTHROPIC_FOUNDRY_API_KEY`, `~/.aws/credentials` and `~/.azure/clauderc`. The Web UI button opens the same window when credentials are missing.

## Setup Git

The wizard has four steps:

1. Look up your GitHub user name to fill in name and email.
2. Find or make an ed25519 SSH key, and test it with `ssh -T git@github.com`.
3. Find or make a GPG key, and test it with a signed message.
4. Set `user.name`, `user.email` and `init.defaultBranch=main`. Install `keychain` and add it to `~/.bashrc`.

You paste the public keys into GitHub yourself. The wizard needs no `gh` login.

## Updates

The TUI downloads a new `maude.py` from GitHub once per day, at or after noon. The new version runs at the next start.
