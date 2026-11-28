# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Active implementation**: `light/` — Maude Light (Windows WSL2 only). This is the only production-ready target today; start here for any user-facing work. The full-appliance code (`scripts/`, `Dockerfile`, `packer/`) is the foundation for the long-term roadmap: macOS support, Linux VM-hosted sandboxes, and Proxmox appliances — but those targets are not yet production. See [`light/CLAUDE.md`](light/CLAUDE.md) for self-contained guidance when working inside `light/`.

## Development Workflow: Agent Delegation (HIGH PRIORITY — standing instruction)

**All code and documentation in this repository must be written by background Sonnet agents and all code changes must be reviewed by a background Fable agent.** This applies every session, not just when the user restates it — including when the orchestrating/main-loop session is itself already running Sonnet.

- **Authoring**: For any non-trivial code or doc change (new/edited `.sh`, `.py`, `.ps1`, `.md`, workflow YAML, etc.), delegate the actual writing to a background agent pinned to Sonnet: `Agent({ description: ..., prompt: <full context — files, line numbers, what to change and why>, model: "sonnet", run_in_background: true })`. Brief it like a colleague with no context on the conversation (per the Agent tool's own guidance) — don't just say "fix the bug," give it the file paths, the root cause, and the specific change.
- **Reviewing**: Before any code change (not docs-only) is presented as complete or committed, run it through a separate background agent pinned to Fable for review: `Agent({ description: ..., prompt: <diff or files to review, what to check for>, model: "fable", run_in_background: true })`. The `fable` alias always means the newest Fable that the installed Claude Code knows, so never pin a version ID; if reviews run on an old Fable, update Claude Code (the alias maps to 5.1 in older builds). The review agent must be a distinct spawn from the authoring agent — an agent must never review its own work — and should look for correctness bugs, security issues, and unrequested scope creep, not just style.
- **Committing (local only) — NO push or release without asking**: Once the Fable review comes back clean — no unresolved findings — commit the change **locally** on `main`. **Never push to GitHub (any branch), create tags, create releases, or dispatch workflows unless the user explicitly says so for that push/release.** Report what is ready and wait for "push" or "release". If the Fable review surfaces real findings, fix them first (Sonnet author → a fresh Fable re-review), and only commit once a review is clean. Follow the repo's usual commit conventions (clear message, `Co-Authored-By` trailer). Direct-to-`main` matches this repo's established practice.
- Do not skip the Fable review step to save time, especially on anything user-facing or production-bound (`light/`). This is the point of the policy, not an optional nicety. The Fable review is the gate: passing it is what authorizes the local commit (pushing still needs the user's explicit go-ahead).
- Use foreground-vs-background judgment as usual: prefer backgrounding both authoring and review agents unless the very next step in the conversation genuinely depends on the result.
- Judgment calls that don't need this ceremony: trivial one-line/config edits, answering questions, pure research/investigation with no code output. When in doubt on a real code or doc change, delegate.

### Test/scratch code safety (HARD RULE — never violate)

**Never write test, scratch, or verification code that runs `rm -rf "$HOME"`, or `rm -rf` on any variable that can expand to a home or other critical path** — including a `HOME` that has been reassigned to a temp dir (e.g. `export HOME=/tmp/x; rm -rf "$HOME"`). The safety guard evaluates the *variable*, sees `$HOME`/critical paths, and blocks the user with a permission prompt. That is unacceptable and must never happen.

- To make a scratch/fake-HOME dir: `d=$(mktemp -d)` and delete it with **`rm -rf "$d"`** — the literal `mktemp` path, never via `$HOME`.
- When a test needs a fake home, set `HOME="$d"` for the test *scope* (subshell/env prefix), but the cleanup must target `"$d"`, never `"$HOME"`.
- Prefer scoping with an env prefix that doesn't reassign the outer shell's HOME: `( HOME="$d"; ...tests... )` then `rm -rf "$d"`.
- This applies to **every subagent too** — include this constraint verbatim in any authoring/review brief where the agent may write or run test harnesses. Review agents in particular tend to write sandbox-HOME harnesses; tell them explicitly to `rm -rf` only a `mktemp` path.
- Same principle for any destructive command (`rm -rf`, `find -delete`, `git clean -fdx`, `chmod -R`): target an explicit `mktemp` path, never a variable that could be a real user path.

## Project Overview

**maude** -- A ready-to-run sandbox appliance for agentic coding, deployable as a VM (VMware/KVM), WSL image, Proxmox appliance, or Docker container. It integrates three upstream projects to create a multi-user Ubuntu environment where users get browser-based terminal access and can rapidly deploy web apps.

**Maude Light** (`light/`) is the current production implementation -- a lightweight WSL2 sandbox that deploys Ubuntu 26.04 (default) or 24.04 (`-Noble` flag) as an isolated distro with a single shared folder to the Windows host. Install with one PowerShell command; no VM or Docker needed.

**Upstream repos** (checked out at `~/gh/`):
- `~/gh/web-term` -- Node.js browser terminal via SSH+tmux (xterm.js frontend)
- `~/gh/appmotel` -- Bash-based PaaS: deploys Python/Node/Go apps as systemd services behind Traefik
- `~/gh/mom` -- Rust setuid tool allowing non-root users to install packages via apt/dnf

## Architecture

### Full Appliance (VM/Docker/WSL image)

```
User browser
    |
    v
Traefik (reverse proxy, HTTP only until maude setup configures a domain)
    |
    +-- web-term  (Node.js, deployed by appmotel, browser terminal)
    +-- user apps (deployed via appmotel)

Each user -> separate Linux account (JIT-created via maude-adduser)
appmotel  -> manages Traefik + app systemd services
mom       -> setuid binary, lets non-root users install packages
```

### Maude Light (WSL2 sandbox)

```
Windows host
    |
    +-- %LOCALAPPDATA%\Maude\Data\Maude\              <- shared folder (default)
    |       +-- Projects/       <- coding projects (directly used by WSL)
    |       +-- .claude/        <- Claude Code config (symlinked from WSL)
    |       +-- .kanna/         <- kanna web UI data (only .kanna/data is linked from WSL)
    |
    +-- WSL2: Maude (Ubuntu 26.04 default, 24.04 with -Noble)
          +-- ~/Maude/          <- drvfs mount of shared folder
          +-- ~/.claude         -> symlink to ~/Maude/.claude
          +-- ~/.kanna          is a local dir; ~/.kanna/data -> ~/Maude/.kanna/data (kanna needs a local unix socket)
          +-- ~/bin/            <- user scripts (front of PATH)
          +-- ~/.local/bin/     <- tool binaries (end of PATH)

Automount: disabled (no /mnt/c, /mnt/d)
Only mount: ~/Maude via /etc/fstab drvfs entry
```

The setup script (`setup-wsl-maude.ps1`) bakes packages into a reusable `Ubuntu-<version>-Template` WSL distro (`Ubuntu-26.04-Template` by default, `Ubuntu-24.04-Template` with `-Noble`), then imports it as `Maude`. Teardown + reinstall takes under a minute because the template is kept.

## Commands

### Testing & Linting
```bash
make test           # Runs tests/run-all-tests.sh (discovers all tests/test-*.sh files)
make test-fast      # Run tests, stop on first failure
make lint           # Runs tests/test-scripts-syntax.sh (bash -n, no execution)
bash tests/test-path-setup.sh   # Run a single test file
python tests/win_tui_smoke.py       # Headless check of maude.py Windows mode (needs textual; NOT run by make test)
```

### Writing Tests

Tests live in `tests/test-*.sh`. Source `tests/lib.sh` for shared helpers:

```bash
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
suite_header "My feature"
assert_eq      "description" "expected" "$actual"
assert_contains "description" "needle" "$haystack"
assert_file_exists "description" "/path/to/file"
assert_executable  "description" "/path/to/bin"
assert_exit_zero    "description" some_command --args
assert_exit_nonzero "description" some_command --args
skip "reason"
suite_summary  # prints results; exits non-zero if any FAIL
```

**Gotcha**: use `PASS=$((PASS+1))` not `((PASS++))` — the latter exits non-zero when the value is 0 under `set -e`.

### Building

```bash
# Docker
make build-docker                          # Build Docker image locally
make run-docker                            # Run with ports :3000, :2222, :8080
make push-docker                           # Push to GHCR

# WSL (builds via Docker -- works on Linux/WSL/Mac, same as CI)
make build-wsl                             # Build WSL rootfs tarball
make wsl-import WSL_DISTRO=maude-dev WSL_DIR=C:\maude-dev  # Register in WSL
make wsl-test                              # Smoke tests (PATH, hostname, mom, users group)
make wsl-update-scripts                    # Hot-patch scripts into running WSL distro (seconds)

# VM (requires Packer + KVM or VMware)
make build-vm-kvm UBUNTU_ISO_URL=...       # Outputs .qcow2
make build-vm-vmware UBUNTU_ISO_URL=...    # Outputs .ova
```

### Releasing
```bash
make tag VERSION=v0.2.0    # Creates + pushes git tag -> triggers CI release workflow
```

## Development Workflow Tiers

- **Tier 1 -- Unit tests (instant)**: `make test` / `make lint`
- **Tier 2 -- Hot script update (seconds)**: `make wsl-update-scripts` then `make wsl-test`
- **Tier 3 -- Full local build (~3 min)**: `make build-wsl && make wsl-import && make wsl-test`
- **Tier 4 -- Official release (CI only)**: `make tag VERSION=vX.Y.Z`

## Deployment Targets & Priority

1. **Maude Light (WSL2)** -- production-ready; single PowerShell command install from GitHub
2. **VM (VMware + KVM)** -- built with Packer, outputs `.ova` and `.qcow2`
3. **WSL image** -- full appliance variant; manual-only (`build-wsl.yml` dispatch), not currently built
4. **Docker** -- `ghcr.io/dirkpetersen/maude:latest`
5. **Proxmox appliance** -- lower priority

## Key Design Constraints

- **WSL detection**: `[ -f /proc/sys/fs/binfmt_misc/WSLInterop ]` -- when true, Traefik binds to `127.0.0.1` only
- **No TLS initially**: HTTP only until `maude setup` configures a domain (ACME handled by appmotel/Traefik)
- **No Azure AD yet**: Authentication is deferred; web-term uses SSH password auth
- **No containers**: appmotel deploys Python, Node.js, and Go apps only (Rust support deferred)
- **PowerShell install uses `curl.exe`** (not `curl`, which aliases to `Invoke-WebRequest`); `iex` may be blocked by antivirus on corporate machines
- **Line endings**: `.gitattributes` enforces LF for all text, CRLF for `.ps1` files only. PowerShell still re-adds CRLF when piping to external processes — use base64 encode/decode to work around this
- MIT licensed

## Maude Light -- Script Pipeline

The `light/` directory contains the WSL2 sandbox implementation:

| Script | Runs as | Purpose |
|--------|---------|---------|
| `setup-wsl-maude.ps1` | Admin (PowerShell) | 7-step orchestrator: WSL, WT, host folder, template, import, bootstrap |
| `teardown-wsl-maude.ps1` | PowerShell (self-elevates) | Unregister distro, remove WT profile + shortcut, optionally remove template |
| `root-bootstrap.sh` | root (inside WSL) | User creation, wsl.conf, fstab mount, mom-inst (.deb), PATH, welcome stub + user-owned welcome copy, Claude Code |
| `dev-mode.sh` | root (inside WSL) | Opt-in dev mode: standard WSL behavior (Windows drives, interop, Windows PATH) + sudo with password for the Maude user; removes sandbox isolation |
| `add-user.sh` + `add-user.ps1` | root (inside WSL) / PowerShell (normal user) | Opt-in second Linux user in the same distro (`add-user.ps1 -UserName dp`): sudo with password, Windows drives mounted for that user only (`uid/gid/umask=077` fstab lines), plus a static "Ubuntu (<VERSION_ID>)" Windows Terminal profile. `wsl.conf` is not changed |
| `maude-bootstrap.sh` | maude user (inside WSL) | dev-station, Bun, kanna-code, skills, reviewer CLIs (Gemini, Codex, OpenCode, Grok — see `docs/multi-cli-reviewers.md`), Kiro CLI + Crush (`lib/cli-tools.sh`), Claude Code config, yolo-mode marker |
| `lib/*.sh` | maude user (inside WSL) | Sourced helpers fetched at runtime: `cli-tools.sh` (Kiro CLI + Crush install-if-missing, `link_kiro_skills`), `llm-mode.sh` (credential-mode detection, must be sourced first), `codex.sh`/`gemini.sh`/`grok.sh`/`opencode.sh` (reviewer CLI install + config), `kanna.sh`, `ensure-tools.sh`, `refresh-md.sh`, `update-skills.sh`. Canonical reviewer skills live at repo-root `.claude/skills/{codex,gemini,grok,opencode}/SKILL.md` |
| `maude` | maude user (inside WSL) | CLI launcher: creates projects, inits git, launches Claude Code (`maude <name>`, `maude list`, `maude delete`, `maude web`, `maude tui`, `maude github`, `maude update`) |
| `maude.py` | maude user (inside WSL) | Textual TUI — always launched via `maude tui`, downloaded fresh from GitHub daily (stamp: `~/.maude-tui-last-update`). Also runs natively on Windows inside `maude.exe` (guarded by `IS_WIN`; Windows mode, red logo, caption "running outside the sandbox" only when the `Maude` WSL distro is registered (`mx.has_sandbox()`), none without a sandbox; `MaudeApp(select=<folder>, model=)` preselects an outside folder and shows a red warning bar; project list = outside-sandbox projects from `HKCU\Software\Maude\Projects` first, then share projects; third column "Features" = `GitHub`/`No Sandbox`; see `docs/windows-exe.md`) |
| `maude_exe.py` | Windows (native) | Stdlib-only launcher packed with `maude.py` into `maude.exe` by PyInstaller (`--onefile`, CI: `build-maude-exe.yml`). Copies itself to `%USERPROFILE%\.local\bin`, adds HKCU "Open in Maude" Explorer entries, routes folders inside the share into the sandbox; a folder outside the share (no flag, what the menu passes) starts the Windows TUI with that folder preselected and a warning, no y/N question (`--windows` launches Claude directly); no arg = TUI in Windows mode. Pure helpers tested by `tests/test-maude-exe.sh`; see `docs/windows-exe.md` |

Key implementation details:
- **Runtime fetch model**: `maude`, `maude-bootstrap.sh`, and `root-bootstrap.sh` pull `lib/*.sh` (cached into `~/.local/lib/maude/`), `maude.py` (daily), `MAUDE.md` (daily, via `lib/refresh-md.sh`), and `welcome-stub.sh` from `GH_RAW=https://raw.githubusercontent.com/dirkpetersen/maude/main/light` at runtime. `main` is therefore the effective release channel for Maude Light — no tag needed for these files to reach installed sandboxes. `MAUDE_RAW` overrides the base URL (e.g. to test a fork/branch)
- Files are piped into WSL via `Get-Content -Raw | wsl ... bash -c "cat > /tmp/..."` (automount is disabled, so wslpath/cp don't work). Package install scripts use base64 encode/decode to avoid PowerShell CRLF corruption when piping
- `/tmp` is cleared on `wsl --terminate` -- files must be re-piped after restart (step 6 re-pipes maude-bootstrap.sh)
- Windows Terminal auto-generates profiles with `source=Microsoft.WSL` -- can't delete them, must hide with pre-hidden stubs
- WT profile cleanup runs BEFORE self-elevation (elevated process has different `$env:LOCALAPPDATA`)
- `~/.claude` is symlinked to `~/Maude/.claude` so settings persist on the host mount
- **Security model**: automount disabled + no generic sudo = AI agent can only access the shared `Maude` folder and installed tools; Claude Code runs in bypassPermissions mode (safe inside sandbox). A `~/.claude/yolo-mode` marker file is created during bootstrap (delete it to disable)
- **DANGER-ZONE.txt**: copied to `~/Maude/DANGER-ZONE.txt` on the shared host mount; warns users about the AI's unrestricted access to this folder. Auto-restored on each startup if missing
- The `maude` CLI auto-updates Claude Code weekly (stamp file: `~/.claude/.last-update-check`)
- **Sandbox instructions split**: `~/.claude/MAUDE.md` is always overwritten on each `maude` launch (sandbox rules, not user-editable); `~/.claude/CLAUDE.md` is created once if missing and user-owned. `CLAUDE.md` includes sandbox rules via `@MAUDE.md`
- `maude delete <name>` is a soft-delete — moves to `Projects/.deleted/`, not permanent
- The `maude` CLI launches Claude Code with `claude opus`

## Maude TUI (`maude tui`)

A Textual-based full-screen TUI which is the default welcome experience for new terminal sessions. Launched by `maude-welcome.sh` on login, by `maude tui`, or in wizard-only mode via `maude github` (`python3 maude.py --github`).

### Layout

```
┌─ Maude ──────────────────────────────────────────────────────────────┐
│  ASCII logo (green)         │  Projects                              │
│  ──────────────             │ ┌────────────────────────────────────┐ │
│  Tips:                      │ │ banana    Modified: Apr 10, 2026   │ │
│   Screen split  Alt+Sh+±    │ │ my-app    Modified: Apr 9, 2026    │ │
│   Share image  drop file    │ │ ...                                │ │
│   Voice  Win+H              │ └────────────────────────────────────┘ │
│  ──────────────             │                                        │
│ Agent        Model Thinking │                                        │
│ (o) Claude.. (o) auto  auto │                                        │
│ ( ) Codex    ( ) fable low  │                                        │
│ ( ) Kiro CLI ( ) opus  ...  │                                        │
│ ... (8)      ...       max  │                                        │
│  ──────────────             │                                        │
│ [x] Clear History           │                                        │
│ [ ] Perplexity MCP          │                                        │
│ [x] Autostart               │                                        │
├─────────────────────────────┴────────────────────────────────────────┤
│ [Open Project] [+ New] [Web UI] [Setup Git] [Settings] [CLI]  │
└──────────────────────────────────────────────────────────────────────┘
```

### Key behaviors
- **Open project**: `app.suspend()` → `claude <model> --continue` (fallback: `claude <model> --resume` only if the project has saved sessions, then `claude <model>`) → resume TUI; cursor is restored to the project that was just open. On Windows (`maude_exe.run_windows`) and macOS, `--continue` is passed only when Claude has saved sessions for the folder (`has_claude_sessions()` / `_mac_has_claude_sessions()`; both conservative: long or non-BMP paths, `CLAUDE_CODE_PROJECT_DIR_NAME` set, and errors count as "has sessions"; NFC/NFD forms of the path are tried), so a project without sessions starts Claude once instead of twice (failed `--continue`, then retry). `MAUDE_STEP_TIMING=1` prints per-step `Maude timing:` lines to stderr on the open path (`maude_exe.timed()`), from the TUI and the Explorer menu alike (the separate `MAUDE_TIMING` quits the TUI at first paint).
- **Delete**: confirm modal → soft-delete to `Projects/.deleted/`
- **New project**: modal dialog, spaces auto-replaced with hyphens, git init
- **Web UI**: launches kanna in its own process group with `CLAUDE_EXECUTABLE=$HOME/bin/claude` so the wrapper handles auth (Foundry/Azure/Bedrock/direct). Click again to stop — `SIGTERM` → `SIGKILL` on the group plus `fuser -k 3210/tcp` as a backstop. Refuses to launch without credentials and pops `CredsEntryScreen` as a recovery path
- **Setup Git**: opens `GitSetupWizard`, a 4-step modal for first-run users. Step 1 looks up the GitHub username via `api.github.com/users/<name>` to pre-fill name/email. Step 2 detects/generates an ed25519 SSH key + verifies via `ssh -T git@github.com`. Step 3 detects/generates a GPG key (ed25519 + cv25519 subkey) and round-trips a clearsign to verify. Step 4 sets `git config user.name/email/init.defaultBranch=main`, runs `mom install -y keychain`, and appends a keychain block to `~/.bashrc`. Step 2's SSH key upload is a manual paste flow; step 3 authenticates `gh` (`gh auth login --web`) and uploads the signing key with it. On Windows/macOS the wizard and To Github run natively with SSH signing instead of gpg (OS OpenSSH, no mom/keychain/`.bashrc`); see `light/CLAUDE.md` and `docs/windows-exe.md`
- **Command Line**: exits TUI, returns to shell prompt
- **"Autostart" checkbox** (last of the toggle column): TUI auto-launches by default for every new terminal session. Unchecking the box creates `~/.maude-tui-disabled` (an *opt-out* flag); `maude-welcome.sh` skips the auto-launch when that file is present and shows the text banner instead. `maude tui` is invoked (not `exec`) so the shell survives after TUI exit
- **"Perplexity MCP" checkbox**: under Clear History, off by default. State is the flag file `~/.maude-perplexity` (exists = checked). `sync_perplexity()` runs at the start of `open_project()`: checked and connector missing -> `claude mcp add --transport http --scope user perplexity-computer https://www.perplexity.ai/rest/computer/mcp` then `claude mcp login`; exists but not Connected -> login only; unchecked does nothing at project open; unticking the box runs `claude mcp remove --scope user` once (`remove_perplexity()`), so a connector added by hand is never touched while the box was never ticked. The connector is user-scope MCP config in `~/.claude.json`. Failures only print a warning; the project always opens. Tested by `tests/test-perplexity-sync.sh`
- **Agent / Model / Thinking pickers** (left column, in this order). **Agent** (`#agent-select`): `Claude Code` (default), `Codex`, `Antigravity`, `Grok CLI`, `Kiro CLI`, `GitHub Copilot CLI`, `OpenCode`, `Crush` (`AGENTS`), saved to `~/.maude/maude-tool` (the legacy `~/.maude-tool` is moved into it once). Binaries come from `AGENT_BIN` (built on `AGENT_BINARIES`, shared with the installers; `INSTALL_LABEL` maps an agent to its `mx.TOOL_INSTALL` key: `Grok CLI` -> `Grok`, `GitHub Copilot CLI` -> `Copilot CLI`). Command lines (`tool_command()`): `claude [model] [--effort L] --continue`, `codex [-m M] [-c model_reasoning_effort=L] [resume --last]` (max is sent as xhigh), `agy [--model M] [--effort L] [-c]`, `grok [-m M] [-c]`, `kiro-cli chat [--model M] [--effort L] [--resume]` (device-code sign-in via `kiro-cli login --use-device-flow` if `kiro-cli whoami` fails), `copilot [--model M] [--continue]`, `opencode [--model P/M] [--continue]`, `crush [--model P/M] [--continue]`. Failures stay visible: a normal quit (`NORMAL_QUIT_CODES`: 0, 130, -2, -15, 0xC000013A; `agent_quit_normally()`) never retries or pauses; otherwise `_after_agent()` retries once without the resume flag when one was used (`agent_retry_fresh()`, `RESUME_FALLBACK_AGENTS`, model and effort kept), and only if that run also fails (or no resume flag was used) it stops with "<agent> exited with code N" plus "try Model: auto / Thinking: auto" for whatever was passed, and waits for Enter. Perplexity sync runs only for Claude Code. A missing binary: sandbox hint "run 'maude update'" (it installs all eight), Windows `mx.ensure_tool(label)` offers the install (`mx.find_tool` plus a hint only for an agent without an installer entry), macOS `mac_ensure_tool`. **Model** (`#model-select`): a fixed pool of `MODEL_SLOTS` radio buttons relabelled (with `rich.text.Text`, so a label is never parsed as markup) when the agent changes. The first entry is always built-in `auto` (no model flag). Defaults: Claude Code `opus` when nothing is saved (auto there means the wrapper's own default, haiku, only when explicitly picked), every other agent `auto`, also when a saved model is not in the agent's list (`resolve_model()`, `initial_selection()`). Lists: embedded `MODELS_FALLBACK_JSON` (identical to `light/maude-models.json`, checked by `tests/test-models-config.sh`) < `~/.maude/models-default.json` (that file, fetched daily at or after noon by `maybe_update_models()` on a daemon thread started in `on_mount`, `validate_models_bytes()`, atomic write, stamp `~/.maude/models-last-update`, `MAUDE_MODELS_REFRESH=0` disables it; the app keeps one cached copy `self._cfg` and re-reads it only when the refresh wrote a new file) < optional user `~/.maude/models.json` (`"models"` replaces, `"models": []` = auto only, `"extra"` appends). `"version"` must be 1 if present. Entries: `{"label": ..., "id": ...}`; a label must be printable, without `[` or `]`, and is cut to `LABEL_MAX` (9); ids are checked with `fullmatch` (`MODEL_ID_RE`), and for Claude Code only an alias or `claude-*` id (`claude_model_ok()`; never `update`/`default`). `~/.maude/maude-model` is CLAUDE CODE ONLY: `<alias or claude-* id>[,<effort>]` (`opus`, `opus,high`; no file = opus; an EMPTY model field, `,` or `,high`, = Claude Code auto. An already-installed older maude.exe's OPEN_SH rejects an empty model and runs opus, so it never gets `claude auto`; the word `auto` is never written). The effort is shared by all agents that have one. Other agents' models live in `~/.maude/agent-models.json` (`save_agent_model()`, `recall_model()`, `remember_model()`), restored when switching back. `maude_dir()` / `maude_exe.maude_dir()` create `~/.maude` with mode 0700. **Thinking** (`#effort-select`): `auto` (passes nothing), `low`, `medium`, `high`, `xhigh`, `max`, enabled only for `EFFORT_AGENTS` (Claude Code, Antigravity, Kiro CLI `--effort`; Codex `-c model_reasoning_effort=`), greyed out for the rest. `maude.exe` (`run_windows`, `OPEN_SH` inside WSL) treats `auto` as no model flag and gives claude only fable/opus/sonnet/haiku/claude-* ids. Pure helpers are tested by `tests/test-ai-tools.sh` and `tests/test-models-config.sh` (both use `tests/maude-extract.py`). On Windows the tip reads `Paste image  Alt+V` instead of `Share image`.
- **Toggles**: one column of one-line checkboxes under the pickers, box left of the label: `Clear History` (`#fresh-context`, fresh=True skips `--continue`), `Perplexity MCP` (`#perplexity`), `Autostart` (`#autostart`, hidden on Windows).
- **Restriction outside the sandbox**: `~/.maude/settings.json` (`load_settings()` / `save_settings(d)`: atomic, mode 0600; loading never touches the file, a corrupt one gives the defaults and is kept as `settings.json.bak` by the next `save_settings`) holds `native_only_approved`, default `True`. When `NATIVE` (Windows/macOS) and it is on, every agent except `NATIVE_APPROVED_AGENTS` (Kiro CLI, GitHub Copilot CLI) is shown disabled (greyed out, not hidden) in the Agent column, and the saved agent is replaced by Kiro CLI **for the session only**: nothing is written, so the saved agent and model come back when the restriction is lifted (`agent_allowed()`, `restrict_agent()`, `initial_selection()`). In the sandbox it has no effect. The public method `MaudeApp.apply_agent_restrictions()` re-reads the settings and refreshes the column (and switches agent if needed); the Settings screen calls it after `save_settings()` (checkbox "Only allow Kiro.dev and Github Copilot outside the Sandbox", on top of the Settings window outside the sandbox). Layout: the sidebar is 49 wide (Agent 22, Model 13, Thinking 10) with `overflow-y: auto` (a one-column scrollbar): it needs about 27 rows (Windows) and so fits 120x35 and bigger (the sandbox, with Autostart shown and a 9-entry model list such as Kiro CLI or Copilot CLI, needs 120x36: at 120x35 only the version line scrolls); at 80x24, 100x30 and 120x30 it scrolls, and `tests/win_tui_smoke.py` (`check_reach`) checks that every picker, every button and the toggles can be reached with Tab, arrows and the mouse there.
- **Credential gate**: `CredsEntryScreen` modal blocks TUI startup if no LLM credentials are configured (checks `ANTHROPIC_API_KEY`, `ANTHROPIC_FOUNDRY_API_KEY`, `~/.aws/credentials`, `~/.azure/clauderc`). On Windows/macOS the same screen, **Settings** button and gate exist, but credentials live in `~/.maude/clauderc` (`clauderc_path()`, same format and parser) which Maude loads into every AI tool it starts (`plat_claude_env()`), and the gate never locks out a native claude login: it shows only when `claude auth status` says "not logged in" and nothing is configured, and Cancel still opens the project. The credentials file is written `export K='V'` (bash-safe), Bedrock keys replace only the `[bedrock]` / `[profile bedrock]` sections of `~/.aws/*` (one-time `.maude-bak`), and the wrapper rules apply natively only when `~/.maude/clauderc` exists. The user can paste shell-style `export` lines; the modal parses recognised credential vars (ANTHROPIC_*/CLAUDE_*/AWS_*/AZURE_*) and writes them to `~/.azure/clauderc` (mode 0600). The screen is titled **Settings** (button formerly "Set Creds"): outside the sandbox it also holds the checkbox "Only allow Kiro.dev and Github Copilot outside the Sandbox" (`native_only_approved` in `~/.maude/settings.json`, Save calls `apply_agent_restrictions()`), the **Anthropic** pane (Claude Login button, ANTHROPIC_API_KEY field, "Use this API key": checked = Maude starts Claude with that key even when logged in, unchecked = kept in `~/.maude/anthropic-key` but not used; Windows/macOS only for now, in the sandbox the field and checkbox are disabled: use Claude Login or Paste) and the **OpenCode** pane (OpenAI-compatible model URL, model name, api_key merged into `~/.config/opencode/opencode.json`, key in `~/.maude/opencode-key` referenced as `{file:...}`); see `light/CLAUDE.md`. Also reachable from the **Settings** bottom-bar button and triggered automatically when the user clicks **Web UI** without credentials
- **Status line**: `~/.claude/statusline.sh` (installed by `maude-bootstrap.sh`) renders Claude Code's status line as `~/cwd  [NN% free]`. The bootstrap merges this into `~/.claude/settings.json` `statusLine.command` while preserving any existing user customisations
- **Exit hint**: after quitting the TUI, prints "Please type menu \<Enter\> to get back to the TUI or type maude help \<Enter\>"
- **Daily update**: `maude.py` self-updates from GitHub once per day, at or after noon local time. Stamp file `~/.maude-tui-last-update` holds today's date in `YYYY-MM-DD` format; the check fires inside `maybe_self_update()` at module entry, before `app.run()`. Downloads are validated (static checks + a subprocess import, written to a temp file then moved; no stamp on failure) and tested by `tests/test-self-update.sh`
- **Colors**: green logo, dusty-rose accents, warm-grey toggles (overrides Textual's default blue accent on `Checkbox`/`RadioButton`)
- **Dependencies**: `textual` Python package — installed via `pip install textual` in `maude-bootstrap.sh`

## PATH Convention

All users must have `~/bin` at the **front** of PATH and `~/.local/bin` also present. The `maude-path.sh` profile script runs at the **end** of `~/.bashrc` so it executes after tool installers (e.g., Claude Code) that prepend their own paths -- then re-enforces `~/bin` first and deduplicates `~/.local/bin`.

## Image Build Strategy (Full Appliance)

- **Base**: Ubuntu 26.04 (full appliance + Maude Light default) / Ubuntu 24.04 (Maude Light with `-Noble`)
- **Package list**: `packages/ubuntu-packages.yaml` defines all packages baked into the image (also `packages/rhel-packages.yaml` for RPM targets)
- **Package installation during build**: `apt` directly (running as root), not mom
- **mom**: installed via `mom-inst` .deb package for post-boot user package management (old raw binary installs are purged first if present)
- **appmotel + web-term**: pulled from their GitHub releases/repos during image build; no submodules

## First Boot & User Experience (Full Appliance)

1. `first-boot.sh` runs via systemd oneshot (sentinel: `/etc/maude/.first-boot-done`):
   - Installs appmotel, deploys web-term, runs WSL detection, configures firewall
2. User logs in -> `maude setup` wizard prompts for domain/hostname
3. New users: Linux account JIT-created by `maude-adduser` (username regex: `^[a-z][a-z0-9_-]{0,31}$`; system names blocked)

## appmotel Permission Model

Three-tier sudo model:
- `apps` user -> `sudo -u appmotel` -> manages all apps
- `appmotel` user -> `sudo systemctl` -> manages only Traefik system service

Always prefix appmotel CLI calls with `sudo -u appmotel appmo ...`

## Key Runtime Config Files

- `/etc/maude/maude.conf` -- `MAUDE_HOSTNAME`, `MAUDE_BASE_DOMAIN`, `MAUDE_TLS_EMAIL`, `MAUDE_DEPLOY_TARGET`, `MAUDE_VERSION`
- `/etc/mom/mom.conf` -- setuid group + deny list path
- `/etc/mom/deny.list` -- blocked packages (nmap, tcpdump, wireshark, metasploit, etc.)
- `/etc/wsl.conf` -- WSL-specific boot config (hostname, systemd, interop)

## CI/CD Workflows

- **build-wsl.yml** -- Builds WSL rootfs tarball. DISABLED: manual (`workflow_dispatch`) only, because only Maude Light ships today and it does not use this build; re-add the `push` trigger when the full appliance is worked on again
- **build-docker.yml** -- Builds and pushes Docker image to GHCR. DISABLED: manual (`workflow_dispatch`) only, same reason as build-wsl.yml
- **release.yml** -- Triggered on version tags (`v*.*.*`); verifies `light/checksums.txt`, builds `maude.exe` (via `build-maude-exe.yml`), and creates the GitHub Release with `maude.exe` + `maude.exe.sha256` only (no WSL tarball, no Docker image), then dispatches `docs.yml`
- **build-maude-exe.yml** -- Windows runner; on push to main (paths: `light/maude_exe.py`, `light/maude.py`, `tests/win_tui_smoke.py`, the workflow), manual dispatch, or `workflow_call` from release.yml: runs `tests/win_tui_smoke.py`, builds `maude.exe` with PyInstaller (version = tag, else `dev`), smoke-tests `--version`/`--selftest`, uploads it as a workflow artifact (no release asset published by this workflow itself)
- **docs.yml** -- Builds the docs site from `docs/` with Zensical (`zensical.toml`, nav defined there) and deploys to GitHub Pages on push to main (paths: `docs/**`, `zensical.toml`, the workflow, `scripts/docs-set-release.py`) or dispatch (optional `tag` input, default newest release)
- **maude.exe download links in docs (rule)**: always point straight at the asset, `https://github.com/dirkpetersen/maude/releases/download/<tag>/maude.exe` (and `.../maude.exe.sha256`) with link text `Download maude.exe (<tag>)`, never at the releases page. The URLs are real versioned URLs in the markdown (so they also work on GitHub). `docs.yml` runs `scripts/docs-set-release.py <tag> docs` before the build, rewriting them to the newest release tag (the `tag` input if it is strict `vX.Y.Z`, else `gh release view`); `release.yml` job `update-docs` (after `create-release`) runs `gh workflow run docs.yml --ref main -f tag=<tag>`: a release made with `GITHUB_TOKEN` does not trigger other workflows, and `workflow_call` would run on the tag ref, which the `github-pages` environment rejects (main only). Zensical 0.1.3 has no `{{ }}` variables or macros (verified), so do not try one. When adding a new maude.exe link, use exactly that URL/text format so the script matches it.
- New pages under `docs/` must be added to the `nav` in `zensical.toml`.
