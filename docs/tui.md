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
| Agent | `Claude Code` (default), `Codex`, `Antigravity`, `Grok CLI`, `Kiro CLI`, `GitHub Copilot CLI`, `OpenCode` or `Crush`. Saved in `~/.maude/maude-tool`. |
| Model | The models of the chosen agent. The first entry is always `auto`: Maude passes no model flag and the agent uses its own default. The list changes when you pick another agent (see below). A new user starts on Claude Code with `opus`; every other agent starts on `auto`. Claude Code's choice is saved in `~/.maude/maude-model` together with the thinking level (`opus,high`; `,high` means Claude Code `auto`). The model of every other agent is saved in `~/.maude/agent-models.json`, and comes back when you pick that agent again. |
| Thinking | `auto` (default, the agent decides), `low`, `medium`, `high`, `xhigh` or `max`. Only for agents with a reasoning-effort setting: Claude Code, Antigravity and Kiro CLI (`--effort`) and Codex (`-c model_reasoning_effort=...`; Codex has no `max`, so Maude sends `xhigh`). For the other agents the column is greyed out. Saved as the second field of `~/.maude/maude-model`. |

Claude Code `auto` in the sandbox means no model word, and then the `~/bin/claude` wrapper picks its own default (haiku), which is not the same as `opus`.

The columns are in the order Agent, Model, Thinking. With eight agents the left side is tall. It fits a terminal of about 35 rows or more. In a smaller one (80x24, or the Windows Terminal default 120x30) it has a scrollbar: use the mouse wheel, or move with Tab and the arrow keys, which scroll the focused control into view.

### Agents

| Agent | Command | Model flag | Resume | Thinking |
|-------|---------|------------|--------|----------|
| Claude Code | `claude [model]` | model as the first word (`--model` outside the sandbox) | `--continue` | `--effort` |
| Codex | `codex` | `-m` | `resume --last` | `-c model_reasoning_effort=` |
| Antigravity | `agy` | `--model` | `-c` | `--effort` |
| Grok CLI | `grok` | `-m` | `-c` | none |
| Kiro CLI | `kiro-cli chat` | `--model` | `--resume` | `--effort` |
| GitHub Copilot CLI | `copilot` | `--model` | `--continue` | none |
| OpenCode | `opencode` | `--model provider/model` | `--continue` | none |
| Crush | `crush` | `--model provider/model` | `--continue` | none |

If an agent exits with an error code, Maude does not paint the TUI over the message. A normal quit (exit code 0, Ctrl+C: 130, a SIGINT/SIGTERM signal, or the Windows Ctrl+C status) is not an error. When a resume flag was used (agents that can fail for want of a session), Maude first starts the agent once more without it, keeping the model. If that run fails too, or no resume flag was used, it prints `<agent> exited with code N` and waits for Enter, adding "try Model: auto" when a model was passed and "Thinking: auto" when `--effort` was passed. A missing agent: in the sandbox `maude update` installs all eight; on Windows and macOS Maude offers to install it when you open a project. Kiro CLI asks for a device-code sign-in on first use.

### Model lists

The list for each agent comes from a JSON file, never from the code alone:

1. Built in: a copy of [`light/maude-models.json`](https://github.com/dirkpetersen/maude/blob/main/light/maude-models.json) inside `maude.py` (used when nothing else is available, for example `maude.exe` offline).
2. `~/.maude/models-default.json`: that same file, downloaded in the background once per day (at or after noon, like `maude.py`). A download is checked first (valid JSON, version 1, every agent has a `models` list) and written atomically. It takes effect at the next start, or at once when it arrives while the TUI runs.
3. `~/.maude/models.json` (optional, yours): per agent, `"models"` replaces the list (`[]` means `auto` only) and `"extra"` adds entries.

```json
{"agents": {"Codex": {"extra": [{"label": "mine", "id": "gpt-x"}]},
            "Crush": {"models": [{"label": "opus", "id": "anthropic/claude-opus-5-5"}]}}}
```

`label` is shown and `id` is what the agent's command line gets. A label must be printable text without `[` or `]`, and is cut to 9 characters (the column width). Ids are checked: letters, digits and `._:/@+#-` only, and for Claude Code only `fable`, `opus`, `sonnet`, `haiku` or a `claude-*` id. `auto` is built in: it is always first and never listed twice. A corrupt file, a bad entry or an unknown `version` is ignored. If the saved model is not in the list of the chosen agent, the agent's default is selected (`opus` for Claude Code, `auto` for the others).

Claude Code, Kiro CLI, GitHub Copilot CLI and OpenCode list `fable`, `opus`, `sonnet`, `haiku` first, then vendor models (Kiro CLI: OpenAI GPT-5.6 and Grok 4.7; GitHub Copilot CLI: OpenAI GPT-6.x and GPT-5.6). Codex lists OpenAI models and Grok CLI lists `grok-4.x`. Antigravity lists `gemini-4-hydrogen-high` and `gemini-3.8-flash-high`: its ids carry the thinking level, so leave Thinking on `auto` for it, or add other variants with `"extra"` (`agy models` lists them). Crush is `auto` only for now: its model ids are not confirmed, so add your own with `"extra"`, or wait for the daily file. Grok CLI has no Claude models because they need a `[model.<id>]` entry in `~/.grok/config.toml` that Maude does not write.

### Outside the sandbox

In `maude.exe` (Windows) and on macOS, the setting `native_only_approved` in `~/.maude/settings.json` is on by default. Then only Kiro CLI and GitHub Copilot CLI can be used: the other agents are greyed out in the Agent column (not hidden), and if the saved agent is not allowed, Kiro CLI is used for that session. Nothing is overwritten, so your saved agent and model come back when the restriction is off. In the sandbox the setting has no effect. Outside the sandbox the Settings window has the checkbox "Only allow Kiro.dev and Github Copilot outside the Sandbox" for it (on by default); Save writes the file and calls `MaudeApp.apply_agent_restrictions()`, which refreshes the column at once (editing the file by hand and calling the same method works too). `load_settings()` and `save_settings()` (atomic, mode 0600) read and write the file; a corrupt file gives the defaults and is kept as `settings.json.bak` by the next save.

## Buttons

| Button | What it does |
|--------|--------------|
| Open Project | Starts the agent in the project folder with the chosen model. It resumes the last session when there is one (`--continue` for Claude Code). |
| + New | Creates a project. Spaces become hyphens. Git is set up for you. |
| Web UI | Starts or stops the [kanna](https://github.com/jakemor/kanna) web UI at `http://127.0.0.1:3210`. |
| Setup Git | A 4-step wizard. See below. Also available as `maude github`. |
| Settings | Opens the Settings window (formerly "Set Creds"). Outside the sandbox it starts with the checkbox "Only allow Kiro.dev and Github Copilot outside the Sandbox" (on by default; Save applies it at once). Five tabs: **Paste** (paste `export` lines for Anthropic, Foundry, AWS or Azure), **Anthropic** (Claude Login button, an `ANTHROPIC_API_KEY` field and "Use this API key": checked = Maude starts Claude with this key, even if you are logged in; unchecked = the key is kept but Claude Login / Bedrock / Foundry are used), **Bedrock**, **Foundry** and **OpenCode** (model URL, model name and api_key of an OpenAI-compatible endpoint, plus a Remove button; OpenCode with Model `auto` then uses it; `http://` is only accepted for localhost). Credentials are saved to `~/.azure/clauderc` (mode 0600); the Anthropic key and the OpenCode key are kept in their own files `~/.maude/anthropic-key` and `~/.maude/opencode-key`, the OpenCode endpoint goes to `~/.config/opencode/opencode.json`, other settings to `~/.maude/settings.json`. **API key mode is Windows/macOS only for now.** In the sandbox the key field and "Use this API key" are greyed out with a note, and that tab saves nothing: use Claude Login, or Paste an `export ANTHROPIC_API_KEY=...` line. When you have both Bedrock and Foundry credentials, a "Default to AWS Bedrock" checkbox appears: ticked, your Foundry or other backend settings are parked (`clauderc.inactive`) and Bedrock is used. While it stays ticked, anything you save on the Paste, Bedrock or Foundry tabs is parked with it (Maude says "Saved; Bedrock stays the default (untick to use it)"); an active Foundry flag or custom endpoint in the main file wins and the box shows unticked (on Windows/macOS a plain API key there wins too; an active `CLAUDE_CODE_USE_BEDROCK=1` counts as Bedrock itself). Ticking it also switches an active "Use this API key" off and says so. The box works from every tab and an empty Save changes nothing. |
| Command Line | Closes the TUI and returns to the shell. |

## Keys

| Key | Action |
|-----|--------|
| `Enter` | Open the selected project |
| `n` | New project |
| `d` | Delete the selected project. It moves to `Projects/.deleted/`. |
| `q` | Quit to the shell |

## Credentials

If no LLM credentials are set, the TUI shows the Settings window at start. Maude checks `ANTHROPIC_API_KEY`, `ANTHROPIC_FOUNDRY_API_KEY`, `~/.aws/credentials` and `~/.azure/clauderc`. The Web UI button opens the same window when credentials are missing.

## Setup Git

The wizard has four steps:

1. Look up your GitHub user name to fill in name and email.
2. Find or make an ed25519 SSH key, and test it with `ssh -T git@github.com`.
3. Find or make a GPG key, and test it with a signed message.
4. Set `user.name`, `user.email` and `init.defaultBranch=main`. Install `keychain` and add it to `~/.bashrc`.

You paste the public keys into GitHub yourself. The wizard needs no `gh` login.

## Updates

The TUI downloads a new `maude.py` from GitHub once per day, at or after noon. The new version runs at the next start.
