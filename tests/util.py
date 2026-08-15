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
RULES_DIR_RELPATH = os.path.join(".claude", "rules-by-path")


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
    env.pop("RULES_BY_PATH_REINFORCE_EVERY", None)
    if extra:
        env.update(extra)
    return env


def run_hook(payload, fake_home, args=(), env=None, timeout=30):
    """Run the hook as Claude Code would: JSON payload on stdin."""
    return subprocess.run(
        [sys.executable, HOOK_PATH, *args],
        input=json.dumps(payload) if isinstance(payload, dict) else payload,
        capture_output=True, text=True, env=isolated_env(fake_home, env),
        timeout=timeout,
    )


def hook_output(proc):
    """Parse the hook's stdout JSON, or None when it stayed silent."""
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def injected_text(proc):
    """The additionalContext of an injection, or None."""
    out = hook_output(proc)
    if not out:
        return None
    return out.get("hookSpecificOutput", {}).get("additionalContext")


def run_admin(args, fake_home, stdin_text="", env=None):
    return subprocess.run(
        [sys.executable, ADMIN_PATH, *args],
        input=stdin_text, capture_output=True, text=True,
        env=isolated_env(fake_home, env), timeout=30,
    )


def scope_dir(root):
    return os.path.join(root, RULES_DIR_RELPATH)


def write_rule(root, name, globs, body, extra_frontmatter=()):
    """Create a rule file directly, bypassing the CLI — tests need to build
    states the CLI would refuse."""
    directory = scope_dir(root)
    os.makedirs(directory, exist_ok=True)
    if isinstance(globs, str):
        globs = [globs]
    lines = ["---"]
    if len(globs) == 1:
        lines.append(f"glob: {globs[0]}")
    else:
        lines.append("glob:")
        lines.extend(f"  - {glob}" for glob in globs)
    lines.extend(extra_frontmatter)
    lines.append("---")
    lines.append("")
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + body.strip() + "\n")
    return path


def write_rules(root, specs):
    """specs: [(name, globs, body)]"""
    for name, globs, body in specs:
        write_rule(root, name, globs, body)
    return scope_dir(root)


def read_payload(tool, file_path, session="test-session", cwd="/tmp"):
    return {
        "tool_name": tool,
        "tool_input": {"file_path": file_path},
        "session_id": session,
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
    }
