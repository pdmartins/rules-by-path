#!/usr/bin/env python3
"""rules-by-path — PreToolUse hook for Claude Code.

When Claude touches a file (Read/Edit/Write/MultiEdit/NotebookEdit), this hook
collects the rules that apply to it and injects them into context via
`hookSpecificOutput.additionalContext`.

A rule is a single markdown file in `.claude/rules-by-path/` that declares the
glob it applies to in its own frontmatter:

    ---
    glob: src/api/**
    remember_after: 30k
    ---
    Every endpoint must validate its input.

Scopes: every `.claude/rules-by-path/` from the touched file's directory up to
the filesystem root, plus the global scope, `~/.claude/rules-by-path/`.

What reaches the model is the rule bodies and nothing else:

    <rules-by-path>
    Every endpoint must validate its input.
    ---
    Never log the request body.
    </rules-by-path>

Design constraints:
- Never blocks the tool call: any internal failure goes to stderr and the hook
  exits 0 with no stdout.
- Each rule *version* is injected at most once per session (the dedup key
  includes a hash of the content, so editing a rule re-injects it), then
  repeated in full once the context has moved on by `remember_after`.
- Files inside `.claude/rules-by-path/` never trigger injection.
- Rule content is untrusted input, and is not dressed up as anything more
  trustworthy than it is. The emitted text carries no provenance and no
  authentication: a rule file is exactly as trusted as the repository's
  CLAUDE.md, which the harness already injects with no ceremony at all. What
  the plugin does defend is the boundary — content cannot close the block early
  nor impersonate the harness itself (see `neutralize`).
- Glob matching is a non-backtracking segment matcher — no regex, hence no
  catastrophic backtracking on a hostile glob.
- Standard library only. Frontmatter is parsed by a small parser here, so the
  plugin has no YAML dependency and no second parser to drift from.

Rules are managed by the `rules-by-path:manage` skill through the companion
script `scripts/rules-by-path-admin.py` in this plugin.
"""

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
import unicodedata

# --- constants -------------------------------------------------------------

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN_COMMAND = os.path.join(PLUGIN_ROOT, "bin", "rules-by-path")

RULES_DIR_RELPATH = os.path.join(".claude", "rules-by-path")
LEGACY_MAP_NAME = "rules-map.yml"
FILE_PATH_KEYS = ("file_path", "notebook_path", "path")

MAX_RULE_CHARS = 4_000  # a rule states constraints; it is not documentation
RULE_WARN_CHARS = 2_000  # `validate` nags above this
MAX_TOTAL_CHARS = 24_000  # ceiling for one injection
MAX_RULES_PER_SCOPE = 256
# The hook only reads this many bytes to find a rule's closing `---`, so the
# admin must refuse to write a frontmatter larger than this (otherwise a rule it
# accepts becomes invisible here). Sized to hold the maximum a rule may legally
# declare: MAX_GLOBS_PER_RULE globs of up to MAX_GLOB_CHARS each, plus keys.
MAX_FRONTMATTER_BYTES = 8_192
MAX_GLOB_CHARS = 256
MAX_GLOBS_PER_RULE = 16
MAX_RULE_NAME_CHARS = 128
MAX_SESSION_ID_CHARS = 120  # keeps <id>.json inside every filesystem's name limit
MAX_SCOPES = 8  # scopes consulted per tool call
MAX_ANCESTOR_STEPS = 64
# Total wall-clock a single tool call may spend matching globs, divided evenly
# among the scopes that apply. Each glob is polynomial on its own, but a scope
# may declare thousands of them; this bounds the aggregate so a hostile repo
# cannot stall every tool call, and the per-scope split stops one scope from
# spending another's share. Fail-open: when a scope exhausts its slice, its
# remaining rules are simply not consulted for this call.
MATCH_BUDGET_SECONDS = 2.0
STATE_MAX_AGE_SECONDS = 14 * 24 * 3600

# How far the context may move on before an already-injected rule is repeated.
# Long-context models drift away from a rule injected hundreds of thousands of
# tokens ago, and a session that never compacts never gets the SessionStart
# reset. `remember_after: never` disables the repeat for one rule.
#
# Tokens are the honest unit: a session that reads three huge files burns 200k
# tokens in 3 tool calls, while one doing 50 tiny greps burns 20k in 50 — the
# call count measures the wrong thing. Calls remain the fallback for when the
# transcript cannot be read, and there is no conversion between the two: no
# faithful tokens-per-call rate exists, and faking precision is worse than
# losing it.
DEFAULT_REMEMBER_TOKENS = 30_000
DEFAULT_REMEMBER_CALLS = 25
REMEMBER_ENV_VAR = "RULES_BY_PATH_REMEMBER_AFTER"
# A bare number below this is read as a leftover from the call-counting era
# (`remember_after: 25`) rather than as an absurdly small token budget.
MIN_REMEMBER_TOKENS = 1_000
# Only the tail of the transcript is read to find the last usage record.
TRANSCRIPT_TAIL_BYTES = 64 * 1024

# The characters a rule file name may carry besides letters and digits. This is
# an allowlist on purpose — see is_valid_rule_name.
RULE_NAME_EXTRA_CHARS = "._-"

LEGACY_NOTICE = (
    "This scope still uses the old rules-map.yml format, so NO rules are being "
    "injected from it. Migrate it by running: "
    f"\"{ADMIN_COMMAND}\" migrate --root <project-root> (or --global). "
    "Tell the user this happened."
)

SESSION_NOTICE = (
    "[rules-by-path] This session has path-scoped rules available. They are "
    "markdown files under `.claude/rules-by-path/` (project) and "
    "`~/.claude/rules-by-path/` (global), and they reach you AUTOMATICALLY: the "
    "moment you touch a file whose glob matches, the rule is injected into your "
    "context. So there is never a reason to open, list, grep or edit those files "
    "yourself — and the recommended setup deny-lists them, so an attempt is "
    "refused rather than answered. To read or change a rule, use the CLI: "
    f"\"{ADMIN_COMMAND}\" list|show|which|add|update, with --root '<repo-root>' "
    "or --global — or the rules-by-path:manage skill, which drives it for you."
)

TRUNCATION_NOTICE = "\n[...rule truncated by the rules-by-path size limit...]"

# The whole of the emitted framing: an opening tag, a closing tag, and a line
# between rules. The tags are not decoration — another injector's document can
# land in the same message right after this one, so without a closing tag there
# is no way to tell where the rules end. The separator is a line rather than a
# blank line because rule bodies contain blank lines.
RULES_OPEN_TAG = "<rules-by-path>"
RULES_CLOSE_TAG = "</rules-by-path>"
RULE_SEPARATOR = "---"

# Framing that rule content must never be able to emit verbatim. Two kinds, and
# the second is the one that matters: this plugin's own tags (content that
# closes the block early would put its text outside it, where it reads as the
# harness talking), and the harness's own markers. Impersonating a rule buys
# the authority of a rule; impersonating Claude Code buys the authority the
# CLAUDE.md is injected with. Only the second is an escalation.
FORGED_FRAMING_TOKENS = (
    RULES_OPEN_TAG,
    RULES_CLOSE_TAG,
    TRUNCATION_NOTICE.strip(),
    "[rules-by-path]",
    "<system-reminder",
    "</system-reminder",
    "<function_results",
    "<function_calls",
    # How the harness labels a hook's additionalContext when it hands it to the
    # model — observed live: `PreToolUse:Read hook additional context: ...`.
    # Content that emits this claims to be the harness introducing a new block.
    "hook additional context",
)


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


# --- frontmatter -----------------------------------------------------------

def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text, source="rule"):
    """Parse the leading `---` block of a rule file.

    Deliberately tiny and strict: `key: value` lines plus `  - item` lines
    under a key. No comments (so a `#` in a glob is literal), no nesting, no
    anchors. The whole point is that there is exactly one parser, with no
    optional dependency that could behave differently — two parsers for the
    same file is how this plugin previously shipped two corruption bugs.

    Returns (fields, body). `fields` maps a key to a string or list of strings.
    """
    # A leading UTF-8 BOM (Notepad, "UTF-8 with BOM", PowerShell Out-File) would
    # make the file not start with `---`, so a perfectly good rule would parse to
    # nothing and be silently ignored. Strip it before the delimiter check.
    if text[:1] == "﻿":
        text = text[1:]
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text

    fields = {}
    current_key = None
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        stripped = raw.strip()
        if stripped.startswith("- ") and current_key is not None:
            item = unquote(stripped[2:])
            if item:
                if not isinstance(fields.get(current_key), list):
                    fields[current_key] = []
                fields[current_key].append(item)
            continue
        if ":" not in stripped:
            warn(f"{source}: frontmatter line not understood: {stripped[:80]!r}")
            current_key = None
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = unquote(value)
        current_key = key
        fields[key] = value if value else []
    return fields, "\n".join(lines[end + 1:])


def globs_of(fields):
    """The globs a rule declares. `glob` may be a single value or a list; the
    plural `globs` is accepted too, because people will write it."""
    raw = fields.get("glob")
    if raw in (None, [], ""):
        raw = fields.get("globs")
    if raw in (None, [], ""):
        return []
    values = raw if isinstance(raw, list) else [raw]
    globs = []
    dropped = 0
    for value in values:
        value = str(value).strip()
        if not value:
            continue
        if len(value) > MAX_GLOB_CHARS:
            warn(f"glob longer than {MAX_GLOB_CHARS} chars ignored: {value[:64]!r}...")
            continue
        if len(globs) >= MAX_GLOBS_PER_RULE:
            dropped += 1  # kept counting so the warning states how many were lost
            continue
        globs.append(value)
    if dropped:
        warn(f"more than {MAX_GLOBS_PER_RULE} globs on one rule; {dropped} ignored "
             f"(these never match — split the rule or remove some globs)")
    return globs


def parse_size(text):
    """An integer with an optional `k`/`M` suffix: `30k`, `1M`, `200000`."""
    text = str(text).strip().lower()
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1_000, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000, text[:-1]
    return int(float(text.strip())) * multiplier


def parse_remember_after(raw, source):
    """(value, unit) for a `remember_after` setting, or None when unset.

    unit is "tokens" or "calls"; a value of 0 means never repeat.

        remember_after: 30k        -> (30000, "tokens")
        remember_after: 30000      -> (30000, "tokens")
        remember_after: 25 calls   -> (25, "calls")
        remember_after: never      -> (0, None)

    Tokens are the default unit because they measure the thing that actually
    causes drift. A bare number below MIN_REMEMBER_TOKENS is refused rather than
    honoured: it is far more likely to be a leftover `remember_after: 25` from
    when the interval was counted in tool calls than a genuine 25-token budget,
    and silently treating it as tokens would repeat the rule on every call.
    """
    if raw in (None, [], ""):
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in ("never", "no", "off", "0"):
        return (0, None)
    unit = "tokens"
    if text.endswith("calls") or text.endswith("call"):
        unit = "calls"
        text = text.rsplit("call", 1)[0]
    elif text.endswith("c"):
        unit = "calls"
        text = text[:-1]
    elif text.endswith("tokens") or text.endswith("token"):
        text = text.rsplit("token", 1)[0]
    try:
        value = parse_size(text)
    except ValueError:
        warn(f"{source}: remember_after not understood: {str(raw)[:32]!r}")
        return None
    if value <= 0:
        return (0, None)
    if unit == "tokens" and value < MIN_REMEMBER_TOKENS:
        warn(f"{source}: remember_after of {value} tokens looks like a call "
             f"count from the old format; using the default instead "
             f"(write '{value} calls' if that is what you meant)")
        return None
    return (value, unit)


def remember_after_of(fields):
    """Per-rule override, or None to use the session default."""
    return parse_remember_after(fields.get("remember_after"), "rule")


def remember_after_default(measured_in_tokens):
    """The interval used by rules that declare none, in the unit the session can
    actually measure."""
    override = parse_remember_after(os.environ.get(REMEMBER_ENV_VAR),
                                    REMEMBER_ENV_VAR)
    if override is not None:
        return override
    if measured_in_tokens:
        return (DEFAULT_REMEMBER_TOKENS, "tokens")
    return (DEFAULT_REMEMBER_CALLS, "calls")


# --- glob matching (non-backtracking) --------------------------------------

def match_segment(pattern, text):
    """Match one path segment: '*' matches any run of characters within the
    segment, '?' exactly one. Two-pointer algorithm, O(len(pattern)*len(text))
    worst case — never exponential."""
    p = t = 0
    star = -1
    mark = 0
    while t < len(text):
        if p < len(pattern) and (pattern[p] == "?" or pattern[p] == text[t]):
            p += 1
            t += 1
        elif p < len(pattern) and pattern[p] == "*":
            star = p
            p += 1
            mark = t
        elif star >= 0:
            p = star + 1
            mark += 1
            t = mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


def match_path(glob_segments, path_segments):
    """Match '/'-split sequences. A '**' segment matches zero or more segments,
    except as the LAST segment where it requires at least one — `src/api/**`
    means "inside src/api", not "src/api itself".

    Bottom-up DP over (glob index, path index): O(G*T) time, O(T) memory, with
    no backtracking, so no input can make it blow up."""
    n_glob = len(glob_segments)
    n_path = len(path_segments)
    row = [False] * (n_path + 1)  # row[t] == "glob[g:] can match path[t:]"
    row[n_path] = True
    for g in range(n_glob - 1, -1, -1):
        prev = row
        row = [False] * (n_path + 1)
        segment = glob_segments[g]
        if segment == "**":
            if g == n_glob - 1:
                for t in range(n_path + 1):
                    row[t] = t < n_path  # trailing '**' needs at least one segment
            else:
                for t in range(n_path, -1, -1):
                    row[t] = prev[t] or (t < n_path and row[t + 1])
        else:
            for t in range(n_path):
                row[t] = match_segment(segment, path_segments[t]) and prev[t + 1]
            row[n_path] = False
    return row[0]


def glob_matches_path(glob, path):
    """Full match of `path` against `glob`, with the documented conveniences:
    a trailing '/' means the whole directory, and a glob with no metacharacter
    matches itself and anything under it."""
    g = glob.strip()
    if g.startswith("./"):
        g = g[2:]
    if g.endswith("/"):
        g = g.rstrip("/") + "/**"
    segments = [s for s in g.split("/") if s]
    targets = [s for s in path.split("/") if s]
    if not any(ch in g for ch in "*?"):  # plain path: itself or anything under it
        return len(targets) >= len(segments) and targets[:len(segments)] == segments
    return match_path(segments, targets)


def glob_matches(glob, rel_path, abs_path):
    """Check `glob` against a file. `rel_path` is None for the global scope.

    Non-absolute globs match the project-relative path (or the absolute path
    minus the leading '/' in the global scope); globs without '/' also match
    the file's basename, so `*.cs` catches any C# file at any depth.
    """
    g = glob.strip()
    if g.startswith("/"):
        targets = [abs_path]
    else:
        targets = [rel_path if rel_path is not None else abs_path.lstrip("/")]
        if "/" not in g.rstrip("/"):
            targets.append(os.path.basename(abs_path))
    return any(glob_matches_path(g, t) for t in targets)


# --- scope discovery -------------------------------------------------------

def derive_rule_name(glob):
    """Default rule filename when `--rule` is not given. A total function: every
    glob yields a usable name.

    It used to drop wildcard segments only at the ENDS, so the most idiomatic
    globs of all produced names the allowlist then refused — `src/**/*.py` came
    out as `src--**--*.py.md` and `add` simply failed. The forms that broke are
    the ones the docs present as the normal path.

        src/**              -> src.md
        src/**/*.py         -> src-py.md
        docs/**/*.md        -> docs-md.md
        *.cs                -> cs.md
        /repos/_hv/**/*.cs  -> repos-hv-cs.md

    A derived name is only ever a fallback. A good rule name is an assertion —
    `handlers-inherit-base.md` — because it is the name a human reads in `list`
    and `which` when deciding which rule to open."""
    words = []
    for segment in glob.strip().strip("/").split("/"):
        if not segment or set(segment) <= {"*"}:
            continue  # a segment that is only wildcards names nothing
        if segment.startswith("*.") and len(segment) > 2:
            segment = segment[2:]  # `*.py` is about py files, not about `*`
        elif segment.lower().endswith(".md") and len(segment) > 3:
            # A glob naming one markdown file: the rule file is markdown too, so
            # `docs/architecture.md` -> `docs-architecture`, not `-architecture-md`.
            segment = segment[:-3]
        words.append(segment)
    name = re.sub(r"[^a-z0-9]+", "-", "-".join(words).lower()).strip("-")
    return (name or "root") + ".md"


def is_valid_rule_name(rule_name):
    """A rule name must be a bounded `*.md` file name built only from letters,
    digits and `._-`.

    An allowlist, not a blocklist, because this name is repository data that
    reaches three dangerous places: a shell (the manage skill runs the CLI with
    the name it read), a filesystem path, and the authenticated injection
    header. A blocklist of ASCII punctuation let both `$(...)`/backticks
    (command substitution, which expands inside double quotes too) and the
    full-width unicode lookalikes of ':' and '|' (header field forgery) through.
    Length matters as well: an unbounded name reaches the filesystem and raises
    OSError instead of failing cleanly.

    Unicode letters stay allowed — a rule named in the user's own language is
    legitimate — and the name is normalized before the check so a macOS
    filesystem handing back a decomposed form still matches."""
    if not isinstance(rule_name, str) or not rule_name.endswith(".md"):
        return False
    if not rule_name or len(rule_name) > MAX_RULE_NAME_CHARS:
        return False
    stem = unicodedata.normalize("NFC", rule_name[:-len(".md")])
    if not stem:
        return False
    return all(ch.isalnum() or ch in RULE_NAME_EXTRA_CHARS for ch in stem)


def is_safely_owned(path):
    """True when `path` is owned by us and not world-writable.

    A rules directory in a world-writable shared parent (say /tmp) would let any
    local user inject instructions into every session below it. Group-writable
    is left alone on purpose: shared-group directories are the norm on team
    machines, and rejecting them would break more than it protects."""
    if os.name == "nt":
        return True  # POSIX ownership bits do not carry over; skip the check
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if info.st_uid != os.geteuid():
        return False
    return not bool(info.st_mode & stat.S_IWOTH)


def scope_is_contained(base_dir, scope_dir):
    """True when `scope_dir` physically lives at `base_dir/.claude/rules-by-path`.

    Validating only what is *inside* the scope is not enough: if `.claude` or
    `.claude/rules-by-path` is itself a symlink, every check below it resolves
    through the same link and passes trivially, so reads, writes and deletes
    land wherever the link points — including the user's global rules."""
    expected = os.path.join(os.path.realpath(base_dir), RULES_DIR_RELPATH)
    return os.path.realpath(scope_dir) == expected


def usable_scope(base_dir, is_global=False):
    """The scope directory for `base_dir`, or None when it is absent or unsafe.

    Physical containment is required for a PROJECT scope, where a symlinked
    `.claude` can arrive inside a cloned repository and redirect everything to
    the attacker's target. It is NOT required for the global scope: `~/.claude`
    is the user's own configuration, symlinking it elsewhere (shared config,
    dotfiles in a repo) is a normal and deliberate choice, and nobody can plant
    that link without already owning the home directory. Ownership of the real
    target is still checked in both cases."""
    scope_dir = os.path.join(base_dir, RULES_DIR_RELPATH)
    if not os.path.isdir(scope_dir):
        return None
    if not is_global and not scope_is_contained(base_dir, scope_dir):
        warn(f"ignoring {scope_dir}: it does not physically live inside {base_dir}")
        return None
    if not is_safely_owned(os.path.realpath(scope_dir)):
        warn(f"ignoring {scope_dir}: not safely owned (world-writable or another user's)")
        return None
    return scope_dir


def find_scopes(start_dir):
    """[(base_dir_or_None, scope_dir, label)] for a touched file: the global
    scope first, then every project scope from the highest ancestor down to the
    touched file's own directory.

    The walk goes all the way to the filesystem root and collects every
    `.claude/rules-by-path` on the way. It used to stop at the first `.git`,
    which silently excluded git submodules: inside one, `.git` is a *file*, so
    `os.path.exists` matched and the walk halted there — a `.cs` under
    `libs/api/src/` received nothing at all, even with a `**/*.cs` rule at the
    parent repository's root.

    What that costs, stated plainly: a rules directory in an ancestor the user
    does not control can now inject into every session below it. The ownership
    and permission filter in `usable_scope` — another user's directory and
    world-writable ones are refused — is what remains of that defence.

    Two orderings, both deliberate, both about who gets served when a budget
    runs out. Global comes first so the user's own rules always get budget
    before rules that arrived with a cloned repository. Among project scopes the
    highest ancestor comes first, and it is the one kept when MAX_SCOPES is
    exceeded: the walk discovers scopes deepest-first, so a naive cap drops it —
    and anyone able to add directories to a repo (a PR into a monorepo, a
    vendored dependency) could bury the outer rules under a chain of nested
    scopes and silently suppress them for that whole subtree.
    """
    scopes = []
    seen = set()
    home = os.path.realpath(os.path.expanduser("~"))

    global_scope = usable_scope(home, is_global=True)
    if global_scope:
        seen.add(os.path.realpath(global_scope))
        scopes.append((None, global_scope, "global"))

    chain = []  # project scopes, deepest first
    directory = start_dir
    steps = 0
    while steps < MAX_ANCESTOR_STEPS:
        steps += 1
        scope_dir = usable_scope(directory)
        if scope_dir:
            real = os.path.realpath(scope_dir)
            if real not in seen:
                seen.add(real)
                chain.append((directory, scope_dir))
        parent = os.path.dirname(directory)
        if parent == directory:
            break  # filesystem root
        directory = parent

    chain.reverse()  # highest ancestor first
    room = max(1, MAX_SCOPES - len(scopes))
    if len(chain) > room:
        warn(f"more than {MAX_SCOPES} scopes apply; keeping the outermost and "
             f"the {room - 1} nearest to the file, ignoring the rest")
        # Keep the outermost scope and the ones closest to the touched file;
        # drop the middle of the chain, which is the part nothing depends on.
        chain = chain[:1] + chain[len(chain) - (room - 1):] if room > 1 else chain[:1]
    for base_dir, scope_dir in chain:
        scopes.append((base_dir, scope_dir, f"project {base_dir}"))
    return scopes


# --- rule loading ----------------------------------------------------------

def read_rule_file(scope_dir, name, body_limit=MAX_RULE_CHARS):
    """Read a rule file safely: name validated, opened without following
    symlinks, must be a regular file.

    Returns (fields, body, truncated) or None. Truncation is reported as a flag
    rather than by appending a marker to the body: the marker then lives in the
    authenticated header, where rule content cannot produce one. A body that
    simply ended with the marker text was otherwise indistinguishable from a
    body this function had cut."""
    if not is_valid_rule_name(name):
        warn(f"invalid rule name, so it is not injected — a rule file name may "
             f"hold only letters, digits and '{RULE_NAME_EXTRA_CHARS}' and must "
             f"end in '.md': {name[:80]!r}")
        return None
    path = os.path.join(scope_dir, name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    read_limit = MAX_FRONTMATTER_BYTES + body_limit + 1
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        warn(f"cannot open rule '{name}' in {scope_dir}: {exc}")
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            warn(f"rule '{name}' is not a regular file; skipped")
            return None
        with os.fdopen(fd, encoding="utf-8", errors="replace") as handle:
            fd = None  # fdopen owns it now
            text = handle.read(read_limit)
    except Exception as exc:
        warn(f"failed reading {path}: {exc}")
        return None
    finally:
        if fd is not None:
            os.close(fd)
    fields, body = parse_frontmatter(text, name)
    # A file that opens like a rule but yields no fields, and filled the whole
    # read window, has a frontmatter with no closing `---` within the limit. The
    # admin refuses to write one this large, so this only happens to a
    # hand-edited file — say so instead of silently treating it as a non-rule.
    if not fields and text.lstrip("﻿").startswith("---") and len(text) >= read_limit:
        warn(f"rule '{name}': frontmatter exceeds {MAX_FRONTMATTER_BYTES} bytes or "
             f"has no closing '---'; not treated as a rule")
    body = body.strip()
    if body_limit <= 0:
        return fields, "", False  # index pass: the caller only wants the frontmatter
    if len(body) > body_limit:
        warn(f"rule '{name}' truncated at {body_limit} chars")
        return fields, body[:body_limit], True
    return fields, body, False


def scope_index(scope_dir):
    """[(name, fields)] for every rule file in a scope, sorted by name.

    Only the frontmatter is read here; the body is read later, and only for the
    rules that actually match. That keeps the per-tool-call cost proportional
    to the number of rules, not to their size."""
    try:
        with os.scandir(scope_dir) as it:
            names = sorted(entry.name for entry in it
                           if entry.name.endswith(".md")
                           and entry.is_file(follow_symlinks=False))
    except OSError as exc:
        warn(f"cannot list {scope_dir}: {exc}")
        return []
    if len(names) > MAX_RULES_PER_SCOPE:
        warn(f"{scope_dir}: {len(names)} rules exceeds the {MAX_RULES_PER_SCOPE} cap")
        names = names[:MAX_RULES_PER_SCOPE]
    entries = []
    for name in names:
        result = read_rule_file(scope_dir, name, body_limit=0)
        # Frontmatter is what makes a file a rule. A plain markdown file that
        # happens to sit in the directory (a README, notes) is not a broken
        # rule — it is simply not a rule, and must not be reported as one.
        if result is not None and result[0]:
            entries.append((name, result[0]))
    return entries


def has_legacy_map(scope_dir):
    return os.path.isfile(os.path.join(scope_dir, LEGACY_MAP_NAME))


# --- per-session state -----------------------------------------------------

def state_dir():
    """Where per-session state lives. Prefers the plugin's own data directory,
    falls back to ~/.claude/cache, then to a per-uid temp directory."""
    candidates = []
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        candidates.append(os.path.join(plugin_data, "state"))
    candidates.append(os.path.join(os.path.expanduser("~"), ".claude", "cache",
                                   "rules-by-path"))
    suffix = f"-{os.getuid()}" if hasattr(os, "getuid") else ""
    candidates.append(os.path.join(tempfile.gettempdir(), f"rules-by-path-state{suffix}"))
    for candidate in candidates:
        try:
            os.makedirs(candidate, mode=0o700, exist_ok=True)
            if os.path.islink(candidate) or not os.path.isdir(candidate):
                continue
            if not is_safely_owned(candidate):
                warn(f"ignoring state directory {candidate}: not safely owned")
                continue
            if os.access(candidate, os.W_OK):
                return candidate
        except Exception:
            continue
    warn("no writable state directory; rules will re-inject on every tool call")
    return None


def state_file_for(session_id):
    """The state file for a session id, which arrives as JSON from another
    process and is therefore not to be trusted as a string.

    Everything else in this area degrades to "stateless but still injecting";
    this used to be the one line that could do worse. `re.sub` raises TypeError
    on a non-string, and this call sits outside main()'s try, so a numeric or
    absent-typed id took the whole injection down — the user's global rules
    included — instead of costing only the dedup. An over-long id had the
    mirror-image effect: ENAMETOOLONG on every save, so every rule re-injected
    in full on every single tool call."""
    directory = state_dir()
    if directory is None:
        return None
    raw = session_id if isinstance(session_id, str) and session_id.strip() else "default"
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", raw)
    if len(safe_id) > MAX_SESSION_ID_CHARS:
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]
        safe_id = safe_id[:MAX_SESSION_ID_CHARS - len(digest) - 1] + "-" + digest
    return os.path.join(directory, (safe_id or "default") + ".json")


def coerce_seen_entry(value):
    """[call number, context tokens or None] from whatever is on disk, or None
    when the entry is unusable. Accepts the bare integer written by earlier
    versions, which recorded only the call number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return [value, None]
    if isinstance(value, list) and value:
        try:
            calls = int(value[0])
        except (TypeError, ValueError):
            return None
        tokens = value[1] if len(value) > 1 else None
        if tokens is not None:
            try:
                tokens = int(tokens)
            except (TypeError, ValueError):
                tokens = None
        return [calls, tokens]
    return None


def context_size(payload):
    """Tokens of context in this session, or None when it cannot be measured.

    The count is read from the transcript the harness already writes: the last
    `usage` record is what the API itself billed, not an estimate from character
    counts. Only the tail of the file is read — a transcript reaches several
    megabytes, and reading one per tool call would cost more than every other
    thing this hook does put together.

    Two known imprecisions, both acceptable against a threshold of tens of
    thousands: the record describes the *previous* request, so it lags by one
    turn; and after a compaction the number drops, which is exactly when
    SessionStart(compact) already clears the state.

    Returns None when there is no transcript, it cannot be read, or no usage
    record is found — the caller then falls back to counting tool calls.
    This is a capability, not a dependency: losing it costs precision, not
    function.
    """
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > TRANSCRIPT_TAIL_BYTES:
                handle.seek(size - TRANSCRIPT_TAIL_BYTES)
                handle.readline()  # drop the partial line the seek landed in
            tail = handle.read()
    except OSError as exc:
        warn(f"transcript not readable ({exc}); counting tool calls instead")
        return None
    total = None
    for line in tail.decode("utf-8", "replace").splitlines():
        if '"usage"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        message = record.get("message")
        message = message if isinstance(message, dict) else {}
        usage = message.get("usage")
        if not isinstance(usage, dict):
            usage = record.get("usage")
        if not isinstance(usage, dict):
            continue
        counted = 0
        for key in ("input_tokens", "cache_creation_input_tokens",
                    "cache_read_input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and value > 0:
                counted += value
        if counted:
            total = counted
    return total


def open_state(state_path):
    """Open the session state under an exclusive lock: (fd, state).

    state = {"calls": int,
             "seen": {dedup_key: [call number, context tokens or None]}}.

    Both measures are recorded because rules choose their own unit: one rule may
    ask to be repeated every 30k tokens and another every 25 calls, in the same
    session. Storing only the session's preferred unit would silently ignore
    whichever rule disagreed with it.

    Parallel tool calls each spawn a hook process, so the read-decide-write
    cycle is serialized; on any failure the hook proceeds statelessly rather
    than blocking the tool call."""
    empty = {"calls": 0, "seen": {}}
    if state_path is None:
        return None, empty
    try:
        # O_NOFOLLOW: this is the one file the hook opens for WRITING, and
        # save_state truncates it. A symlink planted at that path would have its
        # target destroyed and replaced with the hook's JSON. The ELOOP lands in
        # the except below, which degrades to stateless — the fail-open contract.
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(state_path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            warn(f"state path {state_path} is not a regular file; ignoring it")
            os.close(fd)
            return None, empty
        lock_exclusive(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        raw = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw += chunk
        if not raw.strip():
            return fd, empty
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            # Unreadable state (a crash mid-write, or an older format): start
            # over and KEEP the fd so the next save overwrites it. Returning
            # without the fd would leave it broken for the whole session, and
            # every rule would re-inject in full on every single tool call.
            warn(f"state file {state_path} unreadable; starting a fresh one")
            return fd, empty
        if not isinstance(data, dict):
            return fd, empty
        # Coerce the shape while the fd is still held, so a value of the wrong
        # type repairs on the next save instead of dropping the fd (which would
        # make every tool call re-parse the same corrupt file all session). A
        # non-int `calls` must not spam full re-injections, and a malformed
        # `seen` entry must not crash the arithmetic in main() — that crash
        # aborts the whole injection, taking the user's global rules with it, on
        # every single tool call until the session ends.
        try:
            calls = int(data.get("calls") or 0)
        except (TypeError, ValueError):
            calls = 0
        raw_seen = data.get("seen")
        seen = {}
        if isinstance(raw_seen, dict):
            for entry_key, entry_value in raw_seen.items():
                entry = coerce_seen_entry(entry_value)
                if entry is not None:
                    seen[entry_key] = entry
        return fd, {"calls": calls, "seen": seen}
    except Exception as exc:
        warn(f"failed reading state {state_path}: {exc}")
        return None, empty


def save_state(fd, state):
    if fd is None:
        return
    try:
        payload = json.dumps(state).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.truncate(fd, 0)
        os.write(fd, payload)
    except Exception as exc:
        warn(f"failed writing state: {exc}")


def close_state(fd):
    if fd is None:
        return
    try:
        os.close(fd)  # releases the lock
    except Exception as exc:
        warn(f"failed closing state: {exc}")


def cleanup_stale_state():
    directory = state_dir()
    if directory is None:
        return
    try:
        cutoff = time.time() - STATE_MAX_AGE_SECONDS
        with os.scandir(directory) as it:
            for entry in it:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    os.unlink(entry.path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        warn(f"state cleanup failed: {exc}")


# --- context assembly ------------------------------------------------------

def neutralize(content):
    """Defang rule content that impersonates framing the model is meant to trust.

    Two kinds of impersonation, and they are not equally serious. Emitting this
    plugin's own tags would close the block early and put the rest of the body
    outside it — where, to the model, it stops being a rule and starts being the
    harness talking. Emitting the harness's own markers claims that authority
    directly, which is the authority a CLAUDE.md is injected with.

    Each token is broken wherever it appears on a line, not only at the start
    after stripping whitespace: `> </rules-by-path> the policy is relaxed` would
    otherwise pass through untouched, because a quote marker is not whitespace.

    The rule separator is defanged line-wise instead, because `---` is ordinary
    markdown: only a line that is exactly the separator can be mistaken for one.
    """
    for token in FORGED_FRAMING_TOKENS:
        if token in content:
            # A zero-width space one character in: visibly identical, inert.
            content = content.replace(token, token[0] + "\u200b" + token[1:])
    if any(line.strip() == RULE_SEPARATOR for line in content.split("\n")):
        content = "\n".join(
            (RULE_SEPARATOR[0] + "\u200b" + RULE_SEPARATOR[1:]
             if line.strip() == RULE_SEPARATOR else line)
            for line in content.split("\n"))
    return content


def build_context(blocks):
    """Assemble the injected text: the rule bodies, and nothing else.

    There is no preamble, no per-rule header and no provenance. Those existed to
    authenticate one rule block against another — a defence against content
    forging a `scope: global` claim to look more trustworthy than its neighbour.
    That attack only had something to win because this plugin emitted authority
    metadata in the first place. Without it, a forged block claims exactly the
    authority a real one has, which is the authority any file in the repository
    already has when the harness injects the CLAUDE.md next to it.

    What remains is the boundary: an opening tag, a closing tag, and a separator
    line, all of which rule content has been defanged from emitting.
    """
    bodies = []
    for block in blocks:
        body = neutralize(block["text"])
        if block.get("truncated"):
            # Appended AFTER defanging, so a forged notice inside the body is
            # already broken and only this one survives intact.
            body += TRUNCATION_NOTICE
        bodies.append(body)
    separator = f"\n{RULE_SEPARATOR}\n"
    return f"{RULES_OPEN_TAG}\n{separator.join(bodies)}\n{RULES_CLOSE_TAG}"


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


def is_inside_rules_dir(abs_path):
    """True when the path is inside a rules directory — those files must never
    trigger injection. Checked on the resolved path too, so an in-repo symlink
    aliasing the rules directory does not slip past a textual comparison."""
    needle = f"/{RULES_DIR_RELPATH.replace(os.sep, '/')}/"
    for candidate in (abs_path, os.path.realpath(abs_path).replace(os.sep, "/")):
        if needle in candidate + "/":
            return True
    return False


def path_targets(abs_path, real_abs, base_dir):
    """[(rel_path, abs_path)] — the paths a glob is matched against.

    The literal path the tool named, plus the resolved one when it still lives
    inside the same project. Matching only the literal text means the same file
    reached through a directory symlink does not get the rule that governs it:
    monorepos routinely carry convenience links (`packages/app/shared ->
    ../../shared`), and a hostile repo could alias a directory precisely to dodge
    a rule. The resolved path is dropped when it leaves the project, so a link
    pointing outside cannot pull in globs from a scope it does not belong to."""
    if base_dir is None:
        targets = [(None, abs_path)]
        if real_abs != abs_path:
            targets.append((None, real_abs))
        return targets
    targets = [(os.path.relpath(abs_path, base_dir).replace(os.sep, "/"), abs_path)]
    if real_abs != abs_path:
        rel_real = os.path.relpath(real_abs, os.path.realpath(base_dir))
        rel_real = rel_real.replace(os.sep, "/")
        if rel_real != ".." and not rel_real.startswith("../"):
            targets.append((rel_real, real_abs))
    return targets


def collect_candidates(abs_path, scopes):
    """(candidates, legacy_scope_labels) for a touched file.

    A candidate is (scope_dir, label, name, glob, fields) — one per matching
    rule, listing the first glob that matched so provenance stays specific.

    Every scope gets its own slice of the matching budget, and the clock is
    checked per glob rather than per rule. One scope must not be able to spend
    another's time: a nested scope is consulted before the repository root, so a
    shared budget let a vendored directory full of expensive globs starve the
    root's rules on every single tool call — permanently, since the budget is
    recomputed per call."""
    candidates = []
    legacy = []
    budget_hit = False
    real_abs = os.path.realpath(abs_path).replace(os.sep, "/")
    per_scope = MATCH_BUDGET_SECONDS / max(1, len(scopes))
    for base_dir, scope_dir, label in scopes:
        deadline = time.monotonic() + per_scope
        if has_legacy_map(scope_dir):
            legacy.append(label)
        targets = path_targets(abs_path, real_abs, base_dir)
        for name, fields in scope_index(scope_dir):
            if time.monotonic() > deadline:
                budget_hit = True
                break
            for glob in globs_of(fields):
                if time.monotonic() > deadline:
                    budget_hit = True
                    break
                if any(glob_matches(glob, rel, target) for rel, target in targets):
                    candidates.append((scope_dir, label, name, glob, fields))
                    break
    if budget_hit:
        warn(f"glob matching exceeded its {per_scope:.2f}s per-scope budget; the "
             f"remaining rules of that scope were skipped for this tool call")
    return candidates, legacy


def is_due(last_seen, call_number, tokens, interval):
    """Whether a rule already delivered this session should be sent again.

    The question is only ever asked when the rule's glob matched the file being
    touched, so covering the distance is necessary but not sufficient: a rule
    governing a folder nobody opens again is never repeated, however long the
    session runs.

    `interval` is (value, unit) as parsed from `remember_after`; a value of 0
    means never. A token distance in a session that cannot count tokens falls
    back to the default call count, which prefers a coarser schedule to silence.
    Converting between tokens and calls is never attempted — there is no
    faithful rate, and inventing one would misreport precision.
    """
    value, unit = interval
    if not value:
        return False
    last_calls, last_tokens = last_seen
    if unit == "calls":
        return call_number - last_calls >= value
    if tokens is None or last_tokens is None:
        return call_number - last_calls >= DEFAULT_REMEMBER_CALLS
    return tokens - last_tokens >= value


def main():
    payload = json.load(sys.stdin)
    raw_path = extract_file_path(payload)
    if not raw_path:
        return
    cwd = payload.get("cwd") or os.getcwd()
    abs_path = raw_path if os.path.isabs(raw_path) else os.path.join(cwd, raw_path)
    abs_path = os.path.normpath(abs_path).replace(os.sep, "/")
    if is_inside_rules_dir(abs_path):
        return

    scopes = find_scopes(os.path.dirname(abs_path))
    if not scopes:
        return
    candidates, legacy_scopes = collect_candidates(abs_path, scopes)

    tokens = context_size(payload)
    default_interval = remember_after_default(tokens is not None)
    state_path = state_file_for(payload.get("session_id"))
    state_fd, state = open_state(state_path)
    try:
        state["calls"] = state.get("calls", 0) + 1
        call_number = state["calls"]
        seen = state["seen"]

        blocks = []
        total_chars = 0
        for scope_dir, label, name, glob, fields in candidates:
            result = read_rule_file(scope_dir, name)
            if result is None:
                continue
            body, was_truncated = result[1], result[2]
            if not body:
                continue
            # The content hash is part of the key so an edited rule counts as a
            # new rule and is injected again, rather than being treated as
            # already delivered for the rest of the session.
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
            key = f"{os.path.realpath(scope_dir)}::{name}::{digest}"
            last_seen = seen.get(key)

            if last_seen is None:
                text, truncated = body, was_truncated
            elif is_due(last_seen, call_number, tokens,
                        remember_after_of(fields) or default_interval):
                # Repeating means sending the rule again, whole: with no header
                # there is no way to mark a fragment as one. Short rules are
                # what keeps this cheap.
                text, truncated = body, was_truncated
            else:
                continue

            if total_chars + len(text) > MAX_TOTAL_CHARS:
                warn(f"injection budget of {MAX_TOTAL_CHARS} chars reached; "
                     f"rule '{name}' left for the next tool call")
                continue
            total_chars += len(text)
            blocks.append({"name": name, "text": text, "truncated": truncated})
            seen[key] = [call_number, tokens]

        # The legacy notice is told once per scope per session. Repeating it on
        # every tool call would be noise the user cannot silence except by
        # migrating, which is exactly what they may not be ready to do yet.
        for label in legacy_scopes:
            key = f"legacy::{label}"
            if key in seen:
                continue
            blocks.append({"name": "legacy-format", "text": LEGACY_NOTICE})
            seen[key] = [call_number, tokens]

        if blocks:
            # Emit the injection and flush it BEFORE recording the rules as
            # seen: if the process dies in the window, the worst case is
            # re-injecting a rule (a harmless duplicate) rather than marking it
            # delivered when the model never received it. The design prefers a
            # rare double injection to loss.
            payload_out = json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": build_context(blocks),
                },
                "suppressOutput": True,
            })
            sys.stdout.write(payload_out)
            sys.stdout.flush()
        save_state(state_fd, state)  # advances the call counter either way
    finally:
        close_state(state_fd)
    # Best-effort maintenance, kept off the critical path: it runs after the
    # payload is delivered so a slow directory sweep can never delay or drop it.
    # It must be reached on the far more common no-injection path too — an
    # early `return` there meant the sweep only ever ran as a side effect of a
    # successful injection, so sessions that never matched a rule left their
    # state files behind forever.
    cleanup_stale_state()


def session_notice():
    """SessionStart: say up front that the rules directory is the plugin's
    business, not the agent's.

    Without this the agent meets the directory the only way it can — by listing,
    reading or grepping it — and collects a permission denial for every attempt,
    in every session, because the recommended hardening deny-lists exactly those
    paths. A denial explains nothing, so the attempt repeats in the next session.
    Saying it once, before anything is tried, costs about eighty tokens and only
    in sessions that actually have a scope; the denials cost more than that and
    teach nothing."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    if not find_scopes(os.path.abspath(cwd)):
        return  # no rules anywhere near this session: say nothing at all
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": SESSION_NOTICE,
        },
    }))


def reset_session():
    """SessionStart (source compact|clear) mode: drop the session's state so
    rules are re-injected on the next touch — compaction may have summarized
    the injected text away, and /clear discards it entirely."""
    payload = json.load(sys.stdin)
    state_path = state_file_for(payload.get("session_id"))
    if state_path is None:
        return
    try:
        os.unlink(state_path)
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    try:
        if "--reset-session" in sys.argv[1:]:
            reset_session()
        elif "--session-notice" in sys.argv[1:]:
            session_notice()
        else:
            main()
    except Exception as exc:  # never break the tool call because of this hook
        warn(f"unexpected error: {exc}")
    sys.exit(0)
