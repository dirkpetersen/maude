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
assert_eq "tui cmd has no command" "['wsl.exe', '-d', 'Maude', '--cd', '~']" "$(py "m.tui_cmd()")"
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
suite_summary
