#!/usr/bin/env bash
# tests/test-mac-install.sh - macOS "offer to install a missing tool" (light/maude.py):
# mac_install_plan (pure), mac_ensure_tool (the runner, with every side effect injected), mac_offer,
# and the stub-git check. NO installer is ever run: subprocess.run is replaced by a fake that
# records the argv (and the default `subprocess` of the extracted code raises if reached).
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "maude.py macOS tool install offers"

MAUDE_PY="${SCRIPT_DIR}/../light/maude.py"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

cat > "$d/run.py" <<'PY'
import __future__, ast, sys, types

src = open(sys.argv[1], encoding="utf-8").read()
tree = ast.parse(src, feature_version=(3, 9))
want = {"MAC_TOOL_LABELS", "MAC_BREW_URL", "_MAC_BREW_TOOLS", "_MAC_CURL_INSTALL", "mac_install_plan",
        "_mac_find", "mac_ensure_tool", "_mac_offered", "mac_offer"}

def boom(*a, **k):
    raise AssertionError("a real subprocess/input/tool lookup was reached: " + repr(a))

ns = {"subprocess": types.SimpleNamespace(run=boom), "plat_tool": boom, "input": boom}
for n in tree.body:
    names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
    if names & want:
        exec(compile(ast.Module([n], []), "maude.py", "exec", flags=__future__.annotations.compiler_flag), ns)

def out(k, v):
    print(f"{k}={v}")

plan = ns["mac_install_plan"]
# -- the pure plan --
for tool in ("claude", "kiro-cli", "git", "gh", "opencode", "crush", "nonesuch"):
    for brew in (False, True):
        argv, shown, note = plan(tool, brew)
        out(f"plan.{tool}.{'brew' if brew else 'nobrew'}", (argv, shown, bool(note)))
# never sudo, never installs Homebrew itself
every = [plan(t, b) for t in ns["MAC_TOOL_LABELS"] for b in (False, True)]
out("no.sudo", not any("sudo" in " ".join(a or []) + s + n for a, s, n in every))
out("no.brew.install.script", not any("Homebrew/install" in " ".join(a or []) + s for a, s, n in every))
out("hint.has.url", all(("http" in s) for t in ("gh", "opencode", "crush") for a, s, n in [plan(t, False)]))

# -- the runner --
def runner(tool, have, answers, rc=0, brew=None, installed_after=True):
    """have = the set of tools present at the start; running the installer adds `tool` when installed_after."""
    present, said, asked, ran = set(have), [], [], []
    def find(name):
        if name == "brew":
            return brew
        return ("/fake/bin/" + name) if name in present else None
    def ask(q):
        asked.append(q)
        a = answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a
    class R:
        def __init__(self, c): self.returncode = c
    def run(argv, **kw):
        ran.append(argv)
        if rc == 0 and installed_after:
            present.add(tool)
        return R(rc)
    res = ns["mac_ensure_tool"](tool, find=find, ask=ask, run=run, say=lambda m: said.append(m))
    return res, ran, [q.split(" now?")[0] for q in asked], "\n".join(said)

r = runner("claude", {"claude"}, [])
out("run.present", (r[0], r[1], r[2]))
r = runner("claude", set(), ["y"])
out("run.claude.yes", (r[0], r[1]))
out("run.claude.shows", ("curl -fsSL https://claude.ai/install.sh | bash" in r[3], r[2]))
for ans in ("", "n", "N", "no", "maybe"):
    r = runner("claude", set(), [ans])
    out(f"run.claude.answer[{ans}]", (r[0], r[1]))
for exc in (EOFError(), KeyboardInterrupt()):
    r = runner("claude", set(), [exc])
    out(f"run.claude.{type(exc).__name__}", (r[0], r[1]))
r = runner("claude", set(), ["yes"])
out("run.claude.yes-word", (r[0], len(r[1])))
r = runner("claude", set(), ["y"], rc=2)
out("run.claude.fail", (r[0], "failed (exit code 2)" in r[3]))
r = runner("claude", set(), ["y"], installed_after=False)
out("run.claude.notfound", (r[0], "not found yet" in r[3]))
# brew path: the resolved brew path replaces the bare name, never sudo
r = runner("gh", set(), ["y"], brew="/opt/homebrew/bin/brew")
out("run.gh.brew", (r[0], r[1]))
r = runner("crush", set(), ["y"], brew="/opt/homebrew/bin/brew")
out("run.crush.brew", r[1])
# no brew: hint only, nothing asked, nothing run
r = runner("gh", set(), [])
out("run.gh.nobrew", (r[0], r[1], r[2], "brew.sh" in r[3], "cli.github.com" in r[3]))
# git without brew: xcode-select, warns about the dialog, waits for the user, then re-resolves
r = runner("git", set(), ["y", ""])
out("run.git.clt", (r[0], r[1], len(r[2]), "dialog" in r[3]))
r = runner("git", set(), ["n"])
out("run.git.decline", (r[0], r[1]))
r = runner("git", set(), ["y"], brew="/usr/local/bin/brew")
out("run.git.brew", (r[0], r[1]))
r = runner("kiro-cli", set(), ["y"])
out("run.kiro", (r[0], r[1][0][:2], "cli.kiro.dev/install" in r[1][0][2]))

# -- _mac_find: Apple's /usr/bin/git stub does not count without the Command Line Tools --
class RC:
    def __init__(self, c): self.returncode = c
f = ns["_mac_find"]
out("find.stub.noclt", f("git", which=lambda n: "/usr/bin/git", run=lambda *a, **k: RC(2)))
out("find.stub.clt", f("git", which=lambda n: "/usr/bin/git", run=lambda *a, **k: RC(0)))
out("find.stub.oserror", f("git", which=lambda n: "/usr/bin/git", run=lambda *a, **k: (_ for _ in ()).throw(OSError("x"))))
out("find.brewgit", f("git", which=lambda n: "/opt/homebrew/bin/git", run=boom))
out("find.other.stub", f("gh", which=lambda n: "/usr/bin/gh", run=boom))
out("find.missing", f("git", which=lambda n: None, run=boom))

# -- mac_offer: once per process, silent when there is no installer, never reaches a real tool --
calls = []
ns["plat_tool"] = lambda n: calls.append(n) or None          # nothing installed, no brew
ns["input"] = boom
ns["mac_offer"]("gh")
ns["mac_offer"]("gh")
out("offer.gh.nobrew.once", (calls, sorted(ns["_mac_offered"])))
PY

if ! python3 "$d/run.py" "$MAUDE_PY" > "$d/out.txt" 2> "$d/err.txt"; then cat "$d/err.txt"; exit 1; fi
# value of "<key>=<value>" (fixed-string key: keys may contain brackets)
get() { awk -v k="$1=" 'index($0, k) == 1 { print substr($0, length(k) + 1); exit }' "$d/out.txt"; }

assert_eq "claude: official curl installer" "(['/bin/bash', '-c', 'curl -fsSL https://claude.ai/install.sh | bash'], 'curl -fsSL https://claude.ai/install.sh | bash', False)" "$(get plan.claude.nobrew)"
assert_eq "claude: same with brew present" "$(get plan.claude.nobrew)" "$(get plan.claude.brew)"
assert_eq "kiro-cli: vendor installer, same on both" "$(get plan.kiro-cli.nobrew)" "$(get plan.kiro-cli.brew)"
assert_contains "kiro-cli: cli.kiro.dev" "cli.kiro.dev/install" "$(get plan.kiro-cli.nobrew)"
assert_eq "git without brew: xcode-select (GUI dialog noted)" "(['xcode-select', '--install'], 'xcode-select --install', True)" "$(get plan.git.nobrew)"
assert_eq "git with brew" "(['brew', 'install', 'git'], 'brew install git', False)" "$(get plan.git.brew)"
assert_eq "gh with brew" "(['brew', 'install', 'gh'], 'brew install gh', False)" "$(get plan.gh.brew)"
assert_eq "opencode with brew" "(['brew', 'install', 'opencode'], 'brew install opencode', False)" "$(get plan.opencode.brew)"
assert_eq "crush with brew" "(['brew', 'install', 'charmbracelet/tap/crush'], 'brew install charmbracelet/tap/crush', False)" "$(get plan.crush.brew)"
for t in gh opencode crush; do
  assert_contains "$t without brew: hint, no command" "(None, 'Install Homebrew first (https://brew.sh)" "$(get plan.$t.nobrew)"
done
assert_contains "gh hint has its URL" "https://cli.github.com" "$(get plan.gh.nobrew)"
assert_eq "unknown tool: no command" "(None, 'No automatic install is known for nonesuch.', False)" "$(get plan.nonesuch.brew)"
assert_eq "no plan uses sudo" "True" "$(get no.sudo)"
assert_eq "Homebrew itself is never installed" "True" "$(get no.brew.install.script)"
assert_eq "hints carry a URL" "True" "$(get hint.has.url)"

assert_eq "present tool: nothing asked, nothing run" "('/fake/bin/claude', [], [])" "$(get run.present)"
assert_eq "yes: runs the installer once, re-resolves the tool" "('/fake/bin/claude', [['/bin/bash', '-c', 'curl -fsSL https://claude.ai/install.sh | bash']])" "$(get run.claude.yes)"
assert_eq "the command is shown before asking" "(True, ['Install Claude Code'])" "$(get run.claude.shows)"
for a in "" n N no maybe; do
  assert_eq "answer '$a' is a no (default N): nothing runs" "(None, [])" "$(get "run.claude.answer[$a]")"
done
assert_eq "EOF is a no" "(None, [])" "$(get run.claude.EOFError)"
assert_eq "Ctrl+C is a no" "(None, [])" "$(get run.claude.KeyboardInterrupt)"
assert_eq "'yes' is a yes" "('/fake/bin/claude', 1)" "$(get run.claude.yes-word)"
assert_eq "installer failure: None and says so" "(None, True)" "$(get run.claude.fail)"
assert_eq "installed but not found yet: None and says so" "(None, True)" "$(get run.claude.notfound)"
assert_eq "gh via brew uses the resolved brew path" "('/fake/bin/gh', [['/opt/homebrew/bin/brew', 'install', 'gh']])" "$(get run.gh.brew)"
assert_eq "crush via brew" "[['/opt/homebrew/bin/brew', 'install', 'charmbracelet/tap/crush']]" "$(get run.crush.brew)"
assert_eq "gh without brew: hint only, nothing asked or run" "(None, [], [], True, True)" "$(get run.gh.nobrew)"
assert_eq "git via Command Line Tools: runs, waits for Enter, re-resolves" "('/fake/bin/git', [['xcode-select', '--install']], 2, True)" "$(get run.git.clt)"
assert_eq "git declined" "(None, [])" "$(get run.git.decline)"
assert_eq "git via brew" "('/fake/bin/git', [['/usr/local/bin/brew', 'install', 'git']])" "$(get run.git.brew)"
assert_eq "kiro-cli runs the vendor script through bash" "('/fake/bin/kiro-cli', ['/bin/bash', '-c'], True)" "$(get run.kiro)"
assert_eq "stub /usr/bin/git without CLT is not git" "None" "$(get find.stub.noclt)"
assert_eq "stub /usr/bin/git with CLT counts" "/usr/bin/git" "$(get find.stub.clt)"
assert_eq "stub check failing to run counts as missing" "None" "$(get find.stub.oserror)"
assert_eq "a Homebrew git needs no CLT check" "/opt/homebrew/bin/git" "$(get find.brewgit)"
assert_eq "other /usr/bin tools are taken as is" "/usr/bin/gh" "$(get find.other.stub)"
assert_eq "missing git stays missing" "None" "$(get find.missing)"
assert_eq "mac_offer: once per process, silent without installer" "(['gh', 'brew'], ['gh'])" "$(get offer.gh.nobrew.once)"

# Wiring: the macOS branches call the runner, and no sudo appears in the macOS install code.
assert_contains "open_project offers the claude install" 'mac_ensure_tool("claude")' "$(cat "$MAUDE_PY")"
assert_contains "other tools offer their install" "exe = mac_ensure_tool(cmd[0])" "$(cat "$MAUDE_PY")"

suite_summary
