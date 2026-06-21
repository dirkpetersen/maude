---
name: gemini
description: Wield Google's Gemini CLI as a powerful auxiliary tool for code generation, review, analysis, and web research. Use when tasks benefit from a second AI perspective, current web information via Google Search, codebase architecture analysis with the codebase_investigator tool, or parallel code generation. Also use when the user explicitly asks to "ask Gemini", "check with Gemini", or "use gemini-cli". SKIP for simple quick tasks where Claude's own capability suffices.
---

# Gemini CLI

`gemini` (Google's `@google/gemini-cli` v0.46+) is pre-installed in Maude and on PATH.
**Always use `-m gemini-pro-latest`** — this pins to the latest stable Pro model and
avoids the CLI defaulting to an older or experimental version.

## Credentials: ALWAYS source `~/.gemini/.env` first

The API key already lives in `~/.gemini/.env` (written by the Maude TUI). The CLI is
*supposed* to auto-load it, but Claude Code's Bash tool spawns a fresh, non-interactive
shell that does **not** reliably pick it up — which is why `gemini` falsely complains
"Please set an Auth method … GEMINI_API_KEY" even though the key is right there.

**Fix: prefix every `gemini` call with a source of that file, in the same command:**

```bash
set -a; [ -f ~/.gemini/.env ] && . ~/.gemini/.env; set +a
```

The examples below omit this prefix for brevity, but you MUST include it in the same
`bash` invocation as the `gemini` command (each Bash tool call is a fresh shell, so the
source does not carry over between calls). Only treat auth as genuinely broken if a call
**still** fails *after* sourcing `~/.gemini/.env` and confirming `GEMINI_API_KEY` is set.

## Step 0: credential probe (always run first)

```bash
set -a; [ -f ~/.gemini/.env ] && . ~/.gemini/.env; set +a
[ -n "$GEMINI_API_KEY$GOOGLE_API_KEY$GOOGLE_GENAI_USE_VERTEXAI$GOOGLE_GENAI_USE_GCA" ] \
    || { echo "NO CREDS in ~/.gemini/.env"; }
gemini --skip-trust -m gemini-pro-latest -p "reply with the single word: ok" -o text 2>&1; echo "exit=$?"
```

If the probe prints `NO CREDS` (the env file truly has no key), STOP and tell the user to
add one via the Maude TUI: **Set Creds → Paste exports** with
`GEMINI_API_KEY=<key from aistudio.google.com/apikey>`. If a key IS present but the call
still errors, report the actual error — do NOT retry blindly on a broken auth.

## Basic invocation pattern

```bash
set -a; [ -f ~/.gemini/.env ] && . ~/.gemini/.env; set +a
gemini --skip-trust -m gemini-pro-latest -p "your prompt here" --yolo -o text 2>&1
```

Key flags:
- `--skip-trust` — required for headless/automated use in Maude (bypasses trusted-folder gate)
- `-m gemini-pro-latest` — **always include this**; pins to latest Pro model
- `-p "prompt"` — non-interactive prompt (the CLI reads it as an argument, no quoting issues for normal prose)
- `--yolo` / `-y` — auto-approve all tool calls
- `-o text` — human-readable output; use `-o json` for structured parsing
- `-m gemini-2.5-flash` — faster/cheaper model for simple tasks only

## When prompt contains special characters or multi-line code

If the prompt contains backticks, `$`, heredocs, or many newlines, write it to a
temp file under `/tmp` and feed via stdin instead to avoid any shell-escaping issues:

```bash
# Write to unique temp file via Write tool, then:
gemini --skip-trust -m gemini-pro-latest --yolo -o text < /tmp/gemini-query-$$.txt 2>&1
rm -f /tmp/gemini-query-$$.txt
```

Never create temp files inside the project directory — use `/tmp` only.

## Reference files with @

Pass file contents to Gemini without quoting them by using the `@path` syntax:

```bash
gemini --skip-trust -m gemini-pro-latest -p "Review @./src/main.py for bugs and security issues" -o text 2>&1
gemini --skip-trust -m gemini-pro-latest -p "Based on @./package.json and @./src/index.js, suggest improvements" -o text 2>&1
```

For large files or multiple files, this is cleaner than embedding content in the prompt.

## Model selection

| Model | Flag | Best for |
|-------|------|----------|
| gemini-pro-latest | `-m gemini-pro-latest` | **Default — always use this** |
| gemini-2.5-flash | `-m gemini-2.5-flash` | Quick tasks, lower latency, simple lookups |
| gemini-2.5-flash-lite | `-m gemini-2.5-flash-lite` | Trivial one-liners only |

## Quick reference patterns

### Web research (Google Search grounding)
```bash
gemini --skip-trust -m gemini-pro-latest -p "What are the latest changes in [topic]? Use Google Search." -o text 2>&1
```

### Code review
```bash
gemini --skip-trust -m gemini-pro-latest -p "Review @./path/to/file.py for bugs, security issues, and improvements" -o text 2>&1
```

### Codebase architecture analysis
```bash
gemini --skip-trust -m gemini-pro-latest -p "Use the codebase_investigator tool to analyze this project's architecture" -o text 2>&1
```

### Code generation
```bash
gemini --skip-trust -m gemini-pro-latest -p "Create [description]. Apply now." --yolo -o text 2>&1
```

### Test generation
```bash
gemini --skip-trust -m gemini-pro-latest -p "Generate pytest tests for @./src/utils.py focusing on edge cases. Apply now." --yolo -o text 2>&1
```

### JSON output for programmatic parsing
```bash
gemini --skip-trust -m gemini-pro-latest -p "prompt" -o json 2>&1
# Parse: result.response = content, result.stats.models, result.stats.tools
```

### Session resumption (multi-turn workflows)
```bash
# First turn (session saved automatically)
gemini --skip-trust -m gemini-pro-latest -p "Analyze this codebase architecture" -o text 2>&1

# List sessions
gemini --list-sessions

# Continue
echo "What patterns did you find?" | gemini --skip-trust -m gemini-pro-latest -r latest -o text 2>&1
```

## Gemini's unique tools

These are available only through the Gemini CLI:

- **google_web_search** — real-time Google Search; use for current events, latest versions, recent docs
- **codebase_investigator** — deep architectural analysis, cross-file dependency mapping, pattern detection
- **save_memory** — cross-session persistent memory for recurring project context

## Authentication

Credentials are stored in `~/.gemini/.env`. The CLI is meant to auto-load this file, but
in Claude Code's non-interactive Bash shells you must source it yourself (see
**Credentials: ALWAYS source `~/.gemini/.env` first** above). The file holds one of:
- `GEMINI_API_KEY` — Google AI Studio key, no OAuth needed (simplest)
- `GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT` — Vertex AI (needs OAuth/service account; **plain API keys don't work on Vertex**)
- `GOOGLE_GENAI_USE_GCA=true` — OAuth / Code Assist

Set these via **Maude TUI → Set Creds → Paste exports**. Pasting a bare `GEMINI_API_KEY` or `GOOGLE_API_KEY` is all you need for AI Studio.

## Validation

Always verify Gemini's generated code before using it — check for security issues (XSS, injection, eval), test functionality, and review dependencies.
