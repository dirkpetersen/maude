"""Test helper: run code against the pure parts of light/maude.py without importing it (textual may be absent).
Usage: python3 -I tests/maude-extract.py light/maude.py 'python code; the value of the last expression is printed'
Only the top-level names listed in WANT are executed (the state, model-list, settings and command helpers)."""
import __future__
import ast
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
from pathlib import Path

WANT = {
    "MODEL_AUTO", "DEFAULT_MODEL", "STATE_DIR", "MODEL_FILE", "OLD_MODEL_FILE", "AGENTS", "AI_TOOLS", "DEFAULT_TOOL",
    "TOOL_FILE", "OLD_TOOL_FILE", "AGENT_MODELS_FILE", "NATIVE_APPROVED_AGENTS", "FALLBACK_AGENT", "SETTINGS_FILE",
    "SETTINGS_DEFAULTS", "EFFORTS", "DEFAULT_EFFORT", "EFFORT_LABELS", "EFFORT_AGENTS", "RESUME_FALLBACK_AGENTS",
    "MODEL_ID_RE", "MODEL_SLOTS", "UPDATE_URL", "UPDATE_HOUR", "MODELS_URL", "MODELS_DEFAULT_FILE", "MODELS_USER_FILE",
    "MODELS_STAMP", "MODELS_FALLBACK_JSON", "AGENT_BINARIES", "AGENT_BIN", "INSTALL_LABEL", "CLAUDE_ALIASES", "CLAUDE_ID_RE",
    "LABEL_MAX", "CONFIG_VERSION", "claude_model_ok", "model_ok", "default_model", "agent_retry_fresh",
    "tool_model_passed", "agent_exit_note", "_clean_label", "initial_selection", "_parse_settings", "recall_model",
    "remember_model", "NORMAL_QUIT_CODES", "agent_quit_normally",
    "read_tool", "save_tool", "valid_model_id", "effort_for", "effort_args", "tool_command", "parse_model_pref",
    "format_model_pref", "_model_entries", "parse_models_config", "validate_models_bytes", "_read_json",
    "load_models_config", "agent_models", "resolve_model", "maybe_update_models", "load_settings", "save_settings",
    "agent_allowed", "restrict_agent", "read_agent_models", "save_agent_model", "maude_dir",
    "_decode_state", "_migrate_state", "_read_state", "_write_state", "read_model_effort", "_save_model_effort",
    "read_model", "read_effort", "save_model", "save_effort", "replace_retry", "_mac_has_claude_sessions",
}
src = open(sys.argv[1], encoding="utf-8").read()
ns = {"os": os, "re": re, "json": json, "time": time, "tempfile": tempfile, "shutil": shutil,
      "subprocess": subprocess, "urllib": urllib, "datetime": datetime.datetime, "Path": Path,
      "unicodedata": unicodedata, "IS_WIN": False, "NATIVE": False}
flags = __future__.annotations.compiler_flag
for n in ast.parse(src).body:
    names = {t.id for t in getattr(n, "targets", []) if isinstance(t, ast.Name)} | {getattr(n, "name", "")}
    if names & WANT:
        exec(compile(ast.Module([n], []), "maude.py", "exec", flags=flags), ns)
tree = ast.parse(sys.argv[2])
last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
exec(compile(tree, "test", "exec"), ns)
if last is not None:
    print(eval(compile(ast.Expression(last.value), "test", "eval"), ns))
