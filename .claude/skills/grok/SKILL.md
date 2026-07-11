---
name: grok
description: Wield xAI's Grok Build CLI as an auxiliary reviewer and code generator, giving a Grok-4.x second opinion alongside Claude. Use when tasks benefit from a second AI perspective on code review, bug hunting, or an independent implementation attempt. Also use when the user explicitly asks to "ask Grok", "check with Grok/xAI", or "use grok". SKIP for simple quick tasks where Claude's own capability suffices.
---

# Grok Build CLI

`grok` (xAI's official Grok Build CLI, native binary) is pre-installed in Maude and on
PATH. Maude generates `~/.grok/config.toml` with one model alias per credential mode —
you select with `-m` based on the mode detected in Step 0.

## Step 0: credentials + mode detection (always run first)

Credentials live in `~/.azure/clauderc` (written by the Maude TUI → Set Creds). Source
it in the same command as every `grok` call — Bash tool shells are fresh and
non-interactive:

```bash
set -a; [ -f ~/.azure/clauderc ] && . ~/.azure/clauderc; set +a
# Detect mode:
if [ "${CLAUDE_CODE_USE_FOUNDRY:-0}" = "1" ]; then MODE=azure
elif [ "${CLAUDE_CODE_USE_BEDROCK:-0}" = "1" ] || [ -f ~/.aws/credentials ]; then MODE=aws
else MODE=direct; fi
echo "mode=$MODE"
```

Probe with a 1-token call for the detected mode:

```bash
# direct (needs XAI_API_KEY):
grok -p "reply with the single word: ok" --output-format plain 2>&1; echo "exit=$?"
# azure (needs AZURE_AI_API_KEY):
grok -m grok-azure -p "reply with the single word: ok" --output-format plain 2>&1; echo "exit=$?"
# aws (needs AWS_BEARER_TOKEN_BEDROCK — a Bedrock API key, NOT SigV4 creds):
grok -m grok-aws -p "reply with the single word: ok" --output-format plain 2>&1; echo "exit=$?"
```

**AWS caveat:** Grok on Bedrock runs on the OpenAI-compatible "mantle" endpoint, which
requires a Bedrock API key (`AWS_BEARER_TOKEN_BEDROCK`). Classic `~/.aws/credentials`
(SigV4) cannot reach it — in that case fall back to `grok-direct` if `XAI_API_KEY` is
present, otherwise STOP and tell the user which credential is missing (Maude TUI →
**Set Creds → Paste exports**: `XAI_API_KEY=...` from console.x.ai). If a key IS
present but the call still errors, report the actual error — do NOT retry blindly.

## Mode → model reference

| Mode | Flag | Model served |
|------|------|--------------|
| direct | (default, or `-m grok-direct`) | `grok-4.5` via api.x.ai |
| azure | `-m grok-azure` | your Foundry deployment (default `grok-4`) |
| aws | `-m grok-aws` | `xai.grok-4.3` via Bedrock mantle |

## Basic invocation pattern

```bash
set -a; [ -f ~/.azure/clauderc ] && . ~/.azure/clauderc; set +a
grok -p "Review the file /tmp/grok-review-1.diff for bugs and security issues" \
    --output-format plain --permission-mode plan 2>&1
```

Key flags:
- `-p "prompt"` — headless one-shot prompt (no TUI)
- `--output-format plain|json|streaming-json` — use `plain` normally, `json` to parse
- `--permission-mode plan` — read-only reviewer posture; omit when the user wants Grok
  to actually edit files (then use `--permission-mode acceptEdits`)
- `-m <alias>` — model alias from the table above
- `-c` / `-r <id>` — continue / resume a session (stored in `~/.grok/sessions`)

## Passing large or non-trivial content: ALWAYS use a /tmp file

Grok Build is an agent with its own file-reading tools. Never route file content
through the shell — write it to `/tmp` with the Write tool (literal filename, no `$$`),
then reference the path in a short literal prompt:

```bash
grok -p "Review /tmp/grok-review-1.diff for correctness bugs. List findings with line numbers." --output-format plain --permission-mode plan 2>&1
rm -f /tmp/grok-review-1.diff
```

**PROHIBITED — never do any of these** (shell metacharacters corrupt the payload):

```bash
grok -p "Review this: $(cat file.diff)"      # NO — command substitution into -p
grok -p "$(< file.txt)"                       # NO — same thing
echo "$content" | grok -p ...                 # NO — echo exposes it to the shell
grok -p "$(cat <<'EOF' ... EOF)"              # NO — heredoc into -p
```

The ONLY acceptable input: a short literal hand-typed prompt in `-p "..."`, optionally
referencing a `/tmp` file path for the agent to read itself.

## Quick reference patterns

### Code review (second opinion)
```bash
grok -p "Review /tmp/review.diff for bugs, security issues, and missed edge cases" --output-format plain --permission-mode plan 2>&1
```

### Independent implementation attempt
```bash
grok -p "Implement the plan in /tmp/plan.md in this repo" --output-format plain --permission-mode acceptEdits 2>&1
```

### JSON output for programmatic parsing
```bash
grok -p "prompt" --output-format json 2>&1
```

## Validation

Always verify Grok's findings and generated code before acting on them — cross-check
claimed bugs against the actual source, and review generated code for security issues.
