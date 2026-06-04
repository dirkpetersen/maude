#!/bin/bash
# Claude Code status line: "~/path/to/cwd  [NN% free]"
# Reads .context_window.remaining_percentage from Claude Code's JSON input
# so our number matches Claude's own indicator exactly.

set -u

input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // ""')
display_cwd="${cwd/#$HOME/"~"}"

pct_str=""
remaining=$(printf '%s' "$input" | jq -r '.context_window.remaining_percentage // empty' 2>/dev/null)
if [[ "$remaining" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    pct=$(printf '%.0f' "$remaining")
    pct_str="  [${pct}% free]"
fi

printf '%s%s\n' "$display_cwd" "$pct_str"
