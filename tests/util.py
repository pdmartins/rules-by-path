"""Shared helpers for the rules-by-path test suite (stdlib only)."""

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PATH = os.path.join(REPO_ROOT, "hooks", "rules-by-path.py")
ADMIN_PATH = os.path.join(REPO_ROOT, "scripts", "rules-by-path-admin.py")


def load_hook_module():
    loader = importlib.machinery.SourceFileLoader("rules_by_path_hook_under_test", HOOK_PATH)
    spec = importlib.util.spec_from_loader("rules_by_path_hook_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def isolated_env(fake_home, extra=None):
    """Environment for subprocesses with HOME redirected, so tests never touch
    the real ~/.claude (state, global rules)."""
    env = dict(os.environ)
    env["HOME"] = fake_home
    env["USERPROFILE"] = fake_home  # Windows expanduser
    env.pop("HOMEPATH", None)
    env.pop("HOMEDRIVE", None)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    if extra:
        env.update(extra)
    return env


def run_hook(payload, fake_home, args=(), env=None, timeout=30):
    """Run the hook as Claude Code would: JSON payload on stdin."""
    proc = subprocess.run(
        [sys.executable, HOOK_PATH, *args],
        input=json.dumps(payload) if isinstance(payload, dict) else payload,
        capture_output=True, text=True, env=isolated_env(fake_home, env),
        timeout=timeout,
    )
    return proc


def hook_output(proc):
    """Parse the hook's stdout JSON, or None when it stayed silent."""
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def run_admin(args, fake_home, stdin_text="", env=None):
    return subprocess.run(
        [sys.executable, ADMIN_PATH, *args],
        input=stdin_text, capture_output=True, text=True,
        env=isolated_env(fake_home, env), timeout=30,
    )


def write_rule_setup(root, entries):
    """Create .claude/rules-by-path under `root` with the given entries:
    [(glob, rule_name_or_None, content)]. Writes the map in the admin format."""
    base = os.path.join(root, ".claude", "rules-by-path")
    rules_dir = os.path.join(base, "rules")
    os.makedirs(rules_dir, exist_ok=True)
    lines = ["rules:"]
    for glob, rule_name, content in entries:
        lines.append(f'  - glob: "{glob}"')
        if rule_name:
            lines.append(f'    rule: "{rule_name}"')
        name = rule_name or _derive(glob)
        with open(os.path.join(rules_dir, name), "w", encoding="utf-8") as handle:
            handle.write(content)
    with open(os.path.join(base, "rules-map.yml"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return base


def _derive(glob):
    segments = [s for s in glob.strip().strip("/").split("/") if s]
    while segments and set(segments[0]) <= {"*"}:
        segments.pop(0)
    while segments and set(segments[-1]) <= {"*"}:
        segments.pop()
    name = "--".join(segments) or "root"
    return name if name.endswith(".md") else name + ".md"


def read_payload(tool, file_path, session="test-session", cwd="/tmp"):
    return {
        "tool_name": tool,
        "tool_input": {"file_path": file_path},
        "session_id": session,
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
    }
