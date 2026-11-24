#!/usr/bin/env bash
# tests/test-models-config.sh - per-agent model lists (light/maude-models.json), the daily refresh,
# ~/.maude/settings.json, the restriction outside the sandbox and saved-model reconciliation.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "Model lists, settings, restriction"

MAUDE_PY="${SCRIPT_DIR}/../light/maude.py"
MODELS_JSON="${SCRIPT_DIR}/../light/maude-models.json"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir -p "$d/home"
py() { HOME="$d/home" python3 -I "${SCRIPT_DIR}/maude-extract.py" "$MAUDE_PY" "$1"; }
reset() { rm -rf "$d/home/.maude"; mkdir -p "$d/home"; }

# ── the shipped file and the embedded copy ────────────────────────────────────
assert_eq "embedded fallback == light/maude-models.json" "True" \
    "$(python3 -I -c 'import json,sys; print(json.loads(sys.argv[1]) == json.load(open(sys.argv[2], encoding="utf-8")))' \
        "$(py 'MODELS_FALLBACK_JSON')" "$MODELS_JSON")"
assert_eq "shipped file validates (every agent has a list)" "True" "$(py "validate_models_bytes(open(r'$MODELS_JSON','rb').read())")"
assert_eq "file lists no auto (built in)" "0" "$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(sum(1 for a in d["agents"].values() for m in a["models"] if m["id"]=="auto"))' "$MODELS_JSON")"
assert_eq "labels fit the column (<=9)" "0" "$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(sum(1 for a in d["agents"].values() for m in a["models"] if len(m["label"])>9))' "$MODELS_JSON")"

# ── lists: auto first, Claude models next, vendor models after ────────────────
reset
assert_eq "no files -> embedded: Claude Code" "['auto', 'fable', 'opus', 'sonnet', 'haiku']" "$(py '[l for l,_ in agent_models("Claude Code")]')"
for a in "Claude Code" "Kiro CLI" "GitHub Copilot CLI" "OpenCode" "Grok CLI" "Crush"; do
    assert_eq "$a: auto + fable/opus/sonnet/haiku first" "['auto', 'fable', 'opus', 'sonnet', 'haiku']" "$(py "[l for l,_ in agent_models('$a')][:5]")"
done
for a in "Claude Code" "Codex" "Antigravity" "Grok CLI" "Kiro CLI" "GitHub Copilot CLI" "OpenCode" "Crush"; do
    assert_eq "$a: first entry is auto, listed once" "('auto', 1)" "$(py "(agent_models('$a')[0][1], [i for _,i in agent_models('$a')].count('auto'))")"
done
assert_eq "Codex: OpenAI only after auto" "True" "$(py 'all(i.startswith("gpt-") for _,i in agent_models("Codex")[1:])')"
assert_eq "Antigravity: Gemini only after auto" "True" "$(py 'all(i.startswith("gemini-") for _,i in agent_models("Antigravity")[1:])')"
assert_eq "Grok CLI: Claude first, then grok" "True" "$(py '[i.split("-")[0] for _,i in agent_models("Grok CLI")[1:]] == ["claude"]*4 + ["grok"]*3')"
assert_eq "Kiro: Claude then GPT" "True" "$(py '[i.split("-")[0] for _,i in agent_models("Kiro CLI")[1:]] == ["claude"]*4 + ["gpt"]*3')"
assert_eq "Crush: Claude then OpenAI" "True" "$(py '[i.split("/")[0] for _,i in agent_models("Crush")[1:]] == ["anthropic"]*4 + ["openai"]*2')"
assert_eq "unknown agent: just auto" "[('auto', 'auto')]" "$(py 'agent_models("Nope")')"
assert_eq "pool is big enough for every list" "True" "$(py 'all(len(agent_models(a)) <= MODEL_SLOTS for a in AGENTS)')"

# ── merge: default file, user file (models replaces, extra appends), corrupt files ───
mkdir -p "$d/home/.maude"
cat > "$d/home/.maude/models-default.json" <<'JSON'
{"version":1,"agents":{"Codex":{"models":[{"label":"x1","id":"gpt-x1"}]}}}
JSON
assert_eq "default file replaces one agent" "['auto', 'x1']" "$(py '[l for l,_ in agent_models("Codex")]')"
assert_eq "other agents stay embedded" "['auto', 'fable', 'opus', 'sonnet', 'haiku']" "$(py '[l for l,_ in agent_models("Claude Code")]')"
cat > "$d/home/.maude/models.json" <<'JSON'
{"agents":{"Codex":{"extra":[{"label":"mine","id":"my-model"},{"label":"dup","id":"gpt-x1"}]},
           "Crush":{"models":[{"label":"auto","id":"auto"},{"label":"only","id":"p/only"},{"label":"only2","id":"p/only"}]},
           "Nope":{"models":[{"label":"z","id":"z"}]}}}
JSON
assert_eq "user extra appended, duplicate id skipped" "['auto', 'x1', 'mine']" "$(py '[l for l,_ in agent_models("Codex")]')"
assert_eq "user models replace; auto never twice; dup id dropped" "[('auto', 'auto'), ('only', 'p/only')]" "$(py 'agent_models("Crush")')"
assert_eq "unknown agent in user file ignored" "[('auto', 'auto')]" "$(py 'agent_models("Nope")')"
printf '{ not json' > "$d/home/.maude/models.json"
assert_eq "corrupt user file -> default file still used" "['auto', 'x1']" "$(py '[l for l,_ in agent_models("Codex")]')"
printf '{ not json' > "$d/home/.maude/models-default.json"
assert_eq "corrupt default file -> embedded" "['auto', '6.2 astra', '6.2 sol', '6.1 sol', '6.1 terra', '6 luna']" "$(py '[l for l,_ in agent_models("Codex")]')"
printf '[1,2,3]' > "$d/home/.maude/models.json"
assert_eq "wrong-schema user file ignored" "True" "$(py 'load_models_config() == load_models_config()  and len(agent_models("Codex")) == 6')"
printf '\377\376\377' > "$d/home/.maude/models.json"
assert_eq "undecodable user file ignored" "6" "$(py 'len(agent_models("Codex"))')"
cat > "$d/home/.maude/models.json" <<'JSON'
{"agents":{"Codex":{"models":[{"label":"bad id","id":"a b"},{"label":"","id":"ok"},{"label":"way-too-long-label-here","id":"ok2"},{"label":"-x","id":"--evil"}]}}}
JSON
assert_eq "entries with bad id/label dropped; empty list -> keeps embedded" "6" "$(py 'len(agent_models("Codex"))')"
assert_eq "huge user list capped to the pool" "True" "$(py '
import json
ms = [{"label": "m%d" % i, "id": "m%d" % i} for i in range(40)]
MODELS_USER_FILE.write_text(json.dumps({"agents": {"Codex": {"models": ms}}}))
len(agent_models("Codex")) == MODEL_SLOTS and agent_models("Codex")[0][1] == "auto"')"

# ── saved-model reconciliation ────────────────────────────────────────────────
reset
assert_eq "saved model in list kept"       "opus"        "$(py 'resolve_model("Claude Code","opus")')"
assert_eq "saved model not in list -> auto" "auto"       "$(py 'resolve_model("Kiro CLI","opus")')"
assert_eq "auto stays"                      "auto"       "$(py 'resolve_model("Codex","auto")')"
assert_eq "remembered per-agent model wins" "gpt-6.2-sol" "$(py 'resolve_model("Codex","auto","gpt-6.2-sol")')"
assert_eq "stale remembered -> current if valid" "auto"  "$(py 'resolve_model("Codex","auto","gone")')"
assert_eq "both stale -> first entry"       "auto"       "$(py 'resolve_model("Codex","opus","gone")')"
assert_eq "per-agent memory: none yet"      "{}"          "$(py 'read_agent_models()')"
assert_eq "per-agent memory: save+read"     "{'Codex': 'gpt-6.2-sol', 'Kiro CLI': 'claude-opus-5.5'}" "$(py 'save_agent_model("Codex","gpt-6.2-sol") or save_agent_model("Kiro CLI","claude-opus-5.5") or read_agent_models()')"
assert_eq "per-agent memory: bad ignored"   "{'Codex': 'gpt-6.2-sol', 'Kiro CLI': 'claude-opus-5.5'}" "$(py 'save_agent_model("Nope","x") or save_agent_model("Codex","a b") or read_agent_models()')"
printf 'garbage' > "$d/home/.maude/agent-models.json"
assert_eq "per-agent memory: corrupt -> {}" "{}" "$(py 'read_agent_models()')"
printf '{"Codex":"--evil","Crush":"openai/gpt-6.2-sol","Bad":"x"}' > "$d/home/.maude/agent-models.json"
assert_eq "per-agent memory: invalid entries filtered" "{'Crush': 'openai/gpt-6.2-sol'}" "$(py 'read_agent_models()')"
assert_eq "maude-model file untouched by the memory" "no" "$([[ -e "$d/home/.maude/maude-model" ]] && echo yes || echo no)"

# ── daily refresh of ~/.maude/models-default.json ─────────────────────────────
reset
GOOD="$(cat "$MODELS_JSON")"
export GOOD
run_upd() { # $1 = hour, $2 = fetch body expression
    py "
import os
from datetime import datetime
good = os.environ['GOOD'].encode()
r = maybe_update_models(fetch=lambda: $2, now=datetime(2026, 11, 23, $1, 0))
(r, MODELS_DEFAULT_FILE.exists(), MODELS_STAMP.exists())"
}
assert_eq "before noon: nothing"            "(False, False, False)" "$(run_upd 9 'good')"
assert_eq "valid download is stored"         "(True, True, True)"    "$(run_upd 13 'good')"
assert_eq "stamp is today's date"            "2026-11-23"            "$(cat "$d/home/.maude/models-last-update")"
assert_eq "stored file equals the download"  "True"                  "$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])) == json.load(open(sys.argv[2])))' "$d/home/.maude/models-default.json" "$MODELS_JSON")"
assert_eq "no temp files left"               "0"                     "$(ls -A "$d/home/.maude" | grep -c '^\.models-' || true)"
assert_eq "same day: no second fetch"        "(False, True, True)"   "$(run_upd 14 'good + b"BREAK"')"
rm -f "$d/home/.maude/models-default.json" "$d/home/.maude/models-last-update"
assert_eq "garbage rejected, no stamp"       "(False, False, False)" "$(run_upd 13 'b"<html>proxy</html>"')"
assert_eq "partial (one agent) rejected"     "(False, False, False)" "$(run_upd 13 'b"{\"agents\":{\"Codex\":{\"models\":[{\"label\":\"a\",\"id\":\"a\"}]}}}"')"
assert_eq "fetch error swallowed"            "(False, False, False)" "$(run_upd 13 '(_ for _ in ()).throw(OSError("net down"))')"
assert_eq "old stamp: refresh again"         "(True, True, True)"    "$(printf '2026-11-22' > "$d/home/.maude/models-last-update"; run_upd 13 'good')"
assert_eq "refresh url is next to maude.py"  "https://raw.githubusercontent.com/dirkpetersen/maude/main/light/maude-models.json" "$(py 'MODELS_URL')"

# ── settings.json ─────────────────────────────────────────────────────────────
reset
assert_eq "no file -> native_only_approved True" "{'native_only_approved': True}" "$(py 'load_settings()')"
assert_eq "save returns True"                "True" "$(py 'save_settings({"native_only_approved": False})')"
assert_eq "round trip"                       "{'native_only_approved': False}" "$(py 'load_settings()')"
assert_eq "file mode 0600"                   "600" "$(stat -c %a "$d/home/.maude/settings.json")"
assert_eq "no temp files left"               "0"   "$(ls -A "$d/home/.maude" | grep -c '^\.settings-' || true)"
assert_eq "state dir private"                "700" "$(stat -c %a "$d/home/.maude")"
assert_eq "overwrite is atomic + keeps unknown keys" "{'native_only_approved': True, 'x': 1}" "$(py 'save_settings({"native_only_approved": True, "x": 1}) and load_settings()')"
printf '{ broken' > "$d/home/.maude/settings.json"
assert_eq "corrupt -> defaults"              "{'native_only_approved': True}" "$(py 'load_settings()')"
assert_eq "corrupt file kept as .bak"        "{ broken" "$(cat "$d/home/.maude/settings.json.bak")"
assert_eq "corrupt file moved away"          "no" "$([[ -e "$d/home/.maude/settings.json" ]] && echo yes || echo no)"
printf '[1,2]' > "$d/home/.maude/settings.json"
assert_eq "non-object -> defaults + backup"  "{'native_only_approved': True}" "$(py 'load_settings()')"
printf '{"native_only_approved": "no"}' > "$d/home/.maude/settings.json"
assert_eq "wrong type -> default"            "{'native_only_approved': True}" "$(py 'load_settings()')"
printf '\377\376\377' > "$d/home/.maude/settings.json"
assert_eq "undecodable -> defaults"          "{'native_only_approved': True}" "$(py 'load_settings()')"
assert_eq "save after corruption works"      "{'native_only_approved': False}" "$(py 'save_settings({"native_only_approved": False}) and load_settings()')"
assert_eq "unwritable state dir: save False" "False" "$(HOME="$d/home/.maude/settings.json" python3 -I "${SCRIPT_DIR}/maude-extract.py" "$MAUDE_PY" 'save_settings({"native_only_approved": False})')"

# ── restriction outside the sandbox ───────────────────────────────────────────
ON='{"native_only_approved": True}'; OFF='{"native_only_approved": False}'
assert_eq "native + on: Kiro allowed"        "True"  "$(py "agent_allowed('Kiro CLI', $ON, True)")"
assert_eq "native + on: Copilot allowed"     "True"  "$(py "agent_allowed('GitHub Copilot CLI', $ON, True)")"
for a in "Claude Code" "Codex" "Antigravity" "Grok CLI" "OpenCode" "Crush"; do
    assert_eq "native + on: $a disabled"     "False" "$(py "agent_allowed('$a', $ON, True)")"
    assert_eq "native + off: $a allowed"     "True"  "$(py "agent_allowed('$a', $OFF, True)")"
    assert_eq "sandbox + on: $a allowed"     "True"  "$(py "agent_allowed('$a', $ON, False)")"
done
assert_eq "restrict: allowed agent unchanged" "Kiro CLI"    "$(py "restrict_agent('Kiro CLI', $ON, True)")"
assert_eq "restrict: Claude Code -> Kiro CLI" "Kiro CLI"    "$(py "restrict_agent('Claude Code', $ON, True)")"
assert_eq "restrict: sandbox keeps Claude"    "Claude Code" "$(py "restrict_agent('Claude Code', $ON, False)")"
assert_eq "restrict: setting off keeps Codex" "Codex"       "$(py "restrict_agent('Codex', $OFF, True)")"
assert_eq "default NATIVE=False (sandbox): no effect" "True" "$(py "agent_allowed('Crush', $ON)")"
assert_eq "default NATIVE=True reads the setting"     "False" "$(py "NATIVE = True; agent_allowed('Crush', $ON)")"
assert_eq "missing key means restricted"      "False" "$(py "agent_allowed('Crush', {}, True)")"
assert_eq "reconcile outside sandbox: Claude -> Kiro, model reset" "('auto', 'Kiro CLI')" "$(reset; py "NATIVE = True; reconcile('opus','Claude Code')")"
assert_eq "reconcile outside sandbox: Kiro model kept" "('claude-opus-5.5', 'Kiro CLI')" "$(py "NATIVE = True; reconcile('claude-opus-5.5','Kiro CLI')")"

suite_summary
