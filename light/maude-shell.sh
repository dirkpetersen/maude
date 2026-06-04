# /etc/profile.d/maude-shell.sh — sourced by login shells.
# Consolidates Maude's shell tweaks: pip-without-venv, custom PS1,
# help() override, menu() shortcut, and tab completion.

# Allow `pip install` without a venv (safe inside the sandbox)
export PIP_BREAK_SYSTEM_PACKAGES=1

# Maude PS1: show user@_ instead of user@hostname
PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u\[\033[00m\]@\[\033[01;34m\]_\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '

# Override built-in `help`: bare "help" points beginners at maude help.
help() {
    if [[ $# -eq 0 ]]; then
        echo "Please type 'maude help <ENTER>' for help"
    else
        builtin help "$@"
    fi
}

# Shortcut: typing "menu" launches the Maude TUI
menu() {
    maude tui
}

# Maude tab completion
_maude_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ "$COMP_CWORD" -eq 1 ]]; then
        local cmds="web tui list ls delete rm help"
        local projects=""
        if [[ -d "$HOME/Maude/Projects" ]]; then
            projects=$(ls -d "$HOME/Maude/Projects"/*/ 2>/dev/null | xargs -I{} basename {} 2>/dev/null)
        fi
        COMPREPLY=( $(compgen -W "$cmds $projects" -- "$cur") )
    elif [ "$COMP_CWORD" -eq 2 ] && [[ "$prev" == "delete" || "$prev" == "rm" ]]; then
        local projects=""
        if [[ -d "$HOME/Maude/Projects" ]]; then
            projects=$(ls -d "$HOME/Maude/Projects"/*/ 2>/dev/null | xargs -I{} basename {} 2>/dev/null)
        fi
        COMPREPLY=( $(compgen -W "$projects" -- "$cur") )
    fi
}
complete -F _maude_complete maude
