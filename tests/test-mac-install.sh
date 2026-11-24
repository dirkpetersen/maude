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
        "_mac_find", "mac_ensure_tool", "_mac_offered", "mac_offer", "MAC_GIT_STUB", "_mac_clt_cache",
        "mac_clt_ready", "MAC_NOT_FOUND_HINT", "MAC_TOOL_DIRS", "plat_tool", "MAC_NO_GIT", "git_exe",
        "_MAC_BREW_CASKS", "_MAC_CURL_FALLBACK", "AGENT_BINARIES"}
import subprocess as realsp, shutil as realshutil
from pathlib import Path

def boom(*a, **k):
    raise AssertionError("a real subprocess/input/tool lookup was reached: " + repr(a))

def build(ns, skip=()):
    for n in tree.body:
        names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        if names & want and not (names & set(skip)):
            exec(compile(ast.Module([n], []), "maude.py", "exec", flags=__future__.annotations.compiler_flag), ns)
    return ns

ns = build({"subprocess": types.SimpleNamespace(run=boom, TimeoutExpired=realsp.TimeoutExpired), "plat_tool": boom, "input": boom, "IS_MAC": True,
            "IS_WIN": False, "is_native": lambda: True}, skip=("plat_tool", "git_exe"))

def out(k, v):
    print(f"{k}={v}")

plan = ns["mac_install_plan"]
# -- the pure plan --
for tool in ("claude", "kiro-cli", "git", "gh", "opencode", "crush", "codex", "agy", "grok", "copilot", "nonesuch"):
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
out("run.claude.fail.rc2.msg", ("failed (exit code 2)" in runner("claude", set(), ["y"], rc=2)[3]))
out("run.kiro.notfound.hint", ("Kiro CLI.app" in runner("kiro-cli", set(), ["y"], installed_after=False)[3]))
out("run.claude.notfound.generic", ("was installed but is not found yet" in runner("claude", set(), ["y"], installed_after=False)[3]))
out("run.git.clt.missing", (lambda r: (r[0], "not installed yet" in r[3], "was installed" in r[3]))(
    runner("git", set(), ["y", ""], installed_after=False)))
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

# new agents: brew cask when brew exists, the vendor script otherwise; grok has no brew package
r = runner("agy", set(), ["y"], brew="/opt/homebrew/bin/brew")
out("run.agy.brew", r[1])
r = runner("codex", set(), ["y"])
out("run.codex.nobrew", (r[0], r[1][0][:2], "chatgpt.com/codex/install.sh" in r[1][0][2], r[2]))
r = runner("copilot", set(), ["n"], brew="/opt/homebrew/bin/brew")
out("run.copilot.decline", (r[0], r[1]))
r = runner("grok", set(), ["y"], brew="/opt/homebrew/bin/brew")
out("run.grok.brew.still.script", (r[1][0][:2], "x.ai/cli/install.sh" in r[1][0][2]))
out("agent.binaries", ns["AGENT_BINARIES"])
out("agent.binaries.labelled", all(b in ns["MAC_TOOL_LABELS"] for b in ns["AGENT_BINARIES"].values()))
out("agent.binaries.installable", all(plan(b, True)[0] is not None and plan(b, False)[0] is not None for b in ns["AGENT_BINARIES"].values()))
out("agent.sh.only", not any(("sudo" in c or "| sh" in c and "pipefail" not in c) for c in list(ns["_MAC_CURL_FALLBACK"].values()) + [ns["_MAC_CURL_INSTALL"]["grok"]]))

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

# -- pipefail: a failed download must make the whole command fail (curl replaced by `false`; bash only) --
for tool, url in (("claude", "https://claude.ai/install.sh"), ("kiro-cli", "https://cli.kiro.dev/install"),
                  ("grok", "https://x.ai/cli/install.sh"), ("codex", "https://chatgpt.com/codex/install.sh"),
                  ("agy", "https://antigravity.google/cli/install.sh"), ("copilot", "https://gh.io/copilot-install")):
    cmd = ns["_MAC_CURL_INSTALL"].get(tool) or ns["_MAC_CURL_FALLBACK"][tool]
    out(f"pipefail.{tool}.prefix", cmd.startswith("set -o pipefail; curl -fsSL " + url))
    fake = cmd.replace("curl -fsSL " + url, "false")
    out(f"pipefail.{tool}.fails", realsp.run(["/bin/bash", "-c", fake]).returncode != 0)
    nopf = fake.replace("set -o pipefail; ", "")
    out(f"pipefail.{tool}.needed", realsp.run(["/bin/bash", "-c", nopf]).returncode == 0)

# -- plat_tool / git_exe on macOS with the real functions and every lookup faked --
def world(found, clt_ok):
    """found: {dir-or-None: {name: path}}; clt_ok: mutable [bool]. Returns (namespace, xcode-select call count)."""
    calls = []
    class R:
        def __init__(self, c): self.returncode = c
    def fake_run(argv, **kw):
        calls.append(list(argv))
        if argv[:2] == ["xcode-select", "--install"]:
            clt_ok[0] = True
            return R(0)
        return R(0 if clt_ok[0] else 2)
    def which(name, path=None):
        return found.get(path, {}).get(name)
    w = build({"subprocess": types.SimpleNamespace(run=fake_run, TimeoutExpired=realsp.TimeoutExpired), "shutil": types.SimpleNamespace(which=which),
               "IS_MAC": True, "IS_WIN": False, "is_native": lambda: True, "Path": Path, "input": boom, "mx": None})
    return w, calls

ok = [False]
w, calls = world({None: {"git": "/usr/bin/git", "gh": "/usr/bin/gh"}}, ok)
out("tool.stub.none", w["plat_tool"]("git"))
out("tool.stub.rechecked", (w["plat_tool"]("git"), w["git_exe"](), len(calls), w["_mac_clt_cache"][0]))
out("tool.other.untouched", (w["plat_tool"]("gh"), len(calls)))
ok[0] = True                                  # CLT installed outside Maude: noticed without a reset
out("tool.negative.not.cached", (w["plat_tool"]("git"), w["git_exe"](), w["_mac_clt_cache"][0]))
out("tool.positive.cached", (w["plat_tool"]("git"), len(calls)))
ok2 = [True]
w, calls = world({None: {"git": "/usr/bin/git"}}, ok2)
out("tool.real.git", (w["plat_tool"]("git"), w["plat_tool"]("git"), len(calls)))
ok3 = [False]
w, calls = world({None: {"git": "/usr/bin/git"}, "/opt/homebrew/bin": {"git": "/opt/homebrew/bin/git"}}, ok3)
out("tool.stub.brewgit", w["plat_tool"]("git"))
w, calls = world({None: {"git": "/opt/homebrew/bin/git"}}, [False])
out("tool.brewgit.nocheck", (w["plat_tool"]("git"), len(calls)))
ok4 = [False]
w, calls = world({}, ok4)
out("tool.git_exe.missing", (w["plat_tool"]("git"), w["git_exe"]()))
# cache reset after an install attempt: decline-free flow through the runner with the stub present
ok5 = [False]
w, calls = world({None: {"git": "/usr/bin/git"}}, ok5)
said, answers = [], ["y", ""]
out("reset.before", w["plat_tool"]("git"))
res = w["mac_ensure_tool"]("git", ask=lambda q: answers.pop(0), say=said.append)
out("reset.after", (res, ["xcode-select", "--install"] in calls, any("not installed yet" in m for m in said)))
# xcode-select dialog never finished: plat_tool stays None and the message says so
ok6 = [False]
w, calls = world({None: {"git": "/usr/bin/git"}}, ok6)
w["subprocess"].run = lambda argv, **kw: types.SimpleNamespace(returncode=0 if argv[1:] == ["--install"] else 2)
said, answers = [], ["y", ""]
res = w["mac_ensure_tool"]("git", ask=lambda q: answers.pop(0), say=said.append)
out("reset.unfinished", (res, any("not installed yet" in m for m in said), any("was installed" in m for m in said)))
# kiro: the zip fallback / the app's own setup put it in ~/.local/bin, which plat_tool searches
kdir = str(Path.home() / ".local" / "bin")
w, calls = world({kdir: {"kiro-cli": kdir + "/kiro-cli"}}, [True])
# xcode-select -p hanging: treated as not ready, never cached, and called with a timeout
seen = {}
def hang(argv, **kw):
    seen.update(kw)
    raise realsp.TimeoutExpired(argv, kw.get("timeout"))
w2, calls = world({}, [True])
w2["subprocess"] = types.SimpleNamespace(run=hang, TimeoutExpired=realsp.TimeoutExpired)
out("clt.timeout", (w2["mac_clt_ready"](), w2["_mac_clt_cache"][0] is None))
out("clt.timeout.arg", seen.get("timeout") == 10)
out("tool.kiro.localbin", w["plat_tool"]("kiro-cli") == kdir + "/kiro-cli")
PY

if ! python3 "$d/run.py" "$MAUDE_PY" > "$d/out.txt" 2> "$d/err.txt"; then cat "$d/err.txt"; exit 1; fi
# value of "<key>=<value>" (fixed-string key: keys may contain brackets)
get() { awk -v k="$1=" 'index($0, k) == 1 { print substr($0, length(k) + 1); exit }' "$d/out.txt"; }

assert_eq "claude: official curl installer (pipefail)" "(['/bin/bash', '-c', 'set -o pipefail; curl -fsSL https://claude.ai/install.sh | bash'], 'set -o pipefail; curl -fsSL https://claude.ai/install.sh | bash', False)" "$(get plan.claude.nobrew)"
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
assert_eq "yes: runs the installer once, re-resolves the tool" "('/fake/bin/claude', [['/bin/bash', '-c', 'set -o pipefail; curl -fsSL https://claude.ai/install.sh | bash']])" "$(get run.claude.yes)"
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
for t in claude kiro-cli; do
  assert_eq "$t: command starts with set -o pipefail and curl" "True" "$(get pipefail.$t.prefix)"
  assert_eq "$t: a failing download makes the command fail" "True" "$(get pipefail.$t.fails)"
  assert_eq "$t: without pipefail the failure would have been hidden" "True" "$(get pipefail.$t.needed)"
done
assert_eq "codex with brew: cask" "(['brew', 'install', '--cask', 'codex'], 'brew install --cask codex', False)" "$(get plan.codex.brew)"
assert_eq "agy with brew: antigravity-cli cask" "(['brew', 'install', '--cask', 'antigravity-cli'], 'brew install --cask antigravity-cli', False)" "$(get plan.agy.brew)"
assert_eq "copilot with brew: copilot-cli cask" "(['brew', 'install', '--cask', 'copilot-cli'], 'brew install --cask copilot-cli', False)" "$(get plan.copilot.brew)"
assert_contains "codex without brew: vendor script, non-interactive" "CODEX_NON_INTERACTIVE=true sh" "$(get plan.codex.nobrew)"
assert_contains "agy without brew: vendor script" "antigravity.google/cli/install.sh" "$(get plan.agy.nobrew)"
assert_contains "copilot without brew: vendor script" "gh.io/copilot-install" "$(get plan.copilot.nobrew)"
assert_eq "grok: vendor script, same with and without brew" "$(get plan.grok.nobrew)" "$(get plan.grok.brew)"
assert_contains "grok: x.ai script" "https://x.ai/cli/install.sh" "$(get plan.grok.nobrew)"
assert_eq "agy via brew runs the resolved brew path" "[['/opt/homebrew/bin/brew', 'install', '--cask', 'antigravity-cli']]" "$(get run.agy.brew)"
assert_eq "codex without brew runs bash -c with the script" "('/fake/bin/codex', ['/bin/bash', '-c'], True, ['Install Codex'])" "$(get run.codex.nobrew)"
assert_eq "declined copilot runs nothing" "(None, [])" "$(get run.copilot.decline)"
assert_eq "grok has no brew package: still the script" "(['/bin/bash', '-c'], True)" "$(get run.grok.brew.still.script)"
assert_eq "agent binary names" "{'codex': 'codex', 'antigravity': 'agy', 'grok': 'grok', 'copilot': 'copilot'}" "$(get agent.binaries)"
assert_eq "every agent binary has a label and a plan with and without brew" "True True" "$(get agent.binaries.labelled) $(get agent.binaries.installable)"
assert_eq "no sudo in the new scripts" "True" "$(get agent.sh.only)"
assert_eq "installer non-zero exit is reported as a failure" "True" "$(get run.claude.fail.rc2.msg)"
assert_eq "kiro not on PATH after install: Kiro-specific hint" "True" "$(get run.kiro.notfound.hint)"
assert_eq "other tools keep the generic not-found message" "True" "$(get run.claude.notfound.generic)"
assert_eq "xcode-select path, git still missing: not installed yet, not 'was installed'" "(None, True, False)" "$(get run.git.clt.missing)"
assert_eq "plat_tool(git): Apple stub without CLT is None" "None" "$(get tool.stub.none)"
assert_eq "stub without CLT: re-checked each time (not cached), git_exe never the bare name" "(None, '/nonexistent/git', 3, None)" "$(get tool.stub.rechecked)"
assert_eq "other tools do not trigger the CLT check" "('/usr/bin/gh', 3)" "$(get tool.other.untouched)"
assert_eq "CLT installed outside Maude is noticed without a reset" "('/usr/bin/git', '/usr/bin/git', True)" "$(get tool.negative.not.cached)"
assert_eq "a positive answer is cached" "('/usr/bin/git', 4)" "$(get tool.positive.cached)"
assert_eq "real /usr/bin/git when xcode-select -p succeeds (one run for two lookups)" "('/usr/bin/git', '/usr/bin/git', 1)" "$(get tool.real.git)"
assert_eq "xcode-select timeout: not ready, not cached" "(False, True)" "$(get clt.timeout)"
assert_eq "xcode-select is called with a timeout" "True" "$(get clt.timeout.arg)"
assert_contains "detach on macOS checks for missing git first" 'if IS_MAC and not plat_tool("git"):' "$(cat "$MAUDE_PY")"
assert_eq "stub on PATH but Homebrew git exists: Homebrew git" "/opt/homebrew/bin/git" "$(get tool.stub.brewgit)"
assert_eq "Homebrew git needs no CLT check" "('/opt/homebrew/bin/git', 0)" "$(get tool.brewgit.nocheck)"
assert_eq "no git at all: None and a nonexistent path" "(None, '/nonexistent/git')" "$(get tool.git_exe.missing)"
assert_eq "install attempt resets the cache: stub becomes real git" "None" "$(get reset.before)"
assert_eq "...and the runner then finds it, no not-installed message" "('/usr/bin/git', True, False)" "$(get reset.after)"
assert_eq "CLT dialog unfinished: None, says not installed yet" "(None, True, False)" "$(get reset.unfinished)"
assert_eq "kiro-cli in ~/.local/bin is found by plat_tool" "True" "$(get tool.kiro.localbin)"

# Wiring: the macOS branches call the runner, and no sudo appears in the macOS install code.
assert_contains "open_project offers the claude install" 'mac_ensure_tool("claude")' "$(cat "$MAUDE_PY")"
assert_contains "other tools offer their install" "exe = mac_ensure_tool(cmd[0])" "$(cat "$MAUDE_PY")"

suite_summary
