#!/usr/bin/env bash
# tests/test-update-native.sh - Update Maude on Windows/macOS: release JSON, versions, asset choice,
# sha check, rename-aside swap, old-file cleanup, 42 relaunch decision, macOS maude.py fetch, step plan.
# No network: every download goes through a fake urlopen. Only mktemp dirs are created and removed.
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "Update Maude (Windows exe + macOS)"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

cat > "$d/run.py" <<'PY'
import ast, hashlib, io, json, os, re, subprocess, sys, time, types, urllib.request
from pathlib import Path

light, tmp = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(light))
import maude_exe as m

def out(name, ok, detail=""):
    print(f"{name}\t{'ok' if ok else 'FAIL ' + str(detail)}")

# ── versions
pv = m.parse_version
out("parse v1.2.3", pv("v1.2.3") == (1, 2, 3))
out("parse 1.2.3", pv("1.2.3") == (1, 2, 3))
out("parse dev", pv("dev") is None)
out("parse prerelease", pv("v1.2.3-rc1") is None)
out("parse junk/None", pv("") is None and pv(None) is None)
ud = m.update_decision
out("newer", ud("v1.2.3", "v1.2.4") == "update")
out("newer minor beats patch (numeric compare)", ud("v1.9.9", "v1.10.0") == "update")
out("same", ud("v1.2.3", "v1.2.3") == "current")
out("older tag never downgrades", ud("v1.2.3", "v1.2.2") == "current")
out("v prefix mix", ud("1.2.3", "v1.2.4") == "update")
out("dev is never replaced", ud("dev", "v9.9.9") == "dev")

# ── release JSON / asset choice
def rel(tag="v1.2.4", names=("maude.exe", "maude.exe.sha256", "other.tar.gz")):
    return {"tag_name": tag, "assets": [{"name": n, "browser_download_url": "https://evil.example/" + n} for n in names]}
r = m.exe_release(rel())
out("assets found", r is not None and r[0] == "v1.2.4")
out("urls rebuilt, not taken from JSON", r[1] == m.RELEASE_DL + "v1.2.4/maude.exe" and r[2] == m.RELEASE_DL + "v1.2.4/maude.exe.sha256", r)
out("missing sha asset", m.exe_release(rel(names=("maude.exe",))) is None)
out("missing exe asset", m.exe_release(rel(names=("maude.exe.sha256",))) is None)
out("bad tag", m.exe_release(rel(tag="latest")) is None and m.exe_release(rel(tag="v1.2")) is None)
out("not a dict / no assets", m.exe_release([]) is None and m.exe_release({"tag_name": "v1.0.0"}) is None)

# ── swap_exe: rename aside, move in, restore on failure
t1 = tmp / "swap"; t1.mkdir()
exe, new = t1 / "maude.exe", t1 / "maude.exe.update"
exe.write_bytes(b"OLD"); new.write_bytes(b"NEW")
aside = m.swap_exe(str(exe), str(new), 4242)
out("swap: new in place", exe.read_bytes() == b"NEW")
out("swap: old aside as .old-<pid>", aside == str(exe) + ".old-4242" and Path(aside).read_bytes() == b"OLD", aside)
out("swap: update file consumed", not new.exists())
exe2, new2 = t1 / "b.exe", t1 / "b.exe.update"
exe2.write_bytes(b"OLD2"); new2.write_bytes(b"NEW2")
count = [0]
def flaky(src, dst):
    count[0] += 1
    if count[0] == 2:
        raise PermissionError("locked")
    return os.replace(src, dst)
try:
    m.swap_exe(str(exe2), str(new2), 7, replace=flaky); raised = False
except PermissionError:
    raised = True
out("swap failure: raised, old exe restored", raised and exe2.read_bytes() == b"OLD2" and not (t1 / "b.exe.old-7").exists())
new3 = t1 / "c.exe.update"; new3.write_bytes(b"N3")
out("swap: no target -> no aside", m.swap_exe(str(t1 / "c.exe"), str(new3), 1) is None and (t1 / "c.exe").read_bytes() == b"N3")

# ── cleanup_old
t2 = tmp / "clean"; t2.mkdir()
for n in ("maude.exe", "maude.exe.old-1", "maude.exe.old-22", "maude.exe.update", "other.exe.old-3"):
    (t2 / n).write_bytes(b"x")
out("cleanup removes only maude.exe.old-*", m.cleanup_old(str(t2 / "maude.exe")) == 2
    and sorted(os.listdir(t2)) == ["maude.exe", "maude.exe.update", "other.exe.old-3"], sorted(os.listdir(t2)))
(t2 / "maude.exe.update-1").write_bytes(b"x"); (t2 / "maude.exe.update-2").write_bytes(b"x")
os.utime(t2 / "maude.exe.update-1", (1, 1))
m.cleanup_old(str(t2 / "maude.exe"))
out("cleanup: stale staged files go, a fresh one stays", sorted(os.listdir(t2)) == ["maude.exe", "maude.exe.update", "maude.exe.update-2", "other.exe.old-3"], sorted(os.listdir(t2)))
(t2 / "Maude.exe.old-9").write_bytes(b"x")
out("cleanup: case-insensitive, also for a lower-cased target", m.cleanup_old(str(t2 / "maude.exe")) == 1 and not (t2 / "Maude.exe.old-9").exists())
out("cleanup of a missing dir is silent", m.cleanup_old(str(tmp / "nope" / "maude.exe")) == 0)

# ── relaunch decision / command
out("42 relaunches", m.should_relaunch(42) and m.should_relaunch(42, 0) and m.should_relaunch(42, None))
out("other values do not", not (m.should_relaunch(None) or m.should_relaunch(0) or m.should_relaunch(1) or m.should_relaunch(42, 1)))
out("relaunch argv", m.relaunch_cmd("C:\\x\\maude.exe", "C:\\p", "opus") == ["C:\\x\\maude.exe", "--model", "opus", "C:\\p"]
    and m.relaunch_cmd("e") == ["e"] and m.relaunch_cmd("e", "", "bad model!") == ["e"])
out("relaunch keeps --no-install", m.relaunch_cmd("e", "f", "", True) == ["e", "--no-install", "f"])

# version regexes are ASCII only; installed-vs-running decision
out("parse: non-ASCII digits are not versions", pv("v١.٢.٣") is None)
so = m.should_overwrite_installed
out("overwrite: newer/equal running replaces", so("v1.3.0", "v1.2.0") and so("v1.2.0", "v1.2.0"))
out("overwrite: older running never replaces newer installed", not so("v1.1.0", "v1.2.0"))
out("overwrite: dev never replaces a release", not so("dev", "v1.2.0") and not so("v1.2.0-rc1", "v1.2.0"))
out("overwrite: unknown/dev installed is replaced", so("v1.0.0", None) and so("v1.0.0", "dev") and so("dev", "dev"))
class FakeProc:
    """Writes its output to the stdout file it is given (as the real child does), like Popen."""
    def __init__(self, text="", hang=False):
        self.text, self.hang, self.pid, self.killed, self.waits = text, hang, 4321, False, 0
    def __call__(self, cmd, stdin=None, stdout=None, stderr=None, env=None):
        stdout.write(self.text.encode()); stdout.flush(); self.cmd, self.env = cmd, env
        return self
    def wait(self, timeout=None):
        self.waits += 1
        if self.hang and not self.killed: raise subprocess.TimeoutExpired("x", timeout)
        return 0
def _boom_popen(*a, **k): raise OSError("no")
fp = FakeProc("maude.exe v1.4.2\nUsage")
out("installed_version reads --version from a file, MAUDE_NO_INSTALL set", m.installed_version("x.exe", popen=fp) == "v1.4.2"
    and fp.cmd == ["x.exe", "--version"] and fp.env["MAUDE_NO_INSTALL"] == "1")
out("installed_version: junk/failure -> None", m.installed_version("x", popen=FakeProc("hello")) is None and m.installed_version("x", popen=_boom_popen) is None)
hp = FakeProc("", hang=True); killed = []
def _kill(p): killed.append(p.pid); p.killed = True
out("installed_version: timeout -> tree killed, None, no blocking wait", m.installed_version("x", popen=hp, timeout=0.1, kill=_kill) is None and killed == [4321])

# aside name taken by something that cannot be removed (a directory): next free name
t3 = tmp / "aside"; t3.mkdir()
(t3 / "maude.exe").write_bytes(b"A"); (t3 / "maude.exe.old-5").mkdir(); (t3 / "n").write_bytes(b"B")
a5 = m.swap_exe(str(t3 / "maude.exe"), str(t3 / "n"), 5)
out("swap: unique aside when .old-<pid> is stuck", a5 == str(t3 / "maude.exe") + ".old-5-1" and (t3 / "maude.exe").read_bytes() == b"B", a5)

# ── update_exe end to end with a fake network
body = b"MZ" + b"payload" * 100
sha = hashlib.sha256(body).hexdigest()
class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
def net(routes):
    seen = []
    def urlopen(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        seen.append(url)
        if url not in routes: raise OSError("no route " + url)
        return Resp(routes[url])
    urlopen.seen = seen
    return urlopen
tag = "v1.2.4"
routes = {m.RELEASE_API: json.dumps(rel(tag)).encode(), m.RELEASE_DL + tag + "/maude.exe": body,
          m.RELEASE_DL + tag + "/maude.exe.sha256": f"{sha}  maude.exe\n".encode()}
def fresh(name, content=b"OLDEXE"):
    p = tmp / name; p.mkdir(); e = p / "maude.exe"; e.write_bytes(content); return e
msgs = []
say = msgs.append
def reset(): m._staged.clear()
e = fresh("u1"); reset()
st = m.update_exe(say, "v1.2.3", str(e), net(routes), 99, running=str(e))
staged_path = e.parent / "maude.exe.update-99"
out("update: staged only, running exe untouched", st == "staged" and e.read_bytes() == b"OLDEXE" and staged_path.read_bytes() == body
    and sorted(os.listdir(e.parent)) == ["maude.exe", "maude.exe.update-99"] and m.staged_tag() == tag, (st, msgs))
nt = net(routes)
st2 = m.update_exe(say, "v1.2.3", str(e), nt, 99, running=str(e))
out("update: second press -> already updated, no network", st2 == "staged" and nt.seen == [] and "Already updated" in msgs[-1], msgs[-1])
m._stage_lock.acquire()
busy = m.update_exe(say, "v1.2.3", str(e), nt, 99, running=str(e))
m._stage_lock.release()
out("update: busy lock refuses a concurrent run", busy == "busy")
newexe = m.apply_staged(say, pid=99)
out("apply: new exe in place, old aside, staged state cleared", newexe == str(e) and e.read_bytes() == body
    and (e.parent / "maude.exe.old-99").read_bytes() == b"OLDEXE" and not m._staged and not staged_path.exists(), os.listdir(e.parent))
out("apply: nothing staged -> None", m.apply_staged(say) is None)
e = fresh("u1b"); reset(); m.update_exe(say, "v1.2.3", str(e), net(routes), 98, running=str(e))
(e.parent / "maude.exe.update-98").write_bytes(b"MZtampered"); msgs.clear()
out("apply: re-hash catches a changed staged file", m.apply_staged(say, pid=98) is None and e.read_bytes() == b"OLDEXE"
    and sorted(os.listdir(e.parent)) == ["maude.exe"] and "keeping the current" in msgs[-1], (msgs, os.listdir(e.parent)))
e = fresh("u1c"); reset(); m.update_exe(say, "v1.2.3", str(e), net(routes), 97, running=str(e)); msgs.clear()
def nope(a, b): raise PermissionError("locked")
out("apply: swap failure keeps old exe, reports", m.apply_staged(say, pid=97, replace=nope) is None and e.read_bytes() == b"OLDEXE"
    and "keeping the current" in msgs[-1], msgs)
e = fresh("d1"); reset(); m.update_exe(say, "v1.2.3", str(e), net(routes), 94, running=str(e))
m.discard_staged()
out("discard_staged: file and state gone", not m._staged and os.listdir(e.parent) == ["maude.exe"], os.listdir(e.parent))
# --no-install: the running (portable) exe is the target even when an installed exe exists
inst = fresh("ni_inst", b"INSTALLED"); port = fresh("ni_port", b"PORTABLE"); reset()
m._no_install = True
m.update_exe(say, "v1.2.3", str(inst), net(routes), 93, running=str(port))
out("update --no-install: stages next to the running exe, not the installed one", m._staged.get("target") == str(port)
    and (port.parent / "maude.exe.update-93").exists() and os.listdir(inst.parent) == ["maude.exe"], m._staged)
m._no_install = False; m.discard_staged()
e = fresh("u2"); msgs.clear(); reset()
st = m.update_exe(say, "v1.2.4", str(e), net(routes), 1, running=str(e))
out("update: same version -> current, untouched", st == "current" and e.read_bytes() == b"OLDEXE" and os.listdir(e.parent) == ["maude.exe"])
e = fresh("u3"); msgs.clear()
nt = net(routes)
st = m.update_exe(say, "dev", str(e), nt, 1, running=str(e))
out("update: dev build -> dev, no network", st == "dev" and nt.seen == [] and e.read_bytes() == b"OLDEXE" and "development build" in msgs[0], msgs)
st = m.update_exe(say, "v1.2.3-rc1", str(e), nt, 1, running=str(e))
out("update: pre-release VERSION counts as dev", st == "dev" and nt.seen == [])
e = fresh("u4"); msgs.clear()
bad = dict(routes); bad[m.RELEASE_DL + tag + "/maude.exe.sha256"] = (("0" * 64) + "  maude.exe\n").encode()
st = m.update_exe(say, "v1.2.3", str(e), net(bad), 1, running=str(e))
out("update: sha mismatch -> failed, exe and dir untouched", st == "failed" and e.read_bytes() == b"OLDEXE" and os.listdir(e.parent) == ["maude.exe"]
    and "checksum mismatch" in " ".join(msgs) and not m._staged, (st, msgs, os.listdir(e.parent)))
e = fresh("u5"); msgs.clear()
nosha = dict(routes); nosha[m.RELEASE_DL + tag + "/maude.exe.sha256"] = b"garbage\n"
st = m.update_exe(say, "v1.2.3", str(e), net(nosha), 1, running=str(e))
out("update: no checksum line -> failed", st == "failed" and e.read_bytes() == b"OLDEXE")
e = fresh("u6"); msgs.clear()
notexe = dict(routes); notexe[m.RELEASE_DL + tag + "/maude.exe"] = b"<html>"
notexe[m.RELEASE_DL + tag + "/maude.exe.sha256"] = (hashlib.sha256(b"<html>").hexdigest() + "  maude.exe").encode()
st = m.update_exe(say, "v1.2.3", str(e), net(notexe), 1, running=str(e))
out("update: not an exe (no MZ) -> failed, cleaned", st == "failed" and os.listdir(e.parent) == ["maude.exe"])
e = fresh("u7"); msgs.clear()
st = m.update_exe(say, "v1.2.3", str(e), net({}), 1, running=str(e))
out("update: network error -> failed, no raise", st == "failed" and e.read_bytes() == b"OLDEXE")
import urllib.error
def rate(req, timeout=0): raise urllib.error.HTTPError("u", 403, "forbidden", {}, None)
msgs.clear()
out("update: HTTP 403 -> rate limit message", m.update_exe(say, "v1.2.3", str(e), rate, 1, running=str(e)) == "failed" and "rate limit" in msgs[-1], msgs)
st = m.update_exe(say, "v1.2.3", urlopen=net({}))
out("update: not a frozen exe -> skipped", st == "skipped")

# run_tui: exit value 42 -> swap a staged exe, then _relaunch(folder, model, exe); other values never relaunch
fake = types.ModuleType("maude")
rv_box = [42]
class A:
    return_code = 0
    def __init__(self, select="", model=""): pass
    def run(self): return rv_box[0]
fake.MaudeApp = A
sys.modules["maude"] = fake
rl = []
m._relaunch = lambda f, mo, exe=None: (rl.append((f, mo, exe)), 0)[1]
rc42 = m.run_tui(str(tmp), "opus")
rv_box[0] = None
rc0 = m.run_tui(str(tmp), "opus")
out("run_tui: 42 relaunches once with folder+model", rc42 == 0 and rc0 == 0 and rl == [(str(tmp), "opus", None)], rl)
rv_box[0] = 0; rl.clear()
e = fresh("rt0"); reset(); m.update_exe(say, "v1.2.3", str(e), net(routes), 92, running=str(e))
rc_plain = m.run_tui(str(tmp), "opus")
out("run_tui: exit without Reload deletes the staged file", rc_plain == 0 and not m._staged and rl == [] and os.listdir(e.parent) == ["maude.exe"], os.listdir(e.parent))
rv_box[0] = 42; rl.clear()
e = fresh("rt1"); reset(); m.update_exe(say, "v1.2.3", str(e), net(routes), 96, running=str(e))
rcs = m.run_tui(str(tmp), "opus")
out("run_tui: 42 + staged -> swap, then relaunch of that exe", rcs == 0 and e.read_bytes() == body and rl == [(str(tmp), "opus", str(e))], (rl, e.read_bytes()[:4]))
rl.clear(); e = fresh("rt2"); reset(); m.update_exe(say, "v1.2.3", str(e), net(routes), 95, running=str(e))
(e.parent / "maude.exe.update-95").write_bytes(b"MZbad")
import contextlib
errbuf = io.StringIO()
with contextlib.redirect_stderr(errbuf):
    rcf = m.run_tui(str(tmp), "opus")
out("run_tui: swap failure is reported on stderr", "keeping the current" in errbuf.getvalue(), errbuf.getvalue())
out("run_tui: swap failure -> rc 1, old exe kept, no relaunch", rcf == 1 and e.read_bytes() == b"OLDEXE" and rl == [], (rcf, rl))
reset()
del sys.modules["maude"]
import importlib; importlib.reload(m)
launched = []
out("_relaunch: not frozen -> nothing started", m._relaunch("", "", popen=lambda *a, **k: launched.append(a)) == 0 and launched == [])
m._no_install = True
sys.frozen = True
class P:
    def __init__(self, cmd): launched.append(cmd)
    def wait(self): return 7
out("_relaunch: frozen -> starts exe with --no-install, returns its rc", m._relaunch("F", "opus", "X.exe", popen=P) == 7
    and launched == [["X.exe", "--no-install", "--model", "opus", "F"]], launched)
del sys.frozen; m._no_install = False

# ── maude.py parts, extracted without importing textual
src = (light / "maude.py").read_text(encoding="utf-8")
tree = ast.parse(src, feature_version=(3, 9))
want = {"native_update_steps", "stream_cmd", "fetch_maude_py", "native_update", "valid_update", "UPDATE_URL"}
ns = {"re": re, "subprocess": subprocess, "time": time, "urllib": urllib, "Path": Path, "os": os, "sys": sys}
for n in tree.body:
    nm = n.name if isinstance(n, (ast.FunctionDef, ast.ClassDef)) else \
        n.targets[0].id if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) else None
    if nm in want:
        exec(compile(ast.Module([n], []), "maude.py", "exec", flags=__import__("__future__").annotations.compiler_flag), ns)
S = ns["native_update_steps"]
out("steps windows+sandbox", [s[0] for s in S("windows", True, "c")] == ["exe", "cmd", "cmd"]
    and S("windows", True, "c")[2][2] == ["wsl.exe", "-d", "Maude", "--", "bash", "-lc", "maude update"])
out("steps windows no sandbox/claude", [s[0] for s in S("windows", False, None)] == ["exe"])
out("steps macos: fetch + claude, never wsl", [s[0] for s in S("macos", True, "c")] == ["macpy", "cmd"] and S("macos", True, "c")[1][2] == ["c", "update"])

good = b"#!/usr/bin/env python3\n" + b"# pad\n" * 2000 + b"def maybe_self_update():\n    pass\n\nif __name__ == \"__main__\":\n    pass\n"
class UL:
    def __init__(self, data): self.data = data; self.urls = []
    def __call__(self, url, timeout=0): self.urls.append(url); return Resp(self.data)
F = ns["fetch_maude_py"]
t = tmp / "mac"; t.mkdir(); target = t / "maude.py"; target.write_bytes(b"old")
msgs.clear(); u = UL(good)
out("mac fetch: updated", F(target, say, u, lambda p: "") == "updated" and target.read_bytes() == good
    and sorted(os.listdir(t)) == ["maude.py"] and u.urls[0].startswith(ns["UPDATE_URL"] + "?cache="), msgs)
out("mac fetch: identical -> current", F(target, say, UL(good), lambda p: "") == "current")
target.write_bytes(b"old")
out("mac fetch: failed import check keeps old", F(target, say, UL(good), lambda p: "boom") == "failed" and target.read_bytes() == b"old"
    and sorted(os.listdir(t)) == ["maude.py"])
out("mac fetch: html page rejected", F(target, say, UL(b"<html>" * 5000), lambda p: "") == "failed" and target.read_bytes() == b"old")
out("mac fetch: syntax error rejected", F(target, say, UL(good + b"def (:\n"), lambda p: "") == "failed" and target.read_bytes() == b"old")
gitroot = tmp / "co"; (gitroot / ".git").mkdir(parents=True); (gitroot / "light").mkdir()
gt = gitroot / "light" / "maude.py"; gt.write_bytes(b"old")
out("mac fetch: refuses inside a git checkout", F(gt, say, UL(good), lambda p: "") == "skipped" and gt.read_bytes() == b"old")
def boom(url, timeout=0): raise OSError("offline")
out("mac fetch: offline -> failed, no raise", F(target, say, boom, lambda p: "") == "failed")

lines = []
rc = ns["stream_cmd"]([sys.executable, "-c", "print('\\x1b[31mred\\x1b[0m'); print('b')"], lines.append)
out("stream_cmd lines + rc", rc == 0 and lines == ["red", "b"], lines)
lines.clear()
out("stream_cmd missing program", ns["stream_cmd"](["/nonexistent/prog"], lines.append) == -1 and "could not start" in lines[0])

calls = []
mx = types.SimpleNamespace(update_exe=lambda say: (calls.append("exe"), say("exe step"), "failed")[2], staged_tag=lambda: None)
ns.update(PLATFORM="windows", HAS_SANDBOX=True, plat_tool=lambda n: "C:\\claude.exe", _child_env=lambda: None, mx=mx)
envs = {}
def fake_stream(cmd, say, env=None):
    calls.append(cmd[0]); envs[cmd[0]] = env; say("out " + cmd[0]); return 0 if cmd[0] != "wsl.exe" else 3
msgs.clear()
errs, kind = ns["native_update"](say, fake_stream)
out("native_update: order and error count", calls == ["exe", "C:\\claude.exe", "wsl.exe"] and errs == 2 and "out wsl.exe" in msgs and kind == "", (calls, errs, kind))
out("native_update: wsl.exe gets WSL_UTF8=1, claude does not", envs["wsl.exe"]["WSL_UTF8"] == "1" and envs["C:\\claude.exe"] is None)
mx.update_exe = lambda say: "staged"; mx.staged_tag = lambda: "v9.9.9"
out("native_update: staged exe -> reload is a must", ns["native_update"](say, lambda *a, **k: 0)[1] == "must")
PY
res=$(python3 -I "$d/run.py" "${SCRIPT_DIR}/../light" "$d" 2>&1) || true
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    if [[ "$line" == *$'\t'* ]]; then
        assert_eq "${line%%$'\t'*}" "ok" "${line#*$'\t'}"
    else
        assert_eq "python crashed: $line" "ok" "crash"
    fi
done <<< "$res"
suite_summary
