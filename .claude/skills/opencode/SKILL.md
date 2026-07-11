---
name: opencode
description: Wield OpenCode with open-weight LLMs (GLM-5.x from Z.ai/Zhipu) as an auxiliary reviewer and code generator, giving an open-model second opinion alongside Claude. Use when tasks benefit from a GLM/open-source-model perspective on code review or generation. Also use when the user explicitly asks to "ask GLM", "check with OpenCode", or "use opencode". SKIP for simple quick tasks where Claude's own capability suffices.
---

# OpenCode CLI (GLM)

`opencode` (opencode.ai) is pre-installed in Maude and on PATH. In Maude its primary
role is driving **open-weight GLM models**. Maude generates
`~/.config/opencode/opencode.json` with the providers pre-declared; keys are resolved
from the environment at runtime via `{env:VAR}` placeholders.

## Step 0: credentials + mode detection (always run first)

Credentials live in `~/.azure/clauderc` (written by the Maude TUI → Set Creds). Source
it in the same command as every `opencode` call — Bash tool shells are fresh and
non-interactive:

```bash
set -a; [ -f ~/.azure/clauderc ] && . ~/.azure/clauderc; set +a
# Detect mode:
if [ "${CLAUDE_CODE_USE_FOUNDRY:-0}" = "1" ]; then MODE=azure
elif [ "${CLAUDE_CODE_USE_BEDROCK:-0}" = "1" ] || [ -f ~/.aws/credentials ]; then MODE=aws
else MODE=direct; fi
echo "mode=$MODE"
```

Probe with a 1-token call for the chosen model (see table below):

```bash
opencode run "reply with the single word: ok" -m zai-coding-plan/glm-5.2 2>&1; echo "exit=$?"
```

If the required key is missing, STOP and tell the user to add it via the Maude TUI:
**Set Creds → Paste exports** (`ZHIPU_API_KEY=...` from z.ai/manage-apikey, or AWS
credentials for Bedrock). If a key IS present but the call still errors, report the
actual error — do NOT retry blindly.

## Mode → model reference

| Mode | Model flag | Notes |
|------|-----------|-------|
| direct | `-m zai-coding-plan/glm-5.2` | GLM Coding Plan key (`ZHIPU_API_KEY`); flagship GLM-5.2 |
| aws | `-m amazon-bedrock/zai.glm-5` | GLM on Bedrock via std AWS chain + `AWS_REGION` (also `zai.glm-4.7`) |
| azure | `-m zai-coding-plan/glm-5.2` | ⚠️ GLM is NOT reachable through Azure — fall back to direct Z.ai; or use `-m azure/<deployment>` for non-GLM models |

**Gotcha:** the `zai-coding-plan` and `zai` providers use the same `ZHIPU_API_KEY` env
var but different endpoints — a Coding Plan key 401s on the metered `zai` provider.
Always pin the provider in the `-m` flag; never rely on a bare model name.

## Basic invocation pattern

```bash
set -a; [ -f ~/.azure/clauderc ] && . ~/.azure/clauderc; set +a
opencode run "Review the file /tmp/oc-review-1.diff for bugs and security issues" \
    -m zai-coding-plan/glm-5.2 2>&1
```

Key flags:
- `-m provider/model-id` — **always include**; pins provider + model explicitly
- `-f /tmp/file` — attach a file to the message (preferred conduit for file content)
- `--format json` — structured event output for programmatic parsing
- `opencode models <provider>` — list currently valid model IDs (use when a model
  errors as unknown; the catalog churns)

## Passing large or non-trivial content: ALWAYS use a /tmp file

OpenCode is an agent with its own file-reading tools. Never route file content through
the shell — write it to `/tmp` with the Write tool (literal filename, no `$$`), then
attach it with `-f` (or reference the path in a short literal prompt):

```bash
opencode run "Review the attached diff for correctness bugs. List findings with line numbers." -f /tmp/oc-review-1.diff -m zai-coding-plan/glm-5.2 2>&1
rm -f /tmp/oc-review-1.diff
```

**PROHIBITED — never do any of these** (shell metacharacters corrupt the payload):

```bash
opencode run "Review this: $(cat file.diff)" ...   # NO — command substitution
opencode run "$(< file.txt)" ...                    # NO — same thing
echo "$content" | opencode run ...                  # NO — echo exposes it to the shell
```

The ONLY acceptable input: a short literal hand-typed prompt, optionally referencing a
`/tmp` file path for the agent to read itself.

## Quick reference patterns

### Code review with GLM (second opinion)
```bash
opencode run "Review /tmp/review.diff for bugs, security issues, and missed edge cases" -m zai-coding-plan/glm-5.2 2>&1
```

### Code generation
```bash
opencode run "Implement the plan in /tmp/plan.md in this repo" -m zai-coding-plan/glm-5.2 2>&1
```

### Quick/cheap tasks
```bash
opencode run "..." -m zai-coding-plan/glm-5-turbo 2>&1
```

## Validation

Always verify OpenCode/GLM's findings and generated code before acting on them —
cross-check claimed bugs against the actual source, and review generated code for
security issues.
