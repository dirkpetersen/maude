#!/usr/bin/env bash
# tests/test-ai-tools.sh - TUI "AI tool" selector helpers and link_kiro_skills.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "AI tool selector"

MAUDE_PY="${SCRIPT_DIR}/../light/maude.py"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir -p "$d/home"

# textual may be absent: pull only the needed names out of maude.py.
cat > "$d/run.py" <<'PY'
import ast, os, shutil, subprocess, sys
from pathlib import Path
src = open(sys.argv[1]).read()
want = {"AI_TOOLS", "DEFAULT_TOOL", "TOOL_FILE", "OLD_TOOL_FILE", "MODELS", "DEFAULT_MODEL", "STATE_DIR",
        "MODEL_FILE", "OLD_MODEL_FILE", "EFFORTS", "DEFAULT_EFFORT", "EFFORT_LABELS", "read_tool", "save_tool",
        "tool_command", "reconcile", "effort_args", "parse_model_pref", "format_model_pref", "maude_dir", "_decode_state", "_migrate_state",
        "_read_state", "_write_state", "read_model_effort", "_save_model_effort", "read_model", "read_effort",
        "save_model", "save_effort"}
ns = {"os": os, "shutil": shutil, "subprocess": subprocess, "Path": Path, "IS_WIN": False}
for n in ast.parse(src).body:
    names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
    if names & want:
        exec(compile(ast.Module([n], []), "maude.py", "exec"), ns)
print(eval(sys.argv[2], ns))
PY
py() { HOME="$d/home" python3 -I "$d/run.py" "$MAUDE_PY" "$1"; }

assert_eq "tool file missing -> default" "Claude Code" "$(py 'read_tool()')"
assert_eq "save + read"       "Crush"       "$(py 'save_tool("Crush") or read_tool()')"
assert_eq "tool saved under ~/.maude/maude-tool" "Crush" "$(cat "$d/home/.maude/maude-tool")"
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
assert_eq "maude_dir creates 0700" "700" "$(HOME="$d/home-fresh" python3 -I "$d/run.py" "$MAUDE_PY" 'maude_dir() and 0' >/dev/null; stat -c %a "$d/home-fresh/.maude")"

# model[,effort] format: parse + format + persistence
assert_eq "parse model,effort"  "('opus', 'high')"   "$(py 'parse_model_pref("opus,high")')"
assert_eq "parse model only"    "('haiku', '')"      "$(py 'parse_model_pref("haiku")')"
assert_eq "parse trailing comma" "('sonnet', '')"    "$(py 'parse_model_pref("sonnet,")')"
assert_eq "parse case/space"    "('fable', 'xhigh')" "$(py 'parse_model_pref(" fable , XHigh \n")')"
assert_eq "parse bad effort"    "('opus', '')"       "$(py 'parse_model_pref("opus,turbo")')"
assert_eq "parse bad model"     "('opus', 'max')"    "$(py 'parse_model_pref("gpt9,max")')"
assert_eq "parse empty"         "('opus', '')"       "$(py 'parse_model_pref("")')"
assert_eq "parse injection"     "('opus', '')"       "$(py 'parse_model_pref("x;rm -rf /,$(id)")')"
assert_eq "format with effort"  "opus,high"          "$(py 'format_model_pref("opus","high")')"
assert_eq "format default"      "opus"               "$(py 'format_model_pref("opus","")')"
assert_eq "format bad effort"   "opus"               "$(py 'format_model_pref("opus","zzz")')"
assert_eq "save model keeps effort" "haiku,low" "$(py 'save_effort("low") or save_model("haiku") or MODEL_FILE.read_text()')"
assert_eq "save effort keeps model" "haiku,max" "$(py 'save_effort("max") or MODEL_FILE.read_text()')"
assert_eq "effort back to default"  "haiku"     "$(py 'save_effort("") or MODEL_FILE.read_text()')"
assert_eq "invalid effort ignored"  "haiku"     "$(py 'save_effort("bogus") or MODEL_FILE.read_text()')"
printf 'zzz,bogus' > "$d/home/.maude/maude-model"
assert_eq "bad file -> defaults" "('opus', '')" "$(py 'read_model_effort()')"
# undecodable / UTF-16 (PowerShell 5.1 redirection) / UTF-8-BOM files must not crash
printf '\377\376\377' > "$d/home/.maude/maude-model"
assert_eq "undecodable model file -> defaults" "('opus', '')" "$(py 'read_model_effort()')"
python3 -I -c 'import sys; open(sys.argv[1], "wb").write("fable,xhigh\r\n".encode("utf-16"))' "$d/home/.maude/maude-model"
assert_eq "UTF-16 BOM model file" "('fable', 'xhigh')" "$(py 'read_model_effort()')"
python3 -I -c 'import sys; open(sys.argv[1], "wb").write("\ufeffsonnet,low".encode("utf-8"))' "$d/home/.maude/maude-model"
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
assert_eq "effort args"         "['--effort', 'high']" "$(py 'effort_args("high")')"
assert_eq "effort args default" "[]"                   "$(py 'effort_args("")')"
assert_eq "effort args invalid" "[]"                   "$(py 'effort_args("x; id")')"
assert_eq "claude + effort"     "['claude', 'opus', '--effort', 'high', '--continue']" "$(py 'tool_command("Claude Code","opus",False,"high")')"
assert_eq "claude fresh effort" "['claude', 'opus', '--effort', 'max']"                 "$(py 'tool_command("Claude Code","opus",True,"max")')"
assert_eq "kiro ignores effort" "['kiro-cli', 'chat']"                                  "$(py 'tool_command("Kiro CLI","opus",True,"max")')"

assert_eq "claude resume"   "['claude', 'opus', '--continue']"  "$(py 'tool_command("Claude Code","opus",False)')"
assert_eq "claude fresh"    "['claude', 'haiku']"               "$(py 'tool_command("Claude Code","haiku",True)')"
assert_eq "kiro resume"     "['kiro-cli', 'chat', '--resume']"  "$(py 'tool_command("Kiro CLI","opus",False)')"
assert_eq "kiro fresh"      "['kiro-cli', 'chat']"              "$(py 'tool_command("Kiro CLI","opus",True)')"
assert_eq "opencode resume" "['opencode', '--continue']"        "$(py 'tool_command("OpenCode","sonnet",False)')"
assert_eq "opencode fresh"  "['opencode']"                      "$(py 'tool_command("OpenCode","sonnet",True)')"
assert_eq "crush resume"    "['crush', '--continue']"           "$(py 'tool_command("Crush","opus",False)')"
assert_eq "crush fresh"     "['crush']"                         "$(py 'tool_command("Crush","opus",True)')"

assert_eq "fable picked -> Claude Code" "('fable', 'Claude Code')" "$(py 'reconcile("fable","Crush","model")')"
assert_eq "tool picked w/ fable -> opus" "('opus', 'Crush')"      "$(py 'reconcile("fable","Crush","tool")')"
assert_eq "startup inconsistent -> opus" "('opus', 'Kiro CLI')"   "$(py 'reconcile("fable","Kiro CLI","tool")')"
assert_eq "consistent unchanged"         "('sonnet', 'Crush')"    "$(py 'reconcile("sonnet","Crush","model")')"
assert_eq "fable + Claude Code ok"       "('fable', 'Claude Code')" "$(py 'reconcile("fable","Claude Code","tool")')"

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
