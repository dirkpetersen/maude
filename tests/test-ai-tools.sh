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
want = {"AI_TOOLS", "DEFAULT_TOOL", "TOOL_FILE", "read_tool", "save_tool", "tool_command", "reconcile"}
ns = {"os": os, "shutil": shutil, "subprocess": subprocess, "Path": Path}
for n in ast.parse(src).body:
    names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
    if names & want:
        exec(compile(ast.Module([n], []), "maude.py", "exec"), ns)
print(eval(sys.argv[2], ns))
PY
py() { HOME="$d/home" python3 -I "$d/run.py" "$MAUDE_PY" "$1"; }

assert_eq "tool file missing -> default" "Claude Code" "$(py 'read_tool()')"
assert_eq "save + read"       "Crush"       "$(py 'save_tool("Crush") or read_tool()')"
printf 'bogus\n' > "$d/home/.maude-tool"
assert_eq "invalid -> default" "Claude Code" "$(py 'read_tool()')"
assert_eq "save invalid ignored" "bogus" "$(py 'save_tool("nope") or TOOL_FILE.read_text().strip()')"

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
