# kanna.sh — sourced by maude-bootstrap.sh and the maude CLI.
# Ensures kanna's data dir exists (it crashes without ~/.kanna) and seeds a
# sensible default settings.json on first use so the user is never prompted
# through kanna's setup / provider wizard.

: "${KANNA_PKG:=kanna-code@0.88.0}"   # keep in sync with light/maude KANNA_PKG

# Return 0 when kanna-code is installed at a version other than the pin.
kanna_needs_fix() {
    local pkg="$KANNA_PKG"
    local pj="$HOME/.bun/install/global/node_modules/kanna-code/package.json"
    [ -r "$pj" ] && command -v bun >/dev/null 2>&1 || return 1
    KANNA_WANT="${pkg##*@}"
    KANNA_HAVE=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$pj" 2>/dev/null | head -1)
    [ -n "$KANNA_HAVE" ] && [ "$KANNA_HAVE" != "$KANNA_WANT" ]
}

# Reinstall kanna-code at the pin (kanna's own self-update could move it to an untested version).
_kanna_fix_version() {
    kanna_needs_fix || return 0
    echo "Updating kanna to $KANNA_WANT. This can take up to 2 minutes, please wait..." >&2
    ( flock -w 600 9 2>/dev/null || true; bun install -g "$KANNA_PKG" ) 9>"$HOME/.maude-pkg-install.lock" >&2 || true
    if declare -F ensure_tool_symlinks >/dev/null; then ensure_tool_symlinks; fi
    return 0
}

# kanna 0.78+ creates a unix socket in ~/.kanna, which drvfs cannot hold
# ("kanna is already starting"). So ~/.kanna is a real local dir and only
# ~/.kanna/data links to the host share. Idempotent, never fails the caller.
ensure_kanna_layout() {
    local k="$HOME/.kanna" h="$HOME/Maude/.kanna" e how bak
    # An empty ~/Maude is the bare mountpoint: drvfs failed, treat as unmounted.
    if [ -z "$(ls -A "$HOME/Maude" 2>/dev/null)" ]; then
        [ -L "$k" ] && [ ! -e "$k" ] && rm -f "$k"
        [ -L "$k/data" ] && [ ! -e "$k/data" ] && rm -f "$k/data"
        mkdir -p "$k/data" 2>/dev/null
        return 0
    fi
    if [ -L "$k" ]; then
        echo "Migrating ~/.kanna: only data/ stays on the Windows share" >&2
        rm -f "$k"
    fi
    mkdir -p "$k" "$h/data" 2>/dev/null || return 0
    for e in "$h"/* "$h"/.[!.]*; do
        case "$e" in */data|*.sock) continue ;; esac
        [ -e "$e" ] || [ -L "$e" ] || continue
        cp -an "$e" "$k/" 2>/dev/null || true
    done
    if [ -d "$k/data" ] && [ ! -L "$k/data" ]; then
        if [ -z "$(ls -A "$h/data" 2>/dev/null)" ]; then
            cp -a "$k/data/." "$h/data/" 2>/dev/null
            how="copied to the host share"
        else
            how="set aside, host data is not empty"
        fi
        bak="$k/data.local-backup.$(date +%s)"
        mv "$k/data" "$bak" 2>/dev/null || return 0
        echo "Migrating ~/.kanna: old local data $how, kept in $bak" >&2
    fi
    [ -L "$k/data" ] || ln -sfn "$h/data" "$k/data" 2>/dev/null
    return 0
}

ensure_kanna_ready() {
    ensure_kanna_layout
    _kanna_fix_version
    local kdir="$HOME/.kanna"
    mkdir -p "$kdir/data" 2>/dev/null

    local data="$kdir/data"
    [ -d "$data" ] || return 0   # couldn't create it; don't try to seed

    local settings="$data/settings.json"
    [[ -f "$settings" ]] && return 0   # never overwrite an existing settings file

    # Fresh per-install anonymous analytics id (do not hardcode a shared one).
    local uid
    uid="anon_$(cat /proc/sys/kernel/random/uuid 2>/dev/null \
        || python3 -c 'import uuid;print(uuid.uuid4())' 2>/dev/null)"
    [ "$uid" = "anon_" ] && uid="anon_$$-${RANDOM}${RANDOM}"

    local tmp="$settings.new.$$"
    cat > "$tmp" <<EOF
{
  "analyticsEnabled": true,
  "analyticsUserId": "$uid",
  "browserSettingsMigrated": true,
  "theme": "system",
  "chatSoundPreference": "never",
  "chatSoundId": "funk",
  "terminal": {
    "scrollbackLines": 1000,
    "minColumnWidth": 450,
    "webglRenderer": false
  },
  "editor": {
    "preset": "cursor",
    "commandTemplate": "cursor {path}"
  },
  "defaultProvider": "claude",
  "providerDefaults": {
    "claude": {
      "model": "opus",
      "modelOptions": {
        "reasoningEffort": "high",
        "contextWindow": "200k",
        "fastMode": false
      },
      "planMode": false,
      "autoPlan": false
    },
    "codex": {
      "model": "gpt-5.4",
      "modelOptions": {
        "reasoningEffort": "high",
        "fastMode": false
      },
      "planMode": false,
      "autoPlan": false
    },
    "cursor": {
      "model": "composer-2.5",
      "modelOptions": {
        "fastMode": false
      },
      "planMode": false,
      "autoPlan": false
    },
    "pi": {
      "model": "~anthropic/claude-fable-latest",
      "modelOptions": {
        "reasoningEffort": "medium"
      },
      "planMode": false,
      "autoPlan": false
    }
  },
  "newSidebarEnabled": true,
  "newProjectsDirectory": "~/Maude/Projects",
  "setupShown": true,
  "setupCompleted": true,
  "setupDismissed": true
}
EOF
    mv "$tmp" "$settings" 2>/dev/null || rm -f "$tmp"
}
