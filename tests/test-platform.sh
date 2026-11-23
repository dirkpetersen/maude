#!/usr/bin/env bash
# tests/test-platform.sh - platform layer of light/maude.py (PLATFORM, feature map, outside-project
# JSON, fast .git/config reader). Pulls only the needed names out of maude.py (no textual needed).
set -o errexit -o nounset -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
suite_header "maude.py platform layer"

MAUDE_PY="${SCRIPT_DIR}/../light/maude.py"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

# ── source-level checks ────────────────────────────────────────────────
assert_eq "line 1 is the shebang" "#!/usr/bin/env python3" "$(head -n 1 "$MAUDE_PY" | tr -d '\r')"
assert_exit_zero "has from __future__ import annotations" grep -q '^from __future__ import annotations' "$MAUDE_PY"
first_import=$(grep -n -m1 -E '^(import|from) ' "$MAUDE_PY" | cut -d: -f1)
future_line=$(grep -n -m1 '^from __future__ import annotations' "$MAUDE_PY" | cut -d: -f1)
assert_eq "__future__ import is the first import" "$first_import" "$future_line"
assert_exit_zero "self-update marker: def maybe_self_update" grep -q 'def maybe_self_update' "$MAUDE_PY"
assert_exit_zero "self-update marker: column-0 __main__ guard" grep -qE '^if __name__ == "__main__":' "$MAUDE_PY"

cat > "$d/run.py" <<'PY'
import ast, json, os, re, shutil, subprocess, sys, types
from pathlib import Path

src_path, tmp = sys.argv[1], Path(sys.argv[2])
src = open(src_path, encoding="utf-8").read()
tree = ast.parse(src, feature_version=(3, 9))  # no syntax newer than 3.9
want = {"FROZEN", "PLATFORM", "IS_WIN", "IS_MAC", "SANDBOXED", "NATIVE", "FEATURES", "FEATURE_WIDGETS",
        "current_platform", "feature_on", "hidden_widgets", "_outside_json", "read_outside_json",
        "_write_outside_json", "add_outside_json", "remove_outside_json", "_GIT_SECTION",
        "_git_config_value", "parse_git_origin_url", "_git_config_path", "git_origin_from_config",
        "is_github_remote", "_load_outside_json", "_update_outside_json", "_read_small", "_SMALL_FILE_MAX",
        "_pkey", "_outside_share"}

def load(platform):
    import stat, tempfile
    ns = {"os": os, "re": re, "json": json, "shutil": shutil, "subprocess": subprocess, "Path": Path,
          "stat": stat, "tempfile": tempfile, "sys": types.SimpleNamespace(platform=platform)}
    for n in tree.body:
        names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
        if names & want:
            exec(compile(ast.Module([n], []), "maude.py", "exec"), ns)
    return ns

def out(k, v):
    print(f"{k}={v}")

for plat, sysname in (("win32", "win"), ("darwin", "mac"), ("linux", "lin"), ("freebsd", "bsd")):
    ns = load(plat)
    out(f"{sysname}.platform", ns["PLATFORM"])
    out(f"{sysname}.flags", ",".join(str(ns[k]) for k in ("IS_WIN", "IS_MAC", "SANDBOXED", "NATIVE")))
    out(f"{sysname}.hidden", ",".join(sorted(ns["hidden_widgets"]())))
    out(f"{sysname}.frozen", ns["FROZEN"])

ns = load("win32")
F = ns["FEATURES"]
out("features.names", ",".join(sorted(F)))
out("features.shape", all(set(v) == {"linux-sandbox", "windows", "macos"} for v in F.values()))
out("features.widgets", sorted(ns["FEATURE_WIDGETS"]) == sorted(F))
out("web.autostart.off", all(not F[f][p] for f in ("web", "autostart") for p in ("windows", "macos")))
out("linux.all.on", all(v["linux-sandbox"] for v in F.values()))

# outside-project JSON (macOS)
ns = load("darwin")
jf = tmp / "sub" / "projects.json"
ns["add_outside_json"]("/a/b", jf); ns["add_outside_json"]("/c", jf); ns["add_outside_json"]("/a/b", jf)
out("json.add", ns["read_outside_json"](jf))
ns["remove_outside_json"]("/a/b", jf)
out("json.remove", ns["read_outside_json"](jf))
jf.write_text("not json"); out("json.bad", ns["read_outside_json"](jf))
jf.write_text('{"x": 1}'); out("json.notlist", ns["read_outside_json"](jf))
out("json.missing", ns["read_outside_json"](tmp / "nope.json"))

# fast .git/config reader
ns = load("linux")
P, G = ns["parse_git_origin_url"], ns["git_origin_from_config"]
cases = {
    "plain": '[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n\turl = git@github.com:o/r.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
    "tight": '[remote "origin"]\nurl=https://github.com/o/r\n',
    "spaces": '  [ remote   "origin" ]  \n   URL   =    https://example.com/x.git   \n',
    "other-remote": '[remote "up"]\n\turl = https://github.com/u/u\n[remote "origin"]\n\turl = https://gitlab.com/o/r\n',
    "only-other": '[remote "up"]\n\turl = https://github.com/u/u\n',
    "none": '[core]\n\tbare = false\n',
    "comment": '[remote "origin"]\n\t# url = bad\n\turl = https://h/x ; trailing\n',
    "quoted": '[remote "origin"]\n\turl = "https://h/a b#c"\n',
    "last-wins": '[remote "origin"]\n\turl = https://h/1\n\turl = https://h/2\n',
    "inline": '[remote "origin"] url = https://h/inline\n',
    "old-syntax": '[remote.origin]\n\turl = https://h/old\n',
    "after-section": '[remote "origin"]\n\tfetch = x\n[branch "main"]\n\turl = https://wrong\n',
    "include": '[include]\n\tpath = other\n[remote "origin"]\n\turl = https://h/x\n',
    "cr-comment": '[remote "origin"]\n\turl = https://h/real\n# c \r\turl = https://evil\n',
    "sep-comment": '[remote "origin"]\n\turl = https://h/real\n# c \x1c\turl = https://evil\n# d \u2028 url = https://evil2\n# e \x85 url = https://evil3\n',
    "crlf": '[remote "origin"]\r\n\turl = https://h/crlf\r\n',
}
for k, v in cases.items():
    out("parse." + k, repr(P(v)))
out("github.ssh", ns["is_github_remote"](P(cases["plain"])))
out("github.gitlab", ns["is_github_remote"](P(cases["other-remote"])))

def mk(rel, text):
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p

# normal repo
mk("r1/.git/config", cases["plain"]); out("repo.dir", G(tmp / "r1"))
# .git file -> relative gitdir (submodule style)
mk("r2/.git", "gitdir: ../meta/modules/r2\n"); mk("meta/modules/r2/config", cases["tight"])
out("repo.gitdir.rel", G(tmp / "r2"))
# .git file -> absolute gitdir
mk("r3/.git", f"gitdir: {tmp / 'abs' / 'g'}\n"); mk("abs/g/config", cases["spaces"])
out("repo.gitdir.abs", G(tmp / "r3"))
# worktree: gitdir has commondir -> main repo's .git (config lives there)
mk("main/.git/config", cases["plain"]); mk("main/.git/worktrees/w/commondir", "../..\n")
mk("wt/.git", f"gitdir: {tmp / 'main' / '.git' / 'worktrees' / 'w'}\n")
out("repo.worktree", G(tmp / "wt"))
# unparseable -> None (caller falls back to git)
mk("r4/.git", "garbage\n"); out("repo.badfile", G(tmp / "r4"))
mk("r5/.git/config", cases["include"]); out("repo.include", G(tmp / "r5"))
(tmp / "r6" / ".git").mkdir(parents=True); out("repo.noconfig", G(tmp / "r6"))
mk("r7/.git", "gitdir: ../nonexistent\n"); out("repo.dangling", G(tmp / "r7"))

# hostile inputs: none may raise or block (a SIGALRM kills the run, so a hang shows as a failure)
import signal
signal.alarm(20)
mk("n1/.git", "gitdir: a\0b\n"); out("hostile.nul.gitfile", G(tmp / "n1"))
mk("n2/g/commondir", "x\0y\n"); mk("n2/.git", f"gitdir: {tmp / 'n2' / 'g'}\n"); out("hostile.nul.commondir", G(tmp / "n2"))
mk("n3/.git/config", "[remote \"origin\"]\n\turl = a\0b\n"); out("hostile.nul.config", repr(G(tmp / "n3")))
if hasattr(os, "mkfifo"):
    (tmp / "f1").mkdir(); os.mkfifo(tmp / "f1" / ".git"); out("hostile.fifo.dotgit", G(tmp / "f1"))
    (tmp / "f2" / ".git").mkdir(parents=True); os.mkfifo(tmp / "f2" / ".git" / "config"); out("hostile.fifo.config", G(tmp / "f2"))
    mk("f3/g/x", ""); os.mkfifo(tmp / "f3" / "g" / "commondir"); mk("f3/.git", f"gitdir: {tmp / 'f3' / 'g'}\n"); out("hostile.fifo.commondir", G(tmp / "f3"))
else:
    for k in ("dotgit", "config", "commondir"):
        out("hostile.fifo." + k, None)
mk("b1/.git/config", "[remote \"origin\"]\n\turl = https://h/x\n" + "# pad\n" * 300000)  # > 1 MB
out("hostile.big.config", G(tmp / "b1"))
mk("b2/.git", "gitdir: x\n" + "y" * 2_000_000); out("hostile.big.gitfile", G(tmp / "b2"))
if os.path.exists("/dev/zero"):
    (tmp / "z" / ".git").mkdir(parents=True); os.symlink("/dev/zero", tmp / "z" / ".git" / "config"); out("hostile.devzero", G(tmp / "z"))
else:
    out("hostile.devzero", None)
signal.alarm(0)

# parser differences vs git
out("bom.text", repr(P("\ufeff" + cases["tight"])))
(tmp / "bom" / ".git").mkdir(parents=True)
(tmp / "bom" / ".git" / "config").write_bytes(b"\xef\xbb\xbf" + cases["plain"].encode())
out("bom.file", G(tmp / "bom"))
out("cont.url", repr(P('[remote "origin"]\n\turl = https://h/a\\\nb\n')))
out("cont.elsewhere", repr(P('[alias]\n\tx = !echo a \\\n b\n[remote "origin"]\n\turl = https://h/x\n')))

# macOS: case-insensitive keys
nm = load("darwin")
out("mac.pkey", nm["_pkey"]("/Users/X/Maude/Projects/Foo") == nm["_pkey"]("/users/x/maude/projects/foo"))
out("mac.inshare", nm["_outside_share"]("/Users/X/MAUDE/Projects/a", "/users/x/Maude"))
out("mac.outshare", nm["_outside_share"]("/Users/x/Other", "/Users/x/Maude"))

# macOS JSON: unparseable file kept as .bad, 0600, no temp leftovers, deep nesting, concurrent writers
jd = tmp / "j"; jd.mkdir()
jb = jd / "projects.json"; jb.write_text("{broken")
nm["add_outside_json"]("/p", jb)
out("jsonx.bad.copy", (jd / "projects.json.bad").read_text())
out("jsonx.bad.new", nm["read_outside_json"](jb))
out("jsonx.mode", oct(jb.stat().st_mode & 0o777) if os.name != "nt" else "0o600")
out("jsonx.leftover", sorted(p.name for p in jd.iterdir() if p.name.endswith(".tmp")))
jb.write_text("["*100000); out("jsonx.deep", nm["read_outside_json"](jb))
jb.write_text("{broken"); nm["add_outside_json"]("/q", jb)
out("jsonx.bad.second", sorted(p.name for p in jd.iterdir() if ".bad" in p.name))
jc = jd / "conc.json"
if hasattr(os, "fork"):
    pids = []
    for i in range(8):
        pid = os.fork()
        if pid == 0:
            try:
                for j in range(5):
                    nm["add_outside_json"](f"/c/{i}/{j}", jc)
            finally:
                os._exit(0)
        pids.append(pid)
    for pid in pids:
        os.waitpid(pid, 0)
    out("jsonx.conc", len(nm["read_outside_json"](jc)))
else:
    out("jsonx.conc", 40)

# Python 3.9: `X | Y` evaluated at runtime (class bases, call args, defaults, assignments) fails on 3.9.
# Annotations are fine (from __future__ import annotations), so skip those subtrees.
skip = set()
for n in ast.walk(tree):
    anns = []
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.returns is not None:
        anns.append(n.returns)
    if isinstance(n, ast.arg) and n.annotation is not None:
        anns.append(n.annotation)
    if isinstance(n, ast.AnnAssign):
        anns.append(n.annotation)
    for a in anns:
        skip.update(id(x) for x in ast.walk(a))
def typelike(x):  # names, attributes, subscripts or None: how `A | B` types look (not int flags / set unions)
    return isinstance(x, (ast.Name, ast.Attribute, ast.Subscript)) or (isinstance(x, ast.Constant) and x.value is None)
bad = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr)
       and id(n) not in skip and typelike(n.left) and typelike(n.right)]
out("py39.bitor", bad)
PY

mkdir "$d/work"
python3 -I "$d/run.py" "$MAUDE_PY" "$d/work" > "$d/out.txt" 2>"$d/err.txt" || { cat "$d/err.txt"; FAIL=$((FAIL+1)); }
get() { grep -m1 "^$1=" "$d/out.txt" | cut -d= -f2-; }

# ── PLATFORM from sys.platform ─────────────────────────────────────────
assert_eq "win32 -> windows"          "windows"       "$(get win.platform)"
assert_eq "darwin -> macos"           "macos"         "$(get mac.platform)"
assert_eq "linux -> linux-sandbox"    "linux-sandbox" "$(get lin.platform)"
assert_eq "other unix -> linux-sandbox" "linux-sandbox" "$(get bsd.platform)"
assert_eq "windows flags"  "True,False,False,True"  "$(get win.flags)"
assert_eq "macos flags"    "False,True,False,True"  "$(get mac.flags)"
assert_eq "linux flags"    "False,False,True,False" "$(get lin.flags)"
assert_eq "not frozen when run as a script" "False" "$(get lin.frozen)"

# ── feature map ────────────────────────────────────────────────────────
all_hidden="#autostart,#btn-creds,#btn-github,#btn-togithub,#btn-update,#btn-web"
assert_eq "feature names" "autostart,creds,github,togithub,update,web" "$(get features.names)"
assert_eq "every feature has all three platforms" "True" "$(get features.shape)"
assert_eq "every feature has a widget"            "True" "$(get features.widgets)"
assert_eq "linux: nothing hidden"                 ""     "$(get lin.hidden)"
assert_eq "linux: all features on"                "True" "$(get linux.all.on)"
assert_eq "windows hides the sandbox-only buttons" "$all_hidden" "$(get win.hidden)"
assert_eq "macOS hides the same buttons as Windows" "$(get win.hidden)" "$(get mac.hidden)"
assert_eq "web and autostart off on Windows and macOS" "True" "$(get web.autostart.off)"

# ── outside projects JSON (macOS) ──────────────────────────────────────
assert_eq "json: add is idempotent"  "['/a/b', '/c']" "$(get json.add)"
assert_eq "json: remove"             "['/c']"         "$(get json.remove)"
assert_eq "json: bad file -> []"     "[]"             "$(get json.bad)"
assert_eq "json: not a list -> []"   "[]"             "$(get json.notlist)"
assert_eq "json: missing -> []"      "[]"             "$(get json.missing)"

# ── fast .git/config parser ────────────────────────────────────────────
assert_eq "parse: plain"            "'git@github.com:o/r.git'" "$(get parse.plain)"
assert_eq "parse: no spaces"        "'https://github.com/o/r'" "$(get parse.tight)"
assert_eq "parse: odd spacing/case" "'https://example.com/x.git'" "$(get parse.spaces)"
assert_eq "parse: picks origin"     "'https://gitlab.com/o/r'" "$(get parse.other-remote)"
assert_eq "parse: only other remote" "''" "$(get parse.only-other)"
assert_eq "parse: no remote"        "''" "$(get parse.none)"
assert_eq "parse: comments"         "'https://h/x'" "$(get parse.comment)"
assert_eq "parse: quoted value"     "'https://h/a b#c'" "$(get parse.quoted)"
assert_eq "parse: last url wins"    "'https://h/2'" "$(get parse.last-wins)"
assert_eq "parse: same-line url"    "'https://h/inline'" "$(get parse.inline)"
assert_eq "parse: [remote.origin]"  "'https://h/old'" "$(get parse.old-syntax)"
assert_eq "parse: key in other section ignored" "''" "$(get parse.after-section)"
assert_eq "parse: [include] -> fall back" "None" "$(get parse.include)"
assert_eq "parse: CR in a comment is not a line break" "'https://h/real'" "$(get parse.cr-comment)"
assert_eq "parse: FS/U+2028/NEL in a comment are not line breaks" "'https://h/real'" "$(get parse.sep-comment)"
assert_eq "parse: CRLF"             "'https://h/crlf'" "$(get parse.crlf)"
assert_eq "github: ssh remote"      "True"  "$(get github.ssh)"
assert_eq "github: gitlab remote"   "False" "$(get github.gitlab)"
assert_eq "repo: .git dir"          "git@github.com:o/r.git" "$(get repo.dir)"
assert_eq "repo: gitdir file (relative)" "https://github.com/o/r" "$(get repo.gitdir.rel)"
assert_eq "repo: gitdir file (absolute)" "https://example.com/x.git" "$(get repo.gitdir.abs)"
assert_eq "repo: worktree follows commondir" "git@github.com:o/r.git" "$(get repo.worktree)"
assert_eq "repo: garbage .git file -> fall back" "None" "$(get repo.badfile)"
assert_eq "repo: include -> fall back" "None" "$(get repo.include)"
assert_eq "repo: missing config -> fall back" "None" "$(get repo.noconfig)"
assert_eq "repo: dangling gitdir -> fall back" "None" "$(get repo.dangling)"

assert_eq "NUL in .git file -> fall back"      "None" "$(get hostile.nul.gitfile)"
assert_eq "NUL in commondir -> fall back"      "None" "$(get hostile.nul.commondir)"
assert_eq "NUL in config -> no crash"          "None" "$(get hostile.nul.config)"
assert_eq "FIFO as .git -> fall back"          "None" "$(get hostile.fifo.dotgit)"
assert_eq "FIFO as .git/config -> fall back"   "None" "$(get hostile.fifo.config)"
assert_eq "FIFO as commondir -> fall back"     "None" "$(get hostile.fifo.commondir)"
assert_eq "oversized config -> fall back"      "None" "$(get hostile.big.config)"
assert_eq "oversized .git file -> fall back"   "None" "$(get hostile.big.gitfile)"
assert_eq "/dev/zero as config -> fall back"   "None" "$(get hostile.devzero)"
assert_eq "BOM before first header (text)"     "'https://github.com/o/r'" "$(get bom.text)"
assert_eq "BOM before first header (file)"     "git@github.com:o/r.git" "$(get bom.file)"
assert_eq "continuation inside remote -> fall back" "None" "$(get cont.url)"
assert_eq "continuation anywhere -> fall back" "None" "$(get cont.elsewhere)"
assert_eq "macOS keys casefold"                "True"  "$(get mac.pkey)"
assert_eq "macOS share check casefolds (inside)" "False" "$(get mac.inshare)"
assert_eq "macOS share check (outside)"        "True"  "$(get mac.outshare)"
assert_eq "json: unparseable file kept as .bad" "{broken" "$(get jsonx.bad.copy)"
assert_eq "json: new file written after backup" "['/p']"  "$(get jsonx.bad.new)"
assert_eq "json: file mode 0600"               "0o600" "$(get jsonx.mode)"
assert_eq "json: no temp files left"           "[]"    "$(get jsonx.leftover)"
assert_eq "json: deep nesting -> []"           "[]"    "$(get jsonx.deep)"
assert_eq "json: second .bad copy is unique"   "['projects.json.bad', 'projects.json.bad.1']" "$(get jsonx.bad.second)"
assert_eq "json: 8 concurrent writers lose nothing" "40" "$(get jsonx.conc)"
assert_eq "no runtime 'X | Y' (Python 3.9)"    "[]"    "$(get py39.bitor)"

suite_summary
