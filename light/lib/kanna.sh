# kanna.sh — sourced by maude-bootstrap.sh and the maude CLI.
# Ensures kanna's data dir exists (it crashes without ~/.kanna) and seeds a
# sensible default settings.json on first use so the user is never prompted
# through kanna's setup / provider wizard.

ensure_kanna_ready() {
    local kdir="$HOME/.kanna"
    # ~/.kanna is normally a symlink to ~/Maude/.kanna. A DANGLING symlink
    # (drvfs mount inactive, or the host folder deleted) makes a plain
    # `mkdir -p "$kdir/data"` fail with EEXIST and kanna still crashes. Repair:
    # if the intended target's parent exists, create the dir through the link;
    # otherwise drop the dead link and fall back to a real local dir
    # (non-persistent, but far better than crashing).
    if [ -L "$kdir" ] && [ ! -e "$kdir" ]; then
        local tgt; tgt=$(readlink "$kdir" 2>/dev/null)
        if [ -n "$tgt" ] && [ -d "$(dirname "$tgt")" ]; then
            mkdir -p "$tgt/data" 2>/dev/null
        else
            rm -f "$kdir" 2>/dev/null
            mkdir -p "$kdir/data" 2>/dev/null
        fi
    else
        mkdir -p "$kdir/data" 2>/dev/null
    fi

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
