"""Shared helpers for the rules-by-path test suite (stdlib only)."""

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The plugin is one directory of this repository — everything Claude Code
# installs lives under PLUGIN_ROOT, and everything outside it (this suite,
# publish.sh) is development scaffolding that never ships.
PLUGIN_ROOT = os.path.join(REPO_ROOT, "plugins", "rules-by-path")
HOOK_PATH = os.path.join(PLUGIN_ROOT, "hooks", "rules-by-path.py")
ADMIN_PATH = os.path.join(PLUGIN_ROOT, "scripts", "rules-by-path-admin.py")
RULES_DIR_RELPATH = os.path.join(".claude", "rules-by-path")
STATE_DIR_RELPATH = os.path.join(".claude", "cache", "rules-by-path")


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


def hook_specific_output(proc):
    """The hookSpecificOutput block, or {} when the hook stayed silent — so a
    test can assert a key (`permissionDecision`) is absent either way."""
    return (hook_output(proc) or {}).get("hookSpecificOutput", {})


def injected_text(proc):
    """The additionalContext of an injection, or None."""
    return hook_specific_output(proc).get("additionalContext")


def run_admin(args, fake_home, stdin_text="", env=None):
    return subprocess.run(
        [sys.executable, ADMIN_PATH, *args],
        input=stdin_text, capture_output=True, text=True,
        env=isolated_env(fake_home, env), timeout=30,
    )


def scope_dir(root):
    return os.path.join(root, RULES_DIR_RELPATH)


def state_dir(home):
    """Where the hook keeps its per-session state under a given HOME."""
    return os.path.join(home, STATE_DIR_RELPATH)


def write_file(path, text, encoding="utf-8"):
    """Write a file verbatim, creating the directories above it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as handle:
        handle.write(text)
    return path


def state_path(home, session):
    """The state file a given session writes under a given HOME."""
    return os.path.join(state_dir(home), f"{session}.json")


def write_state(home, session, text):
    """Plant a session's state file verbatim — how tests hand the hook a state
    it must survive reading."""
    return write_file(state_path(home, session), text)


def read_state(home, session):
    """The state the hook left behind for a session, parsed."""
    with open(state_path(home, session), encoding="utf-8") as handle:
        return json.load(handle)


def write_rule(root, name, globs, body, extra_frontmatter=()):
    """Create a rule file directly, bypassing the CLI — tests need to build
    states the CLI would refuse."""
    directory = scope_dir(root)
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
    return write_file(os.path.join(directory, name),
                      "\n".join(lines) + body.strip() + "\n")


def read_payload(tool, file_path, session="test-session", cwd="/tmp",
                 transcript_path=None):
    payload = {
        "tool_name": tool,
        "tool_input": {"file_path": file_path},
        "session_id": session,
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
    }
    if transcript_path is not None:
        payload["transcript_path"] = transcript_path
    return payload


def write_transcript(path, *token_totals, model="claude-opus-5"):
    """A minimal stand-in for the harness's JSONL: one assistant record per
    entry, each carrying the usage numbers the hook sums to size the context."""
    with open(path, "w", encoding="utf-8") as handle:
        for total in token_totals:
            handle.write(json.dumps({
                "type": "assistant",
                "message": {"model": model,
                            "usage": {"input_tokens": 0,
                                      "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": total,
                                      "output_tokens": 0}},
            }) + "\n")
    return path


def write_config(directory, payload):
    """Create a config.json in a config layer's directory. A mapping is
    serialised; a string is written verbatim, so a test can plant a malformed
    file."""
    return write_file(os.path.join(directory, "config.json"),
                      payload if isinstance(payload, str) else json.dumps(payload))


class SandboxTestCase(unittest.TestCase):
    """A throwaway HOME plus a throwaway project, removed after each test.

    `self.home` stands in for the user's home directory (state cache) and
    `self.global_scope` for the rules directory in it, `self.proj` for a
    checked-out repository and `self.scope` for its rules directory.
    Subclasses declare the directories they need inside the project in
    PROJECT_SUBDIRS, and the file a bare touch acts on in TOUCHED.
    """

    PROJECT_SUBDIRS: "tuple[str, ...]" = ()
    TOUCHED = "src/a.py"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        self.scope = scope_dir(self.proj)
        self.global_scope = scope_dir(self.home)
        os.makedirs(self.home)
        os.makedirs(self.proj)
        for relative in self.PROJECT_SUBDIRS:
            os.makedirs(os.path.join(self.proj, *relative.split("/")), exist_ok=True)

    def admin(self, *args, stdin=""):
        """Run the admin CLI with HOME pointed at this sandbox."""
        return run_admin(list(args), self.home, stdin_text=stdin)

    def read_rule(self, name):
        with open(os.path.join(self.scope, name), encoding="utf-8") as handle:
            return handle.read()

    def hook_for(self, rel=None, session="s1", tool="Read", env=None):
        """Run the hook for a tool call on <proj>/<rel>, TOUCHED by default."""
        target = os.path.join(self.proj, rel if rel is not None else self.TOUCHED)
        payload = read_payload(tool, target, session=session)
        return run_hook(payload, self.home, env=env)

    def inject(self, rel=None, session="s1", tool="Read", env=None):
        """The context the hook injects for a tool call on <proj>/<rel>."""
        return injected_text(self.hook_for(rel, session=session, tool=tool, env=env))

    def touch(self, rel=None, session="s1", tool="Read", env=None):
        """Both halves of one tool call: the process, and the text it injected —
        for the tests that assert on stderr or the exit code as well."""
        proc = self.hook_for(rel, session=session, tool=tool, env=env)
        return proc, injected_text(proc)

    def inject_with_transcript(self, total, rel=None, session="s1"):
        """Same, with a transcript reporting `total` context tokens — one file
        per session, rewritten on every call, as a real session's is."""
        transcript = os.path.join(self.tmp.name, f"{session}.jsonl")
        write_transcript(transcript, total)
        target = os.path.join(self.proj, rel if rel is not None else self.TOUCHED)
        payload = read_payload("Read", target, session=session,
                               transcript_path=transcript)
        return injected_text(run_hook(payload, self.home))
