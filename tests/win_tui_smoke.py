"""Headless check of the Windows mode of light/maude.py. Run: python tests/win_tui_smoke.py
Needs textual. On Windows it takes the real import path; on Linux it forces Windows mode
after import (so it can run anywhere). Uses a fake USERPROFILE with a fake share. No window opens."""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LIGHT = Path(__file__).resolve().parent.parent / "light"
sys.path.insert(0, str(LIGHT))
d = tempfile.mkdtemp()
try:
    home, share = Path(d) / "home", Path(d) / "share"
    proj = share / "Projects" / "demo"
    proj.mkdir(parents=True)
    home.mkdir()
    os.environ.update(USERPROFILE=str(home), HOME=str(home), LOCALAPPDATA=str(Path(d) / "lad"))
    import maude_exe as mx
    mx.fstab_share = lambda: None            # no wsl.exe call
    mx.share_root = lambda *a, **k: str(share)
    out1, out2, gone = Path(d) / "out" / "alpha", Path(d) / "out2" / "demo", Path(d) / "gone"
    out1.mkdir(parents=True); out2.mkdir(parents=True)  # out2 has the same basename as the share project
    (out1 / "f.txt").write_text("x")
    os.utime(out1, (1, 1)); os.utime(out1 / "f.txt", (5000, 5000))  # newest content decides
    os.utime(out2, (9000, 9000))
    os.utime(share / "Projects" / "demo", (99999, 99999))
    reg = [str(gone), str(out1), str(out2), str(out1) + os.sep, str(share / "Projects" / "demo") + os.sep, str(share / "Projects" / "demo" / "sub")]  # fake registry: one folder is missing
    mx.read_projects = lambda: list(reg)
    mx.remove_project = lambda f: reg.remove(f)
    import maude
    if os.name != "nt":                      # Linux: force Windows mode for the checks below
        maude.IS_WIN, maude.mx = True, mx
        maude.PROJECTS_DIR = share / "Projects"
    assert maude.IS_WIN and Path(maude.PROJECTS_DIR).name == "Projects", maude.PROJECTS_DIR
    proj = Path(maude.PROJECTS_DIR) / "demo"  # realpath()ed on Windows (8.3 temp names)

    async def check():
        async with maude.MaudeApp().run_test(size=(140, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            table = app.query_one("#projects-table")
            assert str(table.columns[list(table.columns)[2]].label) == "Features"
            cells = [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]
            # outside first (newest content first), then share; missing folder hidden; same names get a parent
            assert [c[0].strip() for c in cells] == ["demo (out2)", "alpha", "demo (Projects)"], cells
            assert [c[2] for c in cells] == ["No Sandbox"] * 2 + ["·"], cells
            logo = app.query_one("#logo")
            assert logo.size.height >= 6, logo.size
            from textual.geometry import Region
            shown = "".join(s.text for s in logo.render_lines(Region(0, 0, logo.size.width, logo.size.height))[5]) if hasattr(logo, "render_lines") else ""
            assert maude.WIN_CAPTION in shown, shown
            table.move_cursor(row=0)
            app.action_delete_selected(); await pilot.pause()
            from textual.widgets import Label
            assert "folder is not touched" in str(app.screen.query_one("#confirm-sub", Label).render())
            app.screen.query_one("#btn-yes").press(); await pilot.pause()
            assert str(out2) not in reg and str(out1) in reg and out2.is_dir(), reg  # registry entry only
            assert table.row_count == 2
            table.move_cursor(row=1)  # share project: still soft-deleted
            assert maude.IS_WIN and not maude.ConfirmDeleteScreen("x").outside
            for i in maude.WIN_HIDDEN:
                assert not app.query_one(i).display, i
            for i in ("#btn-open", "#btn-new", "#btn-cli", "#fresh-context", "#perplexity"):
                assert app.query_one(i).display, i
            # Radio coupling: fable then Crush must leave exactly one button on per set (opus, Crush).
            from textual.widgets import RadioButton
            rb = lambda sel, n: app.query_one(sel).query(RadioButton)[n]
            await pilot.click(rb("#model-select", 0)); await pilot.pause()
            await pilot.click(rb("#tool-select", 3)); await pilot.pause()
            on = {sel: [str(b.label) for b in app.query_one(sel).query(RadioButton) if b.value]
                  for sel in ("#model-select", "#tool-select")}
            assert on == {"#model-select": ["opus"], "#tool-select": ["Crush"]}, on
            exited = []
            app.exit = lambda *a, **k: exited.append(1)
            app.query_one("#btn-cli").press()
            await pilot.pause()
            assert exited  # Command Line returns to the PowerShell that started maude.exe

    asyncio.run(check())

    # Open project: native claude, continue first, no bypass flag, cwd = project.
    calls = []
    mx.find_claude = lambda up: "C:/c.exe"
    mx._run = lambda cmd, **kw: calls.append((cmd, kw.get("cwd"))) or 0
    cwd0 = os.getcwd()
    maude.open_project(proj, "sonnet", fresh=False)
    assert os.getcwd() == cwd0  # TUI must not hold the project folder
    maude.open_project(proj, "sonnet", fresh=True)
    assert calls == [(["C:/c.exe", "--model", "sonnet", "--continue"], str(proj)),
                     (["C:/c.exe", "--model", "sonnet"], str(proj))], calls
    assert not any("skip" in a or "bypass" in a for c, _ in calls for a in c)
    print("win_tui_smoke ok")
finally:
    os.chdir(LIGHT)
    shutil.rmtree(d, ignore_errors=True)
