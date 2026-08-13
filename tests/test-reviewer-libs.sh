#!/usr/bin/env bash
# test-reviewer-libs.sh — reviewer-CLI libs (llm-mode, codex, opencode, grok)
set -u
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB_DIR="$REPO_ROOT/light/lib"

suite_header "Reviewer CLI libraries"

# ── Syntax ────────────────────────────────────────────────────────────
for _lib in llm-mode.sh codex.sh opencode.sh grok.sh; do
    assert_exit_zero "bash -n $_lib" bash -n "$LIB_DIR/$_lib"
done

# ── Function presence after sourcing ──────────────────────────────────
# shellcheck source=/dev/null
source "$LIB_DIR/llm-mode.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/codex.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/opencode.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/grok.sh"

for _fn in detect_llm_mode maude_fetch_skill foundry_openai_url update_codex update_opencode update_grok; do
    assert_exit_zero "function $_fn defined" declare -F "$_fn"
done

# ── detect_llm_mode: all three modes, against a scratch HOME ─────────
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

# direct: no clauderc, no aws credentials
mode=$(HOME="$SCRATCH" detect_llm_mode)
assert_eq "detect_llm_mode: bare HOME → direct" "direct" "$mode"

# aws via ~/.aws/credentials
mkdir -p "$SCRATCH/.aws"
touch "$SCRATCH/.aws/credentials"
mode=$(HOME="$SCRATCH" detect_llm_mode)
assert_eq "detect_llm_mode: ~/.aws/credentials → aws" "aws" "$mode"

# azure wins over aws (Foundry beats Bedrock, mirroring the claude wrapper)
mkdir -p "$SCRATCH/.azure"
echo 'export CLAUDE_CODE_USE_FOUNDRY=1' > "$SCRATCH/.azure/clauderc"
mode=$(HOME="$SCRATCH" detect_llm_mode)
assert_eq "detect_llm_mode: CLAUDE_CODE_USE_FOUNDRY=1 → azure" "azure" "$mode"

# explicit bedrock flag without ~/.aws/credentials
rm -rf "$SCRATCH/.aws"
echo 'export CLAUDE_CODE_USE_BEDROCK=1' > "$SCRATCH/.azure/clauderc"
mode=$(HOME="$SCRATCH" detect_llm_mode)
assert_eq "detect_llm_mode: CLAUDE_CODE_USE_BEDROCK=1 → aws" "aws" "$mode"

# detection must not leak clauderc vars into the caller's environment
# (run in a clean subshell — the dev machine itself may export these flags)
echo 'export CLAUDE_CODE_USE_FOUNDRY=1' > "$SCRATCH/.azure/clauderc"
leak=$(unset CLAUDE_CODE_USE_FOUNDRY
       HOME="$SCRATCH" detect_llm_mode >/dev/null
       echo "${CLAUDE_CODE_USE_FOUNDRY:-}")
assert_eq "detect_llm_mode: no env leakage" "" "$leak"

# ── clauderc_env: env wins, clauderc fills the gap, no leakage ───────
echo 'export AZURE_OPENAI_RESOURCE=from-clauderc' > "$SCRATCH/.azure/clauderc"
val=$(HOME="$SCRATCH" clauderc_env AZURE_OPENAI_RESOURCE)
assert_eq "clauderc_env: reads from clauderc" "from-clauderc" "$val"
val=$(AZURE_OPENAI_RESOURCE=from-env HOME="$SCRATCH" clauderc_env AZURE_OPENAI_RESOURCE)
assert_eq "clauderc_env: ambient env wins over clauderc" "from-env" "$val"
val=$(HOME="$SCRATCH" clauderc_env NO_SUCH_VAR_XYZ)
assert_eq "clauderc_env: undefined var → empty" "" "$val"

# ── foundry_openai_url: derive the OpenAI endpoint from an APIM gateway ──
val=$(ANTHROPIC_FOUNDRY_BASE_URL="https://gw.azure-api.net/anthropic" foundry_openai_url)
assert_eq "foundry_openai_url: /anthropic -> /openai" \
    "https://gw.azure-api.net/openai" "$val"
val=$(ANTHROPIC_FOUNDRY_BASE_URL="https://gw.azure-api.net/anthropic/" foundry_openai_url)
assert_eq "foundry_openai_url: trailing slash tolerated" \
    "https://gw.azure-api.net/openai" "$val"
val=$(ANTHROPIC_FOUNDRY_BASE_URL="https://x.openai.azure.com/openai/v1" foundry_openai_url)
assert_eq "foundry_openai_url: non-/anthropic URL -> empty" "" "$val"
NOCFG_HOME=$(mktemp -d)
val=$(unset ANTHROPIC_FOUNDRY_BASE_URL; HOME="$NOCFG_HOME" foundry_openai_url)
assert_eq "foundry_openai_url: unset + no clauderc -> empty" "" "$val"
rm -rf "$NOCFG_HOME"

# generators pick the resource name up from clauderc
CFG_HOME=$(mktemp -d)
mkdir -p "$CFG_HOME/.azure"
echo 'export AZURE_OPENAI_RESOURCE=osu-openai-prod' > "$CFG_HOME/.azure/clauderc"
# Explicitly unset ANTHROPIC_FOUNDRY_BASE_URL: this test exercises the
# resource-URL fallback path specifically, which only applies when no Foundry
# /anthropic endpoint is configured. Dev machines commonly have this exported
# ambiently (it's how Claude itself is configured in Foundry mode), which
# would otherwise make foundry_openai_url() win and silently break this test.
( unset ANTHROPIC_FOUNDRY_BASE_URL
  HOME="$CFG_HOME" MAUDE_RAW="file://$REPO_ROOT/light" PATH="/usr/bin:/bin" update_codex >/dev/null 2>&1 ) || true
assert_exit_zero "codex config uses clauderc resource name" \
    grep -q 'osu-openai-prod.openai.azure.com' "$CFG_HOME/.codex/config.toml"
rm -rf "$CFG_HOME"

# ── maude_fetch_skill: fetch from the local repo via file:// ─────────
FETCH_HOME=$(mktemp -d)
for _skill in codex opencode grok gemini; do
    out=$(HOME="$FETCH_HOME" MAUDE_RAW="file://$REPO_ROOT/light" maude_fetch_skill "$_skill")
    assert_eq "maude_fetch_skill $_skill: installs from repo" "installed (new)" "$out"
    assert_file_exists "maude_fetch_skill $_skill: SKILL.md present" \
        "$FETCH_HOME/.claude/skills/$_skill/SKILL.md"
done
# Second fetch is a no-op
out=$(HOME="$FETCH_HOME" MAUDE_RAW="file://$REPO_ROOT/light" maude_fetch_skill codex)
assert_eq "maude_fetch_skill: unchanged → up to date" "up to date" "$out"
# Missing skill fails loudly, non-zero
if out=$(HOME="$FETCH_HOME" MAUDE_RAW="file://$REPO_ROOT/light" maude_fetch_skill no-such-skill); then
    assert_eq "maude_fetch_skill: missing skill returns non-zero" "non-zero" "zero"
else
    assert_contains "maude_fetch_skill: missing skill reports failure" "failed" "$out"
fi
rm -rf "$FETCH_HOME"

# ── Config generators: marker-guard behaviour (no network needed) ────
# The generator halves of update_codex/update_grok run even when the CLI
# install fails, so we can exercise them offline in a scratch HOME.
GEN_HOME=$(mktemp -d)

# Fresh HOME → configs written with the maude marker
( HOME="$GEN_HOME" MAUDE_RAW="file://$REPO_ROOT/light" update_codex >/dev/null 2>&1 ) || true
( HOME="$GEN_HOME" MAUDE_RAW="file://$REPO_ROOT/light" update_grok  >/dev/null 2>&1 ) || true
( HOME="$GEN_HOME" MAUDE_RAW="file://$REPO_ROOT/light" PATH="/usr/bin:/bin" update_opencode >/dev/null 2>&1 ) || true

assert_file_exists "codex config generated"    "$GEN_HOME/.codex/config.toml"
assert_file_exists "grok config generated"     "$GEN_HOME/.grok/config.toml"
assert_file_exists "opencode config generated" "$GEN_HOME/.config/opencode/opencode.json"

assert_exit_zero "codex config carries maude marker" \
    grep -q '^# managed by maude' "$GEN_HOME/.codex/config.toml"
assert_exit_zero "grok config carries maude marker" \
    grep -q '^# managed by maude' "$GEN_HOME/.grok/config.toml"
assert_exit_zero "opencode config carries maude marker" \
    grep -q 'managed by maude' "$GEN_HOME/.config/opencode/opencode.json"

# Content sanity: all three modes pre-declared
assert_exit_zero "codex config declares azure profile" \
    grep -q 'profiles.maude-azure' "$GEN_HOME/.codex/config.toml"
assert_exit_zero "codex config declares aws profile" \
    grep -q 'profiles.maude-aws' "$GEN_HOME/.codex/config.toml"
assert_exit_zero "grok config declares all mode aliases" \
    grep -q 'model.grok-aws' "$GEN_HOME/.grok/config.toml"
assert_exit_zero "grok config disables self-update" \
    grep -q 'auto_update = false' "$GEN_HOME/.grok/config.toml"
assert_exit_zero "opencode config is valid JSON" \
    python3 -c "import json,sys;json.load(open('$GEN_HOME/.config/opencode/opencode.json'))"
assert_exit_zero "opencode config has no literal key material" \
    grep -q '{env:ZHIPU_API_KEY}' "$GEN_HOME/.config/opencode/opencode.json"

# Installer PATH blocks must be stripped from ~/.bashrc (they append AFTER
# maude-path.sh and would put their dirs ahead of ~/bin, bypassing the wrapper)
if [[ -f "$GEN_HOME/.bashrc" ]]; then
    assert_exit_nonzero "codex installer PATH block stripped from .bashrc" \
        grep -q '>>> Codex installer >>>' "$GEN_HOME/.bashrc"
    assert_exit_nonzero "grok installer PATH block stripped from .bashrc" \
        grep -q '>>> grok installer >>>' "$GEN_HOME/.bashrc"
else
    skip "installers did not create a .bashrc (offline?) — strip untestable"
fi

# User-owned config (marker removed) must be preserved
sed -i 's/^# managed by maude$/# mine now/' "$GEN_HOME/.codex/config.toml"
echo '# user addition' >> "$GEN_HOME/.codex/config.toml"
( HOME="$GEN_HOME" MAUDE_RAW="file://$REPO_ROOT/light" update_codex >/dev/null 2>&1 ) || true
assert_exit_zero "codex config: user-owned file preserved" \
    grep -q '# user addition' "$GEN_HOME/.codex/config.toml"

rm -rf "$GEN_HOME"

# ── Pipeline wiring ───────────────────────────────────────────────────
assert_exit_zero "bootstrap lib loop includes reviewer libs" \
    grep -q 'gemini.sh llm-mode.sh codex.sh opencode.sh grok.sh' "$REPO_ROOT/light/maude-bootstrap.sh"
assert_exit_zero "bootstrap installs reviewers below gemini" \
    grep -q 'for _tool in codex opencode grok' "$REPO_ROOT/light/maude-bootstrap.sh"
assert_exit_zero "update_all lib list includes reviewer libs" \
    grep -q 'gemini.sh llm-mode.sh codex.sh opencode.sh grok.sh' "$REPO_ROOT/light/maude"
assert_exit_zero "update_all updates reviewers below gemini" \
    grep -q 'for _rtool in codex opencode grok' "$REPO_ROOT/light/maude"

# Repo-root skills committed for all three tools
for _skill in codex opencode grok; do
    assert_file_exists "repo skill $_skill/SKILL.md" "$REPO_ROOT/.claude/skills/$_skill/SKILL.md"
    assert_exit_zero "skill $_skill has frontmatter" \
        bash -c "head -n1 '$REPO_ROOT/.claude/skills/$_skill/SKILL.md' | grep -q '^---$'"
done

suite_summary
