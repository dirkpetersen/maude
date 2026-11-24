#!/usr/bin/env bash
# tests/test-ai-tools.sh - TUI "Agent" selector helpers (state files, model[,effort], command lines)
# and link_kiro_skills. The model lists, settings and restriction are in tests/test-models-config.sh.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "Agent selector"

MAUDE_PY="${SCRIPT_DIR}/../light/maude.py"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir -p "$d/home"

# textual may be absent: tests/maude-extract.py pulls only the needed names out of maude.py.
py() { HOME="$d/home" python3 -I "${SCRIPT_DIR}/maude-extract.py" "$MAUDE_PY" "$1"; }

assert_eq "agents, in order" "('Claude Code', 'Codex', 'Antigravity', 'Grok CLI', 'Kiro CLI', 'GitHub Copilot CLI', 'OpenCode', 'Crush')" "$(py 'AGENTS')"
assert_eq "tool file missing -> default" "Claude Code" "$(py 'read_tool()')"
assert_eq "save + read"       "Crush"       "$(py 'save_tool("Crush") or read_tool()')"
assert_eq "new agent saved"   "GitHub Copilot CLI" "$(py 'save_tool("GitHub Copilot CLI") or read_tool()')"
assert_eq "tool saved under ~/.maude/maude-tool" "GitHub Copilot CLI" "$(cat "$d/home/.maude/maude-tool")"
printf 'bogus\n' > "$d/home/.maude/maude-tool"
assert_eq "invalid -> default" "Claude Code" "$(py 'read_tool()')"
assert_eq "save invalid ignored" "bogus" "$(py 'save_tool("nope") or TOOL_FILE.read_text().strip()')"

# migration from the old ~/.maude-tool / ~/.maude-model (new file missing): value moved, old file removed
rm -rf "$d/home/.maude"
printf 'Kiro CLI\n' > "$d/home/.maude-tool"
assert_eq "tool migrated" "Kiro CLI" "$(py 'read_tool()')"
assert_eq "new tool file written" "Kiro CLI" "$(cat "$d/home/.maude/maude-tool")"
assert_eq "old tool file removed" "no" "$([[ -e "$d/home/.maude-tool" ]] && echo yes || echo no)"
printf 'Crush' > "$d/home/.maude-tool"
assert_eq "new file wins over old" "Kiro CLI" "$(py 'read_tool()')"
rm -f "$d/home/.maude-tool"
printf 'sonnet\n' > "$d/home/.maude-model"
assert_eq "model migrated" "('sonnet', '')" "$(py 'read_model_effort()')"
assert_eq "new model file written" "sonnet" "$(cat "$d/home/.maude/maude-model")"
assert_eq "old model file removed" "no" "$([[ -e "$d/home/.maude-model" ]] && echo yes || echo no)"
assert_eq "state dir is private" "700" "$(stat -c %a "$d/home/.maude")"
assert_eq "maude_dir returns the dir" "$d/home/.maude" "$(py 'str(maude_dir())')"
mkdir -p "$d/home-fresh"
assert_eq "maude_dir creates 0700" "700" "$(HOME="$d/home-fresh" python3 -I "${SCRIPT_DIR}/maude-extract.py" "$MAUDE_PY" 'maude_dir() and 0' >/dev/null; stat -c %a "$d/home-fresh/.maude")"

# model[,effort] format: parse + format + persistence (any valid model id is kept; auto is the default)
assert_eq "parse model,effort"  "('opus', 'high')"   "$(py 'parse_model_pref("opus,high")')"
assert_eq "parse model only"    "('haiku', '')"      "$(py 'parse_model_pref("haiku")')"
assert_eq "parse auto,effort"   "('auto', 'high')"   "$(py 'parse_model_pref("auto,high")')"
assert_eq "parse trailing comma" "('sonnet', '')"    "$(py 'parse_model_pref("sonnet,")')"
assert_eq "parse case/space"    "('fable', 'xhigh')" "$(py 'parse_model_pref(" fable , XHigh \n")')"
assert_eq "parse bad effort"    "('opus', '')"       "$(py 'parse_model_pref("opus,turbo")')"
assert_eq "parse any model id"  "('gpt-6.2-sol', 'max')" "$(py 'parse_model_pref("gpt-6.2-sol,max")')"
assert_eq "parse provider id"   "('anthropic/claude-opus-5-5', '')" "$(py 'parse_model_pref("anthropic/claude-opus-5-5")')"
assert_eq "parse bad model"     "('auto', 'max')"    "$(py 'parse_model_pref("bad model!,max")')"
assert_eq "parse empty"         "('auto', '')"       "$(py 'parse_model_pref("")')"
assert_eq "parse injection"     "('auto', '')"       "$(py 'parse_model_pref("x;rm -rf /,$(id)")')"
assert_eq "parse leading dash"  "('auto', '')"       "$(py 'parse_model_pref("--yolo")')"
assert_eq "format with effort"  "opus,high"          "$(py 'format_model_pref("opus","high")')"
assert_eq "format auto effort"  "auto,high"          "$(py 'format_model_pref("auto","high")')"
assert_eq "format default"      "opus"               "$(py 'format_model_pref("opus","")')"
assert_eq "format bad effort"   "opus"               "$(py 'format_model_pref("opus","zzz")')"
assert_eq "save model keeps effort" "haiku,low" "$(py 'save_effort("low") or save_model("haiku") or MODEL_FILE.read_text()')"
assert_eq "save effort keeps model" "haiku,max" "$(py 'save_effort("max") or MODEL_FILE.read_text()')"
assert_eq "effort back to default"  "haiku"     "$(py 'save_effort("") or MODEL_FILE.read_text()')"
assert_eq "invalid effort ignored"  "haiku"     "$(py 'save_effort("bogus") or MODEL_FILE.read_text()')"
assert_eq "any model id saved"      "gpt-6.2-sol" "$(py 'save_model("gpt-6.2-sol") or MODEL_FILE.read_text()')"
assert_eq "invalid model not saved" "gpt-6.2-sol" "$(py 'save_model("a b") or MODEL_FILE.read_text()')"
printf 'zzz yyy,bogus' > "$d/home/.maude/maude-model"
assert_eq "bad file -> defaults" "('auto', '')" "$(py 'read_model_effort()')"
# undecodable / UTF-16 (PowerShell 5.1 redirection) / UTF-8-BOM files must not crash
printf '\377\376\377' > "$d/home/.maude/maude-model"
assert_eq "undecodable model file -> defaults" "('auto', '')" "$(py 'read_model_effort()')"
python3 -I -c 'import sys; open(sys.argv[1], "wb").write("fable,xhigh\r\n".encode("utf-16"))' "$d/home/.maude/maude-model"
assert_eq "UTF-16 BOM model file" "('fable', 'xhigh')" "$(py 'read_model_effort()')"
python3 -I -c 'import sys; open(sys.argv[1], "wb").write("﻿sonnet,low".encode("utf-8"))' "$d/home/.maude/maude-model"
assert_eq "UTF-8 BOM model file" "('sonnet', 'low')" "$(py 'read_model_effort()')"
printf '\377\376' > "$d/home/.maude/maude-tool"
assert_eq "undecodable tool file -> default" "Claude Code" "$(py 'read_tool()')"
python3 -I -c 'import sys; open(sys.argv[1], "wb").write("Crush".encode("utf-16"))' "$d/home/.maude/maude-tool"
assert_eq "UTF-16 BOM tool file" "Crush" "$(py 'read_tool()')"
rm -f "$d/home/.maude/maude-model" "$d/home/.maude/maude-tool"
python3 -I -c 'import sys; open(sys.argv[1], "wb").write("haiku,high".encode("utf-16"))' "$d/home/.maude-model"
assert_eq "UTF-16 legacy file migrated" "('haiku', 'high')" "$(py 'read_model_effort()')"
assert_eq "UTF-16 legacy file removed" "no" "$([[ -e "$d/home/.maude-model" ]] && echo yes || echo no)"
printf '\377' > "$d/home/.maude-tool"; rm -f "$d/home/.maude/maude-tool"
assert_eq "undecodable legacy tool: no crash" "Claude Code" "$(py 'read_tool()')"
# an existing old-style file ("opus", "sonnet,high") keeps working
printf 'opus,high' > "$d/home/.maude/maude-model"
assert_eq "old-style file still read" "('opus', 'high')" "$(py 'read_model_effort()')"

# effort flags: Claude Code and Antigravity --effort, Codex -c model_reasoning_effort=, none elsewhere
assert_eq "effort args"         "['--effort', 'high']" "$(py 'effort_args("high")')"
assert_eq "effort args default" "[]"                   "$(py 'effort_args("")')"
assert_eq "effort args invalid" "[]"                   "$(py 'effort_args("x; id")')"
assert_eq "effort args codex"   "['-c', 'model_reasoning_effort=xhigh']" "$(py 'effort_args("xhigh","Codex")')"
assert_eq "effort args agy"     "['--effort', 'max']"  "$(py 'effort_args("max","Antigravity")')"
assert_eq "effort args kiro"    "[]"                   "$(py 'effort_args("max","Kiro CLI")')"
assert_eq "effort agents"       "('Claude Code', 'Codex', 'Antigravity')" "$(py 'EFFORT_AGENTS')"

# command lines per agent (model id + effort + resume)
assert_eq "claude + effort"     "['claude', 'opus', '--effort', 'high', '--continue']" "$(py 'tool_command("Claude Code","opus",False,"high")')"
assert_eq "claude fresh effort" "['claude', 'opus', '--effort', 'max']"                 "$(py 'tool_command("Claude Code","opus",True,"max")')"
assert_eq "claude auto: no model" "['claude', '--effort', 'high', '--continue']"        "$(py 'tool_command("Claude Code","auto",False,"high")')"
assert_eq "claude auto fresh"   "['claude']"                                            "$(py 'tool_command("Claude Code","auto",True)')"
assert_eq "claude resume"       "['claude', 'opus', '--continue']"  "$(py 'tool_command("Claude Code","opus",False)')"
assert_eq "claude fresh"        "['claude', 'haiku']"               "$(py 'tool_command("Claude Code","haiku",True)')"
assert_eq "claude bad model dropped" "['claude']"                   "$(py 'tool_command("Claude Code","--evil x",True)')"
assert_eq "codex resume"        "['codex', '-m', 'gpt-6.2-sol', '-c', 'model_reasoning_effort=high', 'resume', '--last']" "$(py 'tool_command("Codex","gpt-6.2-sol",False,"high")')"
assert_eq "codex fresh auto"    "['codex']"                         "$(py 'tool_command("Codex","auto",True)')"
assert_eq "agy resume"          "['agy', '--model', 'gemini-4-neon', '--effort', 'low', '-c']" "$(py 'tool_command("Antigravity","gemini-4-neon",False,"low")')"
assert_eq "agy fresh auto"      "['agy']"                           "$(py 'tool_command("Antigravity","auto",True)')"
assert_eq "grok resume"         "['grok', '-m', 'grok-4.7', '-c']"  "$(py 'tool_command("Grok CLI","grok-4.7",False,"high")')"
assert_eq "grok fresh auto"     "['grok']"                          "$(py 'tool_command("Grok CLI","auto",True)')"
assert_eq "kiro resume"         "['kiro-cli', 'chat', '--model', 'claude-opus-5.5', '--resume']" "$(py 'tool_command("Kiro CLI","claude-opus-5.5",False)')"
assert_eq "kiro auto resume"    "['kiro-cli', 'chat', '--resume']"  "$(py 'tool_command("Kiro CLI","auto",False)')"
assert_eq "kiro fresh"          "['kiro-cli', 'chat']"              "$(py 'tool_command("Kiro CLI","auto",True,"max")')"
assert_eq "copilot resume"      "['copilot', '--model', 'gpt-6.2-sol', '--continue']" "$(py 'tool_command("GitHub Copilot CLI","gpt-6.2-sol",False,"max")')"
assert_eq "copilot fresh auto"  "['copilot']"                       "$(py 'tool_command("GitHub Copilot CLI","auto",True)')"
assert_eq "opencode resume"     "['opencode', '--model', 'anthropic/claude-sonnet-5-5', '--continue']" "$(py 'tool_command("OpenCode","anthropic/claude-sonnet-5-5",False)')"
assert_eq "opencode fresh auto" "['opencode']"                      "$(py 'tool_command("OpenCode","auto",True)')"
assert_eq "crush resume"        "['crush', '--model', 'openai/gpt-6.2-sol', '--continue']" "$(py 'tool_command("Crush","openai/gpt-6.2-sol",False)')"
assert_eq "crush fresh auto"    "['crush']"                         "$(py 'tool_command("Crush","auto",True)')"

# reconcile: the model must be in the agent's list (else auto); no fable special case any more
assert_eq "model of the agent kept"      "('opus', 'Claude Code')"  "$(py 'reconcile("opus","Claude Code")')"
assert_eq "foreign model -> auto"        "('auto', 'Crush')"        "$(py 'reconcile("fable","Crush","tool")')"
assert_eq "fable ok in Claude Code"      "('fable', 'Claude Code')" "$(py 'reconcile("fable","Claude Code","tool")')"
assert_eq "agent's own id kept"          "('claude-opus-5.5', 'Kiro CLI')" "$(py 'reconcile("claude-opus-5.5","Kiro CLI","model")')"
assert_eq "auto is valid everywhere"     "('auto', 'Codex')"        "$(py 'reconcile("auto","Codex")')"

# link_kiro_skills: four cases with a fake HOME (cleanup targets "$d" only).
source "${SCRIPT_DIR}/../light/lib/cli-tools.sh"
lk() { ( HOME="$d/h"; link_kiro_skills ); }
want="$d/h/.claude/skills"
rm -rf "$d/h"; mkdir -p "$d/h/.claude/skills"
lk
assert_eq "missing -> linked" "$want" "$(readlink "$d/h/.kiro/skills")"
lk
assert_eq "right link kept" "$want" "$(readlink "$d/h/.kiro/skills")"
assert_eq "no backup for right link" "0" "$(ls -d "$d"/h/.kiro/skills.bak.* 2>/dev/null | wc -l)"
rm "$d/h/.kiro/skills"; mkdir "$d/h/.kiro/skills"; echo x > "$d/h/.kiro/skills/keep"
lk
assert_eq "real dir -> linked" "$want" "$(readlink "$d/h/.kiro/skills")"
assert_eq "real dir moved aside" "x" "$(cat "$d"/h/.kiro/skills.bak.*/keep)"
rm -rf "$d/h/.kiro"; mkdir -p "$d/h/.kiro"; ln -s /elsewhere "$d/h/.kiro/skills"
lk
assert_eq "wrong link -> relinked" "$want" "$(readlink "$d/h/.kiro/skills")"
assert_eq "wrong link kept as backup" "/elsewhere" "$(readlink "$d"/h/.kiro/skills.bak.*)"
suite_summary
