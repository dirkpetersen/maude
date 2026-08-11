# User-owned welcome logic, installed to ~/.local/lib/maude/welcome.sh and
# loaded by the thin /etc/profile.d/maude-welcome.sh stub. Refreshed by
# 'maude update' -- sourced by login shells. Bootstraps keychain, then either
# auto-launches the TUI or prints the static welcome banner.

# ── Ensure DANGER-ZONE.txt is present on the shared mount ────────────
if [[ -d "$HOME/Maude" ]] && [[ ! -f "$HOME/Maude/DANGER-ZONE.txt" ]]; then
    curl -fsSL --max-time 10 "https://raw.githubusercontent.com/dirkpetersen/maude/main/light/DANGER-ZONE.txt" \
        -o "$HOME/Maude/DANGER-ZONE.txt" 2>/dev/null || true
fi

# ── Bootstrap keychain before the TUI launches ───────────────────────
# If the user ran `maude github` and set a passphrase on their SSH key,
# keychain keeps ssh-agent alive across shell logins so the passphrase
# is entered once per WSL session, not every TUI launch. We do this BEFORE
# `maude tui` so the TUI inherits a persistent SSH_AUTH_SOCK.
if [[ -t 1 ]] && [[ -z "$MAUDE_WELCOMED" ]] && command -v keychain >/dev/null 2>&1; then
    _maude_key=""
    for _k in "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa"; do
        [[ -f "$_k" ]] && { _maude_key="$_k"; break; }
    done
    # Step 1: attach to (or start) keychain's persistent ssh-agent without
    # loading any key. Sets SSH_AUTH_SOCK in our env; never prompts.
    eval "$(keychain --quiet --noask --eval --agents ssh)" 2>/dev/null || true
    if [[ -n "$_maude_key" ]]; then
        # Step 2: is our key already loaded? Compare fingerprints.
        _maude_fp=$(ssh-keygen -lf "$_maude_key" 2>/dev/null | awk '{print $2}')
        if [[ -n "$_maude_fp" ]] && ! ssh-add -l 2>/dev/null | grep -qF "$_maude_fp"; then
            G=$'\033[1;32m'; C=$'\033[1;36m'; B=$'\033[1;37m'; D=$'\033[2m'; N=$'\033[0m'
            printf '\n'
            printf '  %s### Unlock your GitHub SSH key ###%s\n' "$G" "$N"
            printf '  %s%s%s\n' "$D" "--------------------------------------------" "$N"
            printf '  Enter the passphrase you set in %smaude github%s\n' "$C" "$N"
            printf '  to load %s%s%s into ssh-agent for this session.\n' "$B" "$_maude_key" "$N"
            printf '  %s(Press Enter on a blank line to skip -- you can unlock later from the TUI.)%s\n' "$D" "$N"
            printf '\n'
            eval "$(keychain --quiet --eval --agents ssh "$_maude_key")" 2>/dev/null || true
        fi
        unset _maude_fp
    fi
    unset _maude_key _k
fi

# Auto-start the TUI by default; users can opt out with ~/.maude-tui-disabled.
# Guard with MAUDE_WELCOMED so we don't re-launch when .bashrc re-sources
# this script (it is already sourced via /etc/profile.d on login shells).
if [[ -t 1 ]] && [[ -z "$MAUDE_WELCOMED" ]] && [[ ! -f "$HOME/.maude-tui-disabled" ]] && command -v maude >/dev/null 2>&1; then
    export MAUDE_WELCOMED=1
    maude tui
    return 0 2>/dev/null || true
fi

# Show welcome only in interactive terminals and only once per session
if [[ -t 1 ]] && [[ -z "$MAUDE_WELCOMED" ]]; then
    export MAUDE_WELCOMED=1
    G='\033[1;32m'   # bright green
    C='\033[1;36m'   # bright cyan
    Y='\033[1;33m'   # bright yellow
    B='\033[1;37m'   # bright white
    N='\033[0m'      # reset
    printf "\n"
    printf "${G}  __  __                 _      ${N}\n"
    printf "${G} |  \/  | __ _ _   _  __| | ___ ${N}\n"
    printf "${G} | |\/| |/ _\` | | | |/ _\` |/ _ \\\\${N}\n"
    printf "${G} | |  | | (_| | |_| | (_| |  __/${N}\n"
    printf "${G} |_|  |_|\__,_|\__,_|\__,_|\___|${N}\n"
    printf "\n"
    printf "  ${B}Agentic coding sandbox${N}  -  Ubuntu LTS\n"
    printf "\n"
    printf "  Always type the command '${B}maude${N}' followed by more words as the instruction:\n"
    printf "\n"
    printf "  ${C}maude project-name${N}   Create or open a coding project\n"
    printf "  ${C}maude tui${N}            Interactive project launcher (Textual UI)\n"
    printf "  ${C}menu${N}                 Shortcut for 'maude tui'\n"
    printf "  ${C}maude web${N}            Launch web UI (kanna)\n"
    printf "  ${C}maude github${N}         Wizard for GitHub identity, SSH/GPG keys, signing\n"
    printf "  ${C}maude update${N}         Update maude CLI, Claude Code, the TUI, and kanna\n"
    printf "  ${C}maude list${N}           Show your projects\n"
    printf "  ${C}maude delete name${N}    Delete a project (moves to .deleted/)\n"
    printf "  ${C}maude help${N}           Full usage info\n"
    printf "\n"
    printf "  ${Y}mom install <pkg>${N}   Install system packages (no sudo needed)\n"
    printf "\n"
    printf "  ${B}Tips:${N}\n"
    printf "    Screen split:    Alt+Shift+Plus (vertical) | Alt+Shift+Minus (horizontal)\n"
    printf "    Share an image:  drop it into your Maude folder on Windows, then ask Claude\n"
    printf "                     to look at it (e.g. ~/Maude/Projects/<name>/screenshot.png)\n"
    printf "    Activate mic:    ${B}Win+H${N}  (Windows voice dictation)\n"
    printf "    TUI auto-launch: untick ${B}Start TUI with Maude${N} in the sidebar to opt out\n"
    printf "\n"
    if [[ ! -f "$HOME/.aws/credentials" ]] && [[ ! -f "$HOME/.azure/clauderc" ]]; then
        printf "  ${Y}LLM service credentials not yet set up.${N}\n"
        printf "  ${Y}Open the TUI ('${B}maude tui${Y}' or '${B}menu${Y}') and click ${B}Set Creds${Y}.${N}\n"
        printf "  ${Y}You can pick: paste exports / AWS Bedrock / Azure Foundry.${N}\n"
        printf "\n"
    fi
fi
