#!/usr/bin/env python3
"""
maude.py — Textual TUI for the Maude sandbox.
Always launched via:  maude tui
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from rich.text import Text
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
    RadioButton,
    RadioSet,
    Static,
)

PROJECTS_DIR = Path.home() / "Maude" / "Projects"
DELETED_DIR  = PROJECTS_DIR / ".deleted"
AUTOSTART_FLAG = Path.home() / ".maude-tui-autostart"
KANNA_CMD    = "kanna"
KANNA_PORT   = 3210

UPDATE_URL   = "https://raw.githubusercontent.com/dirkpetersen/maude/main/light/maude.py"
UPDATE_STAMP = Path.home() / ".maude-tui-last-update"
UPDATE_HOUR  = 12  # local-time hour (noon) after which the daily refresh fires

MODELS        = ("opus-1m", "opus", "sonnet-1m", "sonnet", "haiku")
DEFAULT_MODEL = "opus-1m"
MODEL_FILE    = Path.home() / ".maude-model"

LOGO = (
    "  __  __                 _      \n"
    " |  \\/  | __ _ _   _  __| | ___ \n"
    " | |\\/| |/ _` | | | |/ _` |/ _ \\\n"
    " | |  | | (_| | |_| | (_| |  __/\n"
    " |_|  |_|\\__,_|\\__,_|\\__,_|\\___|"
)


# ── Helpers ────────────────────────────────────────────────────────────────

def maybe_self_update() -> None:
    """Refresh maude.py from GitHub once per day, at or after noon local time.
    The new version takes effect on the next launch."""
    now = datetime.now()
    if now.hour < UPDATE_HOUR:
        return
    today = now.date().isoformat()
    try:
        if UPDATE_STAMP.read_text().strip() == today:
            return
    except OSError:
        pass

    target = Path(__file__).resolve()
    try:
        with urllib.request.urlopen(UPDATE_URL, timeout=10) as resp:
            new_bytes = resp.read()
        tmp = target.with_name(target.name + ".new")
        tmp.write_bytes(new_bytes)
        tmp.replace(target)
        UPDATE_STAMP.write_text(today)
    except Exception as err:
        print(f"maude: update check failed ({err}); using cached version",
              file=sys.stderr)


def kill_port(port: int) -> None:
    """Forcefully kill anything listening on the given TCP port (best-effort)."""
    subprocess.run(
        ["fuser", "-k", "-KILL", f"{port}/tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )


def stop_kanna(proc: subprocess.Popen | None) -> None:
    """Kill kanna and the rest of its process group, then free the port."""
    if proc is not None and proc.poll() is None:
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = None
        if pgid is not None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pgid, sig)
                except ProcessLookupError:
                    break
                try:
                    proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
    kill_port(KANNA_PORT)


def get_claude_env() -> dict[str, str]:
    """Parse auth-related env vars from `claude --wdebug` output.

    Captures everything kanna might need to authenticate:
    - ANTHROPIC_*  (direct API + Foundry, e.g. ANTHROPIC_FOUNDRY_BASE_URL,
                    ANTHROPIC_FOUNDRY_API_KEY)
    - CLAUDE_*     (e.g. CLAUDE_CODE_USE_FOUNDRY, CLAUDE_CODE_USE_BEDROCK)
    - AWS_*        (Bedrock: region, profile, access keys, session token)
    """
    env = {}
    prefixes = ("ANTHROPIC_", "CLAUDE_", "AWS_")
    try:
        result = subprocess.run(
            ["claude", "--wdebug"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            # Lines look like: "  ANTHROPIC_MODEL=claude-haiku-4-5"
            if "=" in line and line.split("=", 1)[0].strip().isidentifier():
                key, val = line.split("=", 1)
                key = key.strip()
                if key.startswith(prefixes):
                    env[key] = val.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return env


def check_credentials() -> bool:
    """Return True if Claude Code credentials are configured."""
    # Azure AI Foundry
    if os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"):
        return True
    # Anthropic direct
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    # AWS Bedrock
    aws_creds = Path.home() / ".aws" / "credentials"
    if aws_creds.exists() and aws_creds.stat().st_size > 0:
        return True
    # Azure clauderc
    azure_rc = Path.home() / ".azure" / "clauderc"
    if azure_rc.exists() and azure_rc.stat().st_size > 0:
        return True
    return False


def list_projects() -> list[dict]:
    """Return projects sorted by last-modified time, newest first."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    projects = []
    for p in PROJECTS_DIR.iterdir():
        if p.is_dir() and not p.name.startswith("."):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            modified = (datetime.fromtimestamp(mtime).strftime("%b %d, %Y")
                        if mtime else "unknown")
            projects.append({"name": p.name, "modified": modified,
                             "path": p, "mtime": mtime})
    projects.sort(key=lambda d: d["mtime"], reverse=True)
    return projects


def slugify(name: str) -> str:
    """Replace spaces with hyphens; keep letters, digits, dots, dashes, underscores."""
    name = name.replace(" ", "-")
    name = re.sub(r"[^a-zA-Z0-9._-]", "", name)
    return name.strip("-")


def open_project(project_path: Path, model: str) -> None:
    """Launch Claude Code for a project. Try --continue first, then fresh."""
    os.chdir(project_path)
    ret = subprocess.run(["claude", model, "--continue"], check=False).returncode
    if ret != 0:
        subprocess.run(["claude", model], check=False)


def read_model() -> str:
    """Return the user's saved Claude model alias, or the default."""
    try:
        m = MODEL_FILE.read_text().strip()
        if m in MODELS:
            return m
    except OSError:
        pass
    return DEFAULT_MODEL


def save_model(name: str) -> None:
    if name in MODELS:
        try:
            MODEL_FILE.write_text(name)
        except OSError:
            pass


def soft_delete(project_path: Path) -> None:
    """Move project to .deleted/."""
    DELETED_DIR.mkdir(parents=True, exist_ok=True)
    dest = DELETED_DIR / project_path.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(project_path), dest)


# ── Modal screens ──────────────────────────────────────────────────────────

class NoCredsScreen(ModalScreen[None]):
    """Full-screen error when no LLM credentials are configured."""

    BINDINGS = [
        Binding("escape", "quit_app", show=False),
        Binding("q", "quit_app", show=False),
    ]

    def action_quit_app(self) -> None:
        self.app.exit(1)

    def compose(self) -> ComposeResult:
        with Container(id="nocreds-box"):
            yield Label("No LLM Credentials Found", id="nocreds-title")
            yield Label(
                "Configure one of the following before launching Maude:\n\n"
                "  ANTHROPIC_API_KEY            environment variable\n"
                "  ANTHROPIC_FOUNDRY_API_KEY     environment variable\n"
                "  ~/.aws/credentials            AWS Bedrock\n"
                "  ~/.azure/clauderc             Azure config",
                id="nocreds-detail",
            )
            yield Label("Press  q  or  Esc  to exit.", id="nocreds-hint")

    @on(Button.Pressed, "#btn-nocreds-exit")
    def exit_pressed(self) -> None:
        self.app.exit(1)


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Ask the user to confirm deletion."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def action_cancel(self) -> None:
        self.dismiss(False)

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

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def action_cancel(self) -> None:
        self.dismiss(None)

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
    /* ── Claude Code style: light gray base, dusty rose accents ── */

    Screen {
        background: #1e1e1e;
    }

    Header {
        background: #2a2a2a;
        color: #d4a0a0;
    }

    Footer {
        background: #2a2a2a;
        color: #a09090;
    }

    #layout {
        height: 1fr;
    }

    #sidebar {
        width: 40;
        padding: 1 1;
        background: #242424;
        border-right: solid #b87878;
    }

    #logo {
        height: 5;
        color: #72c09a;
        text-style: bold;
        margin-bottom: 1;
    }

    #divider {
        color: #6a5058;
        height: 1;
        margin-bottom: 1;
    }

    #autostart-label {
        color: #c09898;
        margin-top: 1;
        margin-bottom: 0;
    }

    #autostart {
        margin-top: 0;
    }

    #divider2 {
        color: #6a5058;
        height: 1;
        margin-top: 1;
        margin-bottom: 1;
    }

    #tips-title {
        color: #d4a0a0;
        text-style: bold;
        margin-bottom: 0;
    }

    #tips {
        color: #a09090;
    }

    #divider3 {
        color: #6a5058;
        height: 1;
        margin-top: 1;
        margin-bottom: 1;
    }

    #model-label {
        color: #d4a0a0;
        text-style: bold;
        margin-bottom: 0;
    }

    #model-select {
        background: #242424;
        border: none;
        padding: 0;
        height: auto;
    }

    #model-select:focus-within {
        background: #2a2424;
    }

    /* Tame Textual's default blue toggle/focus accents → warm greys */
    Checkbox, RadioButton {
        background: #242424;
        color: #c09898;
    }

    Checkbox:focus, RadioButton:focus {
        background: #2a2424;
        color: #f0d0d0;
    }

    Checkbox > .toggle--button,
    RadioButton > .toggle--button {
        background: #3a3030;
        color: #d4a0a0;
    }

    Checkbox.-on > .toggle--button,
    RadioButton.-on > .toggle--button {
        color: #72c09a;
    }

    #main {
        padding: 1 2;
    }

    #section-title {
        color: #d4a0a0;
        text-style: bold;
        margin-bottom: 1;
    }

    #projects-table {
        height: 1fr;
        border: solid #b87878;
    }

    DataTable > .datatable--header {
        color: #d4a0a0;
    }

    DataTable > .datatable--cursor {
        background: #383030;
        color: #f0d8d8;
    }

    #bottom-bar {
        height: auto;
        min-height: 3;
        padding: 1 2;
        align: left middle;
        background: #242424;
        border-top: solid #b87878;
        margin-bottom: 1;
    }

    #bottom-bar Button {
        margin-right: 1;
        min-width: 14;
    }

    #btn-open { color: #f0c8c8; }
    #btn-new  { color: #d0b8b8; }
    #btn-web  { color: #c0a8a8; }
    #btn-cli  { color: #b09898; }

    #kanna-url {
        color: #72c09a;
        margin-left: 2;
        content-align: left middle;
        height: 100%;
    }

    /* Modal: confirm delete */
    ConfirmDeleteScreen {
        align: center middle;
    }

    #confirm-box {
        padding: 2 4;
        width: 60;
        height: auto;
        border: solid #c07070;
        background: $surface;
    }

    #confirm-title {
        text-style: bold;
        color: #e09090;
        margin-bottom: 1;
    }

    #confirm-sub {
        color: #c09898;
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
    }

    #new-box {
        padding: 2 4;
        width: 60;
        height: auto;
        border: solid #b87878;
        background: $surface;
    }

    #new-title {
        text-style: bold;
        color: #72c09a;
        margin-bottom: 1;
    }

    #new-sub {
        color: #c09898;
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

    /* Modal: no credentials */
    NoCredsScreen {
        align: center middle;
        background: #1e1e1e 90%;
    }

    #nocreds-box {
        padding: 3 6;
        width: 70;
        height: auto;
        border: heavy #e05050;
        background: #2a1010;
        align: center middle;
    }

    #nocreds-title {
        text-style: bold;
        color: #ff4444;
        text-align: center;
        width: 100%;
        margin-bottom: 2;
    }

    #nocreds-detail {
        color: #e0b0b0;
        margin-bottom: 2;
    }

    #nocreds-hint {
        color: #808080;
        text-align: center;
        width: 100%;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("enter", "open_selected", "Open", show=True),
        Binding("n",     "new_project",   "New",  show=True),
        Binding("d",     "delete_selected","Delete",show=True),
        Binding("q",     "quit_to_shell", "Quit", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._model = read_model()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Static(LOGO, id="logo", markup=False)
                yield Static("─" * 28, id="divider")
                yield Label("Start TUI with Maude", id="autostart-label")
                yield Checkbox("", value=AUTOSTART_FLAG.exists(), id="autostart")
                yield Static("─" * 28, id="divider2")
                yield Label("Tips", id="tips-title")
                yield Static(
                    "Screen split:  Alt+Shift+Plus | Minus\n"
                    "Paste image:   Alt+V (in Claude Code)\n"
                    "Voice dictate: Win+H (Windows mic)",
                    id="tips",
                )
                yield Static("─" * 28, id="divider3")
                yield Label("Claude model", id="model-label")
                with RadioSet(id="model-select"):
                    for m in MODELS:
                        yield RadioButton(m, value=(m == self._model))
            with Vertical(id="main"):
                yield Label("Projects", id="section-title")
                yield DataTable(id="projects-table", cursor_type="row",
                                zebra_stripes=True)
        with Horizontal(id="bottom-bar"):
            yield Button("Open Project", id="btn-open")
            yield Button("+ New",        id="btn-new")
            yield Button("Web UI",       id="btn-web")
            yield Button("Command Line", id="btn-cli")
            yield Static("", id="kanna-url")
        yield Footer()

    def on_mount(self) -> None:
        self._kanna_proc: subprocess.Popen | None = None
        if not check_credentials():
            self.push_screen(NoCredsScreen())
            return
        self._refresh_table()
        self.query_one("#projects-table", DataTable).focus()

    def _refresh_table(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        table.clear(columns=True)
        table.add_columns("  Project", "Last modified")
        for proj in list_projects():
            table.add_row(
                f"  {proj['name']}",
                proj["modified"],
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
        btn = self.query_one("#btn-web", Button)
        # If kanna is (or appears to be) running, stop it forcefully.
        if self._kanna_proc is not None:
            stop_kanna(self._kanna_proc)
            self._kanna_proc = None
            btn.label = "Web UI"
            self.query_one("#kanna-url", Static).update("")
            return
        # Make sure the port isn't held by a stale instance before launching.
        kill_port(KANNA_PORT)
        # Start kanna in its own process group so we can kill the whole tree.
        extra_env = get_claude_env()
        env = {**os.environ, **extra_env}
        self._kanna_proc = subprocess.Popen(
            [KANNA_CMD, "--no-open"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        btn.label = "Stop Web UI"
        url = f"http://localhost:{KANNA_PORT}"
        label = Text("Web UI: ")
        label.append(url, style=f"link {url} #72c09a")
        self.query_one("#kanna-url", Static).update(label)

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

    @on(Checkbox.Changed, "#autostart")
    def autostart_toggled(self, event: Checkbox.Changed) -> None:
        if event.value:
            AUTOSTART_FLAG.touch()
            self.notify("TUI will launch automatically with Maude")
        else:
            AUTOSTART_FLAG.unlink(missing_ok=True)
            self.notify("TUI auto-start disabled")

    @on(RadioSet.Changed, "#model-select")
    def model_changed(self, event: RadioSet.Changed) -> None:
        self._model = str(event.pressed.label)
        save_model(self._model)
        self.query_one("#projects-table", DataTable).focus()

    # ── Callbacks ─────────────────────────────────────────────────────

    def _launch_project(self, path: Path) -> None:
        name = path.name
        with self.suspend():
            open_project(path, self._model)
        self._refresh_table()
        self._select_project(name)

    def _select_project(self, name: str) -> None:
        """Move the table cursor to the row whose key is `name`, if it exists."""
        table = self.query_one("#projects-table", DataTable)
        try:
            index = table.get_row_index(name)
        except KeyError:
            index = -1
        if index >= 0:
            table.move_cursor(row=index)
        table.focus()

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
    maybe_self_update()
    app = MaudeApp()
    app.run()
