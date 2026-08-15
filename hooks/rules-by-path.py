#!/usr/bin/env python3
"""rules-by-path — PreToolUse hook for Claude Code.

When Claude touches a file (Read/Edit/Write/MultiEdit/NotebookEdit), this hook
looks up `.claude/rules-by-path/rules-map.yml` in the project (walking up from
the touched file) and in the global config (`~/.claude/rules-by-path/`), and
injects the matching rule files into context via
`hookSpecificOutput.additionalContext`.

Design constraints:
- Never blocks the tool call: any internal failure goes to stderr and the hook
  exits 0 with no stdout. The only deliberate block is the nested-CLAUDE.md
  guard, which is a policy decision, not a failure.
- Each rule is injected at most once per session (state file per session id).
- Files inside `.claude/rules-by-path/` never trigger injection.
- No dependency is required: PyYAML is used when available; otherwise a
  built-in parser handles the restricted map format the admin script writes.
- Portable: POSIX flock when available, msvcrt on Windows, best-effort
  otherwise.

Rules are managed by the `rules-by-path` skill through the companion script
`scripts/rules-by-path-admin.py` in this plugin.
"""

import json
import os
import re
import sys
import time

# --- constants -------------------------------------------------------------

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN_SCRIPT = os.path.join(PLUGIN_ROOT, "scripts", "rules-by-path-admin.py")

RULES_DIR_RELPATH = os.path.join(".claude", "rules-by-path")
MAP_FILE_NAME = "rules-map.yml"
RULES_SUBDIR_NAME = "rules"
FILE_PATH_KEYS = ("file_path", "notebook_path", "path")
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "cache", "rules-by-path")
STATE_MAX_AGE_SECONDS = 14 * 24 * 3600
MAX_RULE_CHARS = 16_000
MAX_TOTAL_CHARS = 48_000
MAX_MAP_BYTES = 262_144  # a huge/hostile map must not stall every tool call
MAX_GLOB_CHARS = 256  # a hostile glob must not turn into a pathological regex
MAX_MAP_ENTRIES = 512

NESTED_CLAUDE_MD_REASON = (
    "rules-by-path: creating/editing a CLAUDE.md in a subfolder is blocked — "
    "folder-scoped guidance lives in .claude/rules-by-path (only the project "
    "ROOT CLAUDE.md is a file). Correct flow: "
    f"1) python3 \"{ADMIN_SCRIPT}\" which --root <root> --path <folder-or-file> "
    "— if there is no match, skip to step 3 using --glob '<folder>/**'; "
    "2) read the matched rule with `cat <root>/.claude/rules-by-path/rules/<name>.md`; "
    f"3) python3 \"{ADMIN_SCRIPT}\" add --root <root> --glob '<matched-glob>' "
    "--force with the COMPLETE markdown on stdin."
)

HEADER_TEMPLATE = (
    "[rules-by-path] Rules that apply to file '{file}' "
    "(injected automatically by the rules-by-path hook; follow them when "
    "working with this file):"
)
RULE_BLOCK_TEMPLATE = "\n\n--- Rule '{rule}' (scope: {scope}, glob: '{glob}') ---\n{content}"
TRUNCATION_NOTICE = "\n[...rule truncated by the rules-by-path size limit...]"


def warn(message):
    print(f"rules-by-path: {message}", file=sys.stderr)


# --- portable file locking -------------------------------------------------

def lock_exclusive(fd):
    """Best-effort exclusive lock on fd. POSIX flock, msvcrt on Windows;
    silently degrades to no lock (dedup then tolerates a rare double
    injection instead of ever blocking the tool call)."""
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    except ImportError:
        pass
    except Exception as exc:
        warn(f"flock failed: {exc}")
        return
    try:
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    except Exception as exc:
        warn(f"lock unavailable ({exc}); proceeding without it")


# --- map parsing -----------------------------------------------------------

def parse_map_without_yaml(text, map_path):
    """Fallback parser for when PyYAML is missing. Handles the restricted
    format the admin script writes (and simple hand edits):

        rules:
          - glob: "src/api/**"
            rule: "src--api.md"
          - "docs/**"              # bare-string entries too

    Comments and blank lines are ignored. Anything else is skipped with a
    warning so a hand-written exotic YAML degrades loudly, not silently."""

    def unquote(value):
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            return value[1:-1]
        return value

    entries = []
    seen_rules_key = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip() if not raw_line.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped == "rules:" or stripped == "rules: []":
            seen_rules_key = True
            continue
        if stripped.startswith("- glob:"):
            entries.append({"glob": unquote(stripped[len("- glob:"):]), "rule": None})
        elif stripped.startswith("rule:") and entries and entries[-1]["rule"] is None:
            entries[-1]["rule"] = unquote(stripped[len("rule:"):])
        elif stripped.startswith("- "):
            entries.append({"glob": unquote(stripped[2:]), "rule": None})
        else:
            warn(f"{map_path}: line not understood by the fallback parser "
                 f"(install PyYAML for full YAML support): {stripped!r}")
    if not seen_rules_key and entries:
        warn(f"{map_path}: no 'rules:' key found; treating top-level list as the rule list")
    return [{"glob": e["glob"], "rule": e["rule"]} for e in entries if e["glob"]]


def load_raw_entries(map_path):
    """Read and parse a rules map into raw [{'glob': ..., 'rule': ...|None}].
    Tolerant: a broken file skips the whole map with a warning."""
    try:
        size = os.path.getsize(map_path)
        if size > MAX_MAP_BYTES:
            warn(f"{map_path} ignored: {size} bytes exceeds the {MAX_MAP_BYTES} limit")
            return []
        with open(map_path, encoding="utf-8") as handle:
            text = handle.read()
    except Exception as exc:
        warn(f"failed reading {map_path}: {exc}")
        return []

    try:
        import yaml
    except ImportError:
        return parse_map_without_yaml(text, map_path)

    try:
        data = yaml.safe_load(text)
    except Exception as exc:
        warn(f"failed parsing {map_path}: {exc}")
        return []
    if data is None:
        return []
    raw_entries = data.get("rules") if isinstance(data, dict) else data
    if raw_entries is None:
        return []
    if not isinstance(raw_entries, list):
        warn(f"{map_path}: 'rules' should be a list")
        return []
    entries = []
    for raw in raw_entries:
        if isinstance(raw, str):
            entries.append({"glob": raw, "rule": None})
        elif isinstance(raw, dict) and isinstance(raw.get("glob"), str):
            rule = raw.get("rule")
            entries.append({"glob": raw["glob"],
                            "rule": rule if isinstance(rule, str) and rule.strip() else None})
        else:
            warn(f"{map_path}: entry skipped (expected glob string): {raw!r}")
    return entries


def load_map_entries(map_path):
    """Parse rules-map.yml into [{'glob': ..., 'rule': ...}] with the rule
    name resolved, applying the entry-count and glob-size caps."""
    raw_entries = load_raw_entries(map_path)
    if len(raw_entries) > MAX_MAP_ENTRIES:
        warn(f"{map_path}: {len(raw_entries)} entries exceeds the {MAX_MAP_ENTRIES} cap; "
             f"extra entries ignored")
        raw_entries = raw_entries[:MAX_MAP_ENTRIES]
    entries = []
    for raw in raw_entries:
        glob = raw["glob"]
        if len(glob) > MAX_GLOB_CHARS:
            warn(f"{map_path}: glob longer than {MAX_GLOB_CHARS} chars skipped: {glob[:64]!r}...")
            continue
        entries.append({"glob": glob, "rule": raw["rule"] or derive_rule_name(glob)})
    return entries


# --- glob matching ---------------------------------------------------------

def compile_glob(glob):
    """Compile a rules-map glob into a regex over '/'-separated paths.

    Supports `**` (any depth), `*` (within one path segment), `?` (one char).
    Conveniences: a glob with no metacharacters (plain file or directory path)
    matches itself and anything under it; a trailing `/` means the whole
    directory.
    """
    g = glob.strip()
    if g.startswith("./"):
        g = g[2:]
    if g.endswith("/"):
        g = g.rstrip("/") + "/**"
    regex = ""
    i = 0
    while i < len(g):
        ch = g[i]
        if ch == "*":
            if g[i : i + 3] == "**/":
                regex += "(?:[^/]+/)*"
                i += 3
            elif g[i : i + 2] == "**":
                regex += ".*"
                i += 2
            else:
                regex += "[^/]*"
                i += 1
        elif ch == "?":
            regex += "[^/]"
            i += 1
        else:
            regex += re.escape(ch)
            i += 1
    if not any(ch in g for ch in "*?"):
        regex += "(?:/.*)?"  # plain path: itself or anything under it
    return re.compile("^" + regex + "$")


def glob_matches(glob, rel_path, abs_path):
    """Check `glob` against a file. `rel_path` is None for the global scope.

    Non-absolute globs match the project-relative path (or the absolute path
    minus the leading '/' in the global scope); globs without '/' also match
    the file's basename, so `*.cs` catches any C# file at any depth.
    """
    try:
        pattern = compile_glob(glob)
    except re.error as exc:
        warn(f"invalid glob '{glob}': {exc}")
        return False
    g = glob.strip()
    if g.startswith("/"):
        targets = [abs_path]
    else:
        targets = [rel_path if rel_path is not None else abs_path.lstrip("/")]
        if "/" not in g.rstrip("/"):
            targets.append(os.path.basename(abs_path))
    return any(pattern.match(t) for t in targets)


# --- nested CLAUDE.md guard ------------------------------------------------

def is_nested_claude_md(abs_path):
    """True when abs_path is a CLAUDE.md sitting below a repo root (some
    ancestor directory has .git). The file's own directory having .git makes
    it a root itself — nested repos and worktrees stay allowed. No .git
    anywhere: fail-open (not a repo, none of our business)."""
    if os.path.basename(abs_path) != "CLAUDE.md":
        return False
    directory = os.path.dirname(abs_path)
    if os.path.exists(os.path.join(directory, ".git")):
        return False
    while True:
        parent = os.path.dirname(directory)
        if parent == directory:
            return False
        directory = parent
        if os.path.exists(os.path.join(directory, ".git")):
            return True


# --- rules discovery -------------------------------------------------------

def derive_rule_name(glob):
    """Default rule filename: glob path with `/` -> `--`, leading/trailing
    `*`/`**` segments dropped, `.md` appended."""
    segments = [s for s in glob.strip().strip("/").split("/") if s]
    while segments and set(segments[0]) <= {"*"}:
        segments.pop(0)
    while segments and set(segments[-1]) <= {"*"}:
        segments.pop()
    name = "--".join(segments) or "root"
    return name if name.endswith(".md") else name + ".md"


def find_rule_sources(start_dir):
    """Return [(base_dir, map_path, scope_label)] for the file's ancestors,
    nearest first, then the global scope (deduplicated by realpath)."""
    sources = []
    seen_maps = set()
    directory = start_dir
    while True:
        map_path = os.path.join(directory, RULES_DIR_RELPATH, MAP_FILE_NAME)
        if os.path.isfile(map_path):
            real = os.path.realpath(map_path)
            if real not in seen_maps:
                seen_maps.add(real)
                sources.append((directory, map_path, f"project {directory}"))
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    global_map = os.path.join(os.path.expanduser("~"), RULES_DIR_RELPATH, MAP_FILE_NAME)
    if os.path.isfile(global_map) and os.path.realpath(global_map) not in seen_maps:
        sources.append((None, global_map, "global"))
    return sources


def read_rule_content(map_path, rule_name):
    # Flat names only: a path separator would let a hostile map pull arbitrary
    # readable files into context. A '..' inside a flat name is harmless
    # (derive_rule_name legitimately produces e.g. 'data--x..old.md').
    if os.path.isabs(rule_name) or "/" in rule_name or "\\" in rule_name \
            or rule_name in (".", ".."):
        warn(f"invalid rule name (flat names only): {rule_name!r}")
        return None
    rules_dir = os.path.join(os.path.dirname(map_path), RULES_SUBDIR_NAME)
    rule_path = os.path.join(rules_dir, rule_name)
    if not os.path.isfile(rule_path):
        warn(f"rule '{rule_name}' not found in {rules_dir}")
        return None
    # A rule file that is a symlink out of rules/ could pull any readable file
    # (say, a private key) into context; require the real path to stay inside.
    real_rule = os.path.realpath(rule_path)
    real_dir = os.path.realpath(rules_dir)
    if os.path.dirname(real_rule) != real_dir:
        warn(f"rule '{rule_name}' resolves outside {rules_dir}; skipped")
        return None
    try:
        with open(rule_path, encoding="utf-8", errors="replace") as handle:
            content = handle.read(MAX_RULE_CHARS + 1).strip()
    except Exception as exc:
        warn(f"failed reading {rule_path}: {exc}")
        return None
    if len(content) > MAX_RULE_CHARS:
        warn(f"rule '{rule_name}' truncated at {MAX_RULE_CHARS} chars")
        content = content[:MAX_RULE_CHARS] + TRUNCATION_NOTICE
    return content


# --- per-session dedup -----------------------------------------------------

def state_file_for(session_id):
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "default")
    return os.path.join(STATE_DIR, safe_id + ".injected")


def open_state_locked(state_path):
    """Open the session state file under an exclusive lock and return
    (fd, injected_keys). Parallel tool calls each spawn a hook process; the
    lock serializes the read-decide-append cycle so a rule is injected exactly
    once even on a simultaneous first touch. On failure returns (None, set())
    and the hook proceeds without dedup rather than blocking the tool call."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        fd = os.open(state_path, os.O_RDWR | os.O_CREAT, 0o600)
        lock_exclusive(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        raw = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw += chunk
        injected = {line.strip() for line in raw.decode("utf-8", "replace").splitlines()
                    if line.strip()}
        return fd, injected
    except Exception as exc:
        warn(f"failed opening state {state_path}: {exc}")
        return None, set()


def append_injected(state_fd, keys):
    """Append keys to the state fd opened by open_state_locked (positioned at
    EOF after the initial read, still holding the lock)."""
    if state_fd is None or not keys:
        return
    try:
        os.write(state_fd, "".join(key + "\n" for key in keys).encode("utf-8"))
    except Exception as exc:
        warn(f"failed writing state: {exc}")


def close_state(state_fd):
    if state_fd is None:
        return
    try:
        os.close(state_fd)  # releases the lock
    except Exception as exc:
        warn(f"failed closing state: {exc}")


def cleanup_stale_state():
    try:
        cutoff = time.time() - STATE_MAX_AGE_SECONDS
        with os.scandir(STATE_DIR) as it:
            for entry in it:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    os.unlink(entry.path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        warn(f"state cleanup failed: {exc}")


# --- main ------------------------------------------------------------------

def extract_file_path(payload):
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in FILE_PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def main():
    payload = json.load(sys.stdin)
    raw_path = extract_file_path(payload)
    if not raw_path:
        return
    cwd = payload.get("cwd") or os.getcwd()
    abs_path = raw_path if os.path.isabs(raw_path) else os.path.join(cwd, raw_path)
    abs_path = os.path.normpath(abs_path).replace(os.sep, "/")
    if f"/{RULES_DIR_RELPATH.replace(os.sep, '/')}/" in abs_path + "/":
        return  # never inject for the rule files themselves

    if payload.get("tool_name") in WRITE_TOOLS and is_nested_claude_md(abs_path):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": NESTED_CLAUDE_MD_REASON,
            },
        }))
        return

    sources = find_rule_sources(os.path.dirname(abs_path))
    if not sources:
        return

    state_path = state_file_for(payload.get("session_id"))
    state_fd, already_injected = open_state_locked(state_path)
    try:
        blocks = []
        new_keys = []
        total_chars = 0
        for base_dir, map_path, scope in sources:
            rel_path = None
            if base_dir is not None:
                rel_path = os.path.relpath(abs_path, base_dir).replace(os.sep, "/")
            for entry in load_map_entries(map_path):
                if not glob_matches(entry["glob"], rel_path, abs_path):
                    continue
                dedup_key = f"{os.path.realpath(map_path)}::{entry['rule']}"
                if dedup_key in already_injected or dedup_key in new_keys:
                    continue
                content = read_rule_content(map_path, entry["rule"])
                if content is None:
                    continue
                if total_chars + len(content) > MAX_TOTAL_CHARS:
                    warn(f"total limit of {MAX_TOTAL_CHARS} chars reached; "
                         f"rule '{entry['rule']}' left for the next tool call")
                    continue
                total_chars += len(content)
                blocks.append(RULE_BLOCK_TEMPLATE.format(
                    rule=entry["rule"], scope=scope, glob=entry["glob"], content=content))
                new_keys.append(dedup_key)

        if not blocks:
            return

        append_injected(state_fd, new_keys)
    finally:
        close_state(state_fd)
    cleanup_stale_state()

    context = HEADER_TEMPLATE.format(file=abs_path) + "".join(blocks)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": context,
        },
        "suppressOutput": True,
    }))


def reset_session():
    """SessionStart (source compact|clear) mode: drop the session's dedup
    state so rules are re-injected on the next touch — compaction may have
    summarized the injected text away, and /clear discards it entirely."""
    payload = json.load(sys.stdin)
    state_path = state_file_for(payload.get("session_id"))
    try:
        os.unlink(state_path)
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    try:
        if "--reset-session" in sys.argv[1:]:
            reset_session()
        else:
            main()
    except Exception as exc:  # never break the tool call because of this hook
        warn(f"unexpected error: {exc}")
    sys.exit(0)
