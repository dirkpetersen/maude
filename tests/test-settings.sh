#!/usr/bin/env bash
# tests/test-settings.sh - the Settings screen helpers of light/maude.py (no textual needed):
#   * the OpenCode custom-provider merge into ~/.config/opencode/opencode.json (non-destructive, backup, {env:} ref)
#   * the Anthropic API key active/inactive round trip through parse_clauderc, plat_claude_env, plat_tool_env
#   * ~/.maude/settings.json persistence (native_only_approved, the parked key, unknown keys)
# Pulls only the needed names out of maude.py. Uses a mktemp HOME only (never the real ~/.maude, ~/.config, ~/.aws).
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "maude.py Settings helpers"

MAUDE_PY="${SCRIPT_DIR}/../light/maude.py"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir -p "$d/home"

cat > "$d/run.py" <<'PY'
import ast, json, os, re, shutil, subprocess, sys, tempfile, time, types
from pathlib import Path

src = open(sys.argv[1], encoding="utf-8").read()
tree = ast.parse(src, feature_version=(3, 9))
want = {"PLATFORM", "IS_WIN", "IS_MAC", "SANDBOXED", "NATIVE", "current_platform", "is_native", "STATE_DIR",
        "SETTINGS_FILE", "SETTINGS_DEFAULTS", "_parse_settings", "_decode_state", "load_settings", "save_settings",
        "maude_dir", "clauderc_path", "clauderc_inactive_path", "OPENCODE_API_ENV", "CRED_KEY_RE", "parse_clauderc",
        "_unquote_rc_value", "_sh_quote", "_write_private", "_decode_text_strict", "read_clauderc", "write_clauderc",
        "merge_clauderc", "remove_clauderc_keys", "ANTHROPIC_KEY_SETTING", "ANTHROPIC_DISPLACED_SETTING",
        "_CLOUD_FLAGS", "stored_anthropic_key", "apply_anthropic_key", "OPENCODE_PROVIDER", "OPENCODE_NPM",
        "opencode_config_path", "read_opencode_custom", "merge_opencode_config", "claude_env_from", "plat_claude_env",
        "plat_tool_env", "_DUMMY_API_KEY", "_BEDROCK_PROFILE_RE", "AWS_CONFIG_PATH", "credentials_configured",
        "NATIVE_CRED_ENV"}

def load(platform):
    ns = {"os": os, "re": re, "json": json, "shutil": shutil, "subprocess": subprocess, "time": time, "tempfile": tempfile,
          "Path": Path, "sys": types.SimpleNamespace(platform=platform)}
    for n in tree.body:
        names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
        if names & want:
            exec(compile(ast.Module([n], []), "maude.py", "exec"), ns)
    ns["have_claude_login"] = lambda: False
    return ns

def check(name, cond, detail=""):
    print(f"{name}={'ok' if cond else 'FAIL ' + str(detail)}")

home = Path(os.environ["HOME"])
os.environ.pop("ANTHROPIC_API_KEY", None)

# ── settings.json ──
ns = load("win32")
check("settings.default", ns["load_settings"]() == {"native_only_approved": True}, ns["load_settings"]())
s = ns["load_settings"](); s["native_only_approved"] = False; s["anthropic_api_key"] = "sk-ant-x"
check("settings.save", ns["save_settings"](s) is True)
check("settings.roundtrip", ns["load_settings"]() == {"native_only_approved": False, "anthropic_api_key": "sk-ant-x"}, ns["load_settings"]())
check("settings.mode", oct((home / ".maude" / "settings.json").stat().st_mode & 0o777) in ("0o600", "0o666", "0o644"))
ns["save_settings"]({"native_only_approved": True})
(home / ".maude" / "settings.json").unlink()

# ── Anthropic key round trip (windows, macos: Maude loads clauderc; sandbox: the wrapper does) ──
for plat, tag in (("win32", "win"), ("darwin", "mac"), ("linux", "lin")):
    ns = load(plat)
    rc = ns["clauderc_path"]()
    if rc.exists():
        rc.unlink()
    ns["merge_clauderc"]({"AWS_REGION": "us-west-2"})  # something else in the file: must survive
    ns["apply_anthropic_key"]("sk-ant-k1", True)
    check(f"{tag}.active.rc", ns["parse_clauderc"](rc.read_text()) == {"AWS_REGION": "us-west-2", "ANTHROPIC_API_KEY": "sk-ant-k1"}, rc.read_text())
    check(f"{tag}.active.exported", "export ANTHROPIC_API_KEY='sk-ant-k1'" in rc.read_text())
    if tag != "lin":
        check(f"{tag}.active.env", ns["plat_claude_env"]().get("ANTHROPIC_API_KEY") == "sk-ant-k1", ns["plat_claude_env"]())
        check(f"{tag}.active.toolenv", ns["plat_tool_env"]().get("ANTHROPIC_API_KEY") == "sk-ant-k1")
    ns["apply_anthropic_key"]("", False)  # inactive: kept in settings.json, not exported
    check(f"{tag}.inactive.rc", ns["parse_clauderc"](rc.read_text()) == {"AWS_REGION": "us-west-2"}, rc.read_text())
    check(f"{tag}.inactive.parked", ns["stored_anthropic_key"]() == "sk-ant-k1")
    if tag != "lin":
        check(f"{tag}.inactive.env", "ANTHROPIC_API_KEY" not in ns["plat_claude_env"]() and "ANTHROPIC_API_KEY" not in ns["plat_tool_env"](), ns["plat_claude_env"]())
    ns["merge_clauderc"]({"CLAUDE_CODE_USE_FOUNDRY": "1"})   # a rewrite of clauderc keeps the parked key
    ns["merge_clauderc"]({"AWS_REGION": "eu-west-1"})
    check(f"{tag}.inactive.survives", ns["stored_anthropic_key"]() == "sk-ant-k1" and "sk-ant-k1" not in rc.read_text())
    ns["apply_anthropic_key"]("", True)  # re-activate with an empty field: the parked key comes back, Foundry switched off
    check(f"{tag}.reactivate", ns["read_clauderc"]().get("ANTHROPIC_API_KEY") == "sk-ant-k1" and ns["read_clauderc"]().get("CLAUDE_CODE_USE_FOUNDRY") == "0", ns["read_clauderc"]())
    ns["apply_anthropic_key"]("", False)  # deactivate: Foundry comes back
    check(f"{tag}.deactivate.restores", ns["read_clauderc"]().get("CLAUDE_CODE_USE_FOUNDRY") == "1" and "ANTHROPIC_API_KEY" not in ns["read_clauderc"](), ns["read_clauderc"]())
    try:
        ns["apply_anthropic_key"]("sk-ant-new", True); ns["apply_anthropic_key"]("", False)
        check(f"{tag}.newkey.stored", ns["stored_anthropic_key"]() == "sk-ant-new")
    finally:
        pass
    rc.unlink()
    (home / ".maude" / "settings.json").unlink()
    # nothing stored, activate with no key: ValueError, nothing written
    try:
        ns["apply_anthropic_key"]("", True); check(f"{tag}.nokey", False, "no error")
    except ValueError:
        check(f"{tag}.nokey", not rc.exists())
    # a key left empty and inactive: nothing happens
    ns["apply_anthropic_key"]("", False)
    check(f"{tag}.noop", not rc.exists() and not (home / ".maude" / "settings.json").exists())
    for p in (home / ".azure" / "clauderc",):
        if p.exists(): p.unlink()

# a clauderc that is only the OpenCode key is not a Claude credential for the gate (the sandbox check is file based)
ns = load("win32")
ns["merge_clauderc"]({"MAUDE_OPENCODE_API_KEY": "oc"})
check("gate.opencode_only", ns["credentials_configured"]() is False)
ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-g"})
check("gate.with_claude_key", ns["credentials_configured"]() is True)
ns["apply_anthropic_key"]("", False)
check("gate.after_deactivate", ns["credentials_configured"]() is False)
check("rc.keeps_oc_key", ns["read_clauderc"]() == {"MAUDE_OPENCODE_API_KEY": "oc"}, ns["read_clauderc"]())
ns["clauderc_path"]().unlink()
# an empty clauderc after removal is deleted (it would otherwise count as configured)
ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-e"}); ns["remove_clauderc_keys"](["ANTHROPIC_API_KEY"])
check("rc.removed_when_empty", not ns["clauderc_path"]().exists())
# sandbox: plat_tool_env passes only the OpenCode key
ns = load("linux")
ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-s", "MAUDE_OPENCODE_API_KEY": "oc2"})
check("lin.toolenv", ns["plat_tool_env"]() == {"MAUDE_OPENCODE_API_KEY": "oc2"}, ns["plat_tool_env"]())
check("lin.claudeenv", ns["plat_claude_env"]() == {})
ns["clauderc_path"]().unlink()
check("lin.toolenv.empty", ns["plat_tool_env"]() == {})

# ── OpenCode config merge ──
ns = load("win32")
cfg = ns["opencode_config_path"]()
check("oc.path", str(cfg) == str(home / ".config" / "opencode" / "opencode.json"), cfg)
# fresh file
ns["merge_opencode_config"]("https://h.example/v1", "m1", True)
j = json.loads(cfg.read_text())
check("oc.fresh", j["model"] == "maude-custom/m1" and j["provider"]["maude-custom"] == {
    "npm": "@ai-sdk/openai-compatible", "name": "Custom (Maude)",
    "options": {"baseURL": "https://h.example/v1", "apiKey": "{env:MAUDE_OPENCODE_API_KEY}"},
    "models": {"m1": {"name": "m1"}}}, j)
check("oc.nobak.when_new", not cfg.with_name("opencode.json.maude-bak").exists())
check("oc.read", ns["read_opencode_custom"]() == ("https://h.example/v1", "m1"), ns["read_opencode_custom"]())
# existing user file: keep everything, drop the managed marker, backup once, add a second model
cfg.write_text(json.dumps({"//": "managed by maude - x", "theme": "dark", "model": "zai-coding-plan/glm-5.2",
                           "provider": {"zai-coding-plan": {"options": {"apiKey": "{env:ZHIPU_API_KEY}"}},
                                        "maude-custom": {"models": {"old": {"name": "Old"}}, "options": {"timeout": 5}}}}))
orig = cfg.read_text()
ns["merge_opencode_config"]("https://h2.example/v1", "m2", False)
j = json.loads(cfg.read_text())
check("oc.keeps_other", j["theme"] == "dark" and j["provider"]["zai-coding-plan"] == {"options": {"apiKey": "{env:ZHIPU_API_KEY}"}}, j)
check("oc.marker_dropped", "//" not in j)
check("oc.model_key", j["model"] == "maude-custom/m2")
mc = j["provider"]["maude-custom"]
check("oc.models_merged", set(mc["models"]) == {"old", "m2"} and mc["options"]["timeout"] == 5 and mc["options"]["baseURL"] == "https://h2.example/v1", mc)
check("oc.nokey_no_apikey_added", "apiKey" not in mc["options"], mc)
bak = cfg.with_name("opencode.json.maude-bak")
check("oc.backup", bak.read_text() == orig, "backup differs")
ns["merge_opencode_config"]("https://h3.example/v1", "m3", True)
check("oc.backup.once", bak.read_text() == orig)
check("oc.no_secret_in_json", "oc-secret" not in cfg.read_text() and "{env:MAUDE_OPENCODE_API_KEY}" in cfg.read_text())
# a file with comments or a non-object is refused and left untouched
for bad in ('{ // c\n "a": 1 }', '[1, 2]'):
    cfg.write_text(bad)
    try:
        ns["merge_opencode_config"]("https://x/v1", "m", False); check("oc.refuse", False, "no error")
    except OSError:
        check("oc.refuse", cfg.read_text() == bad)
cfg.unlink()
PY

out=$(HOME="$d/home" USERPROFILE="$d/home" python3 -I "$d/run.py" "$MAUDE_PY" 2>&1) || { echo "$out" | tail -20; fail_msg="harness crashed"; assert_eq "harness ran" "ok" "$fail_msg"; }
while IFS= read -r line; do
    [[ "$line" == *=* ]] || continue
    assert_eq "${line%%=*}" "ok" "${line#*=}"
done <<< "$out"
assert_contains "ran every section" "oc.refuse=ok" "$out"
suite_summary
