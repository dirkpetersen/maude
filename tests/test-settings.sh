#!/usr/bin/env bash
# tests/test-settings.sh - the Settings screen helpers of light/maude.py (no textual needed):
#   * the OpenCode custom-provider merge into ~/.config/opencode/opencode.json (non-destructive, backup, symlink,
#     removal, URL validation, key kept in its own 0600 file and referenced as {file:...})
#   * the Anthropic API key active/inactive round trip: native (injected by plat_claude_env, beats a login) and
#     sandbox (clauderc export + ANTHROPIC_BASE_URL for the claude wrapper)
#   * "Default to AWS Bedrock" parking with both clauderc files present
#   * ~/.maude/settings.json persistence
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
import ast, json, os, re, shutil, subprocess, sys, tempfile, time, types, urllib.parse
from pathlib import Path

src = open(sys.argv[1], encoding="utf-8").read()
tree = ast.parse(src, feature_version=(3, 9))
want = {"PLATFORM", "IS_WIN", "IS_MAC", "SANDBOXED", "NATIVE", "current_platform", "is_native", "STATE_DIR",
        "SETTINGS_FILE", "SETTINGS_DEFAULTS", "_parse_settings", "_decode_state", "load_settings", "save_settings",
        "maude_dir", "clauderc_path", "clauderc_inactive_path", "CRED_KEY_RE", "parse_clauderc",
        "_unquote_rc_value", "_sh_quote", "_write_private", "_decode_text_strict", "read_clauderc", "write_clauderc",
        "merge_clauderc", "remove_clauderc_keys", "_parse_rc_strict", "park_clauderc", "ANTHROPIC_ACTIVE_SETTING",
        "ANTHROPIC_BASE", "_ACTIVE_OFF_VARS", "_ACTIVE_UNSET_VARS", "anthropic_key_file", "opencode_key_file",
        "_one_line", "read_secret", "write_secret", "remove_secret", "active_anthropic_key",
        "with_active_anthropic_key", "apply_anthropic_key", "OPENCODE_PROVIDER", "OPENCODE_NPM", "OPENCODE_KEY_REF",
        "opencode_config_path", "opencode_url_error", "_opencode_load", "_opencode_store", "read_opencode_custom",
        "merge_opencode_config", "remove_opencode_custom", "SANDBOX_MODEL_VARS", "anthropic_key_effective", "KEY_AGENTS",
        "OPENCODE_PREV_MODEL_SETTING", "replace_retry", "ANTHROPIC_OWNED_SETTING", "PLAIN_MODEL_RE", "_owned", "key_block_keys", "claude_env_from", "plat_claude_env", "plat_tool_env",
        "_DUMMY_API_KEY", "_BEDROCK_PROFILE_RE", "AWS_CONFIG_PATH", "credentials_configured", "NATIVE_CRED_ENV"}

def load(platform, login=False):
    ns = {"os": os, "re": re, "json": json, "shutil": shutil, "subprocess": subprocess, "time": time,
          "tempfile": tempfile, "urllib": urllib, "Path": Path, "sys": types.SimpleNamespace(platform=platform)}
    for n in tree.body:
        names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
        if names & want:
            exec(compile(ast.Module([n], []), "maude.py", "exec"), ns)
    ns["have_claude_login"] = lambda: login
    return ns

def check(name, cond, detail=""):
    print(f"{name}={'ok' if cond else 'FAIL ' + str(detail)}")

def mode(p):
    return oct(p.stat().st_mode & 0o777)

home = Path(os.environ["HOME"])
for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_BEDROCK"):
    os.environ.pop(v, None)
sj = home / ".maude" / "settings.json"
akey = home / ".maude" / "anthropic-key"
okey = home / ".maude" / "opencode-key"

def reset():
    for p in (sj, akey, okey, home / ".maude" / "clauderc", home / ".maude" / "clauderc.inactive",
              home / ".azure" / "clauderc", home / ".azure" / "clauderc.inactive"):
        if p.exists() or p.is_symlink():
            p.unlink()

# ── settings.json ──
ns = load("win32")
check("settings.default", ns["load_settings"]() == {"native_only_approved": True}, ns["load_settings"]())
s = ns["load_settings"](); s["native_only_approved"] = False; s["x"] = 1
check("settings.save", ns["save_settings"](s) is True)
check("settings.roundtrip", ns["load_settings"]() == {"native_only_approved": False, "x": 1}, ns["load_settings"]())
check("settings.mode0600", mode(sj) == "0o600", mode(sj))
reset()

# ── Anthropic key, Windows/macOS: kept in ~/.maude/anthropic-key, injected by plat_claude_env ──
for plat, tag in (("win32", "win"), ("darwin", "mac")):
    ns = load(plat)
    rc = ns["clauderc_path"]()
    ns["merge_clauderc"]({"AWS_REGION": "us-west-2", "CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_BASE_URL": "https://f",
                          "ANTHROPIC_FOUNDRY_API_KEY": "fk"})
    ns["apply_anthropic_key"]("sk-ant-k1", True)
    check(f"{tag}.active.files", akey.read_text() == "sk-ant-k1" and mode(akey) == "0o600" and mode(sj) == "0o600", (mode(akey), mode(sj)))
    check(f"{tag}.active.flag", ns["load_settings"]().get("anthropic_key_active") is True)
    check(f"{tag}.active.clauderc_untouched", "ANTHROPIC_API_KEY" not in rc.read_text() and "sk-ant-k1" not in sj.read_text(), rc.read_text())
    e = ns["plat_claude_env"]()
    check(f"{tag}.active.env", e.get("ANTHROPIC_API_KEY") == "sk-ant-k1" and e.get("CLAUDE_CODE_USE_FOUNDRY") == "0", e)
    ns2 = load(plat, login=True)  # a native Claude login does not beat the active key
    e2 = ns2["plat_claude_env"]()
    check(f"{tag}.active.beats_login", e2.get("ANTHROPIC_API_KEY") == "sk-ant-k1", e2)
    check(f"{tag}.active.toolenv", ns["plat_tool_env"]("OpenCode").get("ANTHROPIC_API_KEY") == "sk-ant-k1" and ns["plat_tool_env"]("Crush").get("ANTHROPIC_API_KEY") == "sk-ant-k1")
    check(f"{tag}.active.toolenv_only_key_agents", all("ANTHROPIC_API_KEY" not in ns["plat_tool_env"](a) for a in ("Kiro CLI", "GitHub Copilot CLI", "Codex", None)))
    ns["merge_clauderc"]({"OPENAI_API_KEY": "o"})   # pasted exports: unchanged behaviour for every agent
    check(f"{tag}.toolenv.pasted_exports_kept", ns["plat_tool_env"]("Kiro CLI").get("OPENAI_API_KEY") == "o")
    ns["remove_clauderc_keys"](["OPENAI_API_KEY"])
    os.environ["ANTHROPIC_BASE_URL"] = "https://proxy.example"  # ambient base URL is removed for claude only
    check(f"{tag}.active.unsets_base_url", ns["plat_claude_env"]().get("ANTHROPIC_BASE_URL", "x") is None, ns["plat_claude_env"]())
    del os.environ["ANTHROPIC_BASE_URL"]
    ns["apply_anthropic_key"]("", False)  # inactive: kept, not used
    check(f"{tag}.inactive.kept", akey.read_text() == "sk-ant-k1" and ns["load_settings"]().get("anthropic_key_active") is False)
    e = ns["plat_claude_env"]()
    check(f"{tag}.inactive.env", e.get("ANTHROPIC_API_KEY") != "sk-ant-k1" and e.get("CLAUDE_CODE_USE_FOUNDRY") == "1", e)
    check(f"{tag}.inactive.clauderc_flags_unchanged", ns["read_clauderc"]().get("CLAUDE_CODE_USE_FOUNDRY") == "1")
    check(f"{tag}.inactive.not_in_toolenv", "ANTHROPIC_API_KEY" not in ns["plat_tool_env"]("OpenCode"))
    # a clauderc rewrite / parking (Default to AWS Bedrock) cannot strand the key: it is not in clauderc
    ns["apply_anthropic_key"]("", True)
    ns["park_clauderc"](True)
    check(f"{tag}.parked.key_still_injected", ns["plat_claude_env"]().get("ANTHROPIC_API_KEY") == "sk-ant-k1" and ns["clauderc_inactive_path"]().exists())
    ns["park_clauderc"](False)
    # a key pasted into clauderc (Paste pane) is moved to the key file when the user unchecks the box
    ns["apply_anthropic_key"]("", False)
    ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-pasted"})
    ns["apply_anthropic_key"]("", False)
    check(f"{tag}.paste_moved", "ANTHROPIC_API_KEY" not in ns["read_clauderc"]() and akey.read_text() == "sk-ant-pasted", ns["read_clauderc"]())
    # activate with no key at all: error, nothing written
    reset()
    try:
        ns["apply_anthropic_key"]("", True); check(f"{tag}.nokey", False, "no error")
    except ValueError:
        check(f"{tag}.nokey", not akey.exists() and not sj.exists())
    ns["apply_anthropic_key"]("", False)
    check(f"{tag}.noop", not akey.exists() and not sj.exists())
    # the gate counts an active key
    ns["apply_anthropic_key"]("sk-ant-g", True)
    check(f"{tag}.gate.counts_key", ns["credentials_configured"]() is True)
    ns["apply_anthropic_key"]("", False)
    check(f"{tag}.gate.inactive_not_counted", ns["credentials_configured"]() is False)
    reset()

# ── Anthropic key, sandbox: the wrapper reads only clauderc; its ANTHROPIC_BASE_URL branch passes sk-ant- keys ──
ns = load("linux")
rc = ns["clauderc_path"]()
ns["merge_clauderc"]({"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_API_KEY": "fk", "ANTHROPIC_FOUNDRY_BASE_URL": "https://f"})
ns["apply_anthropic_key"]("sk-ant-s1", True)
r = ns["read_clauderc"]()
check("lin.active.exports", r.get("ANTHROPIC_API_KEY") == "sk-ant-s1" and r.get("ANTHROPIC_BASE_URL") == "https://api.anthropic.com", r)
check("lin.active.flags_untouched", r.get("CLAUDE_CODE_USE_FOUNDRY") == "1" and "CLAUDE_CODE_USE_BEDROCK" not in r, r)
check("lin.active.exported_lines", "export ANTHROPIC_API_KEY='sk-ant-s1'" in rc.read_text() and mode(rc) == "0o600", mode(rc))
check("lin.active.env_empty", ns["plat_claude_env"]() == {} and ns["plat_tool_env"]("OpenCode") == {})
check("lin.active.model_ids", all(r.get(k) == v for k, v in ns["SANDBOX_MODEL_VARS"].items()) and r["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-5-5", r)
check("lin.active.effective", ns["anthropic_key_effective"]() is True)
shutil.copy(rc, home / "sandbox-clauderc.copy")  # the bash part below sources it through the wrapper's model block
ns["apply_anthropic_key"]("", False)
r = ns["read_clauderc"]()
check("lin.inactive", "ANTHROPIC_API_KEY" not in r and "ANTHROPIC_BASE_URL" not in r and r.get("CLAUDE_CODE_USE_FOUNDRY") == "1" and akey.read_text() == "sk-ant-s1", r)
check("lin.inactive.model_ids_removed", not any(k in r for k in ns["SANDBOX_MODEL_VARS"]), r)
check("lin.inactive.not_effective", ns["anthropic_key_effective"]() is False)
ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-pasted"})
check("lin.pasted_key_alone_not_effective", ns["anthropic_key_effective"]() is False)
ns["remove_clauderc_keys"](["ANTHROPIC_API_KEY"])
ns["merge_clauderc"]({"ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-1"})   # the user's own ids are kept on activate and on deactivate
ns["apply_anthropic_key"]("", True)
check("lin.own_model_id_kept", ns["read_clauderc"]()["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-4-1" and ns["read_clauderc"]()["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "claude-sonnet-5-5")
ns["apply_anthropic_key"]("", False)
r = ns["read_clauderc"]()
check("lin.own_model_id_kept_on_uncheck", r.get("ANTHROPIC_DEFAULT_OPUS_MODEL") == "claude-opus-4-1" and "ANTHROPIC_DEFAULT_SONNET_MODEL" not in r, r)
ns["remove_clauderc_keys"](["ANTHROPIC_DEFAULT_OPUS_MODEL"])
try:
    ns["apply_anthropic_key"]("not-a-sk-ant-key", True); check("lin.rejects_non_sk_ant", False, "no error")
except ValueError:
    check("lin.rejects_non_sk_ant", "ANTHROPIC_API_KEY" not in ns["read_clauderc"]())
ns["merge_clauderc"]({"ANTHROPIC_BASE_URL": "https://my.proxy/v1"})  # a user's own endpoint: refused, nothing combined
try:
    ns["apply_anthropic_key"]("sk-ant-s2", True); check("lin.custom_base_url_refused", False, "no error")
except ValueError as e:
    r = ns["read_clauderc"]()
    check("lin.custom_base_url_refused", "custom ANTHROPIC_BASE_URL" in str(e) and r.get("ANTHROPIC_BASE_URL") == "https://my.proxy/v1" and "ANTHROPIC_API_KEY" not in r and not any(k in r for k in ns["SANDBOX_MODEL_VARS"]), r)
ns["apply_anthropic_key"]("", False)
check("lin.custom_base_url_kept_on_uncheck", ns["read_clauderc"]().get("ANTHROPIC_BASE_URL") == "https://my.proxy/v1", ns["read_clauderc"]())
reset()

# ── sandbox: Maude's key block is never parked; ownership of what Maude wrote ──
ns = load("linux")
rc, inactive = ns["clauderc_path"](), ns["clauderc_inactive_path"]()
ns["merge_clauderc"]({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2"})
rc.rename(inactive)                                   # Bedrock is the default: parked
ns["apply_anthropic_key"]("sk-ant-m1", True)          # activating re-creates clauderc with the key block
ns["merge_clauderc"]({"ANTHROPIC_FOUNDRY_API_KEY": "fk", "CLAUDE_CODE_USE_FOUNDRY": "1"})
ns["park_clauderc"](True)                              # a Save with the Bedrock-default box unflipped
a, p = ns["read_clauderc"](), ns["read_clauderc"](inactive)
check("park.key_block.stays_active", a.get("ANTHROPIC_API_KEY") == "sk-ant-m1" and a.get("ANTHROPIC_BASE_URL") == "https://api.anthropic.com"
      and all(k in a for k in ns["SANDBOX_MODEL_VARS"]) and "ANTHROPIC_FOUNDRY_API_KEY" not in a and "CLAUDE_CODE_USE_FOUNDRY" not in a, a)
check("park.key_block.rest_parked", p.get("ANTHROPIC_FOUNDRY_API_KEY") == "fk" and p.get("CLAUDE_CODE_USE_BEDROCK") == "1" and "ANTHROPIC_API_KEY" not in p, p)
check("park.key_block.flag_still_on", ns["load_settings"]().get("anthropic_key_active") is True and ns["anthropic_key_effective"]() is True)
ns["park_clauderc"](True)                              # idempotent
check("park.key_block.idempotent", ns["read_clauderc"]() == a and ns["read_clauderc"](inactive) == p)
ns["park_clauderc"](False)                             # Bedrock-default unticked: the rest comes back, the block stays
a2 = ns["read_clauderc"]()
check("park.key_block.restore", a2.get("ANTHROPIC_API_KEY") == "sk-ant-m1" and a2.get("ANTHROPIC_FOUNDRY_API_KEY") == "fk" and not inactive.exists(), a2)
ns["apply_anthropic_key"]("", False)
check("park.key_block.uncheck_removes_only_block", ns["read_clauderc"]() == {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2", "ANTHROPIC_FOUNDRY_API_KEY": "fk", "CLAUDE_CODE_USE_FOUNDRY": "1"}, ns["read_clauderc"]())
reset()
# without an active key a sandbox park moves everything, as before
ns = load("linux")
ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-pasted", "X": "1"}); ns["park_clauderc"](True)
check("park.no_key_flag_parks_everything", not rc.exists() and ns["read_clauderc"](inactive) == {"ANTHROPIC_API_KEY": "sk-ant-pasted", "X": "1"})
reset()
# model ids: what Maude wrote is recorded; a changed SANDBOX_MODEL_VARS cannot strand old ids
ns = load("linux")
ns["apply_anthropic_key"]("sk-ant-i1", True)
old_opus = ns["SANDBOX_MODEL_VARS"]["ANTHROPIC_DEFAULT_OPUS_MODEL"]
ns["SANDBOX_MODEL_VARS"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] = "claude-opus-9-9"   # a later Maude release changes an id
ns["apply_anthropic_key"]("", True)                    # re-activation refreshes Maude's own value
check("ids.refreshed", ns["read_clauderc"]()["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-9-9", ns["read_clauderc"]())
ns["SANDBOX_MODEL_VARS"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] = "claude-opus-10"
ns["apply_anthropic_key"]("", False)                   # uncheck after another change: removed because it equals what Maude wrote
check("ids.no_stranded_after_change", not any(k in ns["read_clauderc"]() for k in ns["SANDBOX_MODEL_VARS"]), ns["read_clauderc"]())
ns["SANDBOX_MODEL_VARS"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] = old_opus
reset()
# the gate: ids / Maude's base URL alone are no credentials
ns = load("linux")
ns["merge_clauderc"]({**ns["SANDBOX_MODEL_VARS"], "ANTHROPIC_BASE_URL": "https://api.anthropic.com"})
check("gate.only_ids_not_configured", ns["credentials_configured"]() is False)
ns["merge_clauderc"]({"ANTHROPIC_FOUNDRY_API_KEY": "fk"})
check("gate.ids_plus_credential_configured", ns["credentials_configured"]() is True)
reset()
ns = load("linux")
ns["merge_clauderc"]({"ANTHROPIC_BASE_URL": "https://my.proxy/v1"})
check("gate.custom_base_url_configured", ns["credentials_configured"]() is True)
reset()
# L1: a wrong id (Bedrock style) is replaced and restored on uncheck; the user's own plain claude-* id stays
ns = load("linux")
ns["merge_clauderc"]({"ANTHROPIC_DEFAULT_OPUS_MODEL": "global.anthropic.claude-opus-5-5", "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-1"})
ns["apply_anthropic_key"]("sk-ant-l1", True)
r = ns["read_clauderc"]()
check("ids.wrong_replaced", r["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-5-5" and r["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "claude-sonnet-4-1", r)
ns["apply_anthropic_key"]("", False)
r = ns["read_clauderc"]()
check("ids.wrong_restored_on_uncheck", r == {"ANTHROPIC_DEFAULT_OPUS_MODEL": "global.anthropic.claude-opus-5-5", "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-1"}, r)
reset()
# L2: the user's own ANTHROPIC_API_KEY is never removed; an unchecked save of a typed key only stores it
ns = load("linux")
ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-mine"})
ns["apply_anthropic_key"]("sk-ant-typed", False)
check("key.typed_unchecked_keeps_users_key", ns["read_clauderc"]() == {"ANTHROPIC_API_KEY": "sk-ant-mine"} and akey.read_text() == "sk-ant-typed", ns["read_clauderc"]())
reset()
ns = load("win32")
ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-mine"})
ns["apply_anthropic_key"]("sk-ant-typed2", False)
check("key.native.typed_unchecked_keeps_users_key", ns["read_clauderc"]() == {"ANTHROPIC_API_KEY": "sk-ant-mine"} and akey.read_text() == "sk-ant-typed2", ns["read_clauderc"]())
reset()
# replace_retry: Windows retries a transient PermissionError, 5 tries; elsewhere one try; other errors propagate
ns = load("win32"); real_replace, calls = os.replace, []
def flaky(n):
    def f(s, d):
        calls.append(1)
        if len(calls) <= n:
            raise PermissionError("locked")
        return real_replace(s, d)
    return f
src_f, dst_f = home / "rr-src", home / "rr-dst"
src_f.write_text("x"); os.replace = flaky(3)
try:
    ns["replace_retry"](src_f, dst_f)
finally:
    os.replace = real_replace
check("replace_retry.win_retries", dst_f.read_text() == "x" and len(calls) == 4, len(calls))
calls.clear(); src_f.write_text("y"); os.replace = flaky(99)
try:
    ns["replace_retry"](src_f, dst_f); check("replace_retry.win_gives_up", False, "no error")
except PermissionError:
    check("replace_retry.win_gives_up", len(calls) == 5, len(calls))
finally:
    os.replace = real_replace
ns = load("linux"); calls.clear(); os.replace = flaky(99)
try:
    ns["replace_retry"](src_f, dst_f); check("replace_retry.linux_single_try", False, "no error")
except PermissionError:
    check("replace_retry.linux_single_try", len(calls) == 1, len(calls))
finally:
    os.replace = real_replace
src_f.unlink(); dst_f.unlink()

# ── parking with both clauderc files present: nothing is deleted unmerged ──
ns = load("win32")
rc, inactive = ns["clauderc_path"](), ns["clauderc_inactive_path"]()
ns["merge_clauderc"]({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2", "SHARED": "parked"})
rc.rename(inactive)
ns["merge_clauderc"]({"ANTHROPIC_FOUNDRY_API_KEY": "fk", "SHARED": "active"})   # a save re-created the active file
ns["park_clauderc"](True)
p = ns["read_clauderc"](inactive)
check("park.both.merged", not rc.exists() and p == {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2", "SHARED": "active", "ANTHROPIC_FOUNDRY_API_KEY": "fk"}, p)
ns["merge_clauderc"]({"ONLY_ACTIVE": "1", "SHARED": "active2"})
ns["park_clauderc"](False)
a = ns["read_clauderc"]()
check("park.restore.both.merged", not inactive.exists() and a.get("AWS_REGION") == "us-west-2" and a.get("SHARED") == "active2" and a.get("ONLY_ACTIVE") == "1", a)
ns["park_clauderc"](True)
check("park.simple.rename", inactive.exists() and not rc.exists())
rc.write_bytes(b"export A='1'\n"); inactive.write_bytes(b"\xff\xfe\x00bad\xff")  # undecodable parked file: refuse, delete nothing
try:
    ns["park_clauderc"](True); check("park.refuses_undecodable", False, "no error")
except OSError:
    check("park.refuses_undecodable", rc.exists() and inactive.exists())
reset()

# ── OpenCode config merge ──
ns = load("win32")
cfg = ns["opencode_config_path"]()
check("oc.path", str(cfg) == str(home / ".config" / "opencode" / "opencode.json"), cfg)
ns["merge_opencode_config"]("https://h.example/v1", "m1", True)
j = json.loads(cfg.read_text())
check("oc.fresh", j["model"] == "maude-custom/m1" and j["provider"]["maude-custom"] == {
    "npm": "@ai-sdk/openai-compatible", "name": "Custom (Maude)",
    "options": {"baseURL": "https://h.example/v1", "apiKey": "{file:~/.maude/opencode-key}"},
    "models": {"m1": {"name": "m1"}}}, j)
check("oc.nobak.when_new", not cfg.with_name("opencode.json.maude-bak").exists())
check("oc.read", ns["read_opencode_custom"]() == ("https://h.example/v1", "m1"), ns["read_opencode_custom"]())
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
check("oc.no_apikey_without_key", "apiKey" not in mc["options"], mc)
bak = cfg.with_name("opencode.json.maude-bak")
check("oc.backup", bak.read_text() == orig and mode(bak) == "0o600", mode(bak))
ns["merge_opencode_config"]("https://h3.example/v1", "m3", True)
check("oc.backup.once", bak.read_text() == orig)
ns["write_secret"](okey, "oc-secret-value")
check("oc.key_file", okey.read_text() == "oc-secret-value" and mode(okey) == "0o600", mode(okey))
check("oc.no_secret_in_json", "oc-secret-value" not in cfg.read_text() and "{file:~/.maude/opencode-key}" in cfg.read_text())
# removal: provider + model + key file; other providers stay
check("oc.remove.changed", ns["remove_opencode_custom"]() is True)
j = json.loads(cfg.read_text())
check("oc.remove.result", "maude-custom" not in j["provider"] and "zai-coding-plan" in j["provider"] and not okey.exists(), j)
check("oc.remove.restores_previous_model", j.get("model") == "zai-coding-plan/glm-5.2" and "opencode_prev_model" not in ns["load_settings"](), (j, ns["load_settings"]()))
cfg.write_text(json.dumps({"provider": {}}))   # no previous model: the model key goes away again
ns["merge_opencode_config"]("https://h.example/v1", "mx", False); ns["remove_opencode_custom"]()
check("oc.remove.no_previous_model", "model" not in json.loads(cfg.read_text()), cfg.read_text())
check("oc.remove.again", ns["remove_opencode_custom"]() is False)
# a model that is not ours stays
cfg.write_text(json.dumps({"model": "other/x", "provider": {"maude-custom": {}}}))
ns["remove_opencode_custom"]()
check("oc.remove.keeps_foreign_model", json.loads(cfg.read_text()).get("model") == "other/x")
# comments / non-object: refused, untouched, and nothing else written (the caller stores the key after the merge)
for bad in ('{ // c\n "a": 1 }', '[1, 2]'):
    cfg.write_text(bad)
    try:
        ns["merge_opencode_config"]("https://x/v1", "m", False); check("oc.refuse", False, "no error")
    except OSError as e:
        check("oc.refuse", cfg.read_text() == bad and not okey.exists(), e)
try:
    cfg.write_text('{ // c\n}'); ns["merge_opencode_config"]("https://x/v1", "m", False)
except OSError as e:
    check("oc.refuse.message_mentions_comments", "comments" in str(e), e)
# a symlinked opencode.json is written through, the link stays
cfg.unlink()
real = home / "dotfiles" / "oc.json"
real.parent.mkdir(parents=True); real.write_text('{"theme": "x"}')
os.symlink(real, cfg)
ns["merge_opencode_config"]("https://s.example/v1", "ms", False)
check("oc.symlink.kept", cfg.is_symlink() and json.loads(real.read_text())["theme"] == "x" and
      json.loads(real.read_text())["model"] == "maude-custom/ms", cfg.is_symlink())
cfg.unlink()
for f in list(real.parent.iterdir()):
    f.unlink()
real.parent.rmdir()

# secrets are written atomically: no temp file is left behind
check("secret.no_tmp_left", not [q for q in (home / ".maude").iterdir() if q.name.startswith(".secret-")])
# ── OpenCode URL validation ──
e = ns["opencode_url_error"]
for url, ok in (("https://h.example/v1", True), ("https://h.example:8443/v1", True), ("http://localhost:11434/v1", True),
                ("http://127.0.0.1:8080/v1", True), ("http://[::1]:8080/v1", True),
                ("http://h.example/v1", False), ("ftp://h.example", False), ("https://", False), ("", False),
                ("https://u:p@h.example/v1", False), ("https://u@h.example/v1", False),
                ("https://h.example/{env:X}", False), ("https://h.example/v1 x", False), ("https://h.example:99999999/v1", False),
                ("javascript:alert(1)", False), ("https://h.example/'v1", False)):
    check(f"url.{url or 'empty'}", (e(url) == "") == ok, e(url))
PY

out=$(HOME="$d/home" USERPROFILE="$d/home" python3 -I "$d/run.py" "$MAUDE_PY" 2>&1) || { echo "$out" | tail -20; assert_eq "harness ran" "ok" "crashed"; }
while IFS= read -r line; do
    [[ "$line" == *=* ]] || continue
    assert_eq "${line%%=*}" "ok" "${line#*=}"
done <<< "$out"
# TRIMMED COPY of the wrapper's model block: dok scripts/claude-wrapper.sh "Model Configuration" lines 934-945 (the
# USING_FOUNDRY/USING_NATIVE if/else of `${VAR:-default}` exports) and the model keyword case at lines 964-972. The
# [1m] suffix block (954-959) is left out because it is skipped when ANTHROPIC_BASE_URL is set. RE-CHECK THIS COPY
# WHENEVER THE WRAPPER CHANGES (fetch the file, diff those line ranges, and update the default ids below). Its
# ANTHROPIC_BASE_URL branch sets neither USING_NATIVE nor USING_FOUNDRY, so without the plain ids in clauderc the
# Bedrock ids would be sent to api.anthropic.com.
wrapper_model() {  # $1 = clauderc to source ("" = none), $2 = model word
    (
        set -a; unset ANTHROPIC_DEFAULT_HAIKU_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_FABLE_MODEL
        [[ -n "$1" ]] && . "$1"
        set +a
        if [[ "${USING_FOUNDRY:-0}" == "1" || "${USING_NATIVE:-0}" == "1" ]]; then
          export ANTHROPIC_DEFAULT_OPUS_MODEL="${ANTHROPIC_DEFAULT_OPUS_MODEL:-claude-opus-5-5}"
        else
          export ANTHROPIC_DEFAULT_HAIKU_MODEL="${ANTHROPIC_DEFAULT_HAIKU_MODEL:-global.anthropic.claude-haiku-5-5}"
          export ANTHROPIC_DEFAULT_SONNET_MODEL="${ANTHROPIC_DEFAULT_SONNET_MODEL:-global.anthropic.claude-sonnet-5-5}"
          export ANTHROPIC_DEFAULT_OPUS_MODEL="${ANTHROPIC_DEFAULT_OPUS_MODEL:-global.anthropic.claude-opus-5-5}"
          export ANTHROPIC_DEFAULT_FABLE_MODEL="${ANTHROPIC_DEFAULT_FABLE_MODEL:-global.anthropic.claude-fable-5-1}"
        fi
        case "$2" in
          fable) echo "$ANTHROPIC_DEFAULT_FABLE_MODEL" ;; opus) echo "$ANTHROPIC_DEFAULT_OPUS_MODEL" ;;
          sonnet) echo "$ANTHROPIC_DEFAULT_SONNET_MODEL" ;; *) echo "$ANTHROPIC_DEFAULT_HAIKU_MODEL" ;;
        esac
    )
}
sandbox_rc="$d/home/sandbox-clauderc.copy"
assert_eq "wrapper model block: without Maude's ids the Bedrock id is sent (the bug)" "global.anthropic.claude-opus-5-5" "$(wrapper_model "" opus)"
assert_eq "wrapper model block: opus -> plain Anthropic id"   "claude-opus-5-5"   "$(wrapper_model "$sandbox_rc" opus)"
assert_eq "wrapper model block: sonnet -> plain Anthropic id" "claude-sonnet-5-5" "$(wrapper_model "$sandbox_rc" sonnet)"
assert_eq "wrapper model block: haiku -> plain Anthropic id"  "claude-haiku-5-5"  "$(wrapper_model "$sandbox_rc" haiku)"
assert_eq "wrapper model block: fable -> plain Anthropic id"  "claude-fable-5-2"  "$(wrapper_model "$sandbox_rc" fable)"
assert_contains "ran every section" "url.https://h.example/'v1=ok" "$out"
suite_summary
