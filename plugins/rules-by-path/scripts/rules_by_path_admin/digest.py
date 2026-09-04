"""`digest`: the raw material the `improve` skill reasons over, distilled by
a script so the model never reads a transcript whole.

Two parts. The harvest sources — every instruction file that could hold a
path-bound rule (CLAUDE.md files, native `.claude/rules/*.md` with their
`paths:`) — listed, not read: the skill reads the ones it needs. And the
user's own turns from the most recent sessions of THIS project, each session
paired with the rules the usage stats remember injecting in it, so a
correction that follows an injection is visible as such.

Restricted to the project's own transcript directory on purpose: a session
from another repository is not evidence about this one's rules. Bounded
everywhere — sessions, turns per session, characters per turn, characters
overall — because this output goes straight into a context window."""

import json
import os
import re
import time

from .common import HOOK, fail, read_regular_file

PROJECTS_RELPATH = os.path.join(".claude", "projects")
NATIVE_RULES_RELPATH = os.path.join(".claude", "rules")
CLAUDE_MD_NAMES = ("CLAUDE.md", "CLAUDE.local.md")
MAX_SOURCE_DEPTH = 4
MAX_SOURCES = 40
IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
                "build", ".claude"}
DEFAULT_SESSIONS = 10
MAX_SESSIONS = 50
DEFAULT_MAX_CHARS = 12_000
MAX_TURNS_PER_SESSION = 40
MAX_TURN_CHARS = 400
MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
# Turns Claude Code writes on the user's behalf, not the user's own words.
HARNESS_TURN_PREFIXES = ("<command-name>", "<local-command-stdout>",
                         "<local-command-caveat>", "<command-message>")
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
NATIVE_PATHS_KEY = "paths"

# ---- user-visible text ------------------------------------------------------
HEADER_SOURCES = "## harvest sources (read the ones that name folders or file types)"
LINE_SOURCE = "- {path}  ({lines} lines{paths})"
NO_SOURCES = "- (none: no CLAUDE.md or .claude/rules/*.md under {root} or ~/.claude)"
HEADER_SESSIONS = "\n## recent sessions of this project ({shown} of {total}, newest first)"
NO_TRANSCRIPTS = "\n## recent sessions: none found in {directory}"
LINE_SESSION = "\n### {date}  session {session}  branch {branch}"
LINE_INJECTED = "rules injected (as far as usage stats remember): {names}"
LINE_INJECTED_NONE = "rules injected: none recorded"
LINE_TURN = "{index}. {text}"
TRUNCATED = " […]"
BUDGET_NOTE = "\n(older sessions omitted: {max_chars}-char budget reached)"
# -----------------------------------------------------------------------------


def project_slug(root):
    """The directory name Claude Code gives a project's transcripts: the
    absolute path with every non-alphanumeric character turned into `-`."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(root))


def transcripts_dir(root):
    return os.path.join(os.path.expanduser("~"), PROJECTS_RELPATH, project_slug(root))


def native_paths_of(path):
    """The `paths:` a native rule declares, or [] — read through the plugin's
    own frontmatter parser, which understands the same list shapes."""
    try:
        fields, _body = HOOK.parse_frontmatter(
            read_regular_file(path, HOOK.MAX_FRONTMATTER_BYTES + 1))
    except OSError:
        return []
    return HOOK.declared_values(fields, (NATIVE_PATHS_KEY,))


def line_count(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def harvest_sources(root):
    """[(path, lines, native paths)] — CLAUDE.md files under the root (to a
    bounded depth) and native rules of both the project and the user."""
    found = []
    root = os.path.abspath(root)
    for directory, subdirs, files in os.walk(root):
        depth = os.path.relpath(directory, root).count(os.sep)
        subdirs[:] = sorted(d for d in subdirs if d not in IGNORED_DIRS
                            and depth < MAX_SOURCE_DEPTH)
        for name in CLAUDE_MD_NAMES:
            if name in files:
                found.append((os.path.join(directory, name), None))
        if len(found) >= MAX_SOURCES:
            break
    claude_dir_md = os.path.join(root, ".claude", "CLAUDE.md")
    if os.path.isfile(claude_dir_md):
        found.append((claude_dir_md, None))
    for base in (root, os.path.expanduser("~")):
        rules_dir = os.path.join(base, NATIVE_RULES_RELPATH)
        if not os.path.isdir(rules_dir):
            continue
        for name in sorted(os.listdir(rules_dir)):
            if name.endswith(".md"):
                path = os.path.join(rules_dir, name)
                found.append((path, native_paths_of(path)))
    return [(path, line_count(path), paths) for path, paths in found[:MAX_SOURCES]]


def user_text_of(record):
    """The user's own words in a transcript record, or None: tool results,
    meta records and the turns the harness writes for slash commands are
    not the user talking."""
    if record.get("type") != "user" or record.get("isMeta"):
        return None
    content = record.get("message", {}).get("content")
    if isinstance(content, list):
        texts = [block.get("text", "") for block in content
                 if isinstance(block, dict) and block.get("type") == "text"]
        content = "\n".join(texts) if texts else None
    if not isinstance(content, str):
        return None
    text = SYSTEM_REMINDER.sub("", content).strip()
    if not text or text.startswith(HARNESS_TURN_PREFIXES):
        return None
    return " ".join(text.split())


def read_session(path):
    """{"session", "date", "branch", "turns"} for one transcript, bounded."""
    session = {"session": os.path.splitext(os.path.basename(path))[0],
               "date": None, "branch": None, "turns": []}
    try:
        if os.path.getsize(path) > MAX_TRANSCRIPT_BYTES:
            return session
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                text = user_text_of(record)
                if text is None:
                    continue
                session["date"] = session["date"] or str(record.get("timestamp", ""))[:10]
                session["branch"] = session["branch"] or record.get("gitBranch")
                if len(text) > MAX_TURN_CHARS:
                    text = text[:MAX_TURN_CHARS] + TRUNCATED
                session["turns"].append(text)
                if len(session["turns"]) >= MAX_TURNS_PER_SESSION:
                    break
    except OSError:
        pass
    return session


def recent_transcripts(directory, limit):
    try:
        entries = [entry for entry in os.scandir(directory)
                   if entry.is_file(follow_symlinks=False) and entry.name.endswith(".jsonl")]
    except OSError:
        return [], 0
    entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    return [entry.path for entry in entries[:limit]], len(entries)


def rules_injected_in(stats, session_id):
    """Rule names the usage stats still associate with a session id: the
    recent-sessions list per rule is bounded, so this is what is remembered,
    not necessarily everything that happened."""
    names = []
    for key, entry in stats["rules"].items():
        if session_id in entry.get("recent_sessions", []):
            names.append(key.rsplit("::", 1)[-1])
    return sorted(names)


def render(sources, sessions, total, root, directory, max_chars):
    lines = [HEADER_SOURCES]
    if sources:
        for path, count, paths in sources:
            shown = f", paths: {', '.join(paths)}" if paths else ""
            lines.append(LINE_SOURCE.format(path=path, lines=count, paths=shown))
    else:
        lines.append(NO_SOURCES.format(root=root))
    if not sessions:
        lines.append(NO_TRANSCRIPTS.format(directory=directory))
        return "\n".join(lines)
    lines.append(HEADER_SESSIONS.format(shown=len(sessions), total=total))
    spent = sum(len(line) for line in lines)
    stats = HOOK.load_stats()
    for session in sessions:
        block = [LINE_SESSION.format(date=session["date"] or "?",
                                     session=session["session"][:8],
                                     branch=session["branch"] or "?")]
        injected = rules_injected_in(stats, session["session"])
        block.append(LINE_INJECTED.format(names=", ".join(injected)) if injected
                     else LINE_INJECTED_NONE)
        block.extend(LINE_TURN.format(index=index, text=text)
                     for index, text in enumerate(session["turns"], 1))
        size = sum(len(line) + 1 for line in block)
        if spent + size > max_chars:
            lines.append(BUDGET_NOTE.format(max_chars=max_chars))
            break
        spent += size
        lines.extend(block)
    return "\n".join(lines)


def cmd_digest(args):
    if args.use_global:
        fail("'digest' needs --root: sessions are read per project, never "
             "across all of them")
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        fail(f"project root does not exist: {root}")
    limit = min(max(1, args.sessions or DEFAULT_SESSIONS), MAX_SESSIONS)
    max_chars = max(1000, args.max_chars or DEFAULT_MAX_CHARS)
    directory = transcripts_dir(root)
    paths, total = recent_transcripts(directory, limit)
    sessions = [read_session(path) for path in paths]
    sessions = [session for session in sessions if session["turns"]]
    print(render(harvest_sources(root), sessions, total, root, directory, max_chars))
    if not sessions and total:
        HOOK.warn(f"{total} transcript(s) found but none held a user turn "
                  f"(all older than {time.strftime('%Y-%m-%d')}? or unreadable)")
