---
name: codex
description: Wield OpenAI's Codex CLI as an auxiliary reviewer and code generator, giving a GPT-5.x second opinion alongside Claude. Use when tasks benefit from a second AI perspective on code review, bug hunting, or an independent implementation attempt. Also use when the user explicitly asks to "ask Codex", "check with Codex/GPT/OpenAI", or "use codex". SKIP for simple quick tasks where Claude's own capability suffices.
---

# Codex CLI

`codex` (OpenAI's Codex CLI, native binary) is pre-installed in Maude and on PATH.
Maude generates `~/.codex/config.toml` with three ready profiles — you select the one
matching the sandbox's credential mode (detected in Step 0).

## Step 0: credentials + mode detection (always run first)

Credentials for all Maude reviewer CLIs live in `~/.azure/clauderc` (written by the
Maude TUI → Set Creds). Claude Code's Bash tool spawns fresh non-interactive shells, so
you MUST source it in the same command as every `codex` call:

```bash
set -a; [ -f ~/.azure/clauderc ] && . ~/.azure/clauderc; set +a
# Detect mode:
if [ "${CLAUDE_CODE_USE_FOUNDRY:-0}" = "1" ]; then MODE=azure
elif [ "${CLAUDE_CODE_USE_BEDROCK:-0}" = "1" ] || [ -f ~/.aws/credentials ]; then MODE=aws
else MODE=direct; fi
echo "mode=$MODE"
```

Then probe with a 1-token call **for the detected mode**:

```bash
# direct (needs OPENAI_API_KEY):
CODEX_API_KEY="$OPENAI_API_KEY" codex exec --skip-git-repo-check "reply with the single word: ok" 2>&1; echo "exit=$?"
# azure (needs AZURE_OPENAI_API_KEY):
codex exec --profile maude-azure --skip-git-repo-check "reply with the single word: ok" 2>&1; echo "exit=$?"
# aws (std AWS credential chain or AWS_BEARER_TOKEN_BEDROCK):
codex exec --profile maude-aws --skip-git-repo-check "reply with the single word: ok" 2>&1; echo "exit=$?"
```

If the required key is missing, STOP and tell the user to add it via the Maude TUI:
**Set Creds → Paste exports** (`OPENAI_API_KEY=...`, `AZURE_OPENAI_API_KEY=...`, or
AWS credentials). If a key IS present but the call still errors, report the actual
error — do NOT retry blindly on broken auth.

## Mode → model reference

| Mode | Invocation | Model served |
|------|-----------|--------------|
| direct | `CODEX_API_KEY="$OPENAI_API_KEY" codex exec ...` | `gpt-5.3-codex` (config default) |
| azure | `codex exec --profile maude-azure ...` | your Foundry deployment (default `gpt-5.3-codex`) |
| aws | `codex exec --profile maude-aws ...` | `openai.gpt-5.5` via Bedrock |

Override per-run with `-m <model>` only if the user asks for a specific model.

## Basic invocation pattern

`codex exec` prints the final answer to stdout and progress to stderr:

```bash
set -a; [ -f ~/.azure/clauderc ] && . ~/.azure/clauderc; set +a
CODEX_API_KEY="$OPENAI_API_KEY" codex exec --skip-git-repo-check --sandbox read-only \
    "Review the file /tmp/codex-review-1.diff for bugs and security issues" 2>/dev/null
```

Key flags:
- `--skip-git-repo-check` — required when running outside a git repo
- `--sandbox read-only` — default reviewer posture (no writes); use
  `--sandbox workspace-write` only when the user wants Codex to modify files
- `-o /tmp/codex-out.md` — also write the final message to a file
- `--json` — JSONL event stream for programmatic parsing
- `--ephemeral` — don't persist the session

## Passing large or non-trivial content: ALWAYS use a /tmp file

Codex is an agent with its own file-reading tools. Never route file content through the
shell — write it to `/tmp` with the Write tool (literal filename, no `$$`), then
reference the path in a short literal prompt:

```bash
codex exec --skip-git-repo-check "Review /tmp/codex-review-1.diff for correctness bugs. List findings with line numbers." 2>/dev/null
rm -f /tmp/codex-review-1.diff
```

Alternatively pipe the whole prompt itself via stdin: `codex exec - < /tmp/prompt.txt`.

**PROHIBITED — never do any of these** (shell metacharacters corrupt the payload):

```bash
codex exec "Review this: $(cat file.diff)"     # NO — command substitution
codex exec "$(< file.txt)"                      # NO — same thing
echo "$content" | codex exec -                  # NO — echo exposes it to the shell
codex exec <<< "$(cat file.diff)"               # NO — here-string interpolation
```

The ONLY acceptable inputs: a short literal hand-typed prompt (optionally referencing a
`/tmp` path), or `codex exec - < /tmp/file` stdin redirect.

## Quick reference patterns

### Code review (second opinion)
```bash
codex exec --skip-git-repo-check --sandbox read-only "Review /tmp/review.diff for bugs, security issues, and missed edge cases" 2>/dev/null
```

### Independent implementation attempt
```bash
codex exec --sandbox workspace-write "Implement the plan described in /tmp/plan.md in this repo" 2>/dev/null
```

### Resume a session
```bash
codex exec resume --last "now address the second finding" 2>/dev/null
```

## Validation

Always verify Codex's findings and generated code before acting on them — cross-check
claimed bugs against the actual source, and review generated code for security issues.
