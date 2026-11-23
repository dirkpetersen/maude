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
D() { py "m.decide(r'$1', r'$S', $2, $3)"; }
O='C:\Users\bob\Documents'; I="$S\\Projects\\a"
assert_eq "inside -> sandbox" sandbox "$(D "$I" None None)"
assert_eq "inside, dotdot escape -> ask" ask "$(D "$S\\..\\x" None None)"
assert_eq "outside, no answer -> ask" ask "$(D "$O" None None)"
assert_eq "outside, y" windows "$(D "$O" None "'y'")"
assert_eq "outside, Yes" windows "$(D "$O" None "'Yes '")"
assert_eq "outside, empty = No" deny "$(D "$O" None "''")"
assert_eq "outside, n" deny "$(D "$O" None "'n'")"
assert_eq "outside, --windows" windows "$(D "$O" "'windows'" None)"
assert_eq "outside, --sandbox" refuse "$(D "$O" "'sandbox'" None)"
assert_eq "inside, --sandbox" sandbox "$(D "$I" "'sandbox'" None)"
assert_eq "prefix trick Maude2" ask "$(D "${S}2\\x" None None)"
assert_eq "case-insensitive inside" sandbox "$(D 'c:\users\BOB\appdata\local\maude\data\maude\p' None None)"
assert_eq "windows cmd continue" "['C:/c.exe', '--model', 'opus', '--continue']" "$(py "m.windows_cmd(r'C:/c.exe','opus',True)")"
assert_eq "windows cmd fallback" "['C:/c.exe', '--model', 'sonnet']" "$(py "m.windows_cmd(r'C:/c.exe','sonnet',False)")"
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
assert_eq "plan kiro quoted url" "[('Kiro CLI', \"irm 'https://cli.kiro.dev/install.ps1' | iex\")]" "$(py "m.install_plan('Kiro CLI', False, False)")"
assert_eq "plan opencode, bun there" "[('OpenCode', 'bun install -g --trust @opencode/cli')]" "$(py "m.install_plan('OpenCode', False, True)")"
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
    log.append(c[-1]); (not rc) and have.update({b: "x" for b in appears}); return rc
r = m.ensure_tool(tool, have.get, lambda q: log.append(q) or ans, run, lambda s: None, lambda: log.append("refresh"))
print(r, "|".join(log))
PY
}
assert_eq "ensure: yes installs bun then crush" "x Install Bun now? [Y/n] |irm bun.sh/install.ps1 | iex|refresh|Install Crush now? [Y/n] |bun install -g --trust @charmland/crush|refresh" "$(et_run Crush y 0 bun,crush)"
assert_eq "ensure: default Enter = yes" "x" "$(et_run 'Claude Code' '' 0 claude | cut -d' ' -f1)"
assert_eq "ensure: no" "None Install Claude Code now? [Y/n] " "$(et_run 'Claude Code' n 0 claude)"
assert_eq "ensure: install fails" "None" "$(et_run 'Kiro CLI' y 1 kiro-cli | cut -d' ' -f1)"
assert_eq "ensure: installed but not found" "None" "$(et_run 'Kiro CLI' y 0 other | cut -d' ' -f1)"

# git offer: pure plan
G="winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements"
assert_eq "git plan: have git" "None" "$(py "m.git_plan(True, True, True)")"
assert_eq "git plan: no winget" "link" "$(py "m.git_plan(False, False, True)")"
assert_eq "git plan: admin" "$G" "$(py "' '.join(m.git_plan(False, True, True))")"
assert_eq "git plan: user scope" "$G --scope user" "$(py "' '.join(m.git_plan(False, True, False))")"
# git offer flow with fakes: log has the messages and "RUN <args>" for each process started
git_run() { python3 -I - "${SCRIPT_DIR}/../light" "$@" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import maude_exe as m
ans, admin, wg = sys.argv[2], sys.argv[3] == "1", sys.argv[4] or None
log = []
def go():
    m.offer_git({}, lambda e: None, lambda: wg, lambda: admin, lambda q: ans, lambda c: log.append("RUN " + " ".join(c)) or 0, log.append)
go(); go()  # second call in the same process must do nothing
print("\n".join(log))
PY
}
assert_eq "git offer: declined runs nothing" "0" "$(git_run n 0 W | grep -c '^RUN')"
assert_contains "git offer: shows command" "Install command: W install --id Git.Git" "$(git_run n 0 W)"
assert_eq "git offer: accepted not admin runs once with scope" "1" "$(git_run y 0 W | grep -c '^RUN .*--scope user$')"
assert_eq "git offer: accepted admin has no scope" "0" "$(git_run y 1 W | grep -c -- '--scope')"
assert_eq "git offer: no winget, link only" "0" "$(git_run y 0 '' | grep -c '^RUN')"
assert_contains "git offer: link text" "Git for Windows is not installed. Get it from https://git-scm.com/download/win (optional)." "$(git_run y 0 '')"

# GitHub CLI offer
GH="winget install --id GitHub.cli -e --source winget --accept-package-agreements --accept-source-agreements"
assert_eq "gh plan: have gh" "None" "$(py "m.gh_plan(True, True)")"
assert_eq "gh plan: no winget" "link" "$(py "m.gh_plan(False, False)")"
assert_eq "gh plan: winget argv, no scope" "$GH" "$(py "' '.join(m.gh_plan(False, True))")"
gh_run() { python3 -I - "${SCRIPT_DIR}/../light" "$@" <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import maude_exe as m
ans, wg = sys.argv[2], sys.argv[3] or None
log = []
def go():
    m.offer_gh({}, lambda e: None, lambda: wg, lambda q: ans, lambda c: log.append("RUN " + " ".join(c)) or 0, log.append)
go(); go()
print("\n".join(log))
PY
}
assert_eq "gh offer: declined runs nothing" "0" "$(gh_run n W | grep -c '^RUN')"
assert_eq "gh offer: accepted runs once" "1" "$(gh_run y W | grep -c '^RUN W install --id GitHub.cli .*source-agreements$')"
assert_contains "gh offer: admin hint" "Windows may ask for admin approval" "$(gh_run n W)"
assert_contains "gh offer: link text" "GitHub CLI is not installed. Get it from https://cli.github.com (optional)." "$(gh_run y '')"

# find_tool looks in the Kiro per-user folder too
assert_eq "find_tool kiro localappdata" 'K' "$(py "m.find_tool('kiro-cli', {'USERPROFILE': r'C:\\u', 'LOCALAPPDATA': r'C:\\L'}, lambda b, path=None: 'K' if path == r'C:\\L\\Programs\\Kiro-Cli' else None)")"

suite_summary
