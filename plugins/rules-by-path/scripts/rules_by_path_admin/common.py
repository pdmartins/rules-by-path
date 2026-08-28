"""Shared plumbing for the rules-by-path admin CLI: the scope it operates on,
the vocabulary of a rule file, and the safe ways to read and write one.

Everything here is used by more than one command module. The hook itself is
imported once, as `HOOK`, so glob matching, frontmatter parsing, name derivation
and containment are the exact code the injection uses — a second implementation
here is how the two drift apart and a guard goes missing."""

import os
import stat
import sys
import tempfile


# How much of a name or value a refusal message echoes back.
MAX_ECHOED_NAME_CHARS = 60
# scripts/rules_by_path_admin/common.py -> the plugin root is three levels up.
HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "hooks", "rules-by-path.py")

# The frontmatter keys this tool owns. RENDERED_KEYS are the ones `render_rule`
# writes from its own arguments, so a copy arriving in a submitted rule is
# dropped rather than written a second time — `remember_after` is the name
# `remember_again_after` carried until 0.4.0, and keeping both would leave the
# setting alive under two names forever. `description` and `enforce` are owned
# too (so `validate` does not report them as unknown keys) but are carried
# through verbatim, like any key this tool knows nothing about.
INTERVAL_KEY = "remember_again_after"
LEGACY_INTERVAL_KEY = "remember_after"
DESCRIPTION_KEY = "description"
ENFORCE_KEY = "enforce"
RENDERED_KEYS = {"glob", "globs", INTERVAL_KEY, LEGACY_INTERVAL_KEY}
OWN_KEYS = RENDERED_KEYS | {DESCRIPTION_KEY, ENFORCE_KEY}


class AdminError(Exception):
    """A user-facing failure. `main` prints it and exits 1.

    An exception rather than an immediate `sys.exit` so a caller that is in the
    middle of a multi-entry operation can catch one bad entry, record it and
    carry on: `migrate` used to die inside its write loop, leaving a scope half
    converted and reporting none of the files it had already created."""


class NotARegularFile(OSError):
    """`read_regular_file` refused: a directory, a device, a fifo — anything a
    bounded read of repository data must not be pointed at."""


def fail(message):
    raise AdminError(message)


def warn(message):
    print(f"rules-by-path-admin: {message}", file=sys.stderr)


def load_hook_module():
    """Import the plugin's hook and return it as a module."""
    import importlib.machinery
    import importlib.util
    if not os.path.isfile(HOOK_PATH):
        fail(f"hook not found: {HOOK_PATH}")
    loader = importlib.machinery.SourceFileLoader("rules_by_path_hook", HOOK_PATH)
    spec = importlib.util.spec_from_loader("rules_by_path_hook", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


try:
    HOOK = load_hook_module()
except AdminError as exc:  # import-time failure: main() is not running yet
    print(f"rules-by-path-admin: {exc}", file=sys.stderr)
    sys.exit(1)

# Taken from the hook rather than re-declared: these two name the directory this
# tool writes into and the legacy file it migrates away from, and a private copy
# that drifts means the admin writes rules the hook never reads.
RULES_DIR_RELPATH = HOOK.RULES_DIR_RELPATH
LEGACY_MAP_NAME = HOOK.LEGACY_MAP_NAME


def scope_for(args):
    """(scope_dir, anchor). The scope must physically live inside the root the
    caller named: without that check, a cloned repo shipping `.claude` or
    `.claude/rules-by-path` as a symlink redirects every read, write and delete
    — a project-scoped add would land in the user's global rules."""
    if args.use_global:
        anchor = os.path.expanduser("~")
    else:
        anchor = os.path.abspath(args.root)
        if not os.path.isdir(anchor):
            fail(f"project root does not exist: {anchor}")
    scope_dir = os.path.join(anchor, RULES_DIR_RELPATH)
    # Physical containment is required for a project scope, where a symlinked
    # `.claude` can arrive in a cloned repo. The global scope is the user's own
    # configuration — symlinking `~/.claude` to shared or versioned config is a
    # normal choice, and nobody can plant that link without owning the home dir.
    if not args.use_global and not HOOK.scope_is_contained(anchor, scope_dir):
        fail(f"{scope_dir} does not physically live inside {anchor} (symlink?); "
             f"refusing to touch it")
    if os.path.isdir(scope_dir) and not HOOK.is_safely_owned(os.path.realpath(scope_dir)):
        fail(f"{scope_dir} is not safely owned (world-writable or another user's); "
             f"refusing to touch it")
    return scope_dir, anchor


def read_regular_file(path, limit):
    """The first `limit` characters of `path`, read without following a symlink
    and without trusting what is at the other end.

    O_NOFOLLOW plus the S_ISREG check is what stops a planted link or a fifo
    from turning a bounded read of repository data into a read of the user's own
    files, or into a hang. Every refusal leaves as an OSError, so each caller
    decides whether it is fatal."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotARegularFile(f"{path} is not a regular file")
        with os.fdopen(fd, encoding="utf-8", errors="replace") as handle:
            fd = None  # fdopen owns it now
            return handle.read(limit)
    finally:
        if fd is not None:
            os.close(fd)


def atomic_write(path, text):
    """Replace `path` atomically, without ever writing through a symlink.

    The temp file is created by mkstemp (random name, O_EXCL, mode 0600) in the
    destination directory: a predictable `path + '.tmp'` is a symlink target an
    attacker can plant in advance, which turns any write into an arbitrary file
    overwrite. `os.replace` then swaps the inode rather than following a link."""
    if os.path.islink(path):
        fail(f"{path} is a symlink; refusing to write through it")
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".rbp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def other_markdown_in(scope_dir):
    """Markdown files in the scope that are not rules (no frontmatter)."""
    rule_names = {name for name, _ in HOOK.scope_index(scope_dir)}
    try:
        with os.scandir(scope_dir) as it:
            return sorted(e.name for e in it
                          if e.name.endswith(".md") and e.name not in rule_names
                          and e.is_file(follow_symlinks=False))
    except OSError:
        return []


def rules_in(scope_dir, body_limit=None):
    """[(name, fields, body)] for every rule in a scope, sorted by name.

    `body_limit` defaults to the plugin's built-in maximum; callers that have a
    config in hand pass the configured one, so what this reports is what the
    hook would actually inject."""
    rules = []
    for name, _fields in HOOK.scope_index(scope_dir):
        result = HOOK.read_rule_file(scope_dir, name,
                                     body_limit or HOOK.MAX_RULE_CHARS)
        if result is not None:
            rules.append((name, result[0], result[1]))
    return rules


def existing_is_not_a_rule(path):
    """True when a regular file is already at `path` but carries no frontmatter,
    i.e. a plain markdown file the user keeps in the scope (a README, notes) that
    happens to collide with a rule name. Overwriting it would silently destroy
    the user's own content, and `add`'s 'use --force' message misframes it as a
    stale rule — so the callers refuse rather than clobber it."""
    if not os.path.isfile(path) or os.path.islink(path):
        return False
    try:
        text = read_regular_file(path, HOOK.MAX_FRONTMATTER_BYTES + 1)
    except OSError:
        return False
    fields, _ = HOOK.parse_frontmatter(text)
    return not fields


def check_glob(glob):
    """A glob is written verbatim into frontmatter, so it must not be able to
    add lines to it."""
    if not glob or any(ch in glob for ch in "\r\n") or not glob.isprintable():
        fail(f"invalid glob (one printable line, no control characters): "
             f"{glob[:MAX_ECHOED_NAME_CHARS]!r}")
    if len(glob) > HOOK.MAX_GLOB_CHARS:
        fail(f"glob longer than {HOOK.MAX_GLOB_CHARS} characters")
    return glob


def check_line_value(label, value):
    """A frontmatter value is written verbatim on its own line, so — exactly like
    a glob — it must not smuggle a newline. Without this,
    `--remember-again-after 'never\\nglob: **'` would inject a second `glob:`
    line and silently widen the rule's scope past what the command declared and
    reported."""
    text = str(value)
    if any(ch in text for ch in "\r\n") or not text.isprintable():
        fail(f"invalid {label} value (one printable line, no control characters): "
             f"{text[:MAX_ECHOED_NAME_CHARS]!r}")
    return text


def preserved_fields(fields, owned_last=False):
    """The frontmatter `render_rule` must carry through unchanged: everything it
    does not write from its own arguments. That includes `description` and
    `enforce`, which this tool knows about but never derives — a show -> edit ->
    update round trip has to return them exactly as they arrived.

    `owned_last` writes those two after the keys this tool knows nothing about,
    which is the order `update` and `migrate` have always produced; `add` keeps
    the order the author submitted. Same keys either way, so the difference is
    only where they land in the frontmatter of a rewritten file."""
    if not owned_last:
        return {key: value for key, value in fields.items()
                if key not in RENDERED_KEYS}
    extra = {key: value for key, value in fields.items() if key not in OWN_KEYS}
    for key in (DESCRIPTION_KEY, ENFORCE_KEY):
        if key in fields:
            extra[key] = fields[key]
    return extra


def rule_path(scope_dir, name):
    if not HOOK.is_valid_rule_name(name):
        fail(f"invalid rule name: a rule file name may hold only letters, digits "
             f"and '{HOOK.RULE_NAME_EXTRA_CHARS}', and must end in '.md' "
             f"(got {name[:80]!r})")
    return os.path.join(scope_dir, name)


def existing_rule_path(scope_dir, name):
    """The path of a rule that must already be there. A symlink counts as absent:
    a rule file is content this tool owns, and following a link out of the scope
    would let a cloned repository choose which file gets shown or rewritten."""
    path = rule_path(scope_dir, name)
    if os.path.islink(path) or not os.path.isfile(path):
        fail(f"no such rule in this scope: {name}")
    return path


def warn_if_long(name, body, config=None):
    soft, hard = HOOK.warn_rule_chars(config), HOOK.max_rule_chars(config)
    if len(body) > soft:
        warn(f"{name} is {len(body)} chars; a rule should state constraints, not "
             f"document behaviour (soft limit {soft}, hard truncation at {hard}). "
             f"A repeat resends the whole body, so length is paid again every "
             f"time the rule is refreshed")
