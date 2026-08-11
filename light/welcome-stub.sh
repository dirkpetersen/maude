# /etc/profile.d/maude-welcome.sh -- thin, stable loader installed once as root
# by root-bootstrap.sh. The real welcome logic lives in a USER-owned copy that
# 'maude update' (and root-bootstrap.sh's initial seed) keep fresh, so future
# welcome/prompt changes ship through 'maude update' instead of needing a root
# re-install of this profile.d script. Keep this stub minimal -- it is NOT
# refreshable after install, so it must stay correct and essentially frozen.
_mw="${HOME}/.local/lib/maude/welcome.sh"
if [ ! -r "$_mw" ] && [ -t 1 ]; then
    # Self-heal: user copy missing. Best-effort one-time fetch, VALIDATED
    # before use (a partial download or an HTML proxy block-page must never be
    # sourced into the login shell). Temp file in the same dir so mv is atomic.
    mkdir -p "${HOME}/.local/lib/maude" 2>/dev/null
    _mwt="${_mw}.new.$$"
    if curl -fsSL --max-time 10 \
        "https://raw.githubusercontent.com/dirkpetersen/maude/main/light/welcome.sh" \
        -o "$_mwt" 2>/dev/null \
        && [ -s "$_mwt" ] && grep -q 'MAUDE_WELCOMED' "$_mwt" 2>/dev/null; then
        mv "$_mwt" "$_mw" 2>/dev/null || rm -f "$_mwt"
    else
        rm -f "$_mwt"
    fi
    unset _mwt
fi
[ -r "$_mw" ] && . "$_mw"
unset _mw
