# update-skills.sh — sourced by maude-bootstrap.sh and the maude CLI.
# Clones github.com/anthropics/skills shallowly and copies the named
# skill folders into ~/.claude/skills, replacing any existing copy.

update_skills() {
    local skills_dir="$HOME/.claude/skills"
    mkdir -p "$skills_dir"
    local tmp; tmp=$(mktemp -d)
    local count=0
    if git clone --quiet --depth 1 https://github.com/anthropics/skills.git "$tmp" 2>/dev/null; then
        local skill
        for skill in claude-api doc-coauthoring docx mcp-builder pdf pptx skill-creator xlsx; do
            if [[ -d "$tmp/skills/$skill" ]]; then
                [[ -L "$skills_dir/$skill" ]] && rm -f "$skills_dir/$skill"
                cp -af "$tmp/skills/$skill" "$skills_dir/"
                count=$((count + 1))
            fi
        done
        rm -rf "$tmp"
        printf '%s\n' "$count"
        return 0
    fi
    rm -rf "$tmp"
    return 1
}
