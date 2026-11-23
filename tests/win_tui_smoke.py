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
    mx.read_share = lambda: None             # no real registry
    mx.store_share = lambda s: None
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
    mx.lxss_names = lambda: ["Ubuntu", "maude"]  # fake WSL registry: the Maude distro is installed
    import maude
    assert maude.HAS_SANDBOX is True
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
            assert [c[0].strip() for c in cells][:2] == ["demo (out2)", "alpha"] and cells[3][0].strip() == "demo (Projects)", cells
            assert set(cells[2][0].strip()) == {"─"} and list(table.rows)[2].value == "__sep__", cells
            assert [c[2] for c in cells] == ["No Sandbox"] * 2 + [cells[2][2], "·"], cells
            table.move_cursor(row=1); await pilot.pause()
            await pilot.press("down"); await pilot.pause()
            assert table.cursor_row == 3, table.cursor_row   # skips the rule: first share project
            await pilot.press("up"); await pilot.pause()
            assert table.cursor_row == 1, table.cursor_row   # last outside project
            from textual.coordinate import Coordinate
            table.cursor_coordinate = Coordinate(2, 0)       # cursor forced onto the rule (message not yet handled)
            assert app._selected_project() is None
            app.action_delete_selected(); await pilot.pause()
            assert len(app.screen_stack) == 1, "delete on the rule must open nothing"
            await pilot.pause()
            assert table.cursor_row != 2, table.cursor_row
            logo = app.query_one("#logo")
            assert logo.size.height >= 6, logo.size
            from textual.geometry import Region
            shown = "".join(s.text for s in logo.render_lines(Region(0, 0, logo.size.width, logo.size.height))[5]) if hasattr(logo, "render_lines") else ""
            assert maude.WIN_CAPTION in shown, shown
            table.move_cursor(row=0)
            app.action_delete_selected(); await pilot.pause()
            from textual.widgets import Label
            assert "folder is not deleted" in str(app.screen.query_one("#confirm-sub", Label).render())
            app.screen.query_one("#btn-yes").press(); await pilot.pause()
            assert str(out2) not in reg and str(out1) in reg and out2.is_dir(), reg  # registry entry only
            for _ in range(40):  # notify() is delivered as a message: wait for it on slow runners
                if any(str(out2) in n.message and "manually" in n.message for n in app._notifications):
                    break
                await pilot.pause(0.05)
            assert any(str(out2) in n.message and "manually" in n.message for n in app._notifications), list(app._notifications)
            assert table.row_count == 3
            table.move_cursor(row=2)  # share project: still soft-deleted
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

    # Started on a folder (Explorer menu): row preselected and focused, warning shown.
    fresh = Path(d) / "newout" / "fresh"
    fresh.mkdir(parents=True)  # not in the (fake) registry
    from textual.coordinate import Coordinate

    async def check_select(folder, model=""):
        n_reg = len(reg)
        async with maude.MaudeApp(select=str(folder), model=model).run_test(size=(140, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            table = app.query_one("#projects-table")
            key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
            want = str(Path(os.path.realpath(folder)))
            assert maude.mx.project_key(key) == maude.mx.project_key(want), (key, want)
            assert [str(r.value) for r in table.rows].count(key) == 1  # never duplicated
            assert table.has_focus, "table must be focused so Enter opens the row"
            warn = app.query_one("#win-warning")
            assert warn.display and "OUTSIDE the sandbox" in str(warn.render()) and "full Windows user rights" in str(warn.render()), warn.render()
            assert str(Path(os.path.realpath(folder)).name) in str(warn.render())
            assert app._selected_project() == Path(want)
            assert len(reg) == n_reg  # the TUI itself never writes the registry
            on = [str(b.label) for b in app.query_one("#model-select").query(maude.RadioButton) if b.value]
            return on

    on = asyncio.run(check_select(out1))                      # existing registry entry (listed once)
    on = asyncio.run(check_select(fresh, "sonnet"))           # not in the registry: still listed and selected
    assert on == ["sonnet"], on                               # --model preselects the picker
    async def check_plain():  # no select: no warning
        async with maude.MaudeApp().run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert not pilot.app.query_one("#win-warning").display
    asyncio.run(check_plain())

    # Registration only on open; the bar follows the cursor; delete hides it; a folder that cannot be listed gets no Enter hint.
    import contextlib
    added, opened, orig_open = [], [], maude.open_project
    mx.add_project = lambda f: added.append(f)
    maude.open_project = lambda *a, **k: opened.append(a)
    want = str(Path(os.path.realpath(fresh)))

    async def check_bar():
        async with maude.MaudeApp(select=str(fresh)).run_test(size=(140, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            table = app.query_one("#projects-table")
            bar = lambda: str(app.query_one("#win-warning").render())
            assert "Press Enter" in bar() and not added, (bar(), added)  # starting registers nothing
            on_row = table.cursor_row
            table.move_cursor(row=table.row_count - 1)  # a share project, below the rule row
            await pilot.pause(); await pilot.pause()
            assert "Press Enter" not in bar() and "full Windows user rights" in bar(), bar()  # cursor left the row
            table.move_cursor(row=on_row); await pilot.pause(); await pilot.pause()
            assert "Press Enter" in bar(), bar()
            # open: registered even though the registry write may fail
            app.suspend = contextlib.nullcontext
            app._launch_project_now(Path(want))
            assert added == [want] and len(opened) == 1, (added, opened)
            mx.add_project = lambda f: (_ for _ in ()).throw(OSError("denied"))
            app._launch_project_now(Path(want))
            assert len(opened) == 2, opened  # registry failure never stops the open
            mx.add_project = lambda f: added.append(f)
        added.clear()
        async with maude.MaudeApp(select=str(fresh)).run_test(size=(140, 40)) as pilot:  # quit without opening
            await pilot.pause()
            await pilot.press("q"); await pilot.pause()
        assert not added, added
        mx.remove_project, saved = (lambda f: None), mx.remove_project
        try:
            async with maude.MaudeApp(select=str(fresh)).run_test(size=(140, 40)) as pilot:
                app = pilot.app
                await pilot.pause()
                assert app._selected_project() == Path(want)
                app._on_confirm_delete(True); await pilot.pause()
                assert not app.query_one("#win-warning").display, "bar must go with the deleted row"
                assert all(mx.project_key(str(r.value)) != mx.project_key(want) for r in app.query_one("#projects-table").rows)
        finally:
            mx.remove_project = saved
        # registry delete denied: the row stays, so the bar and _extra stay too
        def denied(f): raise OSError("denied")
        mx.remove_project, saved = denied, mx.remove_project
        try:
            async with maude.MaudeApp(select=str(fresh)).run_test(size=(140, 40)) as pilot:
                app = pilot.app
                await pilot.pause()
                app._on_confirm_delete(True); await pilot.pause()
                assert app.query_one("#win-warning").display and app._bar_on and app._extra, "bar must stay"
                assert any(mx.project_key(str(r.value)) == mx.project_key(want) for r in app.query_one("#projects-table").rows)
        finally:
            mx.remove_project = saved
        # opening another row, or a share folder given as select=, never writes the Projects key
        added.clear()
        async with maude.MaudeApp(select=str(fresh)).run_test(size=(140, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            app.suspend = contextlib.nullcontext
            app._launch_project_now(Path(os.path.realpath(out1)))
            assert not added, added
        async with maude.MaudeApp(select=str(proj)).run_test(size=(140, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            app.suspend = contextlib.nullcontext
            app._launch_project_now(proj)
            assert not added, added  # share folder: listed anyway, never registered
        if os.name == "nt":  # a drive root (Explorer on a drive background) is listed and selected
            root = os.path.splitdrive(os.getcwd())[0] + "\\"
            async with maude.MaudeApp(select=root).run_test(size=(140, 40)) as pilot:
                app = pilot.app
                await pilot.pause(); await pilot.pause()
                table = app.query_one("#projects-table")
                key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
                assert mx.project_key(key) == mx.project_key(root), (key, root)
                assert "Press Enter" in str(app.query_one("#win-warning").render())
        async with maude.MaudeApp(select=str(Path(d) / "nonexistent")).run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            bar = str(pilot.app.query_one("#win-warning").render())
            assert "Press Enter" not in bar and "full Windows user rights" in bar, bar
    try:
        asyncio.run(check_bar())
    finally:
        maude.open_project = orig_open

    # Three modes: Windows mode with a sandbox (caption) and without one (no caption); the logo is red in both.
    reds = {f"bold {c}" for c in maude._LOGO_GRADIENT_WIN}
    async def check_caption(has_sb):
        maude.HAS_SANDBOX = has_sb
        try:
            logo = maude.make_gradient_logo()
            assert (maude.WIN_CAPTION in logo.plain) == has_sb, logo.plain
            assert logo.spans, logo.spans
            assert all(str(s.style) in reds or str(s.style) == "dim #e05252" for s in logo.spans), logo.spans
            async with maude.MaudeApp(select=str(fresh)).run_test(size=(140, 40)) as pilot:
                app = pilot.app
                await pilot.pause()
                from textual.geometry import Region
                lg = app.query_one("#logo")
                shown = "".join("".join(s.text for s in ln) for ln in lg.render_lines(Region(0, 0, lg.size.width, lg.size.height)))
                assert (maude.WIN_CAPTION in shown) == has_sb, shown
                warn = str(app.query_one("#win-warning").render())
                assert "full Windows user rights" in warn and str(fresh.name) in warn, warn
                assert ("OUTSIDE the sandbox" in warn) == has_sb, warn
                table = app.query_one("#projects-table")
                feats = [str(table.get_row_at(i)[2]) for i in range(table.row_count)]
                assert ("No Sandbox" in feats) == has_sb, feats
        finally:
            maude.HAS_SANDBOX = True
    asyncio.run(check_caption(True))
    asyncio.run(check_caption(False))

    # Open project: native claude, continue first, no bypass flag, cwd = project.
    mx.offer_git = lambda: None  # never look for git or winget on the real system
    mx.offer_gh = lambda: None
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
    # Missing non-Claude tool: install offered (declined here), nothing is run, TUI gets control back.
    calls.clear()
    asked = []
    mx.find_tool = lambda b, *a, **k: None
    import builtins; builtins.input = lambda q="": asked.append(q) or "n"
    maude.open_project(proj, "opus", tool="OpenCode")
    assert calls == [] and any("Install Bun now" in q for q in asked), (calls, asked)
    print("win_tui_smoke ok")
finally:
    os.chdir(LIGHT)
    shutil.rmtree(d, ignore_errors=True)
