# Multi-CLI Reviewer Architecture — Codex, OpenCode, Grok

**Status:** IMPLEMENTED 2026-07-10 (libs, skills, pipeline wiring, tests — see §6;
credential application remains a separate follow-up phase)
**Date:** 2026-07-10
**Scope:** Maude Light (`light/`). The full appliance inherits later via the same libs.

---

## 1. Goal

Maude already ships Google's **Gemini CLI** as an auxiliary "second opinion" tool that
Claude Code can drive via a skill. This document architects three more, installed and
maintained **right below Gemini** in both the bootstrap and update pipelines:

| Tool | Vendor | Primary use in Maude | Typical models |
|------|--------|----------------------|----------------|
| **Codex CLI** (`codex`) | OpenAI | reviewer / codegen | GPT-5.x / Codex family |
| **OpenCode** (`opencode`) | anomalyco (OSS) | reviewer with **open-weight LLMs (GLM)** | GLM-5.x via Z.ai or Bedrock |
| **Grok Build** (`grok`) | xAI | reviewer / codegen | Grok 4.x |

Requirements:

1. **Fully functional at initial deployment** (`maude-bootstrap.sh`) **and after every
   `maude update`** — installs, upgrades, config, and skills all refresh on both paths.
2. **Mode-aware model names.** Maude runs in one of three credential modes (Azure AI
   Foundry, AWS Bedrock, direct API). The same tool needs *different* model identifiers
   and endpoints per mode — this doc pins the matrix (§5).
3. Credentials themselves are **out of scope here** (next phase); §7 defines the
   contract they must satisfy so nothing built now has to change.

---

## 2. The pattern we replicate: `lib/gemini.sh`

Everything below clones the proven Gemini integration shape:

```
light/lib/<tool>.sh          one lib per tool, defines update_<tool>()
  ├─ installs/updates the CLI (idempotent, reports version delta)
  ├─ symlinks the binary into ~/.local/bin  (child processes of Claude Code's
  │    Bash tool don't get bun/installer PATH injection)
  ├─ fetches .claude/skills/<tool>/SKILL.md from the repo root → ~/.claude/skills/<tool>/
  ├─ ensures the tool's env/config file has the Maude-managed keys
  └─ prints "cli:<status>" / "skill:<status>" lines; non-zero rc on failure

maude-bootstrap.sh           sources the lib, runs update_<tool>() once at install
light/maude  update_all()    re-downloads the lib, re-sources it, runs update_<tool>()
.claude/skills/<tool>/SKILL.md   teaches Claude Code how to drive the tool
                                 (credential probe → file-based prompt handoff → stdin)
```

Key invariants carried over:
- **Best-effort, isolated failure**: a tool failing to install/update warns but never
  aborts bootstrap or `maude update` (same as `update_gemini`).
- **Skills fetched from repo root** `.claude/skills/` (committed; `.gitignore` has the
  `!.claude/skills/` exception for exactly this reason).
- **The conduit rule** from the Gemini skill applies to all new skills: large content is
  passed via a `/tmp` file through **stdin**, never `$(cat …)` command substitution.

---

## 3. New components

### 3.1 `light/lib/llm-mode.sh` — shared mode detection (new, shared)

One function, one source of truth, mirroring the `claude` wrapper's precedence:

```bash
detect_llm_mode() {
    # Sources ~/.azure/clauderc (same file the wrapper + TUI use), then:
    #   CLAUDE_CODE_USE_FOUNDRY=1 (+ ANTHROPIC_FOUNDRY_*)   → echo "azure"
    #   CLAUDE_CODE_USE_BEDROCK=1 or ~/.aws/credentials     → echo "aws"
    #   otherwise                                            → echo "direct"
}
```

Used by the three config generators (§3.2–3.4) and by the SKILL.md credential-probe
steps. It never *writes* anything — pure read/report.

### 3.2 `light/lib/codex.sh` — `update_codex()`

- **Install/update:** native binary installer (Codex is a Rust binary; npm is only a
  wrapper): `curl -fsSL https://chatgpt.com/codex/install.sh | sh` — re-running the same
  command upgrades in place. Symlink the resulting `codex` into `~/.local/bin`.
- **Config:** write `~/.codex/config.toml` **only when absent or when it carries the
  `# managed by maude` marker** (user-customized configs are left alone). The file
  defines all three provider profiles up front, so switching credential mode never
  requires a config rewrite — the skill just picks the right `-p` profile:

```toml
# managed by maude
model = "gpt-5.3-codex"          # direct-mode default (API-key path)

[profiles.maude-azure]
model = "gpt-5.3-codex"          # MUST equal the Foundry *deployment name*
model_provider = "azure"

[model_providers.azure]
name = "Azure OpenAI"
base_url = "https://${AZURE_OPENAI_RESOURCE}.openai.azure.com/openai/v1"  # or derived from Foundry, see below
env_key = "AZURE_OPENAI_API_KEY"
wire_api = "responses"

[profiles.maude-aws]
model = "openai.gpt-5.5"
model_provider = "amazon-bedrock"

[model_providers.amazon-bedrock.aws]
region = "us-east-2"             # only region with openai.gpt-5.5 today
```

- **Headless invocation (what the skill uses):**
  `codex exec --skip-git-repo-check -o /tmp/codex-out.md "<prompt>"` — final message to
  stdout, progress to stderr. Inside Maude's sandbox the Claude-equivalent yolo flag
  `--dangerously-bypass-approvals-and-sandbox` is acceptable (same rationale as
  bypassPermissions), but the default skill posture is `--sandbox read-only` since the
  reviewer role rarely needs writes.
- **Notes:** `--full-auto` is deprecated; Entra ID auth is NOT supported (API key only);
  the Azure base URL **must** end in `/v1`.
- **Azure `base_url` derivation:** since Maude's typical Azure setup is an APIM gateway
  exposing each provider by path on one host (`.../anthropic`, `.../openai`, per the
  `ANTHROPIC_FOUNDRY_BASE_URL` already configured for Claude), `update_codex()` prefers
  deriving the OpenAI endpoint from that Foundry URL (`foundry_openai_url()` in
  `light/lib/llm-mode.sh`, swapping a trailing `/anthropic` for `/openai`) and only falls
  back to the `AZURE_OPENAI_RESOURCE` → `...openai.azure.com/openai/v1` form above when no
  Foundry `/anthropic` endpoint is configured.

### 3.3 `light/lib/opencode.sh` — `update_opencode()`

- **Install/update:** via Bun, matching Gemini/kanna exactly:
  `bun install -g opencode-ai` (package is `opencode-ai`, *not* `opencode`), then
  symlink `~/.bun/bin/opencode` → `~/.local/bin/opencode`. (The curl installer works too
  but appends PATH lines to `.bashrc`, which Maude manages itself — Bun avoids that.)
- **Config:** write `~/.config/opencode/opencode.json` when absent / maude-marked
  (`"//": "managed by maude"` key as marker). All providers declared; the default
  `model` is chosen by `detect_llm_mode()` at generation time and the skill can override
  per-run with `-m provider/model`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "//": "managed by maude",
  "model": "zai-coding-plan/glm-5.2",          // direct mode default (GLM coding plan)
  "provider": {
    "zai-coding-plan": { "options": { "apiKey": "{env:ZHIPU_API_KEY}" } },
    "amazon-bedrock":  { "options": { "region": "{env:AWS_REGION}" } },
    "azure":           { "options": { "apiKey": "{env:AZURE_API_KEY}" } }
  }
}
```

- **Headless invocation:** `opencode run "<prompt>" -m <provider/model>`
  (`--format json` available for structured capture).
- **GLM specifics (the reason this tool is here):**
  - Direct: provider **`zai-coding-plan`** (flat-rate GLM Coding Plan), env
    **`ZHIPU_API_KEY`**, base `https://api.z.ai/api/coding/paas/v4`. Flagship:
    **`glm-5.2`**. The metered `zai` provider is a separate endpoint — the two are NOT
    interchangeable; a coding-plan key 401s on the metered path.
  - AWS mode: **GLM is on Bedrock** — `amazon-bedrock/zai.glm-5`,
    `zai.glm-4.7`, `zai.glm-4.7-flash`.
  - Azure mode: GLM is only in Foundry via "Fireworks on Foundry" (third-party runtime,
    not exposed through opencode's `azure` provider) → **Azure-mode users fall back to
    direct Z.ai for GLM**, or use `azure/gpt-*` / `azure/claude-*` models instead.

### 3.4 `light/lib/grok.sh` — `update_grok()`

- **Which CLI:** the **official xAI "Grok Build"** (Rust binary, command `grok`) — not
  the community superagent-ai/grok-cli. Install:
  `curl -fsSL https://x.ai/cli/install.sh | bash` (re-run to upgrade), symlink into
  `~/.local/bin`. Set `auto_update = false` in config — updates flow through
  `maude update` like everything else.
- **Config:** `~/.grok/config.toml` when absent / maude-marked. Grok Build supports
  per-model-alias `base_url` + `env_key` + `api_backend`, so all three modes are
  pre-declared as aliases and the skill selects with `-m`:

```toml
# managed by maude
auto_update = false

[models]
default = "grok-direct"

[model.grok-direct]
model = "grok-4.5"                              # xAI flagship (July 2026)
# auth: XAI_API_KEY from the environment

[model.grok-azure]
model = "grok-4"                                # your Foundry *deployment name*
base_url = "https://${AZURE_AI_RESOURCE}.services.ai.azure.com/openai/v1"
env_key = "AZURE_AI_API_KEY"
api_backend = "chat_completions"

[model.grok-aws]
model = "xai.grok-4.3"
base_url = "https://bedrock-mantle.us-west-2.api.aws/openai/v1"
env_key = "AWS_BEARER_TOKEN_BEDROCK"            # Bedrock API key, NOT SigV4
api_backend = "chat_completions"
```

- **Headless invocation:** `grok -p "<prompt>" --output-format plain` (add
  `--permission-mode plan` for review-only posture; sessions in `~/.grok/sessions`).
- **Notes:** Grok on Bedrock (since 2026-06-15) uses the OpenAI-compatible
  **bedrock-mantle** endpoint with a *Bedrock API key* — classic SigV4-only AWS
  credentials cannot reach it; those users fall back to direct xAI. Grok Build is beta;
  pin expectations accordingly.

### 3.5 Three new skills (repo root, committed)

`.claude/skills/codex/SKILL.md`, `.claude/skills/opencode/SKILL.md`,
`.claude/skills/grok/SKILL.md` — each following the Gemini skill's structure:

1. **Step 0 credential probe**: `set -a; [ -f ~/.azure/clauderc ] && . ~/.azure/clauderc; set +a`
   (one shared credential file for all reviewers — see §7), check the tool's env var for
   the detected mode, then a 1-token "reply ok" probe.
2. **Mode → model table** (from §5) telling Claude which profile/model flag to pass.
3. **The conduit rule**: prompts and code context go through `/tmp` files + stdin.
4. Clear STOP-and-tell-the-user guidance when credentials are absent.

---

## 4. Pipeline wiring (initial deploy + every update)

Ordering requirement: the three tools install/update **immediately below Gemini** in
both pipelines.

**`light/maude-bootstrap.sh`** (initial deployment):
- line ~33: extend the lib-download loop:
  `for _lib in ensure-tools.sh refresh-md.sh update-skills.sh gemini.sh llm-mode.sh codex.sh opencode.sh grok.sh`
- after the existing `update_gemini` block: three parallel blocks calling
  `update_codex`, `update_opencode`, `update_grok` (warn-and-continue, like Gemini).

**`light/maude` → `update_all()`** (every `maude update`):
- lib list at the "Refreshing maude libraries" step gains the four new files, and each is
  re-sourced immediately after download (same as today's four).
- directly below the "Updating Gemini CLI + skill..." step: three new steps consuming
  the same `cli:`/`skill:` status-line protocol.
- `maude-welcome`/TUI tips can mention the tools later — not part of this phase.

**Model-name churn strategy** (this is what keeps it "fully functional after each
update"): default model IDs live **only** in the lib files' config generators, and the
mode→model matrix lives **only** in the SKILL.md files. Both are re-fetched from GitHub
`main` on every `maude update`, so a model rename ships as a one-line lib/skill commit —
no reinstall, no user action. Nothing else in the codebase may hardcode a reviewer
model name.

**Config-preservation rule**: generators overwrite a tool config only if it is absent
or still carries the `managed by maude` marker. A user who hand-edits (thereby removing
or keeping the marker at their choice) owns the file from then on — same philosophy as
`~/.claude/CLAUDE.md` (user-owned) vs `MAUDE.md` (always overwritten).

---

## 5. The mode → model matrix

The single most important table in this document. "Azure" = Azure AI Foundry /
Microsoft Foundry mode; "AWS" = Amazon Bedrock mode.

| Tool | Direct API | Azure (Foundry) | AWS (Bedrock) |
|------|-----------|-----------------|---------------|
| **Codex** | `gpt-5.3-codex` via `OPENAI_API_KEY` (GPT-5.5/5.6 API-key access still rolling out) | deployment of `gpt-5.3-codex` / `gpt-5.5` (5.6 phased); provider `azure`, `wire_api="responses"`, base URL ends `/openai/v1`, `AZURE_OPENAI_API_KEY` | **native `amazon-bedrock` provider** → `openai.gpt-5.5` (us-east-2) or `openai.gpt-5.4`; open-weight `openai.gpt-oss-120b` also available; std AWS chain or `AWS_BEARER_TOKEN_BEDROCK` |
| **OpenCode (GLM)** | `zai-coding-plan/glm-5.2` via `ZHIPU_API_KEY` | ⚠️ **no GLM through Foundry** (Fireworks-hosted only) → keep GLM direct, or switch to `azure/<deployment>` models | `amazon-bedrock/zai.glm-5` or `zai.glm-4.7` via std AWS chain + `AWS_REGION` |
| **Grok** | `grok-4.5` (or `grok-build-0.1` for the CLI-tuned model) via `XAI_API_KEY` | Foundry deployments: `grok-4`, `grok-4-fast-reasoning`, `grok-code-fast-1` (4.3/4.5 not in catalog yet) via OpenAI-compat `/openai/v1` endpoint + `AZURE_AI_API_KEY` | `xai.grok-4.3` via **bedrock-mantle** (`https://bedrock-mantle.<region>.api.aws/openai/v1`) — requires a **Bedrock API key**; SigV4-only → fall back to direct |

Fallback principle encoded in every skill: *if the detected mode can't serve the tool's
model (e.g., GLM in Azure mode, Grok with SigV4-only AWS), fall back to the direct API
if its key is present; otherwise report clearly which credential is missing.*

---

## 6. Repo change list

| File | Change |
|------|--------|
| `light/lib/llm-mode.sh` | NEW — `detect_llm_mode()` |
| `light/lib/codex.sh` | NEW — `update_codex()` + config generator |
| `light/lib/opencode.sh` | NEW — `update_opencode()` + config generator |
| `light/lib/grok.sh` | NEW — `update_grok()` + config generator |
| `.claude/skills/codex/SKILL.md` | NEW — skill (conduit pattern) |
| `.claude/skills/opencode/SKILL.md` | NEW — skill |
| `.claude/skills/grok/SKILL.md` | NEW — skill |
| `light/maude-bootstrap.sh` | lib loop + 3 update blocks below Gemini |
| `light/maude` (`update_all`) | lib list/re-source + 3 update steps below Gemini |
| `light/lib/ensure-tools.sh` | add `codex`, `opencode`, `grok` to symlink set |
| `tests/test-reviewer-libs.sh` | NEW — lint + function-presence + config-marker tests |
| `light/CLAUDE.md`, `CLAUDE.md` | document the new pipeline entries |

---

## 7. Credential contract (next phase — designed for, not built here)

- **One shared credential file**: `~/.azure/clauderc` (mode 0600) remains the single
  place the TUI writes exports. The TUI `CredsEntryScreen` parser grows these
  recognized vars: `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_AI_API_KEY`,
  `XAI_API_KEY`, `ZHIPU_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK` (AWS classic creds keep
  living in `~/.aws/credentials`).
- Every reviewer skill sources that file at Step 0 (exactly like the Gemini skill
  sources `~/.gemini/.env`), so non-interactive Bash-tool spawns always see the keys.
- The generators in §3 reference credentials **only** via `env_key` / `{env:VAR}`
  indirection — no key material is ever written into tool configs. That is why the
  credential phase can land later without touching anything in this document.
- **Governance note (OSU):** enabling Codex/Grok/Z.ai means traffic to OpenAI, xAI, and
  Zhipu endpoints in direct mode. Foundry/Bedrock modes keep traffic inside the
  already-approved Azure/AWS boundaries — worth stating in the OIS review that direct
  mode is the only new egress, and it activates only when a user supplies a key.

## 8. Risks / open items

1. **Model churn** is the #1 operational risk (three vendors × three clouds). Mitigated
   by §4's "models live only in libs+skills, refreshed every update" rule.
2. **Grok Build is beta** (~v0.2.x); its Azure/Bedrock BYOK wiring follows documented
   schema but lacks an official Azure example — needs a live smoke test before GA.
3. **Codex on Bedrock** requires the Mantle IAM policy
   (`AmazonBedrockMantleInferenceAccess`) — an account-admin prerequisite to document.
4. **Disk/startup cost**: three more binaries (~150–300 MB total) baked per-user, not in
   the WSL template — acceptable; revisit template-baking if bootstrap time suffers.
5. **Name collision**: never install the community grok-cli alongside Grok Build (both
   claim `grok`).
