#!/usr/bin/env bash
# tests/test-git-native.sh - Git / SSH / commit-signing helpers of light/maude.py for Windows and macOS:
# SSH stack selection, GIT_SSH_COMMAND, askpass generation, ssh-add args, SSH signing config, gh scopes,
# macOS ~/.ssh/config. Pulls only the needed names out of maude.py (no textual needed).
# No GitHub calls, no key upload; every key and gitconfig lives in a mktemp dir.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "maude.py native Git/SSH helpers"

MAUDE_PY="${SCRIPT_DIR}/../light/maude.py"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

cat > "$d/run.py" <<'PY'
import ast, os, re, shutil, subprocess, sys, tempfile, types
from pathlib import Path

src_path, tmp = sys.argv[1], Path(sys.argv[2])
tree = ast.parse(open(src_path, encoding="utf-8").read(), feature_version=(3, 9))
want = {"IS_WIN", "IS_MAC", "current_platform", "is_native", "ssh_tool", "_fwd", "git_ssh_command",
        "ASKPASS_ENV_VAR", "askpass_script", "askpass_env", "make_askpass", "ssh_add_args",
        "ssh_signing_config", "apply_ssh_signing", "GH_SIGNING_SCOPE", "gh_login_scopes", "gh_login_cmd",
        "MAC_SSH_CONFIG_BLOCK", "ensure_mac_ssh_config", "git_config_get", "git_config_set", "PLATFORM",
        "signing_conflict", "ssh_command_pair", "apply_ssh_command", "parse_ssh_version", "OPENSSH_MISSING_MSG",
        "openssh_available", "askpass_unsupported_reason", "kill_proc_tree", "run_capture", "signing_roundtrip",
        "active_ssh_key", "push_guard_key", "push_ssh_env", "git_ssh_command", "_norm_cmd", "git_config_effective", "existing_ssh_key", "SSH_KEY_PATH", "gh_refresh_cmd", "classify_gh_key_failure"}

def load(platform):
    ns = {"os": os, "re": re, "shutil": shutil, "subprocess": subprocess, "Path": Path,
          "tempfile": tempfile, "sys": types.SimpleNamespace(platform=platform)}
    for n in tree.body:
        names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
        if names & want:
            exec(compile(ast.Module([n], []), "maude.py", "exec"), ns)
    ns["git_exe"] = lambda: "git"  # the real one needs the platform layer (plat_tool)
    return ns

def out(k, v):
    print(f"{k}={v}")

# No ambient agent, and a private HOME: nothing here may touch the real ~/.ssh or an agent with real keys.
os.environ.pop("SSH_AUTH_SOCK", None)
os.environ.pop("SSH_AGENT_PID", None)
os.environ["HOME"] = str(tmp)
W, M, L = load("win32"), load("darwin"), load("linux")
env = {"SystemRoot": "C:\\Windows"}
here = lambda p: True

# SSH stack: the OS's own OpenSSH; sandbox keeps bare names.
out("tool.win", W["ssh_tool"]("ssh-keygen", "windows", env, here))
out("tool.win.missing", W["ssh_tool"]("ssh-keygen", "windows", env, lambda p: False))  # never a bare name
out("openssh.win.missing", W["openssh_available"]("windows", env, lambda p: False))
out("openssh.win.there", W["openssh_available"]("windows", env, here))
out("openssh.lin", L["openssh_available"]("linux-sandbox", {}, lambda p: False))
out("tool.win.noroot", W["ssh_tool"]("ssh", "windows", {}, here))
out("tool.mac", M["ssh_tool"]("ssh-add", "macos", {}, here))
out("tool.mac.missing", M["ssh_tool"]("ssh-add", "macos", {}, lambda p: False))
out("tool.lin", L["ssh_tool"]("ssh", "linux-sandbox", {}, here))
out("tool.default.lin", L["ssh_tool"]("ssh-keygen"))

# GIT_SSH_COMMAND
out("gsc.lin", L["git_ssh_command"]("linux-sandbox"))
out("gsc.win", W["git_ssh_command"]("windows", "C:\\Windows\\System32\\OpenSSH\\ssh.exe"))
out("gsc.win.space", W["git_ssh_command"]("windows", "C:\\Program Files\\OpenSSH\\ssh.exe"))
out("gsc.mac", M["git_ssh_command"]("macos", "/usr/bin/ssh"))

# askpass: static script, passphrase only in the environment
sfx, txt = W["askpass_script"]("windows")
out("askpass.win.suffix", sfx)
out("askpass.win.delayed", "EnableDelayedExpansion" in txt and "!MAUDE_ASKPASS_PW!" in txt and "\r\n" in txt)
sfx, txt = M["askpass_script"]("macos")
out("askpass.mac.suffix", sfx)
pw = "a&b|c%d^e!f\"g h $x 'q'"
out("askpass.static", pw not in txt and "$MAUDE_ASKPASS_PW" in txt)
path = M["make_askpass"]("macos")
body = open(path).read()
out("askpass.file.nosecret", "a&b" not in body)
out("askpass.file.mode", oct(os.stat(path).st_mode & 0o777))
e = M["askpass_env"](path, pw, {"PATH": "/usr/bin", "DISPLAY": "x"})
out("askpass.env", sorted(k for k in e if k.startswith(("SSH_", "MAUDE_", "DISPLAY"))))
out("askpass.env.require", e["SSH_ASKPASS_REQUIRE"])
out("askpass.env.display_kept", e["DISPLAY"])
r = subprocess.run(["/bin/sh", path], capture_output=True, text=True, env=e)
out("askpass.run.exact", r.stdout == pw)
os.unlink(path)

# ssh-add command line
out("add.mac", M["ssh_add_args"]("/h/.ssh/id_ed25519", "macos", "/usr/bin/ssh-add"))
out("add.win", W["ssh_add_args"]("C:\\u\\.ssh\\id_ed25519", "windows", "C:\\Windows\\System32\\OpenSSH\\ssh-add.exe"))
out("add.lin", L["ssh_add_args"]("/h/.ssh/id_ed25519", "linux-sandbox", "ssh-add"))

# SSH signing config (no gpg anywhere)
pub = "C:\\Users\\u\\.ssh\\id_ed25519.pub"
sc = W["ssh_signing_config"](pub, "windows", env, here)
out("sign.win", sc)
out("sign.mac.keys", [k for k, _ in M["ssh_signing_config"]("/Users/u/.ssh/id_ed25519.pub", "macos", {}, lambda p: False)])

# apply: signing only; never overwrites an existing setup
seen = {}
setter = lambda k, v: seen.__setitem__(k, v)
W["apply_ssh_signing"](pub, get=lambda k: "", set_=setter, platform="windows", env=env, exists=here)
out("apply.win.keys", sorted(seen))
out("apply.win.no_sshcmd", "core.sshCommand" not in seen)
seen.clear()
M["apply_ssh_signing"]("/Users/u/.ssh/id_ed25519.pub", get=lambda k: "", set_=setter,
                       platform="macos", env={}, exists=lambda p: False)
out("apply.mac.keys", sorted(seen))
G = lambda d: (lambda k: d.get(k, ""))
cases = {
    "none": {},
    "same": {"gpg.format": "ssh", "user.signingkey": "C:/Users/u/.ssh/id_ed25519.pub", "commit.gpgsign": "true"},
    "gpg_key": {"user.signingkey": "ABCD1234", "commit.gpgsign": "true"},
    "gpg_key_fmt": {"gpg.format": "openpgp", "user.signingkey": "ABCD1234"},
    "other_ssh": {"gpg.format": "ssh", "user.signingkey": "C:/other.pub"},
    "default_gpg": {"commit.gpgsign": "true"},
    "gpgsign_false": {"commit.gpgsign": "false"},
}
for name, d_ in cases.items():
    out("conflict." + name, bool(W["signing_conflict"](pub, G(d_))))
for name in ("gpg_key", "other_ssh", "default_gpg"):
    seen.clear()
    r_ = W["apply_ssh_signing"](pub, get=G(cases[name]), set_=setter, platform="windows", env=env, exists=here)
    out("apply.conflict_untouched." + name, (r_, dict(seen)))

# core.sshCommand: only with MinGit, never when the user has one or GIT_SSH(_COMMAND) is set
mg = "C:\\Users\\u\\AppData\\Local\\Programs\\MinGit\\cmd\\git.exe"
out("sshcmd.mingit", W["ssh_command_pair"](G({}), "windows", env, mg, here))
out("sshcmd.gitforwindows", W["ssh_command_pair"](G({}), "windows", env, "C:\\Program Files\\Git\\cmd\\git.exe", here))
out("sshcmd.existing", W["ssh_command_pair"](G({"core.sshCommand": "x"}), "windows", env, mg, here))
out("sshcmd.env.GIT_SSH", W["ssh_command_pair"](G({}), "windows", {**env, "GIT_SSH": "x"}, mg, here))
out("sshcmd.env.GIT_SSH_COMMAND", W["ssh_command_pair"](G({}), "windows", {**env, "GIT_SSH_COMMAND": "x"}, mg, here))
out("sshcmd.mac", M["ssh_command_pair"](G({}), "macos", {}, mg, here))
out("sshcmd.quoted", W["ssh_command_pair"](G({}), "windows", {"SystemRoot": "C:\\My Win"}, mg, here))
seen.clear()
W["apply_ssh_command"](G({}), setter, "windows", env, mg, here)
out("sshcmd.applied", dict(seen))

# versions / askpass support / gh helpers
out("ver.win", W["parse_ssh_version"]("OpenSSH_for_Windows_9.5p2, LibreSSL 3.8.2"))
out("ver.unix", W["parse_ssh_version"]("OpenSSH_8.2p1 Ubuntu-4ubuntu0.5, OpenSSL"))
out("ver.junk", W["parse_ssh_version"]("nothing"))
out("askpass.old", "8.4" in W["askpass_unsupported_reason"]((7, 9)) )
out("askpass.new", repr(W["askpass_unsupported_reason"]((8, 4))))
out("askpass.unknown", repr(W["askpass_unsupported_reason"](None)))
out("refresh.cmd", W["gh_refresh_cmd"]("gh.exe"))
cf = W["classify_gh_key_failure"]
out("gh.in_use", cf("ssh-signing-key", "HTTP 422: key is already in use")[0])
out("gh.other_error", cf("ssh-key", "HTTP 401 bad credentials")[0])
out("gh.gpg_already", cf("gpg-key", "key already exists")[0])

# active key: id_ed25519, else id_rsa
sshdir = tmp / ".ssh"; sshdir.mkdir()
(sshdir / "id_rsa").write_text("x"); (sshdir / "id_rsa.pub").write_text("ssh-rsa AAAA")
out("activekey.rsa", L["active_ssh_key"]().name)
(sshdir / "id_ed25519").write_text("x"); (sshdir / "id_ed25519.pub").write_text("ssh-ed25519 AAAA")
out("activekey.ed", L["active_ssh_key"]().name)
(sshdir / "id_ed25519").unlink(); (sshdir / "id_ed25519.pub").unlink()
(sshdir / "id_rsa").unlink(); (sshdir / "id_rsa.pub").unlink()
out("activekey.none", L["active_ssh_key"]().name)  # no key at all: the default path
# Push guard key: the sandbox keeps id_ed25519 exactly as before, even when only id_rsa exists
(sshdir / "id_rsa").write_text("x"); (sshdir / "id_rsa.pub").write_text("ssh-rsa AAAA")
out("guard.lin.rsa_only", L["push_guard_key"]().name)
out("guard.mac.rsa_only", M["push_guard_key"]().name)
# push GIT_SSH_COMMAND: sandbox always forces it; natively the user's own setting wins
out("pushenv.lin", L["push_ssh_env"]({"GIT_SSH_COMMAND": "x"}, "y", "linux-sandbox"))
out("pushenv.win.default", W["push_ssh_env"]({}, "", "windows"))
out("pushenv.win.GIT_SSH", W["push_ssh_env"]({"GIT_SSH": "x"}, "", "windows"))
out("pushenv.win.GIT_SSH_COMMAND", W["push_ssh_env"]({"GIT_SSH_COMMAND": "x"}, "", "windows"))
out("pushenv.win.core", W["push_ssh_env"]({}, "D:/mine/ssh.exe", "windows"))
own = "C:/Windows/System32/OpenSSH/ssh.exe"
for k, v in (("plain", own), ("backslash", "C:\\Windows\\System32\\OpenSSH\\ssh.exe"), ("upper", own.upper()), ("quoted", '"' + own + '"')):
    out("pushenv.win.own." + k, W["push_ssh_env"]({}, v, "windows") == {"GIT_SSH_COMMAND": own + " -o BatchMode=yes -o StrictHostKeyChecking=accept-new"})
out("pushenv.mac.core", M["push_ssh_env"]({}, "/opt/x/ssh", "macos"))

# run_capture kills a hanging child after the timeout
import time
t0 = time.time()
rc_, _o = M["run_capture"](["sleep", "30"], 1)
out("capture.timeout", (rc_, time.time() - t0 < 8))
rc_, o_ = M["run_capture"](["echo", "hi"], 5)
out("capture.ok", (rc_, o_))

# real signing round trip with scratch keys (no agent): unencrypted works, encrypted-without-agent fails fast
kg = "/usr/bin/ssh-keygen"
if os.path.exists(kg):
    k1, k2 = tmp / "k_plain", tmp / "k_enc"
    subprocess.run([kg, "-q", "-t", "ed25519", "-N", "", "-C", "a", "-f", str(k1)], check=True)
    subprocess.run([kg, "-q", "-t", "ed25519", "-N", "pw-123", "-C", "b", "-f", str(k2)], check=True)
    out("rt.plain", M["signing_roundtrip"](str(k1) + ".pub", 8, "macos")[0])
    t0 = time.time()
    ok_, why_ = M["signing_roundtrip"](str(k2) + ".pub", 8, "macos")
    out("rt.enc_noagent", (ok_, time.time() - t0 < 8))
    out("rt.missing_tool", W["signing_roundtrip"](str(k1) + ".pub", 8, "windows")[1] == W["OPENSSH_MISSING_MSG"])
    # a passphrase key with no agent must NOT get commit.gpgsign (what the wizard does with the result)
    if shutil.which("git"):
        os.environ["GIT_CONFIG_GLOBAL"] = str(tmp / "gitconfig-enc")
        os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")
        ok_, _ = M["signing_roundtrip"](str(k2) + ".pub", 8, "macos")
        if ok_:
            M["apply_ssh_signing"](str(k2) + ".pub", platform="macos", env={})
        out("enc.gpgsign", repr(M["git_config_get"]("commit.gpgsign")))
        # with the key in a private agent the round trip works
        if shutil.which("ssh-agent") and shutil.which("ssh-add"):
            r_ = subprocess.run(["ssh-agent", "-s"], capture_output=True, text=True)
            ev = dict(re.findall(r"([A-Z_]+)=([^;]+);", r_.stdout))
            old = {}
            try:
                ap = M["make_askpass"]("macos")
                e_ = M["askpass_env"](ap, "pw-123", {**os.environ, **ev})
                a_ = subprocess.run(["ssh-add", str(k2)], env=e_, capture_output=True, stdin=subprocess.DEVNULL, timeout=20)
                os.unlink(ap)
                old = {k: os.environ.get(k) for k in ev}
                os.environ.update(ev)
                out("rt.enc_agent", M["signing_roundtrip"](str(k2) + ".pub", 8, "macos")[0])
            finally:
                for k, v in old.items():
                    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                subprocess.run(["ssh-agent", "-k"], env={**os.environ, **ev}, capture_output=True)
# gh scopes / login command
out("scope.lin", L["gh_login_scopes"]("linux-sandbox"))
out("scope.win", W["gh_login_scopes"]("windows"))
out("scope.mac", M["gh_login_scopes"]("macos"))
out("login.lin", L["gh_login_cmd"]("gh", "linux-sandbox"))
out("login.win", W["gh_login_cmd"]("gh.exe", "windows"))

# macOS ~/.ssh/config: written only when absent
cfg = tmp / "mac" / ".ssh" / "config"
out("mac.cfg.new", M["ensure_mac_ssh_config"](cfg))
t1 = cfg.read_text()
out("mac.cfg.has", all(s in t1 for s in ("AddKeysToAgent yes", "UseKeychain yes", "IgnoreUnknown UseKeychain")))
out("mac.cfg.again", M["ensure_mac_ssh_config"](cfg))
out("mac.cfg.same", cfg.read_text() == t1)
cfg2 = tmp / "mac2" / "config"
cfg2.parent.mkdir(); cfg2.write_text("Host x\n  UseKeychain no\n")
out("mac.cfg.existing_untouched", (M["ensure_mac_ssh_config"](cfg2), cfg2.read_text()))
cfg3 = tmp / "mac3" / "config"
cfg3.parent.mkdir(); cfg3.write_text("Host x\n  User u\n")
out("mac.cfg.append", M["ensure_mac_ssh_config"](cfg3) and cfg3.read_text().startswith("Host x\n  User u\n\n# Added by Maude"))

# real git config under a private GIT_CONFIG_GLOBAL (never the user's gitconfig)
if shutil.which("git"):
    os.environ["GIT_CONFIG_GLOBAL"] = str(tmp / "gitconfig")
    os.environ["HOME"] = str(tmp)
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")
    M["git_config_set"]("user.name", "T")
    ep = tmp / "effproj"; subprocess.run(["git", "init", "-q", str(ep)], check=True)
    eq = tmp / "effproj2"; subprocess.run(["git", "init", "-q", str(eq)], check=True)
    M["git_config_set"]("core.sshCommand", "/glob/ssh")
    subprocess.run(["git", "-C", str(ep), "config", "core.sshCommand", "/proj/ssh"], check=True)
    out("effcfg", (M["git_config_effective"](str(ep), "core.sshCommand"), M["git_config_effective"](str(eq), "core.sshCommand"),
                   M["git_config_effective"](str(eq), "no.such")))
    subprocess.run(["git", "config", "--global", "--unset", "core.sshCommand"], check=True)
    out("git.set.get", M["git_config_get"]("user.name"))
    out("git.missing.empty", M["git_config_get"]("no.such.key"))
    if shutil.which("ssh-keygen"):
        key = tmp / "id_ed25519"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "t@e.x", "-f", str(key)], check=True)
        M["git_config_set"]("user.email", "t@e.x")
        M["apply_ssh_signing"](str(key) + ".pub", platform="macos", env={})  # real git config --global writes (/usr/bin/ssh-keygen as gpg.ssh.program)
        out("git.signing.format", M["git_config_get"]("gpg.format"))
        out("git.signing.program", M["git_config_get"]("gpg.ssh.program") != "" if os.path.exists("/usr/bin/ssh-keygen") else "n/a")
        repo = tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        r = subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "s"], capture_output=True, text=True)
        c = subprocess.run(["git", "-C", str(repo), "cat-file", "commit", "HEAD"], capture_output=True, text=True).stdout
        out("git.signed", r.returncode == 0 and "BEGIN SSH SIGNATURE" in c)
PY

mkdir "$d/work"
python3 -I "$d/run.py" "$MAUDE_PY" "$d/work" > "$d/out.txt" 2>"$d/err.txt" || { cat "$d/err.txt"; FAIL=$((FAIL+1)); }
get() { grep -m1 "^$1=" "$d/out.txt" | cut -d= -f2-; }

# ── SSH stack ──────────────────────────────────────────────────────────
assert_eq "windows: System32 OpenSSH ssh-keygen" "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" "$(get tool.win)"
assert_eq "windows: no OpenSSH folder -> still the System32 path, never a bare name" "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" "$(get tool.win.missing)"
assert_eq "windows: OpenSSH missing is detected" "False" "$(get openssh.win.missing)"
assert_eq "windows: OpenSSH present"             "True"  "$(get openssh.win.there)"
assert_eq "sandbox: always available"            "True"  "$(get openssh.lin)"
assert_eq "windows: SystemRoot missing -> C:\\Windows" "C:\\Windows\\System32\\OpenSSH\\ssh.exe" "$(get tool.win.noroot)"
assert_eq "macos: /usr/bin"   "/usr/bin/ssh-add" "$(get tool.mac)"
assert_eq "macos: not there -> bare name" "ssh-add" "$(get tool.mac.missing)"
assert_eq "sandbox: bare name (unchanged)" "ssh" "$(get tool.lin)"
assert_eq "sandbox default: bare name"     "ssh-keygen" "$(get tool.default.lin)"

# ── GIT_SSH_COMMAND ────────────────────────────────────────────────────
assert_eq "sandbox GIT_SSH_COMMAND is byte-identical to before" \
  "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" "$(get gsc.lin)"
assert_eq "windows GIT_SSH_COMMAND uses OpenSSH, forward slashes" \
  "C:/Windows/System32/OpenSSH/ssh.exe -o BatchMode=yes -o StrictHostKeyChecking=accept-new" "$(get gsc.win)"
assert_eq "windows GIT_SSH_COMMAND quotes a path with spaces" \
  "\"C:/Program Files/OpenSSH/ssh.exe\" -o BatchMode=yes -o StrictHostKeyChecking=accept-new" "$(get gsc.win.space)"
assert_eq "macos GIT_SSH_COMMAND" "/usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" "$(get gsc.mac)"

# ── askpass ────────────────────────────────────────────────────────────
assert_eq "windows askpass is a .cmd"            ".cmd" "$(get askpass.win.suffix)"
assert_eq "windows askpass: delayed expansion"   "True" "$(get askpass.win.delayed)"
assert_eq "mac/linux askpass is a .sh"           ".sh"  "$(get askpass.mac.suffix)"
assert_eq "askpass script text has no passphrase" "True" "$(get askpass.static)"
assert_eq "askpass file on disk has no passphrase" "True" "$(get askpass.file.nosecret)"
assert_eq "askpass file mode 0700"               "0o700" "$(get askpass.file.mode)"
assert_eq "askpass env vars" "['DISPLAY', 'MAUDE_ASKPASS_PW', 'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE']" "$(get askpass.env)"
assert_eq "SSH_ASKPASS_REQUIRE=force"            "force" "$(get askpass.env.require)"
assert_eq "existing DISPLAY is kept"             "x" "$(get askpass.env.display_kept)"
assert_eq "askpass prints the passphrase byte for byte (special chars)" "True" "$(get askpass.run.exact)"

# ── ssh-add ────────────────────────────────────────────────────────────
assert_eq "macos ssh-add stores in the keychain" "['/usr/bin/ssh-add', '--apple-use-keychain', '/h/.ssh/id_ed25519']" "$(get add.mac)"
assert_eq "windows ssh-add"                      "['C:\\\\Windows\\\\System32\\\\OpenSSH\\\\ssh-add.exe', 'C:\\\\u\\\\.ssh\\\\id_ed25519']" "$(get add.win)"
assert_eq "sandbox ssh-add has no keychain flag" "['ssh-add', '/h/.ssh/id_ed25519']" "$(get add.lin)"

# ── SSH signing config ─────────────────────────────────────────────────
assert_eq "windows signing config: ssh format, .pub key, gpgsign, OpenSSH ssh-keygen" \
  "[('gpg.format', 'ssh'), ('user.signingkey', 'C:/Users/u/.ssh/id_ed25519.pub'), ('commit.gpgsign', 'true'), ('gpg.ssh.program', 'C:/Windows/System32/OpenSSH/ssh-keygen.exe')]" "$(get sign.win)"
assert_eq "macos signing config keys" "['gpg.format', 'user.signingkey', 'commit.gpgsign']" "$(get sign.mac.keys)"
assert_eq "windows apply: signing keys only"      "['commit.gpgsign', 'gpg.format', 'gpg.ssh.program', 'user.signingkey']" "$(get apply.win.keys)"
assert_eq "windows apply: no core.sshCommand (decoupled)" "True" "$(get apply.win.no_sshcmd)"
assert_eq "macos apply keys"                      "['commit.gpgsign', 'gpg.format', 'user.signingkey']" "$(get apply.mac.keys)"
assert_eq "conflict: nothing set"                 "False" "$(get conflict.none)"
assert_eq "conflict: our own key again is fine"   "False" "$(get conflict.same)"
assert_eq "conflict: gpg key + gpgsign"           "True"  "$(get conflict.gpg_key)"
assert_eq "conflict: signingkey with gpg.format openpgp" "True" "$(get conflict.gpg_key_fmt)"
assert_eq "conflict: another ssh key"             "True"  "$(get conflict.other_ssh)"
assert_eq "conflict: gpgsign on with default key" "True"  "$(get conflict.default_gpg)"
assert_eq "no conflict: gpgsign=false"            "False" "$(get conflict.gpgsign_false)"
for c in gpg_key other_ssh default_gpg; do
  assert_eq "existing signing setup left untouched ($c)" "([], {})" "$(get apply.conflict_untouched.$c)"
done
assert_eq "core.sshCommand with MinGit"           "('core.sshCommand', 'C:/Windows/System32/OpenSSH/ssh.exe')" "$(get sshcmd.mingit)"
assert_eq "core.sshCommand skipped: Git for Windows" "None" "$(get sshcmd.gitforwindows)"
assert_eq "core.sshCommand skipped: already set"  "None" "$(get sshcmd.existing)"
assert_eq "core.sshCommand skipped: GIT_SSH"      "None" "$(get sshcmd.env.GIT_SSH)"
assert_eq "core.sshCommand skipped: GIT_SSH_COMMAND" "None" "$(get sshcmd.env.GIT_SSH_COMMAND)"
assert_eq "core.sshCommand skipped: macOS"        "None" "$(get sshcmd.mac)"
assert_eq "core.sshCommand quotes a path with spaces" "('core.sshCommand', '\"C:/My Win/System32/OpenSSH/ssh.exe\"')" "$(get sshcmd.quoted)"
assert_eq "core.sshCommand applied through the setter" "{'core.sshCommand': 'C:/Windows/System32/OpenSSH/ssh.exe'}" "$(get sshcmd.applied)"
assert_eq "ssh -V parse (Windows)"                "(9, 5)" "$(get ver.win)"
assert_eq "ssh -V parse (unix)"                   "(8, 2)" "$(get ver.unix)"
assert_eq "ssh -V parse (junk)"                   "None"   "$(get ver.junk)"
assert_eq "OpenSSH < 8.4 refuses SSH_ASKPASS_REQUIRE" "True" "$(get askpass.old)"
assert_eq "OpenSSH 8.4 is fine"                   "''"     "$(get askpass.new)"
assert_eq "unknown OpenSSH version is not refused" "''"    "$(get askpass.unknown)"
assert_eq "gh auth refresh adds the signing scope" "['gh.exe', 'auth', 'refresh', '-h', 'github.com', '-s', 'admin:ssh_signing_key']" "$(get refresh.cmd)"
assert_eq "gh: key in use by another account is a failure" "False" "$(get gh.in_use)"
assert_eq "gh: other errors fail"                 "False" "$(get gh.other_error)"
assert_eq "gh: gpg duplicate handling unchanged"  "True"  "$(get gh.gpg_already)"
assert_eq "active key falls back to id_rsa"       "id_rsa" "$(get activekey.rsa)"
assert_eq "active key prefers id_ed25519"         "id_ed25519" "$(get activekey.ed)"
assert_eq "no key at all: default id_ed25519 path" "id_ed25519" "$(get activekey.none)"
assert_eq "sandbox push guard keeps id_ed25519 (id_rsa only)" "id_ed25519" "$(get guard.lin.rsa_only)"
assert_eq "macOS push guard uses the key in use (id_rsa)"     "id_rsa"     "$(get guard.mac.rsa_only)"
assert_eq "sandbox always forces GIT_SSH_COMMAND" "{'GIT_SSH_COMMAND': 'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new'}" "$(get pushenv.lin)"
assert_eq "windows forces it by default"          "{'GIT_SSH_COMMAND': 'C:/Windows/System32/OpenSSH/ssh.exe -o BatchMode=yes -o StrictHostKeyChecking=accept-new'}" "$(get pushenv.win.default)"
assert_eq "windows respects GIT_SSH"              "{}" "$(get pushenv.win.GIT_SSH)"
assert_eq "windows respects GIT_SSH_COMMAND"      "{}" "$(get pushenv.win.GIT_SSH_COMMAND)"
assert_eq "windows respects the user's own core.sshCommand" "{}" "$(get pushenv.win.core)"
assert_eq "macOS respects the user's own core.sshCommand"   "{}" "$(get pushenv.mac.core)"
for k in plain backslash upper quoted; do
  assert_eq "Maude's own core.sshCommand ($k) still gets BatchMode forced" "True" "$(get pushenv.win.own.$k)"
done
assert_eq "effective core.sshCommand: project-local wins, then global" "('/proj/ssh', '/glob/ssh', '')" "$(get effcfg)"
assert_eq "run_capture kills a hanging child"     "(None, True)" "$(get capture.timeout)"
assert_eq "run_capture returns output"            "(0, 'hi')" "$(get capture.ok)"
if [[ -x /usr/bin/ssh-keygen ]]; then
    assert_eq "signing round trip: unencrypted key"       "True" "$(get rt.plain)"
    assert_eq "signing round trip: passphrase key, no agent -> fails fast" "(False, True)" "$(get rt.enc_noagent)"
    assert_eq "signing round trip: no OpenSSH -> install hint" "True" "$(get rt.missing_tool)"
    assert_eq "passphrase key with no agent does NOT get commit.gpgsign" "''" "$(get enc.gpgsign)"
    assert_eq "signing round trip: passphrase key in an agent" "True" "$(get rt.enc_agent)"
else
    skip "/usr/bin/ssh-keygen not present: signing round trip tests"
fi
# ── gh scopes / login ──────────────────────────────────────────────────
assert_eq "sandbox scope stays write:gpg_key" "write:gpg_key" "$(get scope.lin)"
assert_eq "windows scope"  "admin:ssh_signing_key" "$(get scope.win)"
assert_eq "macos scope"    "admin:ssh_signing_key" "$(get scope.mac)"
assert_eq "sandbox login command is unchanged" "['gh', 'auth', 'login', '--hostname', 'github.com', '--git-protocol', 'ssh', '--web', '--scopes', 'write:gpg_key']" "$(get login.lin)"
assert_eq "windows login command skips ssh-key upload" "['gh.exe', 'auth', 'login', '--hostname', 'github.com', '--git-protocol', 'ssh', '--web', '--scopes', 'admin:ssh_signing_key', '--skip-ssh-key']" "$(get login.win)"

# ── macOS ~/.ssh/config ────────────────────────────────────────────────
assert_eq "mac ssh config written when absent"   "True" "$(get mac.cfg.new)"
assert_eq "mac ssh config has the three lines"   "True" "$(get mac.cfg.has)"
assert_eq "mac ssh config: second run is a no-op" "False" "$(get mac.cfg.again)"
assert_eq "mac ssh config: unchanged on 2nd run" "True" "$(get mac.cfg.same)"
assert_eq "mac ssh config: user's own setting is respected" "(False, 'Host x\\n  UseKeychain no\\n')" "$(get mac.cfg.existing_untouched)"
assert_eq "mac ssh config: appended after existing content" "True" "$(get mac.cfg.append)"

# ── real git, private config ───────────────────────────────────────────
if command -v git >/dev/null 2>&1; then
    assert_eq "git_config_set then get" "T" "$(get git.set.get)"
    assert_eq "git_config_get of a missing key is empty" "" "$(get git.missing.empty)"
    if command -v ssh-keygen >/dev/null 2>&1; then
        assert_eq "git has gpg.format ssh" "ssh" "$(get git.signing.format)"
        if [[ -x /usr/bin/ssh-keygen ]]; then assert_eq "git has gpg.ssh.program" "True" "$(get git.signing.program)"; fi
        assert_eq "a commit is SSH-signed (key file, no agent, no gpg)" "True" "$(get git.signed)"
    else
        skip "ssh-keygen not installed: signing round trip"
    fi
else
    skip "git not installed: git config tests"
fi

suite_summary
