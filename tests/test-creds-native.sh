#!/usr/bin/env bash
# tests/test-creds-native.sh - Settings on Windows/macOS (light/maude.py): one clauderc path per
# platform, plat_claude_env parsing (+ .inactive), the credential gate matrix, Linux unchanged.
# Pulls only the needed names out of maude.py (no textual needed). Uses temp HOMEs only.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "maude.py native credentials (Windows/macOS)"

MAUDE_PY="${SCRIPT_DIR}/../light/maude.py"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

cat > "$d/run.py" <<'PY'
import ast, json, os, re, shutil, subprocess, sys, time, types
from pathlib import Path

src_path = sys.argv[1]
src = open(src_path, encoding="utf-8").read()
tree = ast.parse(src, feature_version=(3, 9))
want = {"PLATFORM", "IS_WIN", "IS_MAC", "SANDBOXED", "NATIVE", "current_platform", "is_native",
        "STATE_DIR", "maude_dir", "clauderc_path", "clauderc_inactive_path", "_BEDROCK_PROFILE_RE",
        "_DUMMY_API_KEY", "claude_env_from", "read_clauderc", "plat_claude_env", "_child_env",
        "CRED_KEY_RE", "parse_clauderc", "write_clauderc", "merge_clauderc", "NATIVE_CRED_ENV",
        "credentials_configured", "check_credentials", "have_claude_login", "claude_login_status",
        "creds_gate_decision", "creds_gate_blocks", "AWS_CREDS_PATH", "AWS_CONFIG_PATH", "FEATURES",
        "hidden_widgets", "feature_on", "FEATURE_WIDGETS", "_unquote_rc_value", "_sh_quote", "_write_private",
        "_decode_text_strict", "plat_tool_env", "creds_gate_precheck", "_LOGIN_OK", "_INI_HEADER_RE",
        "replace_ini_section", "_merge_ini_file", "write_aws_credentials", "parse_creds_text", "CRED_PREFIXES",
        "active_anthropic_key", "with_active_anthropic_key", "anthropic_key_file", "read_secret", "_one_line",
        "ANTHROPIC_ACTIVE_SETTING", "load_settings", "_parse_settings", "_decode_state", "SETTINGS_FILE",
        "SETTINGS_DEFAULTS", "_ACTIVE_OFF_VARS", "_ACTIVE_UNSET_VARS", "KEY_AGENTS",
        "replace_retry"}

def load(platform):
    ns = {"os": os, "re": re, "json": json, "shutil": shutil, "subprocess": subprocess, "time": time,
          "Path": Path, "sys": types.SimpleNamespace(platform=platform)}
    for n in tree.body:
        names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
        if names & want:
            exec(compile(ast.Module([n], []), "maude.py", "exec"), ns)
    ns["plat_real_claude"] = lambda: "claude-fake"
    return ns

def out(k, v):
    print(f"{k}={v}")

home = Path(os.environ["HOME"])

# ── path selection ──
for plat, tag in (("linux", "lin"), ("darwin", "mac"), ("win32", "win")):
    ns = load(plat)
    out(f"{tag}.path", str(ns["clauderc_path"]()).replace(str(home), "~"))
    out(f"{tag}.inactive", str(ns["clauderc_inactive_path"]()).replace(str(home), "~"))
    out(f"{tag}.native", ns["is_native"]())
    out(f"{tag}.maudedir.after.read", (home / ".maude").exists())  # a read must not create ~/.maude
ns = load("darwin")
p = ns["clauderc_path"](create=True)
out("mac.create", f"{p.parent.is_dir()},{oct(p.parent.stat().st_mode & 0o777)}")

# ── write / merge / parse round trip, one code path on every platform ──
for plat, tag in (("darwin", "mac"), ("win32", "win"), ("linux", "lin")):
    ns = load(plat)
    ns["merge_clauderc"]({"ANTHROPIC_FOUNDRY_API_KEY": "k1", "CLAUDE_CODE_USE_FOUNDRY": "1"})
    ns["merge_clauderc"]({"ANTHROPIC_FOUNDRY_BASE_URL": "https://x/anthropic", "ANTHROPIC_FOUNDRY_API_KEY": "k2"})
    path = ns["clauderc_path"]()
    out(f"{tag}.rc.exists", path.is_file())
    out(f"{tag}.rc.mode", oct(path.stat().st_mode & 0o777))
    out(f"{tag}.rc.text", path.read_text().replace("\n", "|"))
    out(f"{tag}.rc.parse", sorted(ns["read_clauderc"]().items()))
    out(f"{tag}.rc.crlf", b"\r" in path.read_bytes())
    out(f"{tag}.configured", ns["credentials_configured"]())
    path.unlink()

# ── plat_claude_env ──
ns = load("linux")
ns["merge_clauderc"]({"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_BASE_URL": "https://f", "ANTHROPIC_FOUNDRY_API_KEY": "fk"})
out("lin.env", ns["plat_claude_env"]())
out("lin.childenv", ns["_child_env"]())
(home / ".azure" / "clauderc").unlink()

for plat, tag in (("darwin", "mac"), ("win32", "win")):
    ns = load(plat)
    ns["have_claude_login"] = lambda: False
    out(f"{tag}.env.empty", ns["plat_claude_env"]())
    out(f"{tag}.childenv.empty", ns["_child_env"]())
    # ambient files alone change nothing: no clauderc + ~/.aws/config [bedrock] + ambient API key -> inherit as is
    (home / ".aws").mkdir(exist_ok=True)
    (home / ".aws" / "config").write_text("[profile bedrock]\nregion = us-west-2\n")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-ambient"
    ns["have_claude_login"] = lambda: True
    out(f"{tag}.env.ambient", f"{ns['plat_claude_env']()} {ns['_child_env']()} {ns['_child_env'](tools=True)}")
    ns["have_claude_login"] = lambda: False
    del os.environ["ANTHROPIC_API_KEY"]
    (home / ".aws" / "config").unlink()
    ns["merge_clauderc"]({"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_BASE_URL": "https://f", "ANTHROPIC_FOUNDRY_API_KEY": "fk"})
    e = ns["plat_claude_env"]()
    out(f"{tag}.env.foundry", sorted(e.items()))
    ce = ns["_child_env"]()
    out(f"{tag}.childenv.foundry", f"{ce['ANTHROPIC_BASE_URL']},{ce['ANTHROPIC_API_KEY']},{ce['CLAUDE_CODE_USE_BEDROCK']},{'PATH' in ce or 'Path' in ce}")
    # non-Claude tools: only the raw exports, no dummy key, no rewrite
    te = ns["_child_env"](tools=True)
    out(f"{tag}.toolenv", f"{te['ANTHROPIC_FOUNDRY_API_KEY']},{'ANTHROPIC_API_KEY' in te},{'ANTHROPIC_BASE_URL' in te}")
    ns["have_claude_login"] = lambda: True
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-ambient"
    te = ns["_child_env"](tools=True)
    out(f"{tag}.toolenv.login", f"{te['ANTHROPIC_API_KEY']},{te['CLAUDE_CODE_USE_FOUNDRY']}")  # login does not strip it for tools
    del os.environ["ANTHROPIC_API_KEY"]
    ns["have_claude_login"] = lambda: False
    # .inactive: parked file is not read; a parked file without the Bedrock flag does not select Bedrock
    rc = ns["clauderc_path"]()
    inactive = ns["clauderc_inactive_path"]()
    (home / ".aws" / "config").write_text("[profile bedrock]\nregion = us-west-2\n")
    rc.rename(inactive)
    out(f"{tag}.env.parked", ns["plat_claude_env"]())
    out(f"{tag}.env.parked.aws.noflag", ns["plat_claude_env"]())
    # Bedrock saved via Settings (flag in the clauderc) and parked as the default -> Bedrock
    inactive.write_text('export CLAUDE_CODE_USE_BEDROCK="1"\nexport ANTHROPIC_FOUNDRY_API_KEY="fk"\n')
    (home / ".aws" / "config").unlink()
    out(f"{tag}.env.parked.noprofile", ns["plat_claude_env"]())
    (home / ".aws" / "config").write_text("[profile bedrock]\nregion = us-west-2\n")
    pe = ns["plat_claude_env"]()
    out(f"{tag}.env.bedrock", sorted(pe.items()))
    out(f"{tag}.env.parked.noleak", "ANTHROPIC_FOUNDRY_API_KEY" in pe)
    # not parked: the Bedrock flag in the live clauderc
    inactive.rename(rc)
    out(f"{tag}.env.bedrock.live", sorted(k for k, v in ns["plat_claude_env"]().items() if k.startswith("CLAUDE_CODE_USE") or k == "AWS_PROFILE"))
    rc.write_text('export ANTHROPIC_FOUNDRY_API_KEY="fk"\n')  # no Bedrock flag: ~/.aws/config alone is not enough
    out(f"{tag}.env.aws.noflag", ns["plat_claude_env"]())
    (home / ".aws" / "config").unlink()
    rc.unlink()
    ns["merge_clauderc"]({"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_BASE_URL": "https://f", "ANTHROPIC_FOUNDRY_API_KEY": "fk"})
    # login present and Foundry creds: the native login wins (like the wrapper), cloud vars cleared
    ns["merge_clauderc"]({"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_BASE_URL": "https://f", "ANTHROPIC_FOUNDRY_API_KEY": "fk",
                          "ANTHROPIC_API_KEY": "sk-x"})
    ns["have_claude_login"] = lambda: True
    e = ns["plat_claude_env"]()
    out(f"{tag}.env.login", sorted((k, v) for k, v in e.items() if k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_BEDROCK")))
    ce = ns["_child_env"]()
    out(f"{tag}.childenv.login", f"{'ANTHROPIC_API_KEY' in ce},{ce['CLAUDE_CODE_USE_FOUNDRY']}")
    ns["clauderc_path"]().unlink()
    # login only, nothing configured: nothing to change
    out(f"{tag}.env.login.only", ns["plat_claude_env"]())
    # custom base URL gets a dummy key
    ns["have_claude_login"] = lambda: False
    ns["merge_clauderc"]({"ANTHROPIC_BASE_URL": "http://llm/v1"})
    out(f"{tag}.env.baseurl", sorted(ns["plat_claude_env"]().items()))
    ns["clauderc_path"]().unlink()
    # incomplete Foundry: nothing but the rc itself
    ns["merge_clauderc"]({"CLAUDE_CODE_USE_FOUNDRY": "1"})
    out(f"{tag}.env.incomplete", sorted(ns["plat_claude_env"]().items()))
    ns["clauderc_path"]().unlink()

# ── pure env: login + ambient vars removed ──
ns = load("darwin")
e = ns["claude_env_from"]({"AWS_X": "1"}, {"ANTHROPIC_API_KEY": "sk-ant-zzz", "CLAUDE_CODE_USE_BEDROCK": "1"}, True, "")
out("pure.norc", ns["claude_env_from"]({}, {"ANTHROPIC_API_KEY": "sk-ant-zzz"}, True, "[profile bedrock]\n"))
out("pure.login.ambient", sorted(e.items(), key=lambda kv: kv[0]))
e = ns["claude_env_from"]({"CLAUDE_CODE_USE_BEDROCK": "1"}, {}, False, "[bedrock]\n")
out("pure.bedrock.bare", sorted((k, v) for k, v in e.items()))
e = ns["claude_env_from"]({"CLAUDE_CODE_USE_BEDROCK": "1"}, {}, False, "# [bedrock] in a comment\nregion=x\n")
out("pure.bedrock.comment", e)

# ── gate matrix ──
ns = load("linux")
G = ns["creds_gate_decision"]
rows = []
for native in (False, True):
    for login_file in (False, True):
        for configured in (False, True):
            for status in (None, False, True):
                rows.append(f"{int(native)}{int(login_file)}{int(configured)}{ {None: 'n', False: 'f', True: 't'}[status] }={int(G(native, login_file, configured, status))}")
out("gate", " ".join(rows))

# creds_gate_blocks: statuses only asked when needed
for plat, tag in (("linux", "lin"), ("darwin", "mac"), ("win32", "win")):
    ns = load(plat)
    asked = []
    ns["claude_login_status"] = lambda *a, _a=asked, **k: _a.append(1) or None
    ns["have_claude_login"] = lambda: False
    out(f"{tag}.blocks.nothing", f"{ns['creds_gate_blocks']()} asked={len(asked)}")
    ns["claude_login_status"] = lambda *a, _a=asked, **k: _a.append(1) or False
    out(f"{tag}.blocks.explicit_false", ns["creds_gate_blocks"]())
    ns["claude_login_status"] = lambda *a, _a=asked, **k: _a.append(1) or True
    out(f"{tag}.blocks.status_true", ns["creds_gate_blocks"]())
    asked.clear()
    ns["have_claude_login"] = lambda: True
    out(f"{tag}.blocks.login_file", f"{ns['creds_gate_blocks']()} asked={len(asked)}")
    ns["have_claude_login"] = lambda: False
    ns["merge_clauderc"]({"AWS_PROFILE": "bedrock"})
    out(f"{tag}.blocks.configured", f"{ns['creds_gate_blocks']()} asked={len(asked)}")
    ns["clauderc_path"]().unlink()

# claude_login_status parsing with a fake claude
ns = load("darwin")
fake = home / "claude-fake"
for body, tag in (('{"loggedIn": true}', "true"), ('{"loggedIn": false}', "false"), ("garbage", "garbage"), ('{"x": 1}', "nokey")):
    fake.write_text(f"#!/bin/sh\ncat <<'EOF'\n{body}\nEOF\n"); fake.chmod(0o755)
    ns["plat_real_claude"] = lambda: str(fake)
    out(f"status.{tag}", ns["claude_login_status"]())
ns["plat_real_claude"] = lambda: str(home / "does-not-exist")
out("status.missing", ns["claude_login_status"]())
fake.write_text("#!/bin/sh\nsleep 5\n"); fake.chmod(0o755)
ns["plat_real_claude"] = lambda: str(fake)
out("status.timeout", ns["claude_login_status"](timeout=0.3))

# ── have_claude_login: refresh-token rule ──
ns = load("linux")
cj = home / ".claude" / ".credentials.json"
cj.parent.mkdir(exist_ok=True)
now = time.time() * 1000
def login(oauth):
    cj.write_text(json.dumps({"claudeAiOauth": oauth}))
    return ns["have_claude_login"]()
out("login.valid", login({"accessToken": "t", "expiresAt": now + 1e6}))
out("login.expired.norefresh", login({"accessToken": "t", "expiresAt": now - 1e6}))
out("login.expired.refresh", login({"accessToken": "t", "expiresAt": now - 1e6, "refreshToken": "r"}))
out("login.expired.refresh.valid", login({"accessToken": "t", "expiresAt": now - 1e6, "refreshToken": "r", "refreshTokenExpiresAt": now + 1e6}))
out("login.expired.refresh.expired", login({"accessToken": "t", "expiresAt": now - 1e6, "refreshToken": "r", "refreshTokenExpiresAt": now - 1e3}))
out("login.noexpiry", login({"accessToken": "t"}))
out("login.notoken", login({"expiresAt": now + 1e6}))
cj.unlink()

# ── quoting: write -> bash source -> literal value; parse reads old and new forms ──
nasty = {
    "ANTHROPIC_FOUNDRY_API_KEY": "$(touch PWNED1)",
    "AWS_A": "`touch PWNED2`",
    "AWS_B": 'a"b',
    "AWS_C": "it's",
    "AWS_D": "sp ace  s",
    "AWS_E": "$HOME;touch PWNED3",
    "AWS_F": "a\\b\\\\c",
    "AWS_G": "'; touch PWNED4; '",
    "AWS_H": "caf\u00e9 \u2603",
    "AWS_I": "",
}
for plat, tag in (("linux", "lin"), ("win32", "win")):
    ns = load(plat)
    work = home / f"q-{tag}"
    work.mkdir()
    ns["write_clauderc"](nasty)
    rcp = ns["clauderc_path"]()
    sh = work / "src.sh"
    sh.write_text('cd "$1" || exit 9\n. "$2"\nfor k in "${@:3}"; do printf %s "${!k}" > "$1/out.$k"; done\n')
    r = subprocess.run(["bash", str(sh), str(work), str(rcp), *nasty], capture_output=True, text=True)
    got = {k: (work / f"out.{k}").read_text(encoding="utf-8") if (work / f"out.{k}").exists() else None for k in nasty}
    out(f"{tag}.quote.bash", got == nasty and r.returncode == 0)
    out(f"{tag}.quote.noexec", sorted(p.name for p in work.iterdir() if p.name.startswith("PWNED")))
    out(f"{tag}.quote.py", ns["read_clauderc"]() == nasty)
    if plat == "linux":  # the reviewer-CLI libs read the same file through light/lib/llm-mode.sh
        lib = Path(src_path).parent / "lib" / "llm-mode.sh"
        r2 = subprocess.run(["bash", "-c", '. "$1"; clauderc_env AWS_G; printf "|"; clauderc_env ANTHROPIC_FOUNDRY_API_KEY', "x", str(lib)],
                            env={"HOME": str(home), "PATH": os.environ["PATH"]}, capture_output=True, text=True, cwd=str(work))
        out("lin.quote.llmmode", r2.stdout == nasty["AWS_G"] + "|" + nasty["ANTHROPIC_FOUNDRY_API_KEY"])
    rcp.unlink()
ns = load("linux")
rcp = ns["clauderc_path"]()
rcp.parent.mkdir(exist_ok=True)
rcp.write_text('export OLD_A="plain value"\nexport OLD_B=\'single\'\nOLD_C=bare\nexport OLD_D="with \'q\'"\n')
out("quote.oldforms", sorted(ns["read_clauderc"]().items()))
ns["merge_clauderc"]({"NEW": "x'y"})
out("quote.merge.keeps", sorted(ns["read_clauderc"]().items()))
out("quote.paste", sorted(ns["parse_creds_text"]("export AWS_X='a'\\''b'\nANTHROPIC_Y=\"c d\"\n").items()))
rcp.unlink()

# ── line breaks: _sh_quote drops everything str.splitlines() would split on, plus NUL; the parser splits on \n only ──
out("lb.quote", ns["_sh_quote"]("a\x0bb\x0cc\x1cd\x1de\x1ef\x85g\u2028h\u2029i\x00j\rk\nl"))
out("lb.quote.plain", ns["_sh_quote"]("it's caf\u00e9\ttab") == "'it'\\''s caf\u00e9\ttab'")
# a value with a Unicode line separator cannot smuggle a second entry through write -> read
ns["write_clauderc"]({"AWS_A": "x\u2028AWS_B=evil\x0bAWS_D=bad\x85", "AWS_C": "z"})
out("lb.roundtrip", sorted(ns["read_clauderc"]().items()))
out("lb.file.lines", len(ns["clauderc_path"]().read_bytes().split(b"\n")) - 1)
ns["clauderc_path"]().unlink()
# the parser: \n separates lines (CRLF too); VT/FF/FS/NEL/U+2028 do not
txt = "export A='x'\r\nexport B='y'\x0b\x0cexport C='z'\u2028export D='w'\x85\nexport E='v'"
out("lb.parse", sorted(ns["parse_clauderc"](txt)))
out("lb.parse.crlf", sorted(ns["parse_clauderc"]("export A=\"1\"\r\nB='2'\r\n").items()))

# ── decoding: undecodable files are never overwritten ──
for plat, tag in (("linux", "lin"), ("win32", "win")):
    ns = load(plat)
    rcp = ns["clauderc_path"](create=True)
    bad = b"export ANTHROPIC_FOUNDRY_API_KEY=caf\xe9\n"
    rcp.write_bytes(bad)
    try:
        ns["merge_clauderc"]({"AWS_X": "1"}); res = "no error"
    except OSError as e:
        res = "refused"
    out(f"{tag}.decode.bad", f"{res},{rcp.read_bytes() == bad},{ns['read_clauderc']()}")
    rcp.write_bytes("export AWS_U='u16'\n".encode("utf-16"))
    ns["merge_clauderc"]({"AWS_X": "1"})
    out(f"{tag}.decode.utf16", sorted(ns["read_clauderc"]().items()))
    rcp.write_bytes(b"\xef\xbb\xbfexport AWS_B='bom'\n")
    ns["merge_clauderc"]({"AWS_X": "1"})
    out(f"{tag}.decode.bom", sorted(ns["read_clauderc"]().items()))
    rcp.unlink()

# ── mode 0600 from creation (chmod forced to fail) ──
ns = load("linux")
old_umask = os.umask(0)
orig_chmod = Path.chmod
Path.chmod = lambda self, *a, **k: (_ for _ in ()).throw(OSError("no chmod"))
try:
    ns["write_clauderc"]({"AWS_X": "1"})
    out("mode.creation", oct(ns["clauderc_path"]().stat().st_mode & 0o777))
finally:
    Path.chmod = orig_chmod
    os.umask(old_umask)
ns["clauderc_path"]().unlink()

# ── AWS merge: other profiles, SSO, comments stay byte for byte ──
creds_in = ("# my keys\n[default]\naws_access_key_id = AKDEFAULT\naws_secret_access_key = sdef\n\n"
            "[bedrock]\naws_access_key_id = OLDKEY\naws_secret_access_key = OLDSECRET\n# about work\n\n"
            "[work]\naws_access_key_id = AKWORK\n; trailing note\n")
config_in = ("[default]\nregion = eu-west-1\n\n[sso-session corp]\nsso_start_url = https://x.awsapps.com/start\nsso_region = us-east-1\n\n"
             "[profile work]\nsso_session = corp\nsso_account_id = 1\n\n[profile bedrock]\nregion = us-east-1\nextra = gone\n"
             "# the next one\n[profile other]\nregion = ap-south-1\n")
for plat, tag in (("linux", "lin"), ("win32", "win")):
    ns = load(plat)
    aws = home / ".aws"
    aws.mkdir(exist_ok=True)
    cp, cf = aws / "credentials", aws / "config"
    cp.write_bytes(creds_in.encode()); cf.write_bytes(config_in.encode())
    cp.chmod(0o644); cf.chmod(0o644)  # a world-readable original: the backup must not stay that way
    ns["AWS_CREDS_PATH"], ns["AWS_CONFIG_PATH"] = cp, cf
    ns["write_aws_credentials"]("AKNEW", "SECNEW", "us-west-2")
    exp_c = creds_in.replace("[bedrock]\naws_access_key_id = OLDKEY\naws_secret_access_key = OLDSECRET\n",
                             "[bedrock]\naws_access_key_id = AKNEW\naws_secret_access_key = SECNEW\n")
    exp_f = config_in.replace("[profile bedrock]\nregion = us-east-1\nextra = gone\n", "[profile bedrock]\nregion = us-west-2\n")
    out(f"{tag}.aws.creds", cp.read_text() == exp_c)
    out(f"{tag}.aws.config", cf.read_text() == exp_f)
    out(f"{tag}.aws.bak", f"{(aws / 'credentials.maude-bak').read_bytes() == creds_in.encode()},{(aws / 'config.maude-bak').read_bytes() == config_in.encode()}")
    out(f"{tag}.aws.bak.mode", f"{oct((aws / 'credentials.maude-bak').stat().st_mode & 0o777)},{oct((aws / 'config.maude-bak').stat().st_mode & 0o777)}")
    ns["write_aws_credentials"]("AKNEW2", "SECNEW2", "us-east-2")  # second save: backup keeps the ORIGINAL
    out(f"{tag}.aws.bak2", (aws / "credentials.maude-bak").read_bytes() == creds_in.encode())
    out(f"{tag}.aws.second", "AKNEW2" in cp.read_text() and "AKNEW\n" not in cp.read_text() and cp.read_text().count("[bedrock]") == 1)
    # no bedrock section yet: appended, rest untouched
    cp.write_bytes(b"[default]\nk = v"); cf.write_bytes(b"")
    ns["write_aws_credentials"]("A", "S", "r")
    out(f"{tag}.aws.append", repr(cp.read_text()))
    out(f"{tag}.aws.fresh.config", repr(cf.read_text()))
    # CRLF file keeps CRLF; fresh install (no files) = what the old writer wrote
    cp.write_bytes(b"[default]\r\nk = v\r\n");
    ns["write_aws_credentials"]("A", "S", "r")
    out(f"{tag}.aws.crlf", repr(cp.read_bytes()))
    cp.unlink(); cf.unlink()
    for p in aws.glob("*.maude-bak"): p.unlink()
    ns["write_aws_credentials"]("A", "S", "r")
    out(f"{tag}.aws.new", repr(cp.read_text() + "|" + cf.read_text()))
    out(f"{tag}.aws.new.nobak", not list(aws.glob("*.maude-bak")))
    cp.write_bytes("[default]\n".encode("utf-16"))
    try:
        ns["write_aws_credentials"]("A", "S", "r"); r16 = "no error"
    except OSError:
        r16 = "refused"
    out(f"{tag}.aws.utf16", r16)
    cp.unlink(); cf.unlink()
    for p in aws.glob("*.maude-bak"): p.unlink()

# ── session cache of a positive `claude auth status` ──
ns = load("darwin")
fake = home / "claude-ok"
fake.write_text("#!/bin/sh\necho '{\"loggedIn\": true}'\n"); fake.chmod(0o755)
ns["plat_real_claude"] = lambda: str(fake)
ns["have_claude_login"] = lambda: False
out("cache.before", ns["creds_gate_precheck"]())
out("cache.status", ns["claude_login_status"]())
ns["plat_real_claude"] = lambda: str(home / "gone")
out("cache.after", ns["creds_gate_precheck"]())
ns = load("linux")
ns["have_claude_login"] = lambda: False
ns["_LOGIN_OK"][0] = True
out("cache.linux.ignored", ns["creds_gate_precheck"]())

# ── feature map ──
ns = load("win32")
out("win.hidden", ",".join(sorted(ns["hidden_widgets"]())))
ns = load("darwin")
out("mac.hidden", ",".join(sorted(ns["hidden_widgets"]())))
ns = load("linux")
out("lin.hidden", ",".join(sorted(ns["hidden_widgets"]())))
PY

mkdir "$d/home"
if ! res=$(env -i HOME="$d/home" PATH="$PATH" python3 "$d/run.py" "$MAUDE_PY" 2>&1); then
    echo "$res"
    FAIL=$((FAIL+1)); echo "  FAIL: run.py crashed"
    suite_summary
fi
val() { printf '%s\n' "$res" | grep -m1 "^$1=" | cut -d= -f2-; }

# path selection
assert_eq "linux: ~/.azure/clauderc" "~/.azure/clauderc" "$(val lin.path)"
assert_eq "mac: ~/.maude/clauderc" "~/.maude/clauderc" "$(val mac.path)"
assert_eq "win: ~/.maude/clauderc" "~/.maude/clauderc" "$(val win.path)"
assert_eq "linux inactive sibling" "~/.azure/clauderc.inactive" "$(val lin.inactive)"
assert_eq "mac inactive sibling" "~/.maude/clauderc.inactive" "$(val mac.inactive)"
assert_eq "win inactive sibling" "~/.maude/clauderc.inactive" "$(val win.inactive)"
assert_eq "linux is not native" "False" "$(val lin.native)"
assert_eq "mac/win are native" "True,True" "$(val mac.native),$(val win.native)"
assert_eq "reading the path never creates ~/.maude" "False,False,False" "$(val lin.maudedir.after.read),$(val mac.maudedir.after.read),$(val win.maudedir.after.read)"
assert_eq "create=True makes ~/.maude mode 0700" "True,0o700" "$(val mac.create)"

# one parser / merge / writer
exp_text="export ANTHROPIC_FOUNDRY_API_KEY='k2'|export CLAUDE_CODE_USE_FOUNDRY='1'|export ANTHROPIC_FOUNDRY_BASE_URL='https://x/anthropic'|"
for t in mac win lin; do
    assert_eq "$t: clauderc written with export K='V' lines, merged" "$exp_text" "$(val $t.rc.text)"
    assert_eq "$t: clauderc mode 0600" "0o600" "$(val $t.rc.mode)"
    assert_eq "$t: LF line endings" "False" "$(val $t.rc.crlf)"
    assert_eq "$t: credentials_configured sees it" "True" "$(val $t.configured)"
done
assert_contains "parse round trip" "('ANTHROPIC_FOUNDRY_API_KEY', 'k2')" "$(val mac.rc.parse)"

# plat_claude_env
assert_eq "linux: plat_claude_env is {} even with a clauderc" "{}" "$(val lin.env)"
assert_eq "linux: _child_env is None" "None" "$(val lin.childenv)"
for t in mac win; do
    assert_eq "$t: nothing configured -> {}" "{}" "$(val $t.env.empty)"
    assert_eq "$t: nothing configured -> inherit (None)" "None" "$(val $t.childenv.empty)"
    f="$(val $t.env.foundry)"
    assert_contains "$t: foundry: base url" "('ANTHROPIC_BASE_URL', 'https://f')" "$f"
    assert_contains "$t: foundry: dummy key" "('ANTHROPIC_API_KEY', 'sk-ant-dummy')" "$f"
    assert_contains "$t: foundry: bedrock off" "('CLAUDE_CODE_USE_BEDROCK', '0')" "$f"
    assert_contains "$t: foundry: clauderc exports" "('ANTHROPIC_FOUNDRY_API_KEY', 'fk')" "$f"
    assert_eq "$t: _child_env merges into a copy of os.environ" "https://f,sk-ant-dummy,0,True" "$(val $t.childenv.foundry)"
    assert_eq "$t: no clauderc: ambient aws config / login / API key change nothing" "{} None None" "$(val $t.env.ambient)"
    assert_eq "$t: tools get the raw exports only (no dummy key, no base url)" "fk,False,False" "$(val $t.toolenv)"
    assert_eq "$t: login does not strip the key for tools" "sk-ant-ambient,1" "$(val $t.toolenv.login)"
    assert_eq "$t: .inactive (parked) clauderc without Bedrock flag is not read" "{}" "$(val $t.env.parked)"
    assert_eq "$t: parked + aws config but no Bedrock flag: nothing" "{}" "$(val $t.env.parked.aws.noflag)"
    assert_eq "$t: parked Bedrock flag but no [bedrock] profile: nothing" "{}" "$(val $t.env.parked.noprofile)"
    assert_eq "$t: parked Foundry exports never leak into the Bedrock env" "False" "$(val $t.env.parked.noleak)"
    assert_eq "$t: live clauderc with Bedrock flag" "['AWS_PROFILE', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_FOUNDRY']" "$(val $t.env.bedrock.live)"
    assert_eq "$t: ~/.aws/config alone (no Bedrock flag saved) does not select Bedrock" "{'ANTHROPIC_FOUNDRY_API_KEY': 'fk'}" "$(val $t.env.aws.noflag)"
    b="$(val $t.env.bedrock)"
    assert_contains "$t: bedrock: use flag" "('CLAUDE_CODE_USE_BEDROCK', '1')" "$b"
    assert_contains "$t: bedrock: profile" "('AWS_PROFILE', 'bedrock')" "$b"
    assert_contains "$t: bedrock: region" "('AWS_DEFAULT_REGION', 'us-west-2')" "$b"
    assert_contains "$t: bedrock: foundry off" "('CLAUDE_CODE_USE_FOUNDRY', '0')" "$b"
    l="$(val $t.env.login)"
    assert_contains "$t: login wins: api key removed" "('ANTHROPIC_API_KEY', None)" "$l"
    assert_contains "$t: login wins: foundry off" "('CLAUDE_CODE_USE_FOUNDRY', '0')" "$l"
    assert_eq "$t: login wins: _child_env drops the key" "False,0" "$(val $t.childenv.login)"
    assert_eq "$t: login only -> {}" "{}" "$(val $t.env.login.only)"
    u="$(val $t.env.baseurl)"
    assert_contains "$t: custom base url gets a dummy key" "('ANTHROPIC_API_KEY', 'sk-ant-dummy')" "$u"
    assert_eq "$t: incomplete foundry adds nothing but the rc" "[('CLAUDE_CODE_USE_FOUNDRY', '1')]" "$(val $t.env.incomplete)"
done
assert_contains "pure: ambient cloud vars cleared by a login" "('ANTHROPIC_API_KEY', None)" "$(val pure.login.ambient)"
assert_contains "pure: ambient bedrock flag set to 0 by a login" "('CLAUDE_CODE_USE_BEDROCK', '0')" "$(val pure.login.ambient)"
assert_contains "pure: [bedrock] header detected" "('AWS_PROFILE', 'bedrock')" "$(val pure.bedrock.bare)"
assert_eq "pure: no clauderc -> nothing, whatever the login/aws config say" "{}" "$(val pure.norc)"
assert_eq "pure: [bedrock] in a comment is not a profile" "{'CLAUDE_CODE_USE_BEDROCK': '1'}" "$(val pure.bedrock.comment)"

# gate matrix: <native><login_file><configured><status n/f/t>=<shows gate>
want="000n=1 000f=1 000t=0 001n=0 001f=0 001t=0 010n=0 010f=0 010t=0 011n=0 011f=0 011t=0"
want="$want 100n=0 100f=1 100t=0 101n=0 101f=0 101t=0 110n=0 110f=0 110t=0 111n=0 111f=0 111t=0"
assert_eq "gate matrix (sandbox rows 0xxx, native rows 1xxx)" "$want" "$(val gate)"
for t in lin mac win; do
    assert_eq "$t: login file passes the gate without asking claude" "False asked=0" "$(val $t.blocks.login_file)"
    assert_eq "$t: configured creds pass the gate without asking claude" "False asked=0" "$(val $t.blocks.configured)"
done
for t in mac win; do
    assert_eq "$t: status true passes" "False" "$(val $t.blocks.status_true)"
done
assert_eq "linux: a status is never consulted" "True" "$(val lin.blocks.status_true)"
assert_eq "linux: nothing configured -> gate, claude never asked" "True asked=0" "$(val lin.blocks.nothing)"
assert_eq "linux: stays gated whatever a status would say" "True" "$(val lin.blocks.explicit_false)"
for t in mac win; do
    assert_eq "$t: status unknown (None) never blocks" "False asked=1" "$(val $t.blocks.nothing)"
    assert_eq "$t: only an explicit 'not logged in' shows the gate" "True" "$(val $t.blocks.explicit_false)"
done

# claude auth status parsing
assert_eq "status: loggedIn true" "True" "$(val status.true)"
assert_eq "status: loggedIn false" "False" "$(val status.false)"
assert_eq "status: garbage -> None" "None" "$(val status.garbage)"
assert_eq "status: no loggedIn key -> None" "None" "$(val status.nokey)"
assert_eq "status: missing binary -> None" "None" "$(val status.missing)"
assert_eq "status: timeout -> None" "None" "$(val status.timeout)"

# hidden widgets: Settings, Update Maude, To Github and Setup Git(hub) are on everywhere now
assert_eq "win hidden set" "#autostart,#btn-web" "$(val win.hidden)"
assert_eq "mac hidden set" "#autostart,#btn-web" "$(val mac.hidden)"
assert_eq "linux hides nothing" "" "$(val lin.hidden)"

# login refresh-token rule (also the sandbox gate)
assert_eq "login: valid token" "True" "$(val login.valid)"
assert_eq "login: expired access token, no refresh token" "False" "$(val login.expired.norefresh)"
assert_eq "login: expired access token + refresh token = still logged in" "True" "$(val login.expired.refresh)"
assert_eq "login: refresh token with a future expiry" "True" "$(val login.expired.refresh.valid)"
assert_eq "login: refresh token itself expired" "False" "$(val login.expired.refresh.expired)"
assert_eq "login: no expiry info = cannot tell = logged in" "True" "$(val login.noexpiry)"
assert_eq "login: no access token" "False" "$(val login.notoken)"

# shell-safe quoting (bash round trip from a script file, nothing executed)
for t in lin win; do
    assert_eq "$t: bash source yields the literal values" "True" "$(val $t.quote.bash)"
    assert_eq "$t: no command in a value was executed" "[]" "$(val $t.quote.noexec)"
    assert_eq "$t: python reads back what it wrote" "True" "$(val $t.quote.py)"
done
assert_eq "llm-mode.sh clauderc_env reads the TUI-written file" "True" "$(val lin.quote.llmmode)"
assert_eq "old double-quoted / single / bare forms still parse" "[('OLD_A', 'plain value'), ('OLD_B', 'single'), ('OLD_C', 'bare'), ('OLD_D', \"with 'q'\")]" "$(val quote.oldforms)"
assert_contains "merge keeps old entries and adds the new quoted one" "('NEW', \"x'y\")" "$(val quote.merge.keeps)"
assert_contains "merge keeps old entries" "('OLD_A', 'plain value')" "$(val quote.merge.keeps)"
assert_eq "_sh_quote drops CR LF VT FF FS GS RS NEL U+2028 U+2029 and NUL" "'abcdefghijkl'" "$(val lb.quote)"
assert_eq "_sh_quote keeps quotes, accents and tabs" "True" "$(val lb.quote.plain)"
assert_eq "no Unicode line break smuggles a second entry" "[('AWS_A', 'xAWS_B=evilAWS_D=bad'), ('AWS_C', 'z')]" "$(val lb.roundtrip)"
assert_eq "one line per entry in the file" "2" "$(val lb.file.lines)"
assert_eq "parse_clauderc splits on newline only (VT, FF, NEL, U+2028 stay inside the line)" "['A', 'B', 'E']" "$(val lb.parse)"
assert_eq "parse_clauderc strips a trailing CR" "[('A', '1'), ('B', '2')]" "$(val lb.parse.crlf)"
assert_eq "pasted single-quote escapes parse" "[('ANTHROPIC_Y', 'c d'), ('AWS_X', \"a'b\")]" "$(val quote.paste)"

# decoding
for t in lin win; do
    assert_eq "$t: undecodable clauderc is refused and left byte for byte" "refused,True,{}" "$(val $t.decode.bad)"
    assert_eq "$t: UTF-16 clauderc is merged, not treated as empty" "[('AWS_U', 'u16'), ('AWS_X', '1')]" "$(val $t.decode.utf16)"
    assert_eq "$t: UTF-8 BOM clauderc is merged" "[('AWS_B', 'bom'), ('AWS_X', '1')]" "$(val $t.decode.bom)"
done
assert_eq "clauderc created 0600 even when chmod fails (umask 0)" "0o600" "$(val mode.creation)"

# AWS profile merge
for t in lin win; do
    assert_eq "$t: credentials: only [bedrock] replaced, rest byte for byte" "True" "$(val $t.aws.creds)"
    assert_eq "$t: config: only [profile bedrock] replaced, rest byte for byte" "True" "$(val $t.aws.config)"
    assert_eq "$t: one-time .maude-bak holds the original bytes" "True,True" "$(val $t.aws.bak)"
    assert_eq "$t: the .maude-bak copy is chmod 0600" "0o600,0o600" "$(val $t.aws.bak.mode)"
    assert_eq "$t: second save keeps the original backup" "True" "$(val $t.aws.bak2)"
    assert_eq "$t: second save replaces, never duplicates" "True" "$(val $t.aws.second)"
    assert_eq "$t: missing section is appended after a blank line" "'[default]\nk = v\n\n[bedrock]\naws_access_key_id = A\naws_secret_access_key = S\n'" "$(val $t.aws.append)"
    assert_eq "$t: empty config gets just the profile" "'[profile bedrock]\nregion = r\n'" "$(val $t.aws.fresh.config)"
    assert_eq "$t: CRLF files keep CRLF" "b'[default]\r\nk = v\r\n\r\n[bedrock]\r\naws_access_key_id = A\r\naws_secret_access_key = S\r\n'" "$(val $t.aws.crlf)"
    assert_eq "$t: fresh install writes what the old writer wrote" "'[bedrock]\naws_access_key_id = A\naws_secret_access_key = S\n|[profile bedrock]\nregion = r\n'" "$(val $t.aws.new)"
    assert_eq "$t: no backup when there was nothing to back up" "True" "$(val $t.aws.new.nobak)"
    assert_eq "$t: UTF-16 aws file is refused" "refused" "$(val $t.aws.utf16)"
done

# gate: cheap precheck, session cache
assert_eq "cache: native, nothing known -> ask claude (None)" "None" "$(val cache.before)"
assert_eq "cache: status true" "True" "$(val cache.status)"
assert_eq "cache: remembered for the session (claude no longer asked)" "False" "$(val cache.after)"
assert_eq "cache: sandbox ignores it" "True" "$(val cache.linux.ignored)"
# maude.exe launcher (maude_exe.py): the --windows / Explorer route gets the same env, only when a clauderc exists
cat > "$d/exe.py" <<'PY'
import contextlib, io, os, sys
light, home = sys.argv[1], sys.argv[2]
sys.path.insert(0, light)
import maude_exe as mx
os.environ["USERPROFILE"] = home
def out(k, v):
    print(f"{k}={v}")
os.makedirs(home + "/.aws")
open(home + "/.aws/config", "w").write("[profile bedrock]\nregion = x\n")
out("exe.aws_only", f"{mx._creds_env()} {'maude' in sys.modules}")  # ~/.aws/config alone: nothing, maude.py not imported
os.makedirs(home + "/.maude")
open(home + "/.maude/anthropic-key", "w").write("sk-ant-x")
open(home + "/.maude/settings.json", "w").write('{"anthropic_key_active": false}')
out("exe.key_inactive", f"{mx._creds_env()} {'maude' in sys.modules}")  # key file but flag off: nothing, maude.py not imported
open(home + "/.maude/settings.json", "w").write('{"anthropic_key_active": true}')
sys.modules["maude"] = None  # make `import maude` fail
err = io.StringIO()
with contextlib.redirect_stderr(err):
    r = mx._creds_env()
out("exe.key_active", f"{r}|{err.getvalue().strip()}")  # key active, no clauderc: maude.py IS loaded (the load fails here)
del sys.modules["maude"]
open(home + "/.maude/settings.json", "wb").write('{"anthropic_key_active": true}'.encode("utf-16"))  # PowerShell 5.1 `>` writes UTF-16
sys.modules["maude"] = None
err = io.StringIO()
with contextlib.redirect_stderr(err):
    r = mx._creds_env()
out("exe.key_utf16", f"{r}|{err.getvalue().strip()}")
del sys.modules["maude"]
open(home + "/.maude/settings.json", "w").write("[" * 200000 + "]" * 200000)  # json raises RecursionError: must not escape
out("exe.key_deep_json", f"{mx._creds_env()} {'maude' in sys.modules}")
open(home + "/.maude/settings.json", "w").write('{"anthropic_key_active": true}')
os.unlink(home + "/.maude/anthropic-key")
out("exe.key_active_nofile", f"{mx._creds_env()} {'maude' in sys.modules}")  # flag on but no key file: nothing
open(home + "/.maude/clauderc", "w").write("export AWS_X='1'\n")
sys.modules["maude"] = None  # make `import maude` fail
err = io.StringIO()
with contextlib.redirect_stderr(err):
    r = mx._creds_env()
out("exe.importfail", f"{r}|{err.getvalue().strip()}")
del sys.modules["maude"]
mx.child_env = lambda: {"A": "1"}
out("exe.hook", mx._creds_env())
def boom():
    raise RuntimeError("bad")
mx.child_env = boom
err = io.StringIO()
with contextlib.redirect_stderr(err):
    r = mx._creds_env()
out("exe.hookfail", f"{r}|{err.getvalue().strip()}")
calls = []
mx.find_claude = lambda up: "C:/c.exe"
mx._run = lambda cmd, **kw: calls.append(kw) or 0
mx.windows_model_effort = lambda up: ("opus", "")
mx.child_env = lambda: {"A": "1"}
mx.run_windows("/tmp/x", "opus", banner=False, fresh=True)
out("exe.run.env", calls[-1])
mx.child_env = lambda: None
mx.run_windows("/tmp/x", "opus", banner=False, fresh=True)
out("exe.run.noenv", calls[-1])
PY
mkdir "$d/exehome"
exe_res=$(env -i PATH="$PATH" python3 "$d/exe.py" "${SCRIPT_DIR}/../light" "$d/exehome" 2>&1) || { echo "$exe_res"; FAIL=$((FAIL+1)); echo "  FAIL: exe.py crashed"; }
eval_val() { printf '%s\n' "$exe_res" | grep -m1 "^$1=" | cut -d= -f2-; }
assert_eq "exe: ~/.aws/config alone loads nothing and never imports maude.py" "None False" "$(eval_val exe.aws_only)"
assert_eq "exe: key file with the flag off loads nothing" "None False" "$(eval_val exe.key_inactive)"
assert_contains "exe: an active Anthropic key alone makes the Explorer route load maude.py" "None|Maude: could not load the saved credentials" "$(eval_val exe.key_active)"
assert_contains "exe: a UTF-16 settings.json (BOM) is read" "None|Maude: could not load the saved credentials" "$(eval_val exe.key_utf16)"
assert_eq "exe: a pathologically nested settings.json loads nothing and does not raise" "None False" "$(eval_val exe.key_deep_json)"
assert_eq "exe: flag on but no key file loads nothing" "None False" "$(eval_val exe.key_active_nofile)"
assert_contains "exe: a failed load prints one warning and starts without" "None|Maude: could not load the saved credentials" "$(eval_val exe.importfail)"
assert_eq "exe: the TUI hook is used when set" "{'A': '1'}" "$(eval_val exe.hook)"
assert_contains "exe: a failing hook warns too" "None|Maude: could not load the saved credentials (bad)" "$(eval_val exe.hookfail)"
assert_eq "exe: run_windows hands the env to Claude Code" "{'cwd': '/tmp/x', 'env': {'A': '1'}}" "$(eval_val exe.run.env)"
assert_eq "exe: no saved credentials -> no env kwarg (inherit)" "{'cwd': '/tmp/x'}" "$(eval_val exe.run.noenv)"

suite_summary