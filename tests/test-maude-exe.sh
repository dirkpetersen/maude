#!/usr/bin/env bash
# tests/test-maude-exe.sh - pure helpers of light/maude_exe.py (run on Linux).
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "maude.exe helpers"
py() { python3 -I -c "import sys; sys.path.insert(0, sys.argv[1]); import maude_exe as m; print($1)" "${SCRIPT_DIR}/../light"; }
assert_eq "install target" 'C:\Users\bob\.local\bin\maude.exe' "$(py "m.install_target(r'C:\\Users\\bob')")"
assert_eq "context cmd" '"C:\x y\maude.exe" "%V"' "$(py "m.context_cmd(r'C:\\x y\\maude.exe')")"
S='C:\Users\bob\AppData\Local\Maude\Data\Maude'
assert_eq "inside share" '~/Maude/Projects/my app' "$(py "m.wsl_dir(r'$S\\Projects\\my app', r'$S')")"
assert_eq "share root itself" '~/Maude' "$(py "m.wsl_dir(r'$S', r'$S')")"
assert_eq "case insensitive" '~/Maude/Projects' "$(py "m.wsl_dir(r'c:\\users\\BOB\\appdata\\local\\maude\\data\\maude\\Projects', r'$S')")"
assert_eq "outside share" 'None' "$(py "m.wsl_dir(r'C:\\Users\\bob\\Documents', r'$S')")"
assert_eq "sibling prefix" 'None' "$(py "m.wsl_dir(r'${S}2\\x', r'$S')")"
assert_eq "version exits 0" 0 "$(python3 -I "${SCRIPT_DIR}/../light/maude_exe.py" --version >/dev/null; echo $?)"
D() { py "m.decide(r'$1', r'$S', $2)"; }
O='C:\Users\bob\Documents'; I="$S\Projects\a"
assert_eq "inside -> sandbox" sandbox "$(D "$I" None)"
assert_eq "inside, dotdot escape -> tui (no question)" tui "$(D "$S\..\x" None)"
assert_eq "outside, no flag (context menu) -> tui" tui "$(D "$O" None)"
assert_eq "outside, --windows" windows "$(D "$O" "'windows'")"
assert_eq "outside, --sandbox" refuse "$(D "$O" "'sandbox'")"
assert_eq "inside, --sandbox" sandbox "$(D "$I" "'sandbox'")"
assert_eq "inside, --windows" windows "$(D "$I" "'windows'")"
assert_eq "prefix trick Maude2" tui "$(D "${S}2\x" None)"
assert_eq "case-insensitive inside" sandbox "$(D 'c:\users\BOB\appdata\local\maude\data\maude\p' None)"
# run_tui: starts MaudeApp(select=, model=) and does NOT register the folder itself (the TUI does when it is opened).
# Real Textual does not re-raise handler errors: App.run() prints them and sets return_code = 1.
rtd=$(mktemp -d)
RT_OUT="$(python3 -I - "${SCRIPT_DIR}/../light" "$rtd" 2>/dev/null <<'PY'
import sys, types
sys.path.insert(0, sys.argv[1])
import maude_exe as m
calls = []
fake = types.ModuleType("maude")
class A:
    return_code = 0
    def __init__(self, select="", model=""): calls.append(("app", select, model))
    def run(self): calls.append(("run",))
fake.MaudeApp = A
sys.modules["maude"] = fake
m.add_project = lambda f: calls.append(("add", f))
d = sys.argv[2]
print("rc", m.run_tui(d, "sonnet"), [(c[0], c[1:] if c[0] != "app" else (c[1] == d, c[2])) for c in calls]); calls.clear()
print("rc", m.run_tui(), calls); calls.clear()
class C(A):
    def run(self): self.return_code = 1   # like Textual: error printed, run() returns, return_code 1
fake.MaudeApp = C
print("rc", m.run_tui(d)); calls.clear()
class B(A):
    def run(self): raise RuntimeError("boom")
fake.MaudeApp = B
print("rc", m.run_tui(d)); calls.clear()
fake.MaudeApp = A
print("rc", m.run_tui(d + "/missing"), calls)  # not a folder: nothing started, nothing registered
for s in (r'C:"', r'C:\foo"', r'C:\x y', r'D:', 'C:' + chr(92), '"', '', '  ""'):
    print(m.fix_folder_arg(s))
calls.clear()
print("rc", m.run_tui("", "haiku"), calls)  # --model without a folder reaches the app
PY
)"
rm -rf "$rtd"
assert_eq "run_tui folder: select + model, no registry write" "rc 0 [('app', (True, 'sonnet')), ('run', ())]" "$(sed -n 1p <<<"$RT_OUT")"
assert_eq "run_tui no folder" "rc 0 [('app', '', ''), ('run',)]" "$(sed -n 2p <<<"$RT_OUT")"
assert_eq "run_tui app error (return_code 1) -> rc 1" "rc 1" "$(sed -n 3p <<<"$RT_OUT")"
assert_eq "run_tui exception -> rc 1, not raised" "rc 1" "$(sed -n 4p <<<"$RT_OUT")"
assert_eq "run_tui missing folder -> rc 2, nothing started" "rc 2 []" "$(sed -n 5p <<<"$RT_OUT")"
assert_eq "drive root arg (quote eaten by argv) -> root" 'C:\' "$(sed -n 6p <<<"$RT_OUT")"
assert_eq "arg trailing quote stripped" 'C:\foo' "$(sed -n 7p <<<"$RT_OUT")"
assert_eq "arg unchanged" 'C:\x y' "$(sed -n 8p <<<"$RT_OUT")"
assert_eq "bare drive without quote stays (cwd on that drive)" 'D:' "$(sed -n 9p <<<"$RT_OUT")"
assert_eq "root unchanged" 'C:\' "$(sed -n 10p <<<"$RT_OUT")"
assert_eq "lone quote -> error" None "$(sed -n 11p <<<"$RT_OUT")"
assert_eq "empty arg -> error" None "$(sed -n 12p <<<"$RT_OUT")"
assert_eq "blank + quotes -> error" None "$(sed -n 13p <<<"$RT_OUT")"
assert_eq "run_tui --model without folder" "rc 0 [('app', '', 'haiku'), ('run',)]" "$(sed -n 14p <<<"$RT_OUT")"
# lxss_names with a fake winreg: missing key, denied key, denied subkey, subkey without DistributionName, other distros
LX_OUT="$(python3 -I - "${SCRIPT_DIR}/../light" 2>&1 <<'PY'
import sys, types
sys.path.insert(0, sys.argv[1])
import maude_exe as m
class K:
    def __init__(s, children=None, name=None, err=None):
        s.children, s.name, s.err = children or {}, name, err
    def __enter__(s): return s
    def __exit__(s, *a): return False
ROOT = {}
fw = types.ModuleType("winreg")
fw.HKEY_CURRENT_USER = "HKCU"
def OpenKey(h, name, *a):
    k = ROOT["root"] if h == "HKCU" else h.children[name]
    if isinstance(k, Exception): raise k
    return k
fw.OpenKey = OpenKey
fw.QueryInfoKey = lambda h: (len(h.children), 0, 0)
fw.EnumKey = lambda h, i: list(h.children)[i]
def QueryValueEx(k, n):
    if k.name is None: raise FileNotFoundError(n)
    return (k.name, 1)
fw.QueryValueEx = QueryValueEx
sys.modules["winreg"] = fw
def run(root):
    ROOT["root"] = root
    r = m.lxss_names()
    print(r, m.sandbox_present(r))
run(FileNotFoundError())
run(PermissionError())
run(K({"{1}": K(name="Maude"), "{2}": PermissionError()}))
run(K({"{1}": K(), "{2}": K(name="Maude")}))
run(K({"{1}": K(name="Ubuntu"), "{2}": K(name="Maude-old")}))
PY
)"
assert_eq "lxss missing key -> [] (no sandbox)" "[] False" "$(sed -n 1p <<<"$LX_OUT")"
assert_eq "lxss denied key -> None (assume sandbox)" "None True" "$(sed -n 2p <<<"$LX_OUT")"
assert_eq "lxss denied subkey -> None" "None True" "$(sed -n 3p <<<"$LX_OUT")"
assert_eq "lxss subkey without DistributionName skipped" "['Maude'] True" "$(sed -n 4p <<<"$LX_OUT")"
assert_eq "lxss other distros do not match" "['Ubuntu', 'Maude-old'] False" "$(sed -n 5p <<<"$LX_OUT")"
assert_eq "sandbox present" True "$(py "m.sandbox_present(['Ubuntu', 'Maude'])")"
assert_eq "sandbox present, case-insensitive" True "$(py "m.sandbox_present(['maude'])")"
assert_eq "sandbox absent (other distros)" False "$(py "m.sandbox_present(['Ubuntu', 'Maude-old'])")"
assert_eq "sandbox absent (WSL never used)" False "$(py "m.sandbox_present([])")"
assert_eq "sandbox unknown -> assume present" True "$(py "m.sandbox_present(None)")"
assert_eq "lxss unreadable off Windows -> None" None "$(py "m.lxss_names()")"
assert_contains "banner with sandbox" "OUTSIDE the sandbox in C:\\x" "$(py "m.banner_text(r'C:\\x')")"
assert_eq "banner without sandbox" "Maude: running on Windows in C:\\x. Maude's AI agents have your full Windows user rights here." "$(py "m.banner_text(r'C:\\x', False)")"
assert_eq "windows cmd continue" "['C:/c.exe', '--model', 'opus', '--continue']" "$(py "m.windows_cmd(r'C:/c.exe','opus',True)")"
assert_eq "windows cmd fallback" "['C:/c.exe', '--model', 'sonnet']" "$(py "m.windows_cmd(r'C:/c.exe','sonnet',False)")"
assert_eq "windows cmd effort" "['C:/c.exe', '--model', 'opus', '--effort', 'high', '--continue']" "$(py "m.windows_cmd(r'C:/c.exe','opus',True,'high')")"
assert_eq "windows cmd bad effort dropped" "['C:/c.exe', '--model', 'opus']" "$(py "m.windows_cmd(r'C:/c.exe','opus',False,'x;id')")"
assert_eq "no bypass flag" 0 "$(py "int(any('skip' in a or 'bypass' in a for a in m.windows_cmd('c','opus',True)))")"
assert_eq "sandbox cmd" "['wsl.exe', '-d', 'Maude', '--cd', '~/Maude/p', '-e', '/usr/bin/env', 'MAUDE_WELCOMED=1', 'bash', '-lc', m.OPEN_SH, 'maude', 'fable']" "$(py "str(m.sandbox_cmd('~/Maude/p','fable')).replace(repr(m.OPEN_SH),'m.OPEN_SH')")"
for ok in opus fable sonnet haiku claude-opus-4-5 sonnet-4.5; do assert_eq "model ok $ok" True "$(py "m.valid_model('$ok')")"; done
for bad in '' 'a b' 'x;rm' '$(id)' '-x' 'a/b'; do assert_eq "model bad '$bad'" False "$(py "m.valid_model(r'''$bad''')")"; done
# share root: local with Projects wins; else first OneDrive with Projects; else local
L='C:\L\Maude\Data\Maude'
sr() { py "m.share_root(r'C:\\L', [r'C:\\OD\\Maude', r'C:\\OD2\\Maude'], lambda p: p in $1)"; }
assert_eq "share local exists" "$L" "$(sr "{r'$L\\Projects', r'C:\\OD\\Maude\\Projects'}")"
assert_eq "share onedrive existing" 'C:\OD2\Maude' "$(sr "{r'C:\\OD2\\Maude\\Projects'}")"
assert_eq "share none -> local" "$L" "$(sr "set()")"
assert_eq "onedrive env order" 'C:\B\Maude C:\P\Maude' "$(py "' '.join(m.onedrive_candidates({'OneDriveCommercial': r'C:\\B', 'OneDriveConsumer': r'C:\\P', 'OneDrive': r'C:\\X', 'USERPROFILE': r'C:\\U'}, lambda p: [], lambda p: False))")"
assert_eq "onedrive plain+folders" 'C:\X\Maude C:\U\OneDrive - Co\Maude C:\U\OneDrive\Maude' "$(py "' '.join(m.onedrive_candidates({'OneDrive': r'C:\\X', 'USERPROFILE': r'C:\\U'}, lambda p: ['OneDrive - Co', 'zz'], lambda p: True))")"
F='C:\Users\bob\AppData\Local\Maude\Data\Maude /home/maude/Maude drvfs defaults,uid=1000,gid=1000 0 0'
assert_eq "fstab default" 'C:\Users\bob\AppData\Local\Maude\Data\Maude' "$(py "m.parse_fstab_share(r'''$F''')")"
assert_eq "fstab 040 spaces" 'C:\Users\bob\OneDrive - Co\Maude' "$(py "m.parse_fstab_share('# x\nC:\\\\Users\\\\bob\\\\OneDrive\\\\040-\\\\040Co\\\\Maude /home/maude/Maude drvfs defaults 0 0\n')")"
assert_eq "fstab other mounts ignored" None "$(py "m.parse_fstab_share('C:\\\\ /mnt/c drvfs defaults 0 0\nLABEL=x / ext4 defaults 0 1\n')")"
assert_eq "fstab garbage" None "$(py "m.parse_fstab_share('\\x00 junk\n\n  \nfoo')")"
assert_eq "fstab empty" None "$(py "m.parse_fstab_share('')")"
# outside-sandbox project list helpers
assert_eq "project key" 'c:\x\proj' "$(py "m.project_key(r'C:\\X\\y\\..\\Proj')")"
assert_eq "features none" "''" "$(py "repr(m.features(False, False))")"
assert_eq "features github" GitHub "$(py "m.features(True, False)")"
assert_eq "features both" "GitHub, No Sandbox" "$(py "m.features(True, True)")"
assert_eq "merge order+labels" "b:True a (X):True s:False a (Projects):False" "$(py "' '.join(d['label'] + ':' + str(d['outside']) for d in m.merge_projects([{'path': r'C:\\X\\a', 'mtime': 1}, {'path': r'C:\\Y\\b', 'mtime': 9}], [{'path': r'C:\\P\\Projects\\a', 'mtime': 1}, {'path': r'C:\\P\\Projects\\s', 'mtime': 5}]))")"
td=$(mktemp -d); mkdir -p "$td/p/.git" "$td/p/sub"; touch -d '@978307200' "$td/p"; touch -d '@1700000000' "$td/p/.git"; touch -d '@1577836800' "$td/p/sub"
assert_eq "content mtime ignores .git, sees children" 1577836800.0 "$(py "m.content_mtime(r'$td/p')")"
assert_eq "content mtime missing" 0.0 "$(py "m.content_mtime(r'$td/none')")"
rm -rf "$td"
# Linux: Features header, no Windows caption
if python3 -I -c "import textual" 2>/dev/null; then
  td=$(mktemp -d)
  assert_eq "linux features column, no caption, no separator" "Features False False" "$(HOME="$td" python3 -I -c "
import sys, asyncio; sys.path.insert(0, sys.argv[1]); import maude
async def go():
    async with maude.MaudeApp().run_test(size=(140, 40)) as p:
        await p.pause(); t = p.app.query_one('#projects-table')
        print(str(t.columns[list(t.columns)[2]].label), maude.WIN_CAPTION in maude.make_gradient_logo().plain, any(k.value == '__sep__' for k in t.rows))
asyncio.run(go())" "${SCRIPT_DIR}/../light" 2>&1 | tail -1)"
  rm -rf "$td"
fi
# Windows mode of maude.py, headless (needs textual; skipped when absent)
if python3 -I -c "import textual" 2>/dev/null; then
  assert_exit_zero "win tui smoke" python3 "${SCRIPT_DIR}/win_tui_smoke.py"
else
  skip "textual not installed: win tui smoke"
fi
# share cache: valid -> no lookup; stale -> lookup + store; fstab failure -> heuristic, not stored
PS() { py "(lambda log: ' '.join([m.pick_share($1, lambda p: p in {r'C:\\G\\Projects'}, lambda: log.append('lookup') or $2, lambda: 'HEUR', lambda s: log.append('store ' + s), lambda t: None)] + log))([])"; }
assert_eq "cache valid" "C:\\G" "$(PS "r'C:\\G'" "r'C:\\X'")"
assert_eq "cache stale" "C:\\X lookup store C:\\X" "$(PS "r'C:\\old'" "r'C:\\X'")"
assert_eq "cache empty" "C:\\X lookup store C:\\X" "$(PS "None" "r'C:\\X'")"
assert_eq "fstab fails -> heuristic" "HEUR lookup" "$(PS "r'C:\\old'" "None")"
# user PATH append
PW() { USERPROFILE='C:\Users\bob' py "m.path_with(r'$1', r'C:\Users\bob\.local\bin')"; }
B='C:\Users\bob\.local\bin'
assert_eq "path empty" "$B" "$(PW '')"
assert_eq "path append" "C:\\X;$B" "$(PW 'C:\X')"
assert_eq "path trailing semicolon" "C:\\X;$B" "$(PW 'C:\X;')"
assert_eq "path present" None "$(PW "C:\\X;$B")"
assert_eq "path present case+slash" None "$(PW 'c:\users\BOB\.LOCAL\bin\;C:\X')"
assert_eq "path present %USERPROFILE%" None "$(PW '%USERPROFILE%\.local\bin;C:\X')"

# tool install offers (pure parts)
assert_eq "plan present" "[]" "$(py "m.install_plan('Crush', True, False)")"
assert_eq "plan claude" "[('Claude Code', 'irm https://claude.ai/install.ps1 | iex')]" "$(py "m.install_plan('Claude Code', False, True)")"
assert_contains "plan kiro is native per-user, not the vendor script" "msiexec /qn, no admin" "$(py "m.install_plan('Kiro CLI', False, False)[0][1]")"
assert_eq "plan kiro has no install.ps1" 0 "$(py "int('install.ps1' in m.install_plan('Kiro CLI', False, False)[0][1])")"
assert_eq "plan opencode, bun there" "[('OpenCode', 'bun install -g --trust opencode-ai')]" "$(py "m.install_plan('OpenCode', False, True)")"
assert_eq "plan crush, no bun" "['Bun', 'Crush']" "$(py "[l for l, c in m.install_plan('Crush', False, False)]")"
assert_eq "plan bun cmd" "irm bun.sh/install.ps1 | iex" "$(py "m.install_plan('OpenCode', False, False)[0][1]")"
assert_eq "path prepend" 'C:\b;C:\a' "$(py "m.path_prepend(r'C:\\a', [r'C:\\b', r'c:\\A'])")"
assert_eq "path prepend empty" 'C:\b' "$(py "m.path_prepend('', [r'C:\\b'])")"
assert_eq "find_tool checks bun dir" 'C:\U\.bun\bin' "$(py "m.find_tool('crush', {'USERPROFILE': r'C:\\U'}, lambda b, path=None: path if path and path.endswith('.bun\\\\bin') else None)")"
assert_eq "find_tool kiro program files" 'C:\PF\Kiro-Cli' "$(py "m.find_tool('kiro-cli', {'USERPROFILE': r'C:\\U', 'ProgramFiles': r'C:\\PF'}, lambda b, path=None: path if path and 'Kiro' in path else None)")"
# ensure_tool flow with fakes: answers, run results, which binaries appear after each run
et_run() { python3 -I - "${SCRIPT_DIR}/../light" "$@" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import maude_exe as m
tool, ans, rc, appears = sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5].split(",")
log, have = [], {}
def run(c):
    assert "-NonInteractive" in c and c[0] == "powershell", c
    log.append(c[-1]); (not rc) and have.update({b: "x" for b in appears}); return rc
def kiro(say):  # stands in for install_kiro: used instead of PowerShell
    log.append("native"); (not rc) and have.update({b: "x" for b in appears}); return rc
r = m.ensure_tool(tool, have.get, lambda q: log.append(q) or ans, run, lambda s: None, lambda: log.append("refresh"),
                  {"Kiro CLI": kiro})
print(r, "|".join(log))
PY
}
assert_eq "ensure: yes installs bun then crush" "x Install Bun now? [Y/n] |irm bun.sh/install.ps1 | iex|refresh|Install Crush now? [Y/n] |bun install -g --trust @charmland/crush|refresh" "$(et_run Crush y 0 bun,crush)"
assert_eq "ensure: default Enter = yes" "x" "$(et_run 'Claude Code' '' 0 claude | cut -d' ' -f1)"
assert_eq "ensure: no" "None Install Claude Code now? [Y/n] " "$(et_run 'Claude Code' n 0 claude)"
assert_eq "ensure: install fails" "None" "$(et_run 'Kiro CLI' y 1 kiro-cli | cut -d' ' -f1)"
assert_eq "ensure: installed but not found" "None" "$(et_run 'Kiro CLI' y 0 other | cut -d' ' -f1)"
assert_eq "ensure: kiro uses the native installer" "kiro-x Install Kiro CLI now? [Y/n] |native|refresh" "$(et_run 'Kiro CLI' y 0 kiro-cli | sed 's/^x/kiro-x/')"
assert_eq "ps_cmd non-interactive" "['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', 'x']" "$(py "m.ps_cmd('x')")"

# git offer: pure plan (always per user, silent, no admin)
G="winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements --scope user --silent --disable-interactivity"
assert_eq "git plan: have git" "None" "$(py "m.git_plan(True, True)")"
assert_eq "git plan: no winget" "link" "$(py "m.git_plan(False, False)")"
assert_eq "git plan: user scope, silent" "$G" "$(py "' '.join(m.git_plan(False, True))")"
# git offer flow with fakes: log has the messages and "RUN <args>" for each process started
git_run() { python3 -I - "${SCRIPT_DIR}/../light" "$@" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import maude_exe as m
ans, wg = sys.argv[2], sys.argv[3] or None
log = []
def go():
    m.offer_git({}, lambda e: None, lambda: wg, lambda q: ans, lambda c: log.append("RUN " + " ".join(c)) or 0, log.append)
go(); go()  # second call in the same process must do nothing
print("\n".join(log))
PY
}
assert_eq "git offer: declined runs nothing" "0" "$(git_run n W | grep -c '^RUN')"
assert_contains "git offer: shows command" "Install command: W install --id Git.Git" "$(git_run n W)"
assert_eq "git offer: accepted runs once, user scope, silent" "1" "$(git_run y W | grep -c '^RUN .*--scope user --silent --disable-interactivity$')"
assert_eq "git offer: no winget, link only" "0" "$(git_run y '' | grep -c '^RUN')"
assert_contains "git offer: link text" "Git for Windows is not installed. Get it from https://git-scm.com/download/win (optional)." "$(git_run y '')"

# Kiro CLI: manifest parsing, msiexec argv, download + install flow with a fake network
Z64=$(printf '0%.0s' $(seq 64))
kpk() { py "m.kiro_package({'packages': [{'kind': 'msi', 'os': 'windows', 'architecture': 'x86_64', 'download': '$1', 'sha256': '$2'}]})"; }
assert_eq "kiro package picks the windows msi" "('https://prod.download.cli.kiro.dev/stable/3.5.0/k.msi', '$Z64')" "$(kpk 3.5.0/k.msi "$Z64")"
assert_eq "kiro package: no windows entry" None "$(py "m.kiro_package({'packages': [{'kind': 'deb', 'os': 'linux'}]})")"
assert_eq "kiro package: junk manifest" None "$(py "m.kiro_package([1])")"
for bad in '../x.msi' '3.5.0/../x.msi' 'https://evil.example/x.msi' '3.5.0/a b.msi' '/x.msi' '3.5.0/x.exe'; do
  assert_eq "kiro package rejects download '$bad'" None "$(kpk "$bad" "$Z64")"
done
assert_eq "kiro package rejects bad sha" None "$(kpk 3.5.0/k.msi xyz)"
assert_eq "msiexec argv: per user, silent, verbose log" "['msiexec', '/i', 'k.msi', '/qn', '/norestart', '/l*v', 'l.log', 'ALLUSERS=2', 'MSIINSTALLPERUSER=1']" "$(py "m.msiexec_cmd('k.msi', 'l.log')")"
kiro_flow() { python3 -I - "${SCRIPT_DIR}/../light" "$@" <<'PY'
import sys, io, os, json, hashlib, urllib.error, http.client
sys.path.insert(0, sys.argv[1]); import maude_exe as m
mode, td = sys.argv[2], sys.argv[3]
msi = b"fake-msi-bytes"
sha = hashlib.sha256(msi).hexdigest() if mode != "badsha" else "0" * 64
pk = {"kind": "msi", "os": "windows", "architecture": "x86_64", "download": "3.5.0/k.msi", "sha256": sha}
man = {"version": "3.5.0", "packages": [] if mode == "nomsi" else [pk]}
routes = {m.KIRO_MANIFEST: json.dumps(man).encode(), m.KIRO_BASE + "/3.5.0/k.msi": msi}
if mode == "nonet":
    routes = {}
if mode == "shape":
    routes[m.KIRO_MANIFEST] = b'{"packages": 5}'
def urlopen(req, timeout=None):
    assert req.full_url.startswith("https://prod.download.cli.kiro.dev/"), req.full_url
    if mode == "ctrlc" and req.full_url.endswith(".msi"):
        raise KeyboardInterrupt
    if mode == "httperr" and req.full_url.endswith(".msi"):
        raise http.client.IncompleteRead(b"x")
    if req.full_url not in routes:
        raise urllib.error.URLError("unreachable")
    return io.BytesIO(routes[req.full_url])
log, said = [], []
def run(c):
    log.append(c); assert open(c[2], "rb").read() == msi  # the verified file is what msiexec gets
    return 1603 if mode == "fail" else 0
rc = m.install_kiro(said.append, run, urlopen, td, td)
print(rc, len(log), "tmp-left=%d" % len([f for f in os.listdir(td) if f.startswith("maude-kiro-")]), "|".join(said))
if log:
    print(log[0][:2] + log[0][3:])
PY
}
td=$(mktemp -d)
assert_eq "kiro install ok" "0 1 tmp-left=0 Downloading Kiro CLI (about 350 MB)...|Installing Kiro CLI for this user..." "$(kiro_flow ok "$td" | head -1)"
assert_eq "kiro install uses silent per-user msiexec" "['msiexec', '/i', '/qn', '/norestart', '/l*v', '$td/maude-kiro-install.log', 'ALLUSERS=2', 'MSIINSTALLPERUSER=1']" "$(kiro_flow ok "$td" | tail -1)"
out=$(kiro_flow fail "$td" | head -1)
assert_contains "kiro msiexec failure prints the code and log path" "msiexec exited with code 1603. Verbose log: $td/maude-kiro-install.log" "$out"
assert_eq "kiro msiexec failure returns the code, cleans up" "1603 1 tmp-left=0" "$(echo "$out" | cut -d' ' -f1-3)"
out=$(kiro_flow badsha "$td" | head -1)
assert_eq "kiro checksum mismatch: no msiexec, cleaned up" "1 0 tmp-left=0" "$(echo "$out" | cut -d' ' -f1-3)"
assert_contains "kiro checksum mismatch is reported" "checksum mismatch" "$out"
assert_eq "kiro manifest without msi" "1 0 tmp-left=0 The Kiro CLI manifest has no Windows x86_64 installer." "$(kiro_flow nomsi "$td" | head -1)"
assert_contains "kiro network failure" "Could not read the Kiro CLI manifest" "$(kiro_flow nonet "$td" | head -1)"
assert_eq "kiro Ctrl+C during the download: cancelled, no msiexec, cleaned up" "1 0 tmp-left=0 Downloading Kiro CLI (about 350 MB)...|Kiro CLI install cancelled." "$(kiro_flow ctrlc "$td" | head -1)"
out=$(kiro_flow httperr "$td" | head -1)
assert_eq "kiro HTTPException (not an OSError) is reported, cleaned up" "1 0 tmp-left=0" "$(echo "$out" | cut -d' ' -f1-3)"
assert_contains "kiro HTTPException message" "download or install failed: IncompleteRead" "$out"
assert_eq "kiro manifest with a wrong type does not raise" "1 0 tmp-left=0 The Kiro CLI manifest has no Windows x86_64 installer." "$(kiro_flow shape "$td" | head -1)"
rm -rf "$td"

# GitHub CLI: per-user release zip, no winget, no admin
assert_eq "gh arch amd64" amd64 "$(py "m.gh_arch({'PROCESSOR_ARCHITECTURE': 'AMD64'})")"
assert_eq "gh arch arm64 (native over wow64)" arm64 "$(py "m.gh_arch({'PROCESSOR_ARCHITECTURE': 'AMD64', 'PROCESSOR_ARCHITEW6432': 'ARM64'})")"
assert_eq "gh arch x86 unsupported" None "$(py "m.gh_arch({'PROCESSOR_ARCHITECTURE': 'x86'})")"
assert_eq "gh dest" 'C:\L\Programs\GitHub CLI' "$(py "m.gh_dest({'LOCALAPPDATA': r'C:\\L'})")"
assert_eq "gh dirs: per-user bin, per-user root, machine install" 'C:\L\Programs\GitHub CLI\bin;C:\L\Programs\GitHub CLI;C:\PF\GitHub CLI' "$(py "';'.join(m.gh_dirs({'LOCALAPPDATA': r'C:\\L', 'ProgramFiles': r'C:\\PF'}))")"
assert_eq "gh dirs: machine install still checked" 'C:\PF\GitHub CLI' "$(py "';'.join(m.gh_dirs({'ProgramFiles': r'C:\\PF'}))")"
assert_eq "gh plan: have gh" None "$(py "m.gh_plan(True, 'amd64', 'C:/d')")"
assert_eq "gh plan: no arch -> link" link "$(py "m.gh_plan(False, None, 'C:/d')")"
assert_contains "gh plan: describes the per-user zip" "gh_<version>_windows_amd64.zip" "$(py "m.gh_plan(False, 'amd64', 'C:/d')")"
assert_eq "gh plan: no admin approval wording" 0 "$(py "int('admin approval' in m.gh_plan(False, 'amd64', 'C:/d'))")"
RJ='{"tag_name":"v2.105.0","assets":[{"name":"gh_2.105.0_windows_amd64.zip"},{"name":"gh_2.105.0_checksums.txt"}]}'
assert_eq "gh release picks tag + names" "('v2.105.0', 'gh_2.105.0_windows_amd64.zip', 'gh_2.105.0_checksums.txt')" "$(py "m.gh_release(__import__('json').loads('$RJ'), 'amd64')")"
assert_eq "gh release: no arm64 asset" None "$(py "m.gh_release(__import__('json').loads('$RJ'), 'arm64')")"
assert_eq "gh release: odd tag rejected" None "$(py "m.gh_release({'tag_name': 'v1.0.0/../x', 'assets': []}, 'amd64')")"
O64=$(printf '1%.0s' $(seq 64))
assert_eq "checksum_for finds the right line" "$O64" "$(py "m.checksum_for('$Z64  other.zip\n$O64  gh.zip\n', 'gh.zip')")"
assert_eq "checksum_for missing" None "$(py "m.checksum_for('$Z64  other.zip', 'gh.zip')")"
assert_eq "checksum_for ignores a short hash" None "$(py "m.checksum_for('abc  gh.zip', 'gh.zip')")"
assert_eq "zip names: safe" "[]" "$(py "m.unsafe_zip_names(['LICENSE', 'bin/gh.exe', 'share/a b.txt'])")"
assert_eq "zip names: unsafe ones" "['../x', '/abs', 'C:/w', 'a/../../b', '\\\\abs', 'a\\\\..\\\\b']" "$(py "m.unsafe_zip_names(['ok', '../x', '/abs', 'C:/w', 'a/../../b', '\\\\abs', 'a\\\\..\\\\b'])")"
assert_eq "gh release: assets wrong type" None "$(py "m.gh_release({'tag_name': 'v1.2.3', 'assets': 5}, 'amd64')")"
assert_eq "gh release: unhashable asset name" None "$(py "m.gh_release({'tag_name': 'v1.2.3', 'assets': [{'name': [1]}]}, 'amd64')")"
assert_eq "kiro package: packages wrong type" None "$(py "m.kiro_package({'packages': 5})")"
assert_eq "http get within the limit" "b'abc'" "$(py "m._http_get('https://x', lambda r, timeout=None: __import__('io').BytesIO(b'abc'), limit=3)")"
hg() { python3 -I -c "import sys, io; sys.path.insert(0, sys.argv[1]); import maude_exe as m
try: m._http_get('https://x', lambda r, timeout=None: io.BytesIO(b'abcd'), limit=3)
except Exception as e: print(type(e).__name__)" "${SCRIPT_DIR}/../light"; }
assert_eq "http get over the limit raises ValueError" "ValueError" "$(hg)"
gh_flow() { python3 -I - "${SCRIPT_DIR}/../light" "$@" <<'PY'
import sys, io, os, json, hashlib, zipfile, urllib.error, http.client
sys.path.insert(0, sys.argv[1]); import maude_exe as m
mode, td = sys.argv[2], sys.argv[3]
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("LICENSE", "mit")
    if mode != "nogh":
        z.writestr("bin/gh.exe", "MZ")
    if mode == "slip":
        z.writestr("../evil.txt", "x")
    if mode == "symlink":
        zi = zipfile.ZipInfo("link"); zi.external_attr = 0o120777 << 16
        z.writestr(zi, "bin/gh.exe")
zb = buf.getvalue()
sha = hashlib.sha256(zb).hexdigest() if mode != "badsha" else "0" * 64
zn, sn, base = "gh_2.105.0_windows_amd64.zip", "gh_2.105.0_checksums.txt", m.GH_DL + "v2.105.0/"
rel = {"tag_name": "v2.105.0", "assets": [{"name": zn}, {"name": sn}]}
routes = {m.GH_API: json.dumps(rel).encode(), base + zn: zb, base + sn: f"{sha}  {zn}\n{'1' * 64}  other\n".encode()}
if mode == "nonet":
    routes = {}
if mode == "shape":
    routes[m.GH_API] = b'{"tag_name": "v2.105.0", "assets": 5}'
def urlopen(req, timeout=None):
    if mode == "ctrlc" and req.full_url.endswith(".zip"):
        raise KeyboardInterrupt
    if mode == "httperr" and req.full_url.endswith(".zip"):
        raise http.client.IncompleteRead(b"x")
    if req.full_url not in routes:
        raise urllib.error.URLError("unreachable")
    return io.BytesIO(routes[req.full_url])
said = []
prog = os.path.join(td, "Programs")
dest = os.path.join(prog, "GitHub CLI")
if mode == "stale":  # a partial earlier attempt: only LICENSE and an empty bin
    os.makedirs(os.path.join(dest, "bin")); open(os.path.join(dest, "LICENSE"), "w").close()
if mode == "oldinstall":  # a complete old install
    os.makedirs(os.path.join(dest, "bin")); open(os.path.join(dest, "bin", "gh.exe"), "w").write("OLD")
if mode == "junk":  # somebody's files: must never be touched
    os.makedirs(dest); open(os.path.join(dest, "junk"), "w").close()
if mode == "restorefail":  # the swap fails and so does putting the old folder back
    os.makedirs(os.path.join(dest, "bin")); open(os.path.join(dest, "bin", "gh.exe"), "w").write("OLD")
    real = os.replace
    def bad(a, b):
        if a.startswith(os.path.join(prog, ".gh-new-")) or ".old-" in a:
            raise PermissionError("locked")
        return real(a, b)
    os.replace = bad
if mode == "swapfail":  # the final rename fails: the old folder must come back
    os.makedirs(os.path.join(dest, "bin")); open(os.path.join(dest, "bin", "gh.exe"), "w").write("OLD")
    real = os.replace
    def bad(a, b):
        if a.startswith(os.path.join(prog, ".gh-new-")):
            raise PermissionError("locked")
        return real(a, b)
    os.replace = bad
rc = m.install_gh({"PROCESSOR_ARCHITECTURE": "AMD64"}, said.append, urlopen, td, dest)
left = [f for f in os.listdir(td) if f.startswith("maude-gh-")] + (
    [f for f in os.listdir(prog) if f.startswith(".gh-new-") or ".old-" in f] if os.path.isdir(prog) else [])
old = os.path.join(dest, "bin", "gh.exe")
oldtxt = open(old).read() if os.path.isfile(old) else "-"
print(rc, "gh.exe=%s" % os.path.isfile(os.path.join(dest, "bin", "gh.exe")), "junk=%s" % os.path.exists(os.path.join(dest, "junk")),
      "evil=%s" % os.path.exists(os.path.join(prog, "evil.txt")), "left=%s" % left, "gh=" + oldtxt, "|".join(said))
PY
}
td=$(mktemp -d)
assert_eq "gh install ok" "0 gh.exe=True junk=False evil=False left=[] gh=MZ Downloading GitHub CLI v2.105.0..." "$(gh_flow ok "$td")"
rm -rf "$td"; td=$(mktemp -d)
assert_eq "gh install over a stale folder" "0 gh.exe=True junk=False evil=False left=[] gh=MZ Downloading GitHub CLI v2.105.0..." "$(gh_flow stale "$td")"
rm -rf "$td"; td=$(mktemp -d)
assert_contains "gh checksum mismatch installs nothing" "1 gh.exe=False junk=False evil=False left=[] gh=- Downloading GitHub CLI v2.105.0...|The GitHub CLI download or install failed: checksum mismatch" "$(gh_flow badsha "$td")"
rm -rf "$td"; td=$(mktemp -d)
out=$(gh_flow slip "$td")
assert_contains "gh zip slip is refused, nothing extracted" "1 gh.exe=False junk=False evil=False left=[] gh=-" "$out"
assert_contains "gh zip slip message" "looks wrong (../evil.txt)" "$out"
rm -rf "$td"; td=$(mktemp -d)
assert_contains "gh zip without bin/gh.exe is refused" "looks wrong (no bin/gh.exe)" "$(gh_flow nogh "$td")"
rm -rf "$td"; td=$(mktemp -d)
assert_contains "gh network failure is reported, not raised" "1 gh.exe=False" "$(gh_flow nonet "$td")"
rm -rf "$td"; td=$(mktemp -d)
assert_contains "gh symlink zip member is refused" "1 gh.exe=False junk=False evil=False left=[] gh=- Downloading GitHub CLI v2.105.0...|The GitHub CLI zip looks wrong (link)" "$(gh_flow symlink "$td")"
rm -rf "$td"; td=$(mktemp -d)
assert_contains "gh Ctrl+C: cancelled, nothing left" "1 gh.exe=False junk=False evil=False left=[] gh=- Downloading GitHub CLI v2.105.0...|GitHub CLI install cancelled." "$(gh_flow ctrlc "$td")"
rm -rf "$td"; td=$(mktemp -d)
out=$(gh_flow httperr "$td")
assert_contains "gh HTTPException is reported, nothing left" "1 gh.exe=False junk=False evil=False left=[]" "$out"
assert_contains "gh HTTPException message" "download or install failed: IncompleteRead" "$out"
rm -rf "$td"; td=$(mktemp -d)
assert_contains "gh release JSON with a wrong type does not raise" "1 gh.exe=False junk=False evil=False left=[] gh=- The latest GitHub CLI release has no Windows zip" "$(gh_flow shape "$td")"
rm -rf "$td"; td=$(mktemp -d)
assert_eq "gh install replaces an old install, aside folder deleted" "0 gh.exe=True junk=False evil=False left=[] gh=MZ Downloading GitHub CLI v2.105.0..." "$(gh_flow oldinstall "$td")"
rm -rf "$td"; td=$(mktemp -d)
out=$(gh_flow junk "$td")
assert_contains "gh refuses a folder holding other files, keeps them" "1 gh.exe=False junk=True evil=False left=[] gh=-" "$out"
assert_contains "gh refusal message" "holds other files; not touching it" "$out"
rm -rf "$td"; td=$(mktemp -d)
out=$(gh_flow swapfail "$td")
assert_contains "gh failed swap puts the old folder back" "1 gh.exe=True junk=False evil=False left=[] gh=OLD" "$out"
assert_contains "gh failed swap is reported" "install failed: locked" "$out"
rm -rf "$td"; td=$(mktemp -d)
out=$(gh_flow restorefail "$td")
assert_contains "gh failed swap and failed restore names the old folder" "Your previous GitHub CLI folder is kept at $td/Programs/GitHub CLI.old-" "$out"
rm -rf "$td"; td=$(mktemp -d)
# gh_dir_replaceable: LICENSE and bin/gh.exe must be regular files
rp() { py "m.gh_dir_replaceable(r'$1')"; }
mkdir -p "$td/ok/bin" "$td/lic/bin" "$td/lnk/bin" "$td/exedir/bin/gh.exe" "$td/exelnk/bin"
: > "$td/ok/LICENSE"; : > "$td/ok/bin/gh.exe"; mkdir "$td/lic/LICENSE"; ln -s "$td/ok/LICENSE" "$td/lnk/LICENSE"; ln -s "$td/ok/bin/gh.exe" "$td/exelnk/bin/gh.exe"
assert_eq "replaceable: LICENSE + bin/gh.exe files" True "$(rp "$td/ok")"
assert_eq "replaceable: missing" True "$(rp "$td/none")"
assert_eq "replaceable: LICENSE is a directory" False "$(rp "$td/lic")"
assert_eq "replaceable: LICENSE is a symlink" False "$(rp "$td/lnk")"
assert_eq "replaceable: bin/gh.exe is a directory" False "$(rp "$td/exedir")"
assert_eq "replaceable: bin/gh.exe is a symlink" False "$(rp "$td/exelnk")"
rm -rf "$td"
gh_run() { python3 -I - "${SCRIPT_DIR}/../light" "$@" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import maude_exe as m
ans, arch, rc = sys.argv[2], sys.argv[3], int(sys.argv[4])
log = []; env = {"LOCALAPPDATA": r"C:\L", "PROCESSOR_ARCHITECTURE": arch, "PATH": r"C:\P"}
def go():
    m.offer_gh(env, lambda e: None, lambda e, say: log.append("INSTALL") or rc, lambda d: log.append("PATH " + d), lambda q: ans, log.append)
go(); go()  # second call in the same process must do nothing
print("\n".join(log)); print("ENV", env["PATH"])
PY
}
assert_eq "gh offer: declined installs nothing" "0" "$(gh_run n AMD64 0 | grep -c '^INSTALL')"
assert_eq "gh offer: accepted installs once" "1" "$(gh_run y AMD64 0 | grep -c '^INSTALL')"
assert_eq "gh offer: user PATH and process PATH get the bin dir" 'PATH C:\L\Programs\GitHub CLI\bin
ENV C:\L\Programs\GitHub CLI\bin;C:\P' "$(gh_run y AMD64 0 | grep -E '^(PATH|ENV)')"
assert_eq "gh offer: failed install leaves PATH alone" 'ENV C:\P' "$(gh_run y AMD64 1 | grep -E '^(PATH|ENV)')"
assert_contains "gh offer: failed install message" "The GitHub CLI install failed." "$(gh_run y AMD64 1)"
assert_eq "gh offer: no admin-approval wording" "0" "$(gh_run n AMD64 0 | grep -c -i 'admin approval')"
assert_contains "gh offer: link text for an unsupported arch" "GitHub CLI is not installed. Get it from https://cli.github.com (optional)." "$(gh_run y x86 0)"
assert_eq "gh offer: no install for an unsupported arch" "0" "$(gh_run y x86 0 | grep -c '^INSTALL')"

# backstops: a native installer, an offer or a winget run must never take the TUI down
bs_run() { python3 -I - "${SCRIPT_DIR}/../light" "$@" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import maude_exe as m
kind, exc = sys.argv[2], {"ctrlc": KeyboardInterrupt, "boom": RuntimeError}[sys.argv[3]]
say = []
def boom(*a, **k):
    raise exc("x")
if kind == "ensure":
    r = m.ensure_tool("Kiro CLI", lambda b: None, lambda q: "y", None, say.append, lambda: None, {"Kiro CLI": boom})
    print(r, "|".join(say[1:]))
else:
    env = {"LOCALAPPDATA": r"C:\L", "PROCESSOR_ARCHITECTURE": "AMD64", "PATH": ""}
    m.offer_gh(env, lambda e: None, boom, lambda d: say.append("PATH"), lambda q: "y", say.append)
    print(env["PATH"] == "", "|".join(say[1:]))
PY
}
assert_eq "ensure: native installer Ctrl+C" "None Kiro CLI install cancelled." "$(bs_run ensure ctrlc)"
assert_eq "ensure: native installer unexpected error" "None The Kiro CLI install failed: x" "$(bs_run ensure boom)"
assert_eq "gh offer: install Ctrl+C" "True GitHub CLI install cancelled." "$(bs_run gh ctrlc)"
assert_eq "gh offer: install unexpected error" "True The GitHub CLI install failed: x" "$(bs_run gh boom)"
# winget older than 1.4 rejects --disable-interactivity: one retry without it
wr_run() { python3 -I - "${SCRIPT_DIR}/../light" "$@" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import maude_exe as m
mode = sys.argv[2]; log = []
def run(c):
    log.append("flag" if "--disable-interactivity" in c else "noflag")
    if "--disable-interactivity" in c:
        return {"old": 0x8A150002, "oldsigned": 0x8A150002 - 2**32, "fail": 1, "other": 0x8A150056}.get(mode, 0)
    return 1 if mode == "fail" else 0
m._git_done = False
m.offer_git({}, lambda e: None, lambda: "W", lambda q: "y", run, lambda s: None)
print(",".join(log))
PY
}
assert_eq "git: ok first time, no retry" "flag" "$(wr_run ok)"
assert_eq "git: old winget retries once without the flag" "flag,noflag" "$(wr_run old)"
assert_eq "git: old winget (signed code) retries too" "flag,noflag" "$(wr_run oldsigned)"
assert_eq "git: a real failure is not retried" "flag" "$(wr_run fail)"
assert_eq "git: another winget error is not retried" "flag" "$(wr_run other)"

# find_tool looks in the Kiro per-user folder too
assert_eq "find_tool kiro localappdata" 'K' "$(py "m.find_tool('kiro-cli', {'USERPROFILE': r'C:\\u', 'LOCALAPPDATA': r'C:\\L'}, lambda b, path=None: 'K' if path == r'C:\\L\\Programs\\Kiro-Cli' else None)")"

# Picker state in <home>/.maude/maude-model|maude-tool on every platform (no registry): read, validation, migration
PREF_OUT="$(python3 -I - "${SCRIPT_DIR}/../light" 2>/dev/null <<'PY'
import sys, os, tempfile, shutil, stat
sys.path.insert(0, sys.argv[1])
import maude_exe as m
out = []
d = tempfile.mkdtemp()
try:
    rd = lambda home, n, old: m.read_state(home, n, old)
    out.append(repr(m.windows_model_effort(d)))                 # nothing anywhere -> defaults
    os.makedirs(os.path.join(d, ".maude"))
    mf = os.path.join(d, ".maude", "maude-model")
    for raw in ("sonnet,high\n", "claude-opus-4-5,turbo", "haiku,", "x;id,max", "fable , MAX ", ""):
        with open(mf, "w") as f: f.write(raw)
        out.append(repr(m.windows_model_effort(d)))
    out.append(m.windows_model(d))
    # legacy ~/.maude-model: value moved into the new file, old one deleted
    os.remove(mf)
    with open(os.path.join(d, ".maude-model"), "w") as f: f.write("sonnet,low\n")
    out.append(repr(m.windows_model_effort(d)))
    out.append(open(mf).read().strip())
    out.append(str(os.path.exists(os.path.join(d, ".maude-model"))))
    # a present new file wins and the legacy file is left alone
    with open(os.path.join(d, ".maude-model"), "w") as f: f.write("opus")
    out.append(repr(m.windows_model_effort(d)))
    out.append(str(os.path.exists(os.path.join(d, ".maude-model"))))
    # maude_dir creates ~/.maude with 0700 (POSIX only)
    d2 = tempfile.mkdtemp()
    try:
        p2 = m.maude_dir(d2)
        out.append(str(p2 == __import__("pathlib").Path(d2) / ".maude" and p2.is_dir()))
        out.append(oct(stat.S_IMODE(os.stat(p2).st_mode)) if os.name != "nt" else "0o700")
        out.append(str(m.maude_dir(d2) == p2))                  # second call is a no-op
    finally:
        shutil.rmtree(d2, ignore_errors=True)
    # undecodable / UTF-16 (PowerShell 5.1 redirection) / UTF-8-BOM state files never crash
    d3 = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d3, ".maude"))
        f3 = os.path.join(d3, ".maude", "maude-model")
        for raw in (b"\xff\xfe\x00\xd8", b"\xff", "opus,high\r\n".encode("utf-16"), "\ufeffsonnet,low".encode("utf-8"), b"opus,max\r\n"):
            with open(f3, "wb") as f: f.write(raw)
            out.append(repr(m.windows_model_effort(d3)))
        os.remove(f3)
        with open(os.path.join(d3, ".maude-model"), "wb") as f: f.write("haiku,low".encode("utf-16"))
        out.append(repr(m.windows_model_effort(d3)))               # UTF-16 legacy file migrates
        out.append(str(os.path.exists(os.path.join(d3, ".maude-model"))))
        os.remove(f3)
        with open(os.path.join(d3, ".maude-model"), "wb") as f: f.write(b"\xff\xfe\xff")
        out.append(repr(m.windows_model_effort(d3)))               # undecodable legacy file: no crash
    finally:
        shutil.rmtree(d3, ignore_errors=True)
finally:
    shutil.rmtree(d, ignore_errors=True)
print("|".join(out))
PY
)"
assert_eq "state files: read, validation, migration" "('opus', '')|('sonnet', 'high')|('claude-opus-4-5', '')|('haiku', '')|('opus', 'max')|('fable', 'max')|('opus', '')|opus|('sonnet', 'low')|sonnet,low|False|('sonnet', 'low')|True|True|0o700|True|('opus', '')|('opus', '')|('opus', 'high')|('sonnet', 'low')|('opus', 'max')|('haiku', 'low')|False|('opus', '')" "$PREF_OUT"

# OPEN_SH: reads ~/.maude/maude-model ("<model>[,<effort>]"), old file as fallback; fake claude echoes its args
OSH_OUT="$(python3 -I - "${SCRIPT_DIR}/../light" 2>/dev/null <<'PY'
import sys, os, subprocess, tempfile, shutil
sys.path.insert(0, sys.argv[1])
import maude_exe as m
d = tempfile.mkdtemp()
try:
    home = os.path.join(d, "h"); os.makedirs(os.path.join(home, ".maude")); os.makedirs(os.path.join(d, "bin"))
    cl = os.path.join(d, "bin", "claude")
    with open(cl, "w") as f: f.write('#!/bin/bash\necho "claude $*"\nexit 1\n')  # always fails -> fallback runs too
    os.chmod(cl, 0o755)
    env = dict(os.environ, HOME=home, PATH=os.path.join(d, "bin") + os.pathsep + os.environ["PATH"])
    def run(*arg):
        r = subprocess.run(["bash", "-c", m.OPEN_SH, "maude", *arg], env=env, capture_output=True, text=True)
        return r.stdout.replace("\n", ";")
    new, old = os.path.join(home, ".maude", "maude-model"), os.path.join(home, ".maude-model")
    out = [run()]                                              # nothing -> opus
    open(old, "w").write("haiku\n"); out.append(run())          # old file fallback
    open(new, "w").write("sonnet,high\n"); out.append(run())    # new file with effort wins
    open(new, "w").write("fable"); out.append(run())           # model only
    open(new, "w").write("opus,turbo"); out.append(run())      # bad effort dropped
    open(new, "w").write("x;touch pwn,max"); out.append(run()) # bad model -> opus, effort kept
    out.append(run("haiku"))                                   # override model keeps the saved effort
    open(new, "wb").write(b"sonnet\r\n"); out.append(run())     # CRLF written by Windows tools
    open(new, "wb").write(b"opus,max\r\n"); out.append(run())
    out.append(str(os.path.exists(os.path.join(d, "pwn")) or os.path.exists(os.path.join(home, "pwn"))))
finally:
    shutil.rmtree(d, ignore_errors=True)
print("|".join(out))
PY
)"
assert_eq "OPEN_SH reads model,effort" "claude opus --continue;claude opus;|claude haiku --continue;claude haiku;|claude sonnet --effort high --continue;claude sonnet --effort high;|claude fable --continue;claude fable;|claude opus --continue;claude opus;|claude opus --effort max --continue;claude opus --effort max;|claude haiku --effort max --continue;claude haiku --effort max;|claude sonnet --continue;claude sonnet;|claude opus --effort max --continue;claude opus --effort max;|False" "$OSH_OUT"

suite_summary
