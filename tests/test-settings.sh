#!/usr/bin/env bash
# tests/test-settings.sh - the Settings screen helpers of light/maude.py (no textual needed):
#   * the OpenCode custom-provider merge into ~/.config/opencode/opencode.json (non-destructive, backup, symlink,
#     removal, URL validation, key kept in its own 0600 file and referenced as {file:...})
#   * the Anthropic API key active/inactive round trip, Windows/macOS only (injected by plat_claude_env, beats a
#     login); in the sandbox the mode is disabled: a stale flag is ignored and nothing is written
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
        "_ACTIVE_OFF_VARS", "_ACTIVE_UNSET_VARS", "anthropic_key_file", "opencode_key_file",
        "_one_line", "read_secret", "write_secret", "remove_secret", "active_anthropic_key",
        "with_active_anthropic_key", "apply_anthropic_key", "OPENCODE_PROVIDER", "OPENCODE_NPM", "OPENCODE_KEY_REF",
        "opencode_config_path", "opencode_url_error", "_opencode_load", "_opencode_store", "read_opencode_custom",
        "merge_opencode_config", "remove_opencode_custom", "KEY_AGENTS",
        "OPENCODE_PREV_MODEL_SETTING", "replace_retry", "bedrock_parked_state", "claude_env_from", "plat_claude_env", "plat_tool_env",
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
    # a key pasted into clauderc (Paste pane) stays while a key file exists (the stored key wins, the file is never overwritten)
    ns["apply_anthropic_key"]("", False)
    ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-pasted"})
    ns["apply_anthropic_key"]("", False)
    check(f"{tag}.paste_kept_when_key_file_exists", ns["read_clauderc"]().get("ANTHROPIC_API_KEY") == "sk-ant-pasted" and akey.read_text() == "sk-ant-k1", (ns["read_clauderc"](), akey.read_text()))
    ns["apply_anthropic_key"]("", False); ns["remove_clauderc_keys"](["ANTHROPIC_API_KEY"])
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

# ── Sandbox: API key mode is not available yet; a stale flag is ignored and nothing is written ──
ns = load("linux")
rc = ns["clauderc_path"]()
ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-pasted"})   # a pasted key keeps working through the wrapper
ns["write_secret"](akey, "sk-ant-stale"); s = ns["load_settings"](); s["anthropic_key_active"] = True; ns["save_settings"](s)
check("lin.flag_ignored", ns["active_anthropic_key"]() == "" and ns["plat_claude_env"]() == {} and ns["plat_tool_env"]("OpenCode") == {})
before = (rc.read_text(), akey.read_text(), sj.read_text())
ns["apply_anthropic_key"]("sk-ant-new", True); ns["apply_anthropic_key"]("", False)
check("lin.apply_is_noop", (rc.read_text(), akey.read_text(), sj.read_text()) == before)
rc.unlink()
check("lin.gate.flag_not_counted", ns["credentials_configured"]() is False)
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

# a stored key file is never overwritten by a clauderc key; an empty field keeps the stored key
ns = load("win32")
ns["write_secret"](akey, "sk-ant-stored"); ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-pasted"})
ns["apply_anthropic_key"]("", True)
check("key.stored_beats_clauderc_key", akey.read_text() == "sk-ant-stored" and ns["read_clauderc"]().get("ANTHROPIC_API_KEY") == "sk-ant-pasted"
      and ns["active_anthropic_key"]() == "sk-ant-stored", (akey.read_text(), ns["read_clauderc"]()))
reset()
ns["merge_clauderc"]({"ANTHROPIC_API_KEY": "sk-ant-only-in-rc"})   # no key file: the clauderc key is taken over (and moved)
ns["apply_anthropic_key"]("", True)
check("key.rc_key_taken_when_no_file", akey.read_text() == "sk-ant-only-in-rc" and "ANTHROPIC_API_KEY" not in ns["read_clauderc"]())
reset()
# load_settings never raises, even for pathologically nested JSON
sj.parent.mkdir(parents=True, exist_ok=True); sj.write_text("[" * 200000 + "]" * 200000)
ns = load("win32")
check("settings.deep_json_defaults", ns["load_settings"]() == {"native_only_approved": True})
reset()
# _write_private is atomic: 0600 even over a wider file, no temp left, a symlinked target is written through
ns = load("win32")
rcp = ns["clauderc_path"](create=True); rcp.write_text("export A='1'\n"); rcp.chmod(0o644)
ns["write_clauderc"]({"B": "2"})
check("write_private.mode_0600_over_wider", mode(rcp) == "0o600" and rcp.read_text() == "export B='2'\n", mode(rcp))
check("write_private.no_tmp_left", not [q for q in rcp.parent.iterdir() if q.name.startswith(".tmp-")])
tgt = home / "real-clauderc"; tgt.write_text("old"); rcp.unlink(); os.symlink(tgt, rcp)
ns["write_clauderc"]({"C": "3"})
check("write_private.symlink_written_through", rcp.is_symlink() and tgt.read_text() == "export C='3'\n" and mode(tgt) == "0o600", tgt.read_text())
rcp.unlink(); tgt.unlink()
reset()
# bedrock_parked_state(): the one predicate for the Settings box reader and plat_claude_env()
ns = load("win32")
rc, inactive = ns["clauderc_path"](), ns["clauderc_inactive_path"]()
check("parked.none", ns["bedrock_parked_state"]() is False)
ns["write_clauderc"]({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2"}, inactive)
check("parked.only_inactive", ns["bedrock_parked_state"]() is True)
rc.write_text("")
check("parked.empty_active", ns["bedrock_parked_state"]() is True)
rc.write_bytes(b"\xff\xfe\x00bad\xff")
check("parked.undecodable_active", ns["bedrock_parked_state"]() is True)
ns["write_clauderc"]({"AWS_REGION": "eu-west-1"}, rc)
check("parked.both_active_without_foundry_flag", ns["bedrock_parked_state"]() is True)
ns["write_clauderc"]({"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_API_KEY": "fk"}, rc)
check("parked.both_active_foundry_means_not_parked", ns["bedrock_parked_state"]() is False)
rc.unlink(); inactive.unlink()
# plat_claude_env natively: both files, active only has AWS_REGION -> Bedrock effective, the active vars still pass
(home / ".aws").mkdir(exist_ok=True); (home / ".aws" / "config").write_text("[profile bedrock]\nregion = us-west-2\n")
ns = load("win32")
ns["write_clauderc"]({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2"}, ns["clauderc_inactive_path"]())
ns["write_clauderc"]({"AWS_REGION": "eu-west-1", "MY_VAR": "x"}, ns["clauderc_path"]())
e = ns["plat_claude_env"]()
check("parked.env.both_files_bedrock", e.get("CLAUDE_CODE_USE_BEDROCK") == "1" and e.get("AWS_REGION") == "eu-west-1" and e.get("MY_VAR") == "x", e)
ns["write_clauderc"]({"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_BASE_URL": "https://f", "ANTHROPIC_FOUNDRY_API_KEY": "fk"}, ns["clauderc_path"]())
e = ns["plat_claude_env"]()
check("parked.env.stale_inactive_next_to_active_foundry", e.get("ANTHROPIC_BASE_URL") == "https://f" and e.get("CLAUDE_CODE_USE_BEDROCK") == "0", e)
(home / ".aws" / "config").unlink()
reset()
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
assert_contains "ran every section" "url.https://h.example/'v1=ok" "$out"
suite_summary
