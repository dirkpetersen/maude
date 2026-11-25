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


async def wait_ui_ready(pilot, screen, timeout=15.0):
    """Wait until CredsEntryScreen._init_ui has run (it sets the checkbox, the mode and the status after a refresh);
    asserting initial state before that races on slow CI runners."""
    ok = await wait_until(pilot, lambda: screen._ui_ready, timeout)
    assert ok and screen._ui_ready, "Settings screen never finished _init_ui"


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
            assert set(hidden) == {"#btn-web", "#autostart"}, hidden  # Settings, Update Maude, To Github, Setup Git(hub) are on
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
            assert not app.query_one("#effort-select").disabled  # Kiro CLI has --effort
            assert maude.read_tool() == "Claude Code" and not maude.MODEL_FILE.exists(), "a restricted start writes nothing"
            await pilot.click(rb("#agent-select", 7)); await pilot.pause()   # Crush: disabled, a click changes nothing
            assert get_on() == {"#agent-select": ["Kiro CLI"], "#model-select": ["auto"]} and app._tool == "Kiro CLI", get_on()
            await pilot.click(rb("#model-select", 2)); await pilot.pause()    # Kiro: opus
            await wait_until(pilot, lambda: app._model == "claude-opus-5.5")
            assert get_on() == {"#agent-select": ["Kiro CLI"], "#model-select": ["opus"]} and maude.read_agent_models().get("Kiro CLI") == "claude-opus-5.5" and not maude.MODEL_FILE.exists(), get_on()
            await pilot.click(rb("#agent-select", 5)); await pilot.pause()    # GitHub Copilot CLI: its own list
            await wait_until(pilot, lambda: app._tool == "GitHub Copilot CLI" and labels("#model-select")[-1] == "6.1 terra")
            assert labels("#model-select") == ["auto", "fable", "opus", "sonnet", "haiku", "6.2 sol", "6.2 astra", "6.1 terra"], labels("#model-select")
            # the current id (claude-opus-5.5) is also in Copilot's list, so it stays selected
            assert get_on() == {"#agent-select": ["GitHub Copilot CLI"], "#model-select": ["opus"]} and app._model == "claude-opus-5.5", get_on()
            await pilot.click(rb("#model-select", 5)); await pilot.pause()    # 6.2 sol
            await wait_until(pilot, lambda: app._model == "gpt-6.2-sol")
            assert maude.read_agent_models().get("GitHub Copilot CLI") == "gpt-6.2-sol" and maude.read_tool() == "GitHub Copilot CLI"
            assert app.query_one("#effort-select").disabled      # Copilot CLI has no reasoning-effort setting
            await pilot.click(rb("#agent-select", 4)); await pilot.pause()    # back to Kiro CLI: remembers opus (gpt-6.2-sol is not in its list)
            await wait_until(pilot, lambda: app._tool == "Kiro CLI" and app._model == "claude-opus-5.5")
            assert get_on() == {"#agent-select": ["Kiro CLI"], "#model-select": ["opus"]}, get_on()
            await pilot.click(rb("#model-select", 0)); await pilot.pause()    # leave the state at auto
            await wait_until(pilot, lambda: app._model == "auto")
            assert get_on() == {"#agent-select": ["Kiro CLI"], "#model-select": ["auto"]}, get_on()
            # The public hook the Settings screen calls: switching the restriction off enables every agent.
            maude.save_settings({"native_only_approved": False}); app.apply_agent_restrictions(); await pilot.pause()
            await wait_until(pilot, lambda: dis() == [])
            assert dis() == [] and app._tool == "Kiro CLI" and maude.read_tool() == "Kiro CLI", dis()
            await pilot.click(rb("#agent-select", 1)); await pilot.pause()    # Codex: Thinking becomes available
            await wait_until(pilot, lambda: app._tool == "Codex" and not app.query_one("#effort-select").disabled)
            assert labels("#model-select") == ["auto", "6.2 astra", "6.2 sol", "6.1 sol", "6.1 terra", "6 luna"], labels("#model-select")
            maude.save_settings({"native_only_approved": True}); app.apply_agent_restrictions(); await pilot.pause()
            await wait_until(pilot, lambda: app._tool == "Kiro CLI")           # a now-forbidden agent falls back to Kiro CLI
            assert app._tool == "Kiro CLI" and get_on()["#agent-select"] == ["Kiro CLI"] and not app.query_one("#effort-select").disabled, get_on()
            assert maude.read_tool() == "Codex", "the forced Kiro CLI must not overwrite the saved choice"
            maude.save_settings({"native_only_approved": False}); app.apply_agent_restrictions(); await pilot.pause()
            await wait_until(pilot, lambda: app._tool == "Codex")      # restriction lifted: the saved agent is back
            assert app._tool == "Codex" and dis() == [], (app._tool, dis())
            maude.save_settings({"native_only_approved": True}); app.apply_agent_restrictions(); await pilot.pause()
            await wait_until(pilot, lambda: app._tool == "Kiro CLI")
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
    sd.mkdir(exist_ok=True); mf.write_text("sonnet,high\n"); tf.write_text("Kiro CLI")   # maude-model is Claude Code's
    (sd / "agent-models.json").write_text('{"Kiro CLI": "claude-sonnet-5.5"}')                # Kiro's own model
    maude.save_settings({"native_only_approved": False})  # every agent usable: the clicks below need Claude/Codex
    (home / ".maude-model").write_text("haiku")  # legacy file must lose against the new file
    async def check_files():
        from textual.widgets import RadioButton, Static
        async with maude.MaudeApp().run_test(size=(120, 35)) as pilot:
            app = pilot.app
            await pilot.pause()
            lit = lambda sel: [str(b.label) for b in app.query_one(sel).query(RadioButton) if b.value]
            await wait_until(pilot, lambda: lit("#model-select") == ["sonnet"] and lit("#agent-select") == ["Kiro CLI"] and lit("#effort-select") == ["high"])
            await wait_until(pilot, lambda: all(app.query_one(i).region.height > 0 for i in ("#model-select", "#agent-select", "#effort-select", "#fresh-context", "#perplexity", "#maude-version")))
            assert lit("#model-select") == ["sonnet"] and lit("#agent-select") == ["Kiro CLI"], (lit("#model-select"), lit("#agent-select"))
            assert lit("#effort-select") == ["high"], lit("#effort-select")
            # Layout at a typical 120x35 terminal: every picker and the toggles sit fully inside the sidebar.
            # (In smaller terminals the sidebar scrolls: see check_reach.)
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
            await wait_until(pilot, lambda: maude.read_agent_models().get("Kiro CLI") == "claude-haiku-5.5")
            assert mf.read_text().strip() == "sonnet,high", mf.read_text().strip()                # maude-model stays Claude Code's
            await pilot.click(app.query_one("#agent-select").query(RadioButton)[1]); await pilot.pause()   # Codex
            await wait_until(pilot, lambda: tf.read_text().strip() == "Codex")
            assert tf.read_text().strip() == "Codex" and mf.read_text().strip() == "sonnet,high", (tf.read_text().strip(), mf.read_text().strip())
            assert app._model == "auto" and not app.query_one("#effort-select").disabled   # new agent: auto
            await pilot.click(app.query_one("#effort-select").query(RadioButton)[4]); await pilot.pause()
            await wait_until(pilot, lambda: mf.read_text().strip() == "sonnet,xhigh")
            assert mf.read_text().strip() == "sonnet,xhigh", mf.read_text().strip()               # the effort is shared; the model kept
            await pilot.click(app.query_one("#effort-select").query(RadioButton)[0]); await pilot.pause()
            await wait_until(pilot, lambda: mf.read_text().strip() == "sonnet")
            assert mf.read_text().strip() == "sonnet", mf.read_text().strip()                     # auto effort = model alone
            await pilot.click(app.query_one("#effort-select").query(RadioButton)[2]); await pilot.pause()
            await wait_until(pilot, lambda: mf.read_text().strip() == "sonnet,medium")
            assert mf.read_text().strip() == "sonnet,medium", mf.read_text().strip()
            await pilot.click(app.query_one("#agent-select").query(RadioButton)[4]); await pilot.pause()   # back to Kiro: remembered haiku
            await wait_until(pilot, lambda: tf.read_text().strip() == "Kiro CLI" and app._model == "claude-haiku-5.5")
            assert not app.query_one("#effort-select").disabled
            assert "Codex" not in maude.read_agent_models() or maude.read_agent_models()["Codex"] == "auto"
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
    mf.write_text("bad model!,high"); assert maude.read_model_effort() == ("opus", "high")
    mf.write_text("gpt-6.2-sol,high"); assert maude.read_model_effort() == ("opus", "high")   # maude-model is Claude Code only
    mf.write_text(",high"); assert maude.read_model_effort() == ("auto", "high")             # empty field = Claude Code auto
    mf.unlink(); tf.unlink()
    assert maude.read_model_effort() == ("opus", "") and maude.read_tool() == "Claude Code"   # nothing saved: opus (H1)
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
    # Settings on Windows: the button opens the screen, Save writes <home>/.maude/clauderc (never ~/.azure),
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
            await wait_ui_ready(pilot, app.screen)
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
    # Binaries line up with the installers, and every agent that may run outside the sandbox can be installed.
    for ag in maude.AGENTS:
        label = maude.INSTALL_LABEL.get(ag, ag)
        assert label in mx.TOOL_INSTALL, (ag, label)
        assert mx.TOOL_INSTALL[label][0] == maude.AGENT_BIN[ag], (ag, mx.TOOL_INSTALL[label], maude.AGENT_BIN[ag])
    for ag in maude.NATIVE_APPROVED_AGENTS:
        assert maude.INSTALL_LABEL.get(ag, ag) in mx.TOOL_INSTALL and (maude.INSTALL_LABEL.get(ag, ag) in mx.NATIVE_INSTALL
                                                                      or ag), ag
    # Missing binary: the install is OFFERED (ensure_tool) when an entry exists; the hint is only for a truly
    # missing binary without an installer. An agent that is on the PATH just runs.
    import builtins, io, contextlib
    asked0, had = [], mx.TOOL_INSTALL.pop("Codex", None)
    real_input, real_find = builtins.input, mx.find_tool
    builtins.input = lambda q="": asked0.append(q) or ""
    try:
        envs.clear()
        mx.find_tool = lambda b, *a, **k: None              # nothing on the PATH
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            maude.open_project(proj, "auto", tool="Codex")  # no installer entry, not found: hint, nothing runs
        assert envs == [] and asked0 and "Codex" in buf.getvalue() and "codex" in buf.getvalue(), (envs, asked0, buf.getvalue())
        mx.find_tool = lambda b, *a, **k: "C:/found/" + b + ".exe"   # no installer entry, but on the PATH: runs
        envs.clear(); asked0.clear()
        maude.open_project(proj, "auto", tool="Codex", fresh=True)
        assert envs and envs[-1][0] == "C:/found/codex.exe" and not asked0, (envs, asked0)
    finally:
        builtins.input, mx.find_tool = real_input, real_find
        if had is not None:
            mx.TOOL_INSTALL["Codex"] = had
    offered = []
    mx.ensure_tool = lambda tool, *a, **k: offered.append(tool) or "C:/" + tool.replace(" ", "") + ".exe"
    envs.clear()
    maude.open_project(proj, "auto", tool="GitHub Copilot CLI", fresh=True)   # install label differs from the agent name
    assert offered == ["Copilot CLI"] and envs[-1][0] == "C:/CopilotCLI.exe", (offered, envs)
    # A failing agent: a model was passed -> no silent retry, the exit code is shown ("try Model: auto");
    # no model + resume flag -> one retry without it.
    rc_seq, shown, runs = [], [], []
    mx._run = lambda cmd, **kw: runs.append(cmd) or rc_seq.pop(0)
    real_input = builtins.input; builtins.input = lambda q="": shown.append(q) or ""
    try:
        rc_seq[:] = [3, 3]                                   # resume run fails, the fresh retry (same model) fails too
        maude.open_project(proj, "claude-opus-5.5", tool="Kiro CLI", effort="high")
        assert [r[1:] for r in runs] == [["chat", "--model", "claude-opus-5.5", "--effort", "high", "--resume"],
                                         ["chat", "--model", "claude-opus-5.5", "--effort", "high"]], runs
        assert len(shown) == 1 and "exited with code 3" in shown[0], shown
        assert "try Model: auto / Thinking: auto" in shown[0], shown
        runs.clear(); shown.clear(); rc_seq[:] = [3, 0]                # a model was passed, the retry succeeds: no pause
        maude.open_project(proj, "claude-opus-5.5", tool="Kiro CLI")
        assert len(runs) == 2 and not shown, (runs, shown)
        runs.clear(); shown.clear(); rc_seq[:] = [1, 0]
        maude.open_project(proj, "auto", tool="Codex")
        assert [r[1:] for r in runs] == [["resume", "--last"], []] and not shown, (runs, shown)  # retried fresh, ok
        runs.clear(); shown.clear(); rc_seq[:] = [1, 2]
        maude.open_project(proj, "auto", tool="Codex")
        assert len(runs) == 2 and shown and "exited with code 2" in shown[0] and "try Model" not in shown[0], (runs, shown)
        runs.clear(); shown.clear(); rc_seq[:] = [0]
        maude.open_project(proj, "auto", tool="Codex", fresh=True)
        assert len(runs) == 1 and not shown, (runs, shown)
        for code in (130, 0xC000013A, -2, -15):                        # a deliberate quit: no retry, no pause
            runs.clear(); shown.clear(); rc_seq[:] = [code]
            maude.open_project(proj, "gpt-6.2-sol", tool="Codex")
            assert len(runs) == 1 and not shown, (code, runs, shown)
    finally:
        builtins.input = real_input
    mx._run = lambda cmd, **kw: envs.append((cmd[0], kw.get("env"))) or 0
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
    # Rapid mode switching on the Settings screen (no pauses): no crash, exactly one pane visible.
    async def check_rapid():
        async with maude.MaudeApp().run_test(size=(140, 45)) as pilot:
            app = pilot.app
            await pilot.pause()
            app.query_one("#btn-creds").press(); await pilot.pause()
            await wait_until(pilot, lambda: isinstance(app.screen, maude.CredsEntryScreen))
            scr = app.screen
            assert isinstance(scr, maude.CredsEntryScreen)
            await wait_ui_ready(pilot, scr)
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
            await wait_ui_ready(pilot, scr)
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
    # Settings screen (formerly Set Creds): label, native-only checkbox (persisted + re-greys the Agent column),
    # Anthropic pane (key active/inactive), OpenCode pane, and nothing from hidden panes is saved.
    from textual.widgets import RadioButton, Checkbox, Button
    oc_cfg = home / ".config" / "opencode" / "opencode.json"
    oc_cfg.parent.mkdir(parents=True)
    oc_cfg.write_text('{"model": "zai-coding-plan/glm-5.2", "theme": "t", "//": "managed by maude - x", '
                      '"provider": {"zai-coding-plan": {"options": {"apiKey": "{env:ZHIPU_API_KEY}"}}}}')
    settings_file = home / ".maude" / "settings.json"

    async def open_settings(pilot):
        app = pilot.app
        await wait_table(pilot)
        btn = app.query_one("#btn-creds")
        assert str(btn.label) == "Settings", btn.label
        btn.press(); await pilot.pause()
        await wait_until(pilot, lambda: isinstance(app.screen, maude.CredsEntryScreen))
        assert isinstance(app.screen, maude.CredsEntryScreen), app.screen
        await pilot.pause()
        await wait_query(pilot, lambda: app.screen, "#btn-creds-save")
        await wait_ui_ready(pilot, app.screen)
        return app.screen

    async def press_mode(pilot, scr, mode):
        (await wait_query(pilot, lambda: scr, "#btn-creds-mode-" + mode)).press(); await pilot.pause()
        await wait_until(pilot, lambda: scr.query_one("#creds-pane-" + mode).display)
        for m in ("paste", "anthropic", "bedrock", "foundry", "opencode"):
            assert scr.query_one("#creds-pane-" + m).display == (m == mode), (m, mode)
        # the screen moves focus to the pane's first field after the refresh: let that settle before the test focuses things
        await wait_until(pilot, lambda: scr.focused is not None and "#" + scr.focused.id == scr._FOCUS[mode])

    async def save_and_close(pilot, scr):
        scr.query_one("#btn-creds-save").press(); await pilot.pause()
        await wait_until(pilot, lambda: len(pilot.app.screen_stack) == 1)
        assert len(pilot.app.screen_stack) == 1, (pilot.app.screen_stack, "mode=" + scr.mode, "status=" + str(scr.query_one("#creds-status").render()),
                                                    "focus=" + str(scr.focused), "rc=" + (rc_file.read_text() if rc_file.exists() else "-"))

    async def check_settings_screen():
        disabled = lambda a: [str(b.label) for b in a.query_one("#agent-select").query(RadioButton) if b.disabled]
        akey_file, okey_file = home / ".maude" / "anthropic-key", home / ".maude" / "opencode-key"
        maude.save_settings({"native_only_approved": True})
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:
            app = pilot.app
            scr = await open_settings(pilot)
            assert str((await wait_query(pilot, lambda: scr, "#creds-title")).render()) == "Settings"
            cb = await wait_query(pilot, lambda: scr, "#creds-native-only")  # visible above the mode buttons, whatever pane
            assert cb.value is True and "Only allow Kiro.dev and Github Copilot outside the Sandbox" in str(cb.label), cb.label
            assert cb.region.height == 1 and scr.query_one("#creds-native-only").region.y < scr.query_one("#creds-tabs").region.y
            assert len(disabled(app)) == len(maude.AGENTS) - 2, disabled(app)
            # Anthropic pane: Claude Login button inside it, masked key field, "Use this API key" unchecked
            await press_mode(pilot, scr, "anthropic")
            assert str(scr.query_one("#btn-creds-login", Button).label) == "Claude Login"
            assert scr.query_one("#creds-anthropic-key").password is True
            assert scr.query_one("#creds-anthropic-use", Checkbox).value is False
            assert "even if you are logged in" in str(scr.query_one("#creds-pane-anthropic").query("Label").last().render())
            assert scr.query_one("#creds-oc-key").password is True
            # M3: Save with nothing changed in the Anthropic pane touches no key file
            scr.query_one("#btn-creds-save").press(); await pilot.pause()
            await wait_until(pilot, lambda: len(app.screen_stack) == 1)
            assert not akey_file.exists() and not rc_file.exists(), "an unchanged Anthropic pane must not write"
            scr = await open_settings(pilot)
            cb = scr.query_one("#creds-native-only", Checkbox)
            await press_mode(pilot, scr, "anthropic")
            # text typed in panes that are not the active mode is never saved
            (await wait_query(pilot, lambda: scr, "#creds-oc-url")).value = "https://hidden.example/v1"
            scr.query_one("#creds-oc-model").value = "hidden-model"
            scr.query_one("#creds-oc-key").value = "oc-hidden-key"
            scr.query_one("#creds-text").text = "export ANTHROPIC_API_KEY=sk-ant-hidden-paste\n"
            scr.query_one("#creds-anthropic-key").value = "sk-ant-test1"
            scr.query_one("#creds-anthropic-use", Checkbox).value = True
            cb.value = False                                   # native-only off
            await save_and_close(pilot, scr)
            await wait_until(pilot, lambda: disabled(app) == [])
            assert disabled(app) == [], disabled(app)          # apply_agent_restrictions() ran: everything enabled
        sj = settings_file.read_text()
        assert maude.load_settings()["native_only_approved"] is False and maude.load_settings()["anthropic_key_active"] is True, sj
        assert akey_file.read_text() == "sk-ant-test1" and "sk-ant-test1" not in sj, "the key lives in its own file"
        assert not rc_file.exists(), "natively the active key is injected, never written to the clauderc"
        assert not any(s in sj + akey_file.read_text() + oc_cfg.read_text() for s in
                       ("hidden-paste", "oc-hidden-key", "hidden.example", "hidden-model")), "hidden panes were saved"
        assert not okey_file.exists()
        assert maude.plat_claude_env().get("ANTHROPIC_API_KEY") == "sk-ant-test1"   # wins even over a claude login
        # Second run: state is shown; deactivating keeps the key file and injects nothing
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:
            app = pilot.app
            scr = await open_settings(pilot)
            assert scr.query_one("#creds-native-only", Checkbox).value is False
            await press_mode(pilot, scr, "anthropic")
            use = scr.query_one("#creds-anthropic-use", Checkbox)
            assert use.value is True and scr.query_one("#creds-anthropic-key").value == ""  # the key is never shown
            use.value = False
            await save_and_close(pilot, scr)
        assert akey_file.read_text() == "sk-ant-test1" and maude.load_settings()["anthropic_key_active"] is False
        assert maude.plat_claude_env() == {} and maude._child_env() is None
        # Activating again with an empty field reuses the stored key; the OpenCode pane merges non-destructively
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:
            app = pilot.app
            scr = await open_settings(pilot)
            await press_mode(pilot, scr, "anthropic")
            scr.query_one("#creds-anthropic-use", Checkbox).value = True
            await save_and_close(pilot, scr)
            assert maude.plat_claude_env().get("ANTHROPIC_API_KEY") == "sk-ant-test1", maude.plat_claude_env()
            scr = await open_settings(pilot)
            await press_mode(pilot, scr, "opencode")
            scr.query_one("#creds-oc-model").value = "qwen3-coder"
            scr.query_one("#creds-oc-key").value = "oc-secret"
            for bad in ("ftp://nope", "http://h.example/v1", "https://u:p@h.example/v1", "https://h.example/{env:X}"):
                scr.query_one("#creds-oc-url").value = bad        # invalid: stays open with an error, saves nothing
                scr.query_one("#creds-status").update("")
                scr.query_one("#btn-creds-save").press(); await pilot.pause()
                await wait_until(pilot, lambda: str(scr.query_one("#creds-status").render()) != "")
                assert isinstance(app.screen, maude.CredsEntryScreen) and str(scr.query_one("#creds-status").render()), bad
                assert not okey_file.exists() and "maude-custom" not in oc_cfg.read_text(), bad
            scr.query_one("#creds-oc-url").value = "https://llm.example/v1"
            scr.query_one("#creds-oc-model").value = "bad#model"   # a # in the model name is rejected
            scr.query_one("#creds-status").update("")
            scr.query_one("#btn-creds-save").press(); await pilot.pause()
            await wait_until(pilot, lambda: str(scr.query_one("#creds-status").render()) != "")
            assert isinstance(app.screen, maude.CredsEntryScreen) and not okey_file.exists() and "maude-custom" not in oc_cfg.read_text()
            scr.query_one("#creds-oc-model").value = "qwen3-coder"
            scr.query_one("#creds-native-only", Checkbox).value = True
            await save_and_close(pilot, scr)
            await wait_until(pilot, lambda: len(disabled(app)) == len(maude.AGENTS) - 2)
            assert len(disabled(app)) == len(maude.AGENTS) - 2, disabled(app)  # checked again: greyed out again
        assert okey_file.read_text() == "oc-secret" and not rc_file.exists()
        # A commented opencode.json is refused with a message, the key file is not written, and nothing is half-saved
        keep = oc_cfg.read_text()
        oc_cfg.write_text('{ // my notes\n "theme": "t" }')
        okey_file.unlink()
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:
            app = pilot.app
            scr = await open_settings(pilot)
            await press_mode(pilot, scr, "opencode")
            scr.query_one("#creds-oc-url").value = "https://llm.example/v1"
            scr.query_one("#creds-oc-model").value = "m"
            scr.query_one("#creds-oc-key").value = "oc-secret2"
            scr.query_one("#btn-creds-save").press(); await pilot.pause()
            await wait_until(pilot, lambda: "comments" in str(scr.query_one("#creds-status").render()))
            assert isinstance(app.screen, maude.CredsEntryScreen) and "comments" in str(scr.query_one("#creds-status").render())
            assert not okey_file.exists() and oc_cfg.read_text().startswith("{ // my notes")
            scr.query_one("#btn-creds-cancel").press(); await pilot.pause()
            await wait_until(pilot, lambda: len(app.screen_stack) == 1)
        oc_cfg.write_text(keep)
        okey_file.write_text("oc-secret")
        # The Remove button deletes the custom provider and the key file
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:
            app = pilot.app
            scr = await open_settings(pilot)
            await press_mode(pilot, scr, "opencode")
            assert scr.query_one("#creds-oc-url").value == "https://llm.example/v1" and scr.query_one("#creds-oc-model").value == "qwen3-coder"
            scr.query_one("#btn-oc-remove").press(); await pilot.pause(); await pilot.pause()
            await wait_until(pilot, lambda: not okey_file.exists())
            assert not okey_file.exists() and scr.query_one("#creds-oc-url").value == ""
            rm = _json.loads(oc_cfg.read_text())
            assert "maude-custom" not in rm["provider"] and rm["model"] == "zai-coding-plan/glm-5.2", rm  # the previous model is back
            scr.query_one("#btn-creds-cancel").press(); await pilot.pause()
            await wait_until(pilot, lambda: len(app.screen_stack) == 1)
        # Put the custom provider back for the final checks below
        maude.merge_opencode_config("https://llm.example/v1", "qwen3-coder", True)
        maude.write_secret(okey_file, "oc-secret")
        # Reach: at 80x24 and 100x30 every pane's fields and buttons can be focused and shown inside the body
        for size in ((80, 24), (100, 30)):
            async with maude.MaudeApp().run_test(size=size) as pilot:
                app = pilot.app
                scr = await open_settings(pilot)
                body = scr.query_one("#creds-body")
                for tab in ("paste", "anthropic", "bedrock", "foundry", "opencode"):
                    b = scr.query_one("#btn-creds-mode-" + tab)
                    assert b.region.x >= 0 and b.region.right <= size[0] and b.region.bottom <= size[1], (size, tab, b.region)
                    await press_mode(pilot, scr, tab)
                    widgets = list(scr.query_one("#creds-pane-" + tab).query("Input, TextArea, Button, Checkbox"))
                    assert widgets, tab
                    for w in widgets:
                        w.focus()
                        ok = await wait_until(pilot, lambda: scr.focused is w and w.region.height > 0 and w.region.overlaps(body.region))
                        assert ok, (size, tab, w.id, w.region, body.region, body.scroll_y, body.max_scroll_y, body.virtual_size, scr.focused)
                    first = next(w for w in widgets if w.id in ("creds-text", "creds-anthropic-key", "creds-aws-key", "creds-foundry-url", "creds-oc-url"))
                    first.focus()
                    if tab != "paste":  # the paste area is taller than the body at 24 rows: it scrolls
                        assert await wait_until(pilot, lambda: body.region.contains_region(first.region)), (size, tab, first.region, body.region)
                for sel in ("#creds-native-only", "#btn-creds-save", "#btn-creds-cancel"):
                    w = scr.query_one(sel); w.focus(); await pilot.pause()
                    assert w.region.height > 0 and w.region.bottom <= size[1] and w.region.right <= size[0], (size, sel, w.region)
                st = scr.query_one("#creds-status")
                assert st.region.height >= 1 and st.region.right <= size[0] and st.region.bottom <= size[1], (size, st.region)
                scr.query_one("#btn-creds-cancel").press(); await pilot.pause()
                await wait_until(pilot, lambda: len(app.screen_stack) == 1)
    import json as _json
    saved_env = dict(os.environ)
    try:
        asyncio.run(check_settings_screen())
    finally:
        os.environ.clear(); os.environ.update(saved_env)
    import json as _json
    oc = _json.loads(oc_cfg.read_text())
    assert oc["theme"] == "t" and "//" not in oc and oc["model"] == "maude-custom/qwen3-coder", oc
    assert oc["provider"]["zai-coding-plan"]["options"]["apiKey"] == "{env:ZHIPU_API_KEY}", oc
    mc = oc["provider"]["maude-custom"]
    assert mc["npm"] == "@ai-sdk/openai-compatible" and mc["options"] == {"baseURL": "https://llm.example/v1", "apiKey": "{file:~/.maude/opencode-key}"}, mc
    assert "qwen3-coder" in mc["models"] and "oc-secret" not in oc_cfg.read_text()
    assert (oc_cfg.parent / "opencode.json.maude-bak").is_file()
    assert (home / ".maude" / "opencode-key").read_text() == "oc-secret" and maude.load_settings()["native_only_approved"] is True
    (home / ".maude" / "opencode-key").unlink(); (home / ".maude" / "anthropic-key").unlink()
    # "Default to AWS Bedrock": only a flip of the box during this Save parks clauderc and switches the key off
    inactive_file = home / ".maude" / "clauderc.inactive"
    async def check_default_bedrock():
        akey_file = home / ".maude" / "anthropic-key"
        (home / ".aws").mkdir(exist_ok=True)
        (home / ".aws" / "credentials").write_text("[bedrock]\naws_access_key_id = A\n")
        maude.merge_clauderc({"ANTHROPIC_FOUNDRY_API_KEY": "fk", "CLAUDE_CODE_USE_FOUNDRY": "1"})
        maude.write_secret(akey_file, "sk-ant-b1")
        s = maude.load_settings(); s["anthropic_key_active"] = True; s["native_only_approved"] = True; maude.save_settings(s)
        # 0) BOTH files present when the screen opens (a stale clauderc.inactive next to an active Foundry setup): the
        #    box reads unchecked, and an unflipped Paste Save leaves clauderc, clauderc.inactive and the env untouched
        maude.write_clauderc({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2"}, inactive_file)
        flags = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_FOUNDRY")
        env_before = {k: os.environ.get(k) for k in flags}
        rc_before, inactive_before = rc_file.read_text(), inactive_file.read_text()
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:
            scr = await open_settings(pilot)
            cb = await wait_query(pilot, lambda: scr, "#creds-default-bedrock")
            await wait_until(pilot, lambda: cb.display)
            assert cb.display and cb.value is False and scr._default_bedrock_initial is False, (cb.display, cb.value)
            await save_and_close(pilot, scr)
        assert "CLAUDE_CODE_USE_FOUNDRY" in rc_file.read_text()  # the Foundry setup stays active
        assert rc_file.read_text() == rc_before and inactive_file.read_text() == inactive_before, "an unflipped Save must change no file"
        assert {k: os.environ.get(k) for k in flags} == env_before, "an unflipped Save must not change the environment"
        inactive_file.unlink()
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:   # 1) box shown, not flipped: a plain Save keeps the key
            scr = await open_settings(pilot)
            cb = await wait_query(pilot, lambda: scr, "#creds-default-bedrock")
            await wait_until(pilot, lambda: cb.display and scr._default_bedrock_initial == cb.value)
            assert cb.display and cb.value is False and scr._default_bedrock_initial is False, (cb.display, cb.value)
            await save_and_close(pilot, scr)
        assert maude.load_settings()["anthropic_key_active"] is True and rc_file.exists() and not inactive_file.exists()
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:   # 2) flipped to Bedrock: key off, clauderc parked, key kept
            scr = await open_settings(pilot)
            cb = await wait_query(pilot, lambda: scr, "#creds-default-bedrock")
            await wait_until(pilot, lambda: cb.display and scr._default_bedrock_initial == cb.value)
            cb.value = True
            await save_and_close(pilot, scr)
        assert maude.load_settings()["anthropic_key_active"] is False and akey_file.read_text() == "sk-ant-b1"
        assert inactive_file.exists() and not rc_file.exists() and maude.plat_claude_env().get("ANTHROPIC_API_KEY") != "sk-ant-b1"
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:   # 3) shown as Bedrock; activate the key (box untouched)
            scr = await open_settings(pilot)
            cb = await wait_query(pilot, lambda: scr, "#creds-default-bedrock")
            await wait_until(pilot, lambda: cb.display and scr._default_bedrock_initial == cb.value)
            assert cb.display and cb.value is True and scr._default_bedrock_initial is True, (cb.display, cb.value)
            await press_mode(pilot, scr, "anthropic")
            scr.query_one("#creds-anthropic-use", Checkbox).value = True
            await save_and_close(pilot, scr)
        assert maude.load_settings()["anthropic_key_active"] is True and inactive_file.exists()
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:   # 4) a Paste Save with the box still unflipped keeps the key
            scr = await open_settings(pilot)
            cb4 = await wait_query(pilot, lambda: scr, "#creds-default-bedrock")
            await wait_until(pilot, lambda: cb4.display and cb4.value is True and scr._default_bedrock_initial is True)
            await save_and_close(pilot, scr)
        assert maude.load_settings()["anthropic_key_active"] is True and inactive_file.exists() and not rc_file.exists()
    # Sandbox: API key mode is not available yet: the key field and the checkbox are disabled with a note and the
    # Anthropic pane saves nothing (only this screen runs with the sandbox flags; a stale flag in settings.json is ignored)
    async def check_sandbox_settings():
        akey_file, azure_rc = home / ".maude" / "anthropic-key", home / ".azure" / "clauderc"
        s = maude.load_settings(); s["anthropic_key_active"] = True; maude.save_settings(s)
        maude.write_secret(akey_file, "sk-ant-stale")
        before = settings_file.read_text()
        async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:
            app = pilot.app
            await wait_table(pilot)
            maude.IS_WIN = False
            try:
                app.push_screen(maude.CredsEntryScreen())
                await wait_until(pilot, lambda: isinstance(app.screen, maude.CredsEntryScreen))
                scr = app.screen
                await wait_query(pilot, lambda: scr, "#btn-creds-save")
                await wait_ui_ready(pilot, scr)
                assert len(scr.query("#creds-native-only")) == 0, "no native-only checkbox in the sandbox"
                await press_mode(pilot, scr, "anthropic")
                key, use = scr.query_one("#creds-anthropic-key"), scr.query_one("#creds-anthropic-use", Checkbox)
                assert key.disabled and use.disabled and use.value is False, (key.disabled, use.disabled, use.value)
                note = str(scr.query_one("#creds-anthropic-note").render())
                assert "not available in the sandbox yet" in note and "Claude Login" in note and "Paste" in note, note
                assert not scr.query_one("#btn-creds-login", Button).disabled
                key.value = "sk-ant-typed"; use.value = True       # set in code: Save must still write nothing
                scr.query_one("#btn-creds-save").press(); await pilot.pause()
                await wait_until(pilot, lambda: len(app.screen_stack) == 1)
                assert len(app.screen_stack) == 1, (app.screen_stack, str(scr.query_one("#creds-status").render()))
            finally:
                maude.IS_WIN = True
        assert settings_file.read_text() == before and akey_file.read_text() == "sk-ant-stale", "sandbox save wrote something"
        assert not azure_rc.exists() and not rc_file.exists()
        akey_file.unlink()
        s = maude.load_settings(); s.pop("anthropic_key_active", None); maude.save_settings(s)
    asyncio.run(check_sandbox_settings())
    saved_env = dict(os.environ)
    try:
        asyncio.run(check_default_bedrock())
    finally:
        os.environ.clear(); os.environ.update(saved_env)
        for f in (home / ".aws" / "credentials", rc_file, inactive_file, home / ".maude" / "anthropic-key"):
            if f.exists():
                f.unlink()
        s = maude.load_settings(); s.pop("anthropic_key_active", None); maude.save_settings(s)
    # Parked on Bedrock (only clauderc.inactive), box shown checked and NOT flipped: a Save from Paste, Bedrock or Foundry
    # re-creates the active clauderc, which must be parked again (merged) so Bedrock stays effective and the box checked.
    async def check_parked_unflipped():
        azure = home / ".azure"
        (home / ".aws").mkdir(exist_ok=True)
        (home / ".aws" / "credentials").write_text("[bedrock]\naws_access_key_id = A\n")
        (home / ".aws" / "config").write_text("[profile bedrock]\nregion = us-west-2\n")
        s = maude.load_settings(); s["native_only_approved"] = True; maude.save_settings(s)

        async def run_case(mode, sandbox):
            rcp = (azure / "clauderc") if sandbox else rc_file
            inp = rcp.with_name("clauderc.inactive")
            for f in (rc_file, inactive_file, azure / "clauderc", azure / "clauderc.inactive"):
                if f.exists():
                    f.unlink()
            maude.write_clauderc({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2", "ANTHROPIC_FOUNDRY_API_KEY": "oldfk"}, inp)
            async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:
                app = pilot.app
                await wait_table(pilot)
                try:
                    if sandbox:
                        maude.IS_WIN = False
                        app.push_screen(maude.CredsEntryScreen())
                        await wait_until(pilot, lambda: isinstance(app.screen, maude.CredsEntryScreen))
                        scr = app.screen
                        await wait_query(pilot, lambda: scr, "#btn-creds-save")
                        await wait_ui_ready(pilot, scr)
                    else:
                        scr = await open_settings(pilot)
                    cb = await wait_query(pilot, lambda: scr, "#creds-default-bedrock")
                    await wait_until(pilot, lambda: cb.display and cb.value is True and scr._parked_at_open)
                    assert cb.value is True and scr._parked_at_open and scr._default_bedrock_initial, (mode, sandbox)
                    if mode == "paste":
                        scr.query_one("#creds-text").text = "export AWS_REGION=eu-west-1\n"
                    elif mode == "bedrock":
                        await press_mode(pilot, scr, "bedrock")
                        scr.query_one("#creds-aws-key").value = "AKIAXX"; scr.query_one("#creds-aws-secret").value = "sec"
                    else:
                        await press_mode(pilot, scr, "foundry")
                        scr.query_one("#creds-foundry-url").value = "https://new.example/anthropic"; scr.query_one("#creds-foundry-key").value = "newfk"
                    scr.query_one("#btn-creds-save").press(); await pilot.pause()
                    await wait_until(pilot, lambda: len(app.screen_stack) == 1)
                    assert len(app.screen_stack) == 1, (mode, sandbox, str(scr.query_one("#creds-status").render()))
                    # ended parked: no active file, the saved values merged into the parked one, the reader says Bedrock
                    assert not rcp.exists() and inp.exists(), (mode, sandbox, rcp.exists(), inp.exists())
                    parked = maude.read_clauderc(inp)
                    assert parked.get("CLAUDE_CODE_USE_BEDROCK") == "1" and maude.bedrock_parked_state() is True, (mode, sandbox, parked)
                    if mode == "paste":
                        assert parked.get("AWS_REGION") == "eu-west-1", parked
                    elif mode == "bedrock":
                        assert parked.get("AWS_ACCESS_KEY_ID") == "AKIAXX", parked
                    else:
                        assert parked.get("ANTHROPIC_FOUNDRY_API_KEY") == "newfk", parked
                        await wait_until(pilot, lambda: any("Bedrock stays the default" in n.message for n in app._notifications))
                        assert any("Bedrock stays the default" in n.message for n in app._notifications), list(app._notifications)
                    if not sandbox:  # Bedrock is what Claude Code gets, not "no backend" and not the Foundry endpoint
                        e = maude.plat_claude_env()
                        assert e.get("CLAUDE_CODE_USE_BEDROCK") == "1" and e.get("ANTHROPIC_BASE_URL") != "https://new.example/anthropic", (mode, e)
                finally:
                    maude.IS_WIN = True
            if not sandbox:  # reopening: still shown as the Bedrock default
                async with maude.MaudeApp().run_test(size=(140, 50)) as pilot:
                    scr = await open_settings(pilot)
                    cb = await wait_query(pilot, lambda: scr, "#creds-default-bedrock")
                    await wait_until(pilot, lambda: cb.display and cb.value is True)
                    assert cb.value is True and scr._parked_at_open, mode
                    scr.query_one("#btn-creds-cancel").press(); await pilot.pause()
                    await wait_until(pilot, lambda: len(pilot.app.screen_stack) == 1)

        for mode in ("paste", "bedrock", "foundry"):
            await run_case(mode, False)
        await run_case("paste", True)     # the same with the sandbox paths (~/.azure/clauderc)
        await run_case("foundry", True)
    saved_env = dict(os.environ)
    try:
        asyncio.run(check_parked_unflipped())
    finally:
        os.environ.clear(); os.environ.update(saved_env)
        for f in (home / ".aws" / "credentials", home / ".aws" / "config", rc_file, inactive_file,
                  home / ".azure" / "clauderc", home / ".azure" / "clauderc.inactive"):
            if f.exists():
                f.unlink()    # Setup Git(hub) and To Github are on natively: the wizard opens with the native titles, the push screen
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
            await wait_ui_ready(pilot, app.screen)
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
    # M2: a model label that looks like markup never crashes compose or the Model column (labels are rich Text).
    from textual.widgets import RadioButton as _RB, Checkbox as _CB
    real_am = maude.agent_models
    maude.agent_models = lambda a, cfg=None: [("auto", "auto"), ("[/x]", "m1"), ("[b]y", "m2")]
    async def check_markup():
        async with maude.MaudeApp().run_test(size=(120, 35)) as pilot:
            app = pilot.app
            shown = lambda: [str(b.label) for b in app.query_one("#model-select").query(_RB) if b.display]
            await wait_until(pilot, lambda: shown() == ["auto", "[/x]", "[b]y"])
            assert shown() == ["auto", "[/x]", "[b]y"], shown()
    try:
        asyncio.run(check_markup())
    finally:
        maude.agent_models = real_am
    assert maude._clean_label("[/x]") == "" and maude._clean_label("ok") == "ok"
    # M3: the sidebar scrolls instead of clipping. At 80x24, 100x30 and 120x30 every picker, every button and the
    # toggles can be reached with the keyboard (Tab, arrows) and with the mouse (after scrolling).
    maude.save_settings({"native_only_approved": False}); maude.save_tool("Kiro CLI")
    async def check_reach(size):
        async with maude.MaudeApp().run_test(size=size) as pilot:
            app = pilot.app
            await pilot.pause()
            sb = app.query_one("#sidebar")
            await wait_until(pilot, lambda: sb.virtual_size.height > 0 and app.query_one("#perplexity").region.height > 0)
            assert sb.styles.overflow_y == "auto", sb.styles.overflow_y
            assert sb.max_scroll_x == 0, (size, "no horizontal clipping", sb.virtual_size, sb.size)
            def visible(w):
                return w.region.height > 0 and w.region.y >= sb.region.y and w.region.bottom <= sb.region.bottom
            # focusing a widget scrolls it into view
            for sel in ("#agent-select", "#model-select", "#effort-select", "#fresh-context", "#perplexity"):
                w = app.query_one(sel); w.focus(); await pilot.pause()
                await wait_until(pilot, lambda: visible(w))
                assert visible(w), (size, sel, w.region, sb.region, sb.scroll_y)
            # arrows inside the radio sets move the cursor onto buttons that are scrolled into view
            for sel in ("#agent-select", "#model-select"):
                rs = app.query_one(sel); rs.focus(); await pilot.pause()
                for _ in range(len([b for b in rs.query(_RB) if b.display])):
                    await pilot.press("down"); await pilot.pause()
                    cur = rs._nodes[rs._selected]
                    await wait_until(pilot, lambda: visible(cur))
                    assert visible(cur), (size, sel, cur.label, cur.region, sb.region)
            # Tab walks the whole sidebar; each focused widget in it is visible; Perplexity MCP is reached
            app.query_one("#projects-table").focus(); await pilot.pause()
            seen = []
            for _ in range(14):
                await pilot.press("tab"); await pilot.pause()
                f = app.focused
                if f is not None and sb in f.ancestors:
                    await wait_until(pilot, lambda: visible(f))
                    assert visible(f), (size, f.id, f.region, sb.region)
                    seen.append(f.id)
            assert {"agent-select", "model-select", "effort-select", "fresh-context", "perplexity"} <= set(seen), (size, seen)
            # mouse: scroll to the bottom, click a toggle that was clipped, scroll back, click an agent
            sb.scroll_end(animate=False); await pilot.pause()
            cb = app.query_one("#fresh-context", _CB)
            await wait_until(pilot, lambda: visible(cb))
            await pilot.click("#fresh-context"); await pilot.pause()
            await wait_until(pilot, lambda: cb.value)
            assert cb.value, (size, "click on a scrolled toggle")
            await pilot.click("#fresh-context"); await pilot.pause()
            await wait_until(pilot, lambda: not cb.value)
            sb.scroll_home(animate=False); await pilot.pause()
            kiro = app.query_one("#agent-select").query(_RB)[4]
            await wait_until(pilot, lambda: visible(kiro))
            await pilot.click(kiro); await pilot.pause()
            assert app._tool == "Kiro CLI", app._tool
    for size in ((80, 24), (100, 30), (120, 30), (120, 35)):
        asyncio.run(check_reach(size))
    maude.save_settings({"native_only_approved": True}); maude.save_tool("Claude Code")
    print("win_tui_smoke ok")
finally:
    os.chdir(LIGHT)
    shutil.rmtree(d, ignore_errors=True)
