#!/usr/bin/env python3
"""
maude.py — Textual TUI for the Maude sandbox.
Always launched via:  maude tui
"""

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

PROJECTS_DIR = Path.home() / "Maude" / "Projects"
DELETED_DIR  = PROJECTS_DIR / ".deleted"
AUTOSTART_FLAG = Path.home() / ".maude-tui-autostart"
KANNA_CMD    = "kanna"

LOGO = (
    "  __  __                 _      \n"
    " |  \\/  | __ _ _   _  __| | ___ \n"
    " | |\\/| |/ _` | | | |/ _` |/ _ \\\n"
    " | |  | | (_| | |_| | (_| |  __/\n"
    " |_|  |_|\\__,_|\\__,_|\\__,_|\\___|"
)


# ── Helpers ────────────────────────────────────────────────────────────────

def list_projects() -> list[dict]:
    """Return sorted list of project dicts with name and mtime."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    projects = []
    for p in sorted(PROJECTS_DIR.iterdir()):
        if p.is_dir() and not p.name.startswith("."):
            try:
                mtime = p.stat().st_mtime
                modified = datetime.fromtimestamp(mtime).strftime("%b %d, %Y")
            except OSError:
                modified = "unknown"
            projects.append({"name": p.name, "modified": modified, "path": p})
    return projects


def slugify(name: str) -> str:
    """Replace spaces with hyphens; keep letters, digits, dots, dashes, underscores."""
    name = name.replace(" ", "-")
    name = re.sub(r"[^a-zA-Z0-9._-]", "", name)
    return name.strip("-")


def open_project(project_path: Path) -> None:
    """Launch Claude Code for a project. Try --continue first, then fresh."""
    os.chdir(project_path)
    ret = subprocess.run(["claude", "opus-1m", "--continue"], check=False).returncode
    if ret != 0:
        subprocess.run(["claude", "opus-1m"], check=False)


def soft_delete(project_path: Path) -> None:
    """Move project to .deleted/."""
    DELETED_DIR.mkdir(parents=True, exist_ok=True)
    dest = DELETED_DIR / project_path.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(project_path), dest)


# ── Modal screens ──────────────────────────────────────────────────────────

class ConfirmDeleteScreen(ModalScreen[bool]):
    """Ask the user to confirm deletion."""

    def __init__(self, project_name: str) -> None:
        super().__init__()
        self.project_name = project_name

    def compose(self) -> ComposeResult:
        with Container(id="confirm-box"):
            yield Label(f"Delete project '{self.project_name}'?", id="confirm-title")
            yield Label("It will be moved to .deleted/  and can be recovered manually.",
                        id="confirm-sub")
            with Horizontal(id="confirm-buttons"):
                yield Button("Delete", variant="error",   id="btn-yes")
                yield Button("Cancel", variant="primary", id="btn-no")

    @on(Button.Pressed, "#btn-yes")
    def confirmed(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#btn-no")
    def cancelled(self) -> None:
        self.dismiss(False)


class NewProjectScreen(ModalScreen[str | None]):
    """Prompt for a new project name."""

    def compose(self) -> ComposeResult:
        with Container(id="new-box"):
            yield Label("New Project", id="new-title")
            yield Label("Spaces are replaced with hyphens automatically.", id="new-sub")
            yield Input(placeholder="project-name", id="new-input")
            with Horizontal(id="new-buttons"):
                yield Button("Create", variant="success", id="btn-create")
                yield Button("Cancel", variant="primary", id="btn-cancel")

    @on(Button.Pressed, "#btn-create")
    def create(self) -> None:
        raw = self.query_one("#new-input", Input).value.strip()
        name = slugify(raw)
        self.dismiss(name if name else None)

    @on(Button.Pressed, "#btn-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def submitted(self) -> None:
        self.create()


# ── Main app ───────────────────────────────────────────────────────────────

class MaudeApp(App):
    """Maude TUI — project launcher for Claude Code."""

    CSS = """
    /* ── Palette (dark purple terminal friendly) ── */
    $bg:         #1a1625;
    $sidebar-bg: #211b30;
    $border:     #5a4e78;
    $logo-green: #72c09a;
    $label-dim:  #9080b8;
    $title:      #c4aff0;
    $row-sel:    #2e2544;

    Screen {
        background: $bg;
    }

    Header {
        background: #251e38;
        color: $title;
    }

    Footer {
        background: #251e38;
        color: $label-dim;
    }

    #layout {
        height: 1fr;
    }

    #sidebar {
        width: 36;
        padding: 1 2;
        background: $sidebar-bg;
        border-right: solid $border;
    }

    #logo {
        height: 5;
        color: $logo-green;
        text-style: bold;
        margin-bottom: 1;
    }

    #divider {
        color: $border;
        height: 1;
        margin-bottom: 1;
    }

    #autostart-label {
        color: $label-dim;
        margin-top: 1;
        margin-bottom: 0;
    }

    #autostart {
        margin-top: 0;
    }

    #main {
        padding: 1 2;
        background: $bg;
    }

    #section-title {
        color: $title;
        text-style: bold;
        margin-bottom: 1;
    }

    #projects-table {
        height: 1fr;
        border: solid $border;
    }

    DataTable > .datatable--header {
        background: #2a2040;
        color: $title;
    }

    DataTable > .datatable--cursor {
        background: $row-sel;
        color: #e8d8ff;
    }

    #bottom-bar {
        height: 5;
        padding: 1 2;
        align: left middle;
        border-top: solid $border;
        background: $sidebar-bg;
    }

    #bottom-bar Button {
        margin-right: 1;
    }

    #btn-open { background: #3d6b52; color: #c8f0d8; border: tall #4e8a69; }
    #btn-new  { background: #3d4e7a; color: #c8d4f8; border: tall #5060a0; }
    #btn-web  { background: #3a3550; color: #c0b8e0; border: tall #504870; }
    #btn-cli  { background: #2e2a40; color: #a098c0; border: tall #443e60; }

    /* Modal: confirm delete */
    ConfirmDeleteScreen {
        align: center middle;
        background: rgba(0,0,0,0.6);
    }

    #confirm-box {
        padding: 2 4;
        width: 60;
        height: auto;
        border: solid #8a4a5a;
        background: #251830;
    }

    #confirm-title {
        text-style: bold;
        color: #e08090;
        margin-bottom: 1;
    }

    #confirm-sub {
        color: $label-dim;
        margin-bottom: 2;
    }

    #confirm-buttons {
        height: auto;
        align: center middle;
    }

    #confirm-buttons Button {
        margin: 0 1;
    }

    /* Modal: new project */
    NewProjectScreen {
        align: center middle;
        background: rgba(0,0,0,0.6);
    }

    #new-box {
        padding: 2 4;
        width: 60;
        height: auto;
        border: solid #4a7a5a;
        background: #1a2530;
    }

    #new-title {
        text-style: bold;
        color: $logo-green;
        margin-bottom: 1;
    }

    #new-sub {
        color: $label-dim;
        margin-bottom: 1;
    }

    #new-input {
        margin-bottom: 2;
    }

    #new-buttons {
        height: auto;
        align: center middle;
    }

    #new-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("enter", "open_selected", "Open", show=True),
        Binding("n",     "new_project",   "New",  show=True),
        Binding("d",     "delete_selected","Delete",show=True),
        Binding("q",     "quit_to_shell", "Quit", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Static(LOGO, id="logo", markup=False)
                yield Static("─" * 28, id="divider")
                yield Label("Start TUI with Maude", id="autostart-label")
                yield Checkbox("", value=AUTOSTART_FLAG.exists(), id="autostart")
            with Vertical(id="main"):
                yield Label("Projects", id="section-title")
                yield DataTable(id="projects-table", cursor_type="row",
                                zebra_stripes=True)
        with Horizontal(id="bottom-bar"):
            yield Button("Open Project", id="btn-open")
            yield Button("+ New",        id="btn-new")
            yield Button("Web UI",       id="btn-web")
            yield Button("Command Line", id="btn-cli")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        table.clear(columns=True)
        table.add_columns("  Project", "Last modified", "")
        for proj in list_projects():
            table.add_row(
                f"  {proj['name']}",
                proj["modified"],
                "[red]del[/red]",
                key=proj["name"],
            )

    def _selected_project(self) -> Path | None:
        table = self.query_one("#projects-table", DataTable)
        if table.cursor_row < 0:
            return None
        row_key = table.get_row_at(table.cursor_row)
        name = str(row_key[0]).strip()
        path = PROJECTS_DIR / name
        return path if path.exists() else None

    # ── Actions ───────────────────────────────────────────────────────

    def action_open_selected(self) -> None:
        path = self._selected_project()
        if path:
            self._launch_project(path)

    def action_new_project(self) -> None:
        self.push_screen(NewProjectScreen(), self._on_new_project)

    def action_delete_selected(self) -> None:
        path = self._selected_project()
        if path:
            self.push_screen(ConfirmDeleteScreen(path.name), self._on_confirm_delete)

    def action_quit_to_shell(self) -> None:
        self.exit()

    # ── Button handlers ───────────────────────────────────────────────

    @on(Button.Pressed, "#btn-open")
    def btn_open(self) -> None:
        self.action_open_selected()

    @on(Button.Pressed, "#btn-new")
    def btn_new(self) -> None:
        self.action_new_project()

    @on(Button.Pressed, "#btn-web")
    def btn_web(self) -> None:
        subprocess.Popen([KANNA_CMD, "--no-open"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.notify("kanna launched — Ctrl+click the URL in your terminal")

    @on(Button.Pressed, "#btn-cli")
    def btn_cli(self) -> None:
        self.exit()

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        """Double-click / Enter on a row opens the project."""
        name = str(event.row_key.value).strip()
        path = PROJECTS_DIR / name
        if path.exists():
            self._launch_project(path)

    @on(DataTable.CellSelected)
    def cell_selected(self, event: DataTable.CellSelected) -> None:
        """Click on the 'del' cell triggers delete."""
        table = self.query_one("#projects-table", DataTable)
        # column index 2 is the del column
        if event.coordinate.column == 2:
            name = str(table.get_row_at(event.coordinate.row)[0]).strip()
            path = PROJECTS_DIR / name
            if path.exists():
                self.push_screen(ConfirmDeleteScreen(name), self._on_confirm_delete)

    @on(Checkbox.Changed, "#autostart")
    def autostart_toggled(self, event: Checkbox.Changed) -> None:
        if event.value:
            AUTOSTART_FLAG.touch()
            self.notify("TUI will launch automatically with Maude")
        else:
            AUTOSTART_FLAG.unlink(missing_ok=True)
            self.notify("TUI auto-start disabled")

    # ── Callbacks ─────────────────────────────────────────────────────

    def _launch_project(self, path: Path) -> None:
        with self.suspend():
            open_project(path)
        self._refresh_table()

    def _on_new_project(self, name: str | None) -> None:
        if not name:
            return
        path = PROJECTS_DIR / name
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(path), "init", "--quiet"], check=False)
        self._refresh_table()
        self._launch_project(path)

    def _on_confirm_delete(self, confirmed: bool) -> None:
        if not confirmed:
            return
        path = self._selected_project()
        if path:
            soft_delete(path)
            self.notify(f"'{path.name}' moved to .deleted/")
            self._refresh_table()


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = MaudeApp()
    app.run()
