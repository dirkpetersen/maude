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


async def wait_until(pilot, cond, timeout=5.0):
    """Poll cond() until it is truthy or the timeout expires (slow CI runners); returns the final bool."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        try:
            if cond():
                return True
        except Exception:
            pass  # widget not mounted yet etc.: keep waiting
        if loop.time() >= end:
            break
        await pilot.pause(0.05)
    try:
        return bool(cond())
    except Exception:
        return False


async def wait_query(pilot, screen_getter, selector, timeout=5.0):
    """Wait until selector exists on screen_getter() (a screen or the app) and return the widget.
    On timeout the plain query_one raises the real NoMatches."""
    await wait_until(pilot, lambda: len(screen_getter().query(selector)) > 0, timeout)
    return screen_getter().query_one(selector)


async def wait_table(pilot, rows=1, timeout=5.0):
    """Wait for #projects-table on the app screen to have at least `rows` rows; returns it."""
    app = pilot.app
    table = await wait_query(pilot, lambda: app, "#projects-table", timeout)
    await wait_until(pilot, lambda: table.row_count >= rows, timeout)
    return table


LIGHT = Path(__file__).resolve().parent.parent / "light"
sys.path.insert(0, str(LIGHT))
d = tempfile.mkdtemp()
try:
    home, share = Path(d) / "home", Path(d) / "share"
    proj = share / "Projects" / "demo"
    proj.mkdir(parents=True)
    home.mkdir()
    os.environ.update(USERPROFILE=str(home), HOME=str(home), LOCALAPPDATA=str(Path(d) / "lad"),
                      MAUDE_MODELS_REFRESH="0")  # the TUI starts a daily model-list download otherwise
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
            table = await wait_table(pilot, 4)
            assert str(table.columns[list(table.columns)[2]].label) == "Features"
            cells = [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]
            # outside first (newest content first), then share; missing folder hidden; same names get a parent
            assert [c[0].strip() for c in cells][:2] == ["demo (out2)", "alpha"] and cells[3][0].strip() == "demo (Projects)", cells
            assert set(cells[2][0].strip()) == {"─"} and list(table.rows)[2].value == "__sep__", cells
            assert [c[2] for c in cells] == ["No Sandbox"] * 2 + [cells[2][2], "·"], cells
            table.move_cursor(row=1); await pilot.pause()
            await wait_until(pilot, lambda: table.cursor_row == 1)
            await pilot.press("down"); await pilot.pause()
            await wait_until(pilot, lambda: table.cursor_row == 3)
            assert table.cursor_row == 3, table.cursor_row   # skips the rule: first share project
            await pilot.press("up"); await pilot.pause()
            await wait_until(pilot, lambda: table.cursor_row == 1)
            assert table.cursor_row == 1, table.cursor_row   # last outside project
            from textual.coordinate import Coordinate
            table.cursor_coordinate = Coordinate(2, 0)       # cursor forced onto the rule (message not yet handled)
            assert app._selected_project() is None
            app.action_delete_selected(); await pilot.pause()
            assert len(app.screen_stack) == 1, "delete on the rule must open nothing"
            await pilot.pause()
            await wait_until(pilot, lambda: table.cursor_row != 2)
            assert table.cursor_row != 2, table.cursor_row
            logo = app.query_one("#logo")
            await wait_until(pilot, lambda: logo.size.height >= 6)
            assert logo.size.height >= 6, logo.size
            from textual.geometry import Region
            shown = "".join(s.text for s in logo.render_lines(Region(0, 0, logo.size.width, logo.size.height))[5]) if hasattr(logo, "render_lines") else ""
            assert maude.WIN_CAPTION in shown, shown
            table.move_cursor(row=0)
            app.action_delete_selected(); await pilot.pause()
            from textual.widgets import Label
            sub = await wait_query(pilot, lambda: app.screen, "#confirm-sub")
            assert "folder is not deleted" in str(sub.render())
            (await wait_query(pilot, lambda: app.screen, "#btn-yes")).press(); await pilot.pause()
            await wait_until(pilot, lambda: str(out2) not in reg)
            assert str(out2) not in reg and str(out1) in reg and out2.is_dir(), reg  # registry entry only
            # notify() is delivered as a message: wait for it on slow runners
            await wait_until(pilot, lambda: any(str(out2) in n.message and "manually" in n.message for n in app._notifications))
            assert any(str(out2) in n.message and "manually" in n.message for n in app._notifications), list(app._notifications)
            await wait_until(pilot, lambda: table.row_count == 3)
            assert table.row_count == 3
            table.move_cursor(row=2)  # share project: still soft-deleted
            assert maude.IS_WIN and not maude.ConfirmDeleteScreen("x").outside
            assert maude.current_platform() == "windows"
            hidden = maude.hidden_widgets()  # from the FEATURES map: everything off on Windows for now
            assert set(hidden) == {"#btn-web", "#autostart"}, hidden  # Set Creds, Update Maude, To Github, Setup Git(hub) are on
            for i in hidden:
                assert not app.query_one(i).display, i
            for i in ("#btn-open", "#btn-new", "#btn-cli", "#btn-creds", "#btn-update", "#btn-github", "#btn-togithub", "#fresh-context", "#perplexity"):
                assert app.query_one(i).display, i
            # Pickers: column order Agent, Model, Thinking; 8 agents in the fixed order; outside the sandbox only
            # Kiro CLI and GitHub Copilot CLI are enabled (the rest greyed out, not hidden); the saved agent
            # (none: Claude Code) is switched to Kiro CLI; the Model list follows the agent, auto first.
            from textual.widgets import RadioButton
            rb = lambda sel, n: app.query_one(sel).query(RadioButton)[n]
            labels = lambda sel: [str(b.label) for b in app.query_one(sel).query(RadioButton) if b.display]
            get_on = lambda: {sel: [str(b.label) for b in app.query_one(sel).query(RadioButton) if b.value]
                              for sel in ("#agent-select", "#model-select")}
            cols = [app.query_one(i).region.x for i in ("#agent-col", "#model-col", "#effort-col")]
            assert cols == sorted(cols) and len(set(cols)) == 3, cols            # Agent | Model | Thinking, left to right
            assert [str(app.query_one(i).render()) for i in ("#agent-label", "#model-label", "#effort-label")] == ["Agent", "Model", "Thinking"]
            AG = ["Claude Code", "Codex", "Antigravity", "Grok CLI", "Kiro CLI", "GitHub Copilot CLI", "OpenCode", "Crush"]
            assert labels("#agent-select") == AG == list(maude.AGENTS), labels("#agent-select")
            dis = lambda: [str(b.label) for b in app.query_one("#agent-select").query(RadioButton) if b.disabled]
            assert dis() == [a for a in AG if a not in ("Kiro CLI", "GitHub Copilot CLI")], dis()
            assert all(b.display for b in app.query_one("#agent-select").query(RadioButton))  # greyed out, not hidden
            await wait_until(pilot, lambda: get_on() == {"#agent-select": ["Kiro CLI"], "#model-select": ["auto"]})
            assert get_on() == {"#agent-select": ["Kiro CLI"], "#model-select": ["auto"]}, get_on()
            assert labels("#model-select") == ["auto", "fable", "opus", "sonnet", "haiku", "5.6 sol", "5.6 terra", "5.6 luna"], labels("#model-select")
            assert app.query_one("#effort-select").disabled      # Kiro CLI has no reasoning-effort setting
            await pilot.click(rb("#agent-select", 7)); await pilot.pause()   # Crush: disabled, a click changes nothing
            assert get_on() == {"#agent-select": ["Kiro CLI"], "#model-select": ["auto"]} and app._tool == "Kiro CLI", get_on()
            await pilot.click(rb("#model-select", 2)); await pilot.pause()    # Kiro: opus
            await wait_until(pilot, lambda: app._model == "claude-opus-5.5")
            assert get_on() == {"#agent-select": ["Kiro CLI"], "#model-select": ["opus"]} and maude.read_model() == "claude-opus-5.5", get_on()
            await pilot.click(rb("#agent-select", 5)); await pilot.pause()    # GitHub Copilot CLI: its own list
            await wait_until(pilot, lambda: app._tool == "GitHub Copilot CLI" and labels("#model-select")[-1] == "6.1 terra")
            assert labels("#model-select") == ["auto", "fable", "opus", "sonnet", "haiku", "6.2 sol", "6.2 astra", "6.1 terra"], labels("#model-select")
            # the current id (claude-opus-5.5) is also in Copilot's list, so it stays selected
            assert get_on() == {"#agent-select": ["GitHub Copilot CLI"], "#model-select": ["opus"]} and app._model == "claude-opus-5.5", get_on()
            await pilot.click(rb("#model-select", 5)); await pilot.pause()    # 6.2 sol
            await wait_until(pilot, lambda: app._model == "gpt-6.2-sol")
            assert maude.read_model() == "gpt-6.2-sol" and maude.read_tool() == "GitHub Copilot CLI"
            await pilot.click(rb("#agent-select", 4)); await pilot.pause()    # back to Kiro CLI: remembers opus (gpt-6.2-sol is not in its list)
            await wait_until(pilot, lambda: app._tool == "Kiro CLI" and app._model == "claude-opus-5.5")
            assert get_on() == {"#agent-select": ["Kiro CLI"], "#model-select": ["opus"]}, get_on()
            await pilot.click(rb("#model-select", 0)); await pilot.pause()    # leave the state at auto
            await wait_until(pilot, lambda: app._model == "auto")
            assert get_on() == {"#agent-select": ["Kiro CLI"], "#model-select": ["auto"]}, get_on()
            # The public hook the Settings screen calls: switching the restriction off enables every agent.
            maude.save_settings({"native_only_approved": False}); app.apply_agent_restrictions(); await pilot.pause()
            await wait_until(pilot, lambda: dis() == [])
            assert dis() == [] and app._tool == "Kiro CLI", dis()
            await pilot.click(rb("#agent-select", 1)); await pilot.pause()    # Codex: Thinking becomes available
            await wait_until(pilot, lambda: app._tool == "Codex" and not app.query_one("#effort-select").disabled)
            assert labels("#model-select") == ["auto", "6.2 astra", "6.2 sol", "6.1 sol", "6.1 terra", "6 luna"], labels("#model-select")
            maude.save_settings({"native_only_approved": True}); app.apply_agent_restrictions(); await pilot.pause()
            await wait_until(pilot, lambda: app._tool == "Kiro CLI")           # a now-forbidden agent falls back to Kiro CLI
            assert app._tool == "Kiro CLI" and get_on()["#agent-select"] == ["Kiro CLI"] and app.query_one("#effort-select").disabled, get_on()
            assert dis() == [a for a in AG if a not in ("Kiro CLI", "GitHub Copilot CLI")], dis()
            # PowerShell button (Windows): suspends, runs powershell.exe -NoLogo in the selected
            # project's folder (else home) and comes back to the TUI; it must NOT exit the app.
            import contextlib
            assert str(app.query_one("#btn-cli").label) == "PowerShell"
            import signal
            exited, ran, sig = [], [], []
            app.exit = lambda *a, **k: exited.append(1)
            real_suspend, real_find = app.suspend, maude.find_powershell
            maude.find_powershell = lambda: "C:/fake/powershell.exe"   # also works on Linux / without PowerShell
            sig_before = signal.getsignal(signal.SIGINT)
            def fake_run(cmd, **kw):
                sig.append(signal.getsignal(signal.SIGINT))
                ran.append((cmd, kw.get("cwd"), kw.get("env")))
                return subprocess.CompletedProcess(cmd, 0)
            real_run, maude.subprocess.run = maude.subprocess.run, fake_run
            state = {'in': 0, 'out': 0}
            @contextlib.contextmanager
            def strict_suspend():  # like App.suspend(): NO try/finally, an exception skips the resume
                state['in'] += 1
                yield
                state['out'] += 1
            app.suspend = strict_suspend
            try:
                table.move_cursor(row=2)                      # share project (row 2 after the outside delete)
                await wait_until(pilot, lambda: app._selected_project() == proj)
                app.query_one("#btn-cli").press()
                await wait_until(pilot, lambda: len(ran) == 1)
                assert ran[0][0] == ["C:/fake/powershell.exe", "-NoLogo"], ran
                assert ran[0][1] == str(proj), ran
                # Ctrl+C regression: SIGINT is ignored while PowerShell runs (asyncio's handler would
                # cancel the app), and the previous handler is back afterwards.
                assert sig[0] == signal.SIG_IGN, sig
                assert signal.getsignal(signal.SIGINT) == sig_before, (sig_before, signal.getsignal(signal.SIGINT))
                table.cursor_coordinate = Coordinate(1, 0)    # the rule: nothing selected
                await wait_until(pilot, lambda: app._selected_project() is None)
                app.query_one("#btn-cli").press()
                await wait_until(pilot, lambda: len(ran) == 2)
                assert ran[1][1] == os.path.expanduser("~"), ran
                # a selected path that is not a directory: home, not a WinError 267 for PowerShell
                app._selected_project = lambda: Path(d) / "f.txt"
                (Path(d) / "f.txt").write_text("x")
                app.query_one("#btn-cli").press()
                await wait_until(pilot, lambda: len(ran) == 3)
                assert ran[2][1] == os.path.expanduser("~"), ran
                del app._selected_project
                # start failure inside the REAL suspend: notify, driver resumed, TUI still usable
                def boom(cmd, **kw): raise PermissionError("blocked by policy")
                maude.subprocess.run = boom
                app.query_one("#btn-cli").press()
                await wait_until(pilot, lambda: any("Could not start PowerShell" in n.message for n in app._notifications))
                assert any("blocked by policy" in n.message for n in app._notifications), list(app._notifications)
                assert state['in'] == state['out'] == 4, state   # every suspend was resumed, also after the failure
                assert signal.getsignal(signal.SIGINT) == sig_before
                # powershell.exe not found: notify, no suspend at all
                maude.find_powershell = lambda: ""
                app.query_one("#btn-cli").press()
                await wait_until(pilot, lambda: any("not found" in n.message for n in app._notifications))
                assert state['in'] == 4, state                  # not found: never suspended
            finally:
                maude.subprocess.run, app.suspend, maude.find_powershell = real_run, real_suspend, real_find
            assert not exited and len(app.screen_stack) == 1, (exited, app.screen_stack)  # app still running
            await wait_until(pilot, lambda: app.query_one("#projects-table").row_count >= 1)
            assert app.query_one("#btn-cli").display
            env = ran[0][2]  # raw tool env (tools=True): None when there is no clauderc
            assert env is None or isinstance(env, dict)
            assert any(b.key == "q" and b.action == "quit_to_shell" and b.show for b in app.BINDINGS), "q must stay a visible Quit"
            # Update Maude: visible on Windows, runs native_update (stubbed: no network) into the log,
            # and "Reload UI" exits the app with 42 (maude_exe.run_tui relaunches the exe on that).
            seen = []
            maude.native_update = lambda say, *a, **k: (say("Updating maude.exe..."), say("maude.exe v9.9.9 downloaded."), (0, "must"))[2]
            exits = []
            app.exit = lambda *a, **k: exits.append(a)
            app.query_one("#btn-update").press()
            await pilot.pause()
            await wait_until(pilot, lambda: type(app.screen).__name__ == "UpdateMaudeScreen")
            scr = app.screen
            assert type(scr).__name__ == "UpdateMaudeScreen", scr
            from textual.widgets import Button, RichLog
            reload_btn = await wait_query(pilot, lambda: scr, "#btn-update-reload")
            await wait_until(pilot, lambda: not reload_btn.disabled)
            await wait_until(pilot, lambda: "maude.exe v9.9.9 downloaded." in " | ".join(
                str(getattr(l, "text", l)) for l in scr.query_one("#update-log", RichLog).lines))
            log = " | ".join(str(getattr(l, "text", l)) for l in scr.query_one("#update-log", RichLog).lines)
            assert "maude.exe v9.9.9 downloaded." in log, log
            assert not scr.query_one("#btn-update-reload", Button).disabled
            assert scr.query_one("#btn-update-close", Button).disabled  # staged exe: Reload is the only way out
            scr.query_one("#btn-update-reload", Button).press()
            await wait_until(pilot, lambda: exits)  # the exit can land a moment later on slow CI runners
            assert exits == [(42,)], exits
            # With something staged, Escape must reload too (same exit value 42).
            (await wait_query(pilot, lambda: app, "#btn-update")).press()
            await pilot.pause()
            await wait_until(pilot, lambda: type(app.screen).__name__ == "UpdateMaudeScreen" and app.screen is not scr)
            scr = app.screen
            assert type(scr).__name__ == "UpdateMaudeScreen", scr
            reload_btn = await wait_query(pilot, lambda: scr, "#btn-update-reload")
            await wait_until(pilot, lambda: not reload_btn.disabled)
            await pilot.press("escape")
            await wait_until(pilot, lambda: len(exits) >= 2)
            assert exits == [(42,), (42,)], exits

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
            table = await wait_table(pilot)
            want = str(Path(os.path.realpath(folder)))
            sel_key = lambda: table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
            await wait_until(pilot, lambda: maude.mx.project_key(sel_key()) == maude.mx.project_key(want) and table.has_focus)
            key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
            assert maude.mx.project_key(key) == maude.mx.project_key(want), (key, want)
            assert [str(r.value) for r in table.rows].count(key) == 1  # never duplicated
            assert table.has_focus, "table must be focused so Enter opens the row"
            warn = app.query_one("#win-warning")
            await wait_until(pilot, lambda: warn.display and "OUTSIDE the sandbox" in str(warn.render()))
            assert warn.display and "OUTSIDE the sandbox" in str(warn.render()) and "full Windows user rights" in str(warn.render()), warn.render()
            assert str(Path(os.path.realpath(folder)).name) in str(warn.render())
            await wait_until(pilot, lambda: app._selected_project() == Path(want))
            assert app._selected_project() == Path(want)
            assert len(reg) == n_reg  # the TUI itself never writes the registry
            on = [str(b.label) for b in app.query_one("#model-select").query(maude.RadioButton) if b.value]
            return on

    on = asyncio.run(check_select(out1))                      # existing registry entry (listed once)
    on = asyncio.run(check_select(fresh, "sonnet"))           # not in the registry: still listed and selected
    assert on == ["auto"], on                                 # outside the sandbox Claude Code is not allowed: --model is ignored
    maude.save_settings({"native_only_approved": False})
    on = asyncio.run(check_select(fresh, "sonnet"))
    assert on == ["sonnet"], on                               # --model preselects the picker (and selects Claude Code)
    assert maude.read_tool() == "Claude Code" and maude.read_model() == "sonnet"
    maude.save_settings({"native_only_approved": True})
    async def check_plain():  # no select: no warning
        async with maude.MaudeApp().run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await wait_table(pilot)
            warn = await wait_query(pilot, lambda: pilot.app, "#win-warning")
            await pilot.pause()
            assert not warn.display
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
            table = await wait_table(pilot)
            bar = lambda: str(app.query_one("#win-warning").render())
            await wait_until(pilot, lambda: "Press Enter" in bar())
            assert "Press Enter" in bar() and not added, (bar(), added)  # starting registers nothing
            on_row = table.cursor_row
            table.move_cursor(row=table.row_count - 1)  # a share project, below the rule row
            await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: "Press Enter" not in bar())
            assert "Press Enter" not in bar() and "full Windows user rights" in bar(), bar()  # cursor left the row
            table.move_cursor(row=on_row); await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: "Press Enter" in bar())
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
                await wait_table(pilot)
                await wait_until(pilot, lambda: app._selected_project() == Path(want))
                assert app._selected_project() == Path(want)
                app._on_confirm_delete(True); await pilot.pause()
                await wait_until(pilot, lambda: not app.query_one("#win-warning").display)
                assert not app.query_one("#win-warning").display, "bar must go with the deleted row"
                await wait_until(pilot, lambda: all(mx.project_key(str(r.value)) != mx.project_key(want) for r in app.query_one("#projects-table").rows))
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
                await wait_table(pilot)
                await wait_until(pilot, lambda: app._bar_on and app._extra)
                app._on_confirm_delete(True); await pilot.pause(); await pilot.pause()
                assert app.query_one("#win-warning").display and app._bar_on and app._extra, "bar must stay"
                assert any(mx.project_key(str(r.value)) == mx.project_key(want) for r in app.query_one("#projects-table").rows)
        finally:
            mx.remove_project = saved
        # opening another row, or a share folder given as select=, never writes the Projects key
        added.clear()
        async with maude.MaudeApp(select=str(fresh)).run_test(size=(140, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await wait_table(pilot)
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
                table = await wait_table(pilot)
                sel_key = lambda: table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
                await wait_until(pilot, lambda: mx.project_key(sel_key()) == mx.project_key(root))
                key = sel_key()
                assert mx.project_key(key) == mx.project_key(root), (key, root)
                await wait_until(pilot, lambda: "Press Enter" in str(app.query_one("#win-warning").render()))
                assert "Press Enter" in str(app.query_one("#win-warning").render())
        async with maude.MaudeApp(select=str(Path(d) / "nonexistent")).run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await wait_table(pilot)
            await wait_until(pilot, lambda: "full Windows user rights" in str(pilot.app.query_one("#win-warning").render()))
            bar = str(pilot.app.query_one("#win-warning").render())
            assert "Press Enter" not in bar and "full Windows user rights" in bar, bar
    try:
        asyncio.run(check_bar())
    finally:
        maude.open_project = orig_open
    # Picker state lives in <home>/.maude/maude-model|maude-tool (all platforms): loaded at startup, written on change.
    sd = home / ".maude"; mf, tf = sd / "maude-model", sd / "maude-tool"
    sd.mkdir(exist_ok=True); mf.write_text("claude-sonnet-5.5,high\n"); tf.write_text("Kiro CLI")  # an id of Kiro's list
    maude.save_settings({"native_only_approved": False})  # every agent usable: the clicks below need Claude/Codex
    (home / ".maude-model").write_text("haiku")  # legacy file must lose against the new file
    async def check_files():
        from textual.widgets import RadioButton, Static
        async with maude.MaudeApp().run_test(size=(120, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            lit = lambda sel: [str(b.label) for b in app.query_one(sel).query(RadioButton) if b.value]
            await wait_until(pilot, lambda: lit("#model-select") == ["sonnet"] and lit("#agent-select") == ["Kiro CLI"] and lit("#effort-select") == ["high"])
            await wait_until(pilot, lambda: all(app.query_one(i).region.height > 0 for i in ("#model-select", "#agent-select", "#effort-select", "#fresh-context", "#perplexity", "#maude-version")))
            assert lit("#model-select") == ["sonnet"] and lit("#agent-select") == ["Kiro CLI"], (lit("#model-select"), lit("#agent-select"))
            assert lit("#effort-select") == ["high"], lit("#effort-select")   # kept, though Kiro CLI ignores it
            # Layout at a roomy 120x40 terminal: every picker and the toggles sit fully inside the sidebar.
            # (At 80x24 the 8-agent column does not fit: the sidebar scrolls.)
            side = app.query_one("#sidebar").region
            for i in ("#model-select", "#agent-select", "#effort-select", "#fresh-context", "#perplexity", "#maude-version"):
                r = app.query_one(i).region
                assert r.height > 0 and r.y >= side.y and r.bottom <= side.bottom and r.right <= side.right, (i, r, side)
            # Toggles: one line each, box left of the label, same x, Perplexity MCP directly under Clear History.
            from textual.widgets import Checkbox
            fresh, perp = app.query_one("#fresh-context", Checkbox), app.query_one("#perplexity", Checkbox)
            assert str(fresh.label) == "Clear History" and str(perp.label) == "Perplexity MCP", (fresh.label, perp.label)
            assert not app.query_one("#autostart").display            # Windows: no Autostart
            for cb in (fresh, perp):
                assert cb.region.height == 1 and cb.region.x == fresh.region.x, (cb.id, cb.region)
            assert perp.region.y == fresh.region.y + 1, (fresh.region, perp.region)
            assert [w.id for w in app.query_one("#sidebar").children if isinstance(w, Checkbox)] == ["fresh-context", "perplexity", "autostart"]
            # Linux sandbox layout (Autostart shown): same column, one more row, still inside the sidebar.
            try:
                app.query_one("#autostart").display = True
                await pilot.pause()
                await wait_until(pilot, lambda: app.query_one("#autostart").region.height == 1)
                auto = app.query_one("#autostart", Checkbox)
                assert str(auto.label) == "Autostart" and auto.region.height == 1 and auto.region.x == fresh.region.x, auto.region
                assert auto.region.y == perp.region.y + 1 and auto.region.bottom <= side.bottom, (auto.region, side)
                assert app.query_one("#maude-version").region.bottom <= side.bottom
            finally:
                app.query_one("#autostart").display = False
            assert [str(b.label) for b in app.query_one("#effort-select").query(RadioButton)] == ["auto", "low", "medium", "high", "xhigh", "max"]
            tips = str(app.query_one("#tips", Static).render())
            assert "Alt+V" in tips and "Paste image" in tips and "drop" not in tips, tips
            await pilot.click(app.query_one("#model-select").query(RadioButton)[4]); await pilot.pause()   # Kiro: haiku
            await wait_until(pilot, lambda: mf.read_text() == "claude-haiku-5.5,high")
            assert mf.read_text() == "claude-haiku-5.5,high", mf.read_text()       # model change keeps the level
            await pilot.click(app.query_one("#agent-select").query(RadioButton)[1]); await pilot.pause()   # Codex
            await wait_until(pilot, lambda: tf.read_text() == "Codex" and mf.read_text() == "auto,high")
            assert tf.read_text() == "Codex" and mf.read_text() == "auto,high", (tf.read_text(), mf.read_text())  # new agent: auto
            assert not app.query_one("#effort-select").disabled
            await pilot.click(app.query_one("#effort-select").query(RadioButton)[4]); await pilot.pause()
            await wait_until(pilot, lambda: mf.read_text() == "auto,xhigh")
            assert mf.read_text() == "auto,xhigh", mf.read_text()
            await pilot.click(app.query_one("#effort-select").query(RadioButton)[0]); await pilot.pause()
            await wait_until(pilot, lambda: mf.read_text() == "auto")
            assert mf.read_text() == "auto", mf.read_text()                        # auto effort = model alone
            await pilot.click(app.query_one("#effort-select").query(RadioButton)[2]); await pilot.pause()
            await wait_until(pilot, lambda: mf.read_text() == "auto,medium")
            assert mf.read_text() == "auto,medium", mf.read_text()
            await pilot.click(app.query_one("#agent-select").query(RadioButton)[4]); await pilot.pause()   # back to Kiro: remembered haiku
            await wait_until(pilot, lambda: mf.read_text() == "claude-haiku-5.5,medium" and tf.read_text() == "Kiro CLI")
            assert mf.read_text() == "claude-haiku-5.5,medium" and app.query_one("#effort-select").disabled, mf.read_text()
            assert maude.read_agent_models().get("Codex") == "auto"
    asyncio.run(check_files())
    maude.save_settings({"native_only_approved": True})
    assert maude.EFFORTS == mx.EFFORTS
    assert (home / ".maude-model").read_text() == "haiku"  # the legacy file was never touched while the new one existed
    # Legacy files: moved once into ~/.maude, then deleted. Bad values: defaults.
    mf.unlink(); tf.unlink()
    assert maude.read_model() == "haiku" and mf.read_text() == "haiku" and not (home / ".maude-model").exists()
    (home / ".maude-tool").write_text("Crush\n")
    assert maude.read_tool() == "Crush" and tf.read_text().strip() == "Crush" and not (home / ".maude-tool").exists()
    tf.write_text("Bogus"); assert maude.read_tool() == "Claude Code"
    mf.write_text("sonnet,bogus"); assert maude.read_model_effort() == ("sonnet", "")
    mf.write_text("opus,max"); assert maude.read_model_effort() == ("opus", "max")
    mf.write_text("bad model!,high"); assert maude.read_model_effort() == ("auto", "high")
    mf.write_text("gpt-6.2-sol,high"); assert maude.read_model_effort() == ("gpt-6.2-sol", "high")   # any valid id is kept
    mf.unlink(); tf.unlink()
    assert maude.read_model_effort() == ("auto", "") and maude.read_tool() == "Claude Code"   # nothing saved
    assert maude.maude_dir() == sd and sd.is_dir()

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
                lg = await wait_query(pilot, lambda: app, "#logo")
                await wait_until(pilot, lambda: lg.size.height >= 6)
                shown_of = lambda: "".join("".join(s.text for s in ln) for ln in lg.render_lines(Region(0, 0, lg.size.width, lg.size.height)))
                await wait_until(pilot, lambda: (maude.WIN_CAPTION in shown_of()) == has_sb)
                shown = shown_of()
                assert (maude.WIN_CAPTION in shown) == has_sb, shown
                await wait_table(pilot)
                await wait_until(pilot, lambda: "full Windows user rights" in str(app.query_one("#win-warning").render()))
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
    mx.offer_git = lambda: None  # never look for git, or offer to install MinGit, on the real system
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
    calls.clear()
    maude.open_project(proj, "sonnet", fresh=False, effort="high")
    maude.open_project(proj, "sonnet", fresh=True, effort="x;id")
    assert calls == [(["C:/c.exe", "--model", "sonnet", "--effort", "high", "--continue"], str(proj)),
                     (["C:/c.exe", "--model", "sonnet"], str(proj))], calls
    calls[:] = [calls[0], calls[1]]
    assert not any("skip" in a or "bypass" in a for c, _ in calls for a in c)
    calls.clear()
    maude.open_project(proj, "auto", fresh=False, effort="low")  # auto: no --model flag at all
    assert calls == [(["C:/c.exe", "--effort", "low", "--continue"], str(proj))], calls
    calls.clear()
    # Set Creds on Windows: the button opens the screen, Save writes <home>/.maude/clauderc (never ~/.azure),
    # and the saved env reaches Claude Code and the other tools.
    rc_file = home / ".maude" / "clauderc"
    assert maude.clauderc_path() == rc_file
    async def check_creds():
        async with maude.MaudeApp().run_test(size=(140, 45)) as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one("#btn-creds").press(); await pilot.pause()
            await wait_until(pilot, lambda: isinstance(app.screen, maude.CredsEntryScreen))
            assert isinstance(app.screen, maude.CredsEntryScreen), app.screen
            await pilot.pause()  # let the first render settle (a click within milliseconds of opening is not a human click)
            # slow CI runners: children can mount after the screen is pushed
            (await wait_query(pilot, lambda: app.screen, "#btn-creds-mode-foundry")).press(); await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: app.screen.query_one("#creds-pane-foundry").display)
            (await wait_query(pilot, lambda: app.screen, "#creds-foundry-url")).value = "https://u.example/anthropic"
            (await wait_query(pilot, lambda: app.screen, "#creds-foundry-key")).value = "kk"
            (await wait_query(pilot, lambda: app.screen, "#btn-creds-save")).press(); await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: len(app.screen_stack) == 1)
            assert len(app.screen_stack) == 1, app.screen_stack
    saved_env = dict(os.environ)
    try:
        asyncio.run(check_creds())
    finally:
        os.environ.clear(); os.environ.update(saved_env)  # the screen also updates this process's env
    assert rc_file.is_file() and not (home / ".azure").exists()
    assert "export ANTHROPIC_FOUNDRY_BASE_URL='https://u.example/anthropic'" in rc_file.read_text(), rc_file.read_text()
    maude.have_claude_login = lambda: False
    envs, old_run, old_ensure = [], mx._run, mx.ensure_tool
    mx._run = lambda cmd, **kw: envs.append((cmd[0], kw.get("env"))) or 0
    maude.open_project(proj, "sonnet", fresh=True)  # Claude Code through run_windows
    assert envs[-1][0] == "C:/c.exe" and envs[-1][1]["ANTHROPIC_BASE_URL"] == "https://u.example/anthropic", envs
    assert envs[-1][1]["ANTHROPIC_API_KEY"] == "sk-ant-dummy" and "PATH" in {k.upper() for k in envs[-1][1]}, envs
    mx.ensure_tool = lambda tool, *a, **k: "C:/" + tool.replace(" ", "") + ".exe"
    for tool in ("Kiro CLI", "OpenCode", "Crush"):
        envs.clear()
        maude.open_project(proj, "opus", tool=tool)
        assert envs and envs[-1][1]["ANTHROPIC_FOUNDRY_API_KEY"] == "kk", (tool, envs)
    # A new agent without a Windows installer entry: a hint and nothing runs (never a crash).
    asked0, had = [], mx.TOOL_INSTALL.pop("Codex", None)
    import builtins; real_input = builtins.input; builtins.input = lambda q="": asked0.append(q) or ""
    try:
        envs.clear()
        maude.open_project(proj, "auto", tool="Codex")
    finally:
        builtins.input = real_input
        if had is not None:
            mx.TOOL_INSTALL["Codex"] = had
    assert envs == [] and asked0 and "Codex" in asked0[0], (envs, asked0)
    mcp = []
    real_run = subprocess.run
    subprocess.run = lambda cmd, **kw: mcp.append((cmd, kw.get("env"))) or subprocess.CompletedProcess(cmd, 0, "", "")
    try:
        maude._claude_mcp("get", "x", capture=True)
    finally:
        subprocess.run = real_run
    assert mcp and mcp[0][1]["ANTHROPIC_FOUNDRY_API_KEY"] == "kk", mcp
    # No saved credentials: the child env is None (inherit), as before.
    rc_file.unlink()
    assert maude._child_env() is None
    mx._run, mx.ensure_tool = old_run, old_ensure
    # Rapid mode switching on the Set Creds screen (no pauses): no crash, exactly one pane visible.
    async def check_rapid():
        async with maude.MaudeApp().run_test(size=(140, 45)) as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one("#btn-creds").press(); await pilot.pause()
            await wait_until(pilot, lambda: isinstance(app.screen, maude.CredsEntryScreen))
            scr = app.screen
            assert isinstance(scr, maude.CredsEntryScreen)
            for b in ("#btn-creds-mode-foundry", "#btn-creds-mode-bedrock", "#btn-creds-mode-paste", "#btn-creds-cancel"):
                await wait_query(pilot, lambda: scr, b)  # all children mounted before the rapid presses
            calls = []
            real_switch = scr._switch_mode
            scr._switch_mode = lambda m: (calls.append(m), real_switch(m))[1]
            for _ in range(6):
                for b in ("#btn-creds-mode-foundry", "#btn-creds-mode-bedrock", "#btn-creds-mode-paste", "#btn-creds-mode-foundry"):
                    scr.query_one(b).press()  # no pause between presses
            # (the buttons' own message pumps run concurrently, so which press lands last is not fixed)
            body = {"paste": "#creds-text", "bedrock": "#creds-aws-key", "foundry": "#creds-foundry-url"}
            panes = {"paste": "#creds-pane-paste", "bedrock": "#creds-pane-bedrock", "foundry": "#creds-pane-foundry"}
            one_visible = lambda: all(scr.query_one(pid).display == (m == scr.mode) for m, pid in panes.items())
            await wait_until(pilot, lambda: len(calls) >= 24, timeout=10.0)  # every queued press handled
            await pilot.pause()
            assert len(calls) >= 24, len(calls)
            assert all(len(scr.query(sel)) == 1 for sel in body.values()), "all mode bodies stay mounted"
            assert one_visible(), "exactly one mode body visible"
            scr.query_one("#btn-creds-cancel").press(); await pilot.pause()
            await wait_until(pilot, lambda: len(app.screen_stack) == 1)
    asyncio.run(check_rapid())
    # Deterministic regression: a late RowHighlighted after the app has exited (screens torn down) must not raise.
    async def check_bar_after_exit():
        async with maude.MaudeApp(select=str(fresh)).run_test(size=(140, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await wait_until(pilot, lambda: app._bar_on and app._select)
            assert app._bar_on and app._select
        for key in (None, str(fresh), maude.SEP_KEY):  # called AFTER the run_test block has exited
            app._update_bar(key)
    asyncio.run(check_bar_after_exit())
    # Text left in a pane that is no longer shown is never saved: type into Paste, switch to Foundry, Save.
    async def check_hidden_pane():
        async with maude.MaudeApp().run_test(size=(140, 45)) as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one("#btn-creds").press(); await pilot.pause()
            await wait_until(pilot, lambda: isinstance(app.screen, maude.CredsEntryScreen))
            scr = app.screen
            assert isinstance(scr, maude.CredsEntryScreen)
            await pilot.pause()
            await wait_query(pilot, lambda: scr, "#btn-creds-mode-foundry")
            (await wait_query(pilot, lambda: scr, "#creds-text")).text = "export ANTHROPIC_API_KEY=sk-ant-hidden-pane\n"
            (await wait_query(pilot, lambda: scr, "#creds-aws-key")).value = "AKIAHIDDENPANE"
            scr.query_one("#btn-creds-mode-foundry").press(); await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: not scr.query_one("#creds-pane-paste").display)
            assert not scr.query_one("#creds-pane-paste").display
            (await wait_query(pilot, lambda: scr, "#creds-foundry-url")).value = "https://v.example/anthropic"
            (await wait_query(pilot, lambda: scr, "#creds-foundry-key")).value = "fk"
            (await wait_query(pilot, lambda: scr, "#btn-creds-save")).press(); await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: len(app.screen_stack) == 1)
            assert len(app.screen_stack) == 1, app.screen_stack
    saved_env = dict(os.environ)
    try:
        asyncio.run(check_hidden_pane())
    finally:
        os.environ.clear(); os.environ.update(saved_env)
    hp = rc_file.read_text()
    assert "ANTHROPIC_FOUNDRY_BASE_URL='https://v.example/anthropic'" in hp, hp
    assert "hidden-pane" not in hp and "HIDDENPANE" not in hp, hp  # the Paste and Bedrock panes were not the active mode
    assert not (home / ".aws" / "credentials").exists() or "HIDDENPANE" not in (home / ".aws" / "credentials").read_text()
    rc_file.unlink()
    # Setup Git(hub) and To Github are on natively: the wizard opens with the native titles, the push screen
    # opens (hint while gh is not authenticated), and nothing talks to GitHub or the real ~/.ssh.
    old_pt, old_auth = maude.plat_tool, maude.gh_is_authed
    maude.plat_tool = lambda n: "C:/fake/" + n + ".exe"   # git and gh "installed": no offer, no suspend
    maude.gh_is_authed = lambda: False
    async def check_git_screens():
        async with maude.MaudeApp().run_test(size=(140, 45)) as pilot:
            app = pilot.app
            await pilot.pause()
            await wait_table(pilot)
            app.query_one("#btn-github").press(); await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: isinstance(app.screen, maude.GitSetupWizard))
            assert isinstance(app.screen, maude.GitSetupWizard), app.screen
            from textual.widgets import Label
            title = await wait_query(pilot, lambda: app.screen, "#wiz-title")
            await wait_until(pilot, lambda: "Step 1 of 4" in str(title.render()))
            assert "Step 1 of 4" in str(app.screen.query_one("#wiz-title", Label).render())
            assert maude.GitSetupWizard.NATIVE_STEP_TITLES[3].endswith("Git Config & Signing")
            assert "Keychain" not in app.screen._title(3)
            (await wait_query(pilot, lambda: app.screen, "#wiz-cancel")).press(); await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: len(app.screen_stack) == 1)
            assert len(app.screen_stack) == 1, app.screen_stack
            table = await wait_table(pilot)
            table.move_cursor(row=table.row_count - 1); await pilot.pause()
            (await wait_query(pilot, lambda: app, "#btn-togithub")).press(); await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: isinstance(app.screen, maude.PushToGithubScreen))
            assert isinstance(app.screen, maude.PushToGithubScreen), app.screen
            help_ = await wait_query(pilot, lambda: app.screen, "#push-help")
            await wait_until(pilot, lambda: "not authenticated" in str(help_.render()))
            assert "not authenticated" in str(app.screen.query_one("#push-help", Label).render())
            (await wait_query(pilot, lambda: app.screen, "#btn-push-cancel")).press(); await pilot.pause()
            await wait_until(pilot, lambda: len(app.screen_stack) == 1)
            assert len(app.screen_stack) == 1, app.screen_stack
    try:
        asyncio.run(check_git_screens())
    finally:
        maude.plat_tool, maude.gh_is_authed = old_pt, old_auth
    # Native credential gate: `claude auth status` runs off the UI thread; only an explicit "not logged in" shows
    # the screen, and Cancel still opens the project.
    import time as _time
    old_cfg, old_status, old_open = maude.credentials_configured, maude.claude_login_status, maude.MaudeApp._launch_project_now
    maude.credentials_configured = lambda: False
    def slow_status(*a, **k):
        _time.sleep(0.4)
        return False
    maude.claude_login_status = slow_status
    async def check_gate():
        opened = []
        async with maude.MaudeApp().run_test(size=(140, 45)) as pilot:
            app = pilot.app
            await pilot.pause()
            app._launch_project_now = lambda path, fresh=None: opened.append(path)
            t0 = _time.perf_counter()
            app._launch_project(proj)
            assert _time.perf_counter() - t0 < 0.2, "the UI thread must not wait for claude auth status"
            assert not opened
            await wait_until(pilot, lambda: isinstance(app.screen, maude.CredsEntryScreen))
            assert isinstance(app.screen, maude.CredsEntryScreen), app.screen
            # slow CI runners: the screen is pushed before its children are mounted
            (await wait_query(pilot, lambda: app.screen, "#btn-creds-cancel")).press(); await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: opened)
            assert opened == [proj], opened  # Cancel on Windows/macOS still opens the project
            await wait_until(pilot, lambda: len(app.screen_stack) == 1)
            maude.claude_login_status = lambda *a, **k: None  # unknown answer: no screen at all
            opened.clear()
            app._launch_project(proj)
            await wait_until(pilot, lambda: opened)
            await pilot.pause()
            assert opened == [proj] and len(app.screen_stack) == 1, (opened, app.screen_stack)
    maude.save_settings({"native_only_approved": False})  # the gate is about Claude Code, which the default restriction switches off here
    maude.save_tool("Claude Code")                        # (the restriction had saved Kiro CLI)
    try:
        asyncio.run(check_gate())
    finally:
        maude.save_settings({"native_only_approved": True})
        maude.credentials_configured, maude.claude_login_status = old_cfg, old_status    # Missing non-Claude tool: install offered (declined here), nothing is run, TUI gets control back.
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
