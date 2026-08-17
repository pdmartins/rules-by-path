#!/usr/bin/env python3
"""rules-by-path-admin — management CLI for the rules-by-path plugin.

A rule is one markdown file in `.claude/rules-by-path/` that declares its own
glob in frontmatter. There is no index file to keep in sync, so there is
nothing this tool can corrupt on your behalf.

The recommended hardening deny-lists the rules directory for Claude's file
tools; this script is the sanctioned management channel, taking `--root` or
`--global` plus the rule content on stdin and doing all file I/O internally.
Used by the `rules-by-path:manage` skill.

Subcommands:
  init                        create the scope directory
  list                        one line per rule: file <- globs
  show   --rule N             print a rule's full content
  which  --path P             which rules cover a path (the hook's own matching)
  add    --glob G [--glob G]  create a rule; markdown body read from stdin
         [--rule N] [--force] [--reinforce N|never]
  update --rule N             replace a rule's body (stdin), keeping its globs
  remove --rule N | --glob G  delete a rule file
  validate                    check every rule: frontmatter, globs, size, safety
  migrate                     convert a legacy rules-map.yml scope to this format

Scope: --root <project-root> (project) or --global (~/.claude).
"""

import argparse
import os
import stat
import sys
import tempfile

RULES_DIR_RELPATH = os.path.join(".claude", "rules-by-path")
LEGACY_MAP_NAME = "rules-map.yml"
LEGACY_RULES_SUBDIR = "rules"
# A legacy map is a list of globs, never a document. The bound matters because
# the file is repository data and used to be read whole.
MAX_LEGACY_MAP_BYTES = 256 * 1024
MAX_ECHOED_NAME_CHARS = 60
HOOK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "hooks", "rules-by-path.py")


class AdminError(Exception):
    """A user-facing failure. `main` prints it and exits 1.

    An exception rather than an immediate `sys.exit` so a caller that is in the
    middle of a multi-entry operation can catch one bad entry, record it and
    carry on: `migrate` used to die inside its write loop, leaving a scope half
    converted and reporting none of the files it had already created."""


def fail(message):
    raise AdminError(message)


def warn(message):
    print(f"rules-by-path-admin: {message}", file=sys.stderr)


def load_hook_module():
    """Import the plugin's hook so glob matching, frontmatter parsing, name
    derivation and containment are the exact code the injection uses — a second
    implementation here is how the two drift apart and a guard goes missing."""
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


def rules_in(scope_dir):
    """[(name, fields, body)] for every rule in a scope, sorted by name."""
    rules = []
    for name, _fields in HOOK.scope_index(scope_dir):
        result = HOOK.read_rule_file(scope_dir, name)
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
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(HOOK.MAX_FRONTMATTER_BYTES + 1)
    except OSError:
        return False
    fields, _ = HOOK.parse_frontmatter(text)
    return not fields


OWN_KEYS = {"glob", "globs", "reinforce", "description"}


def check_glob(glob):
    """A glob is written verbatim into frontmatter, so it must not be able to
    add lines to it."""
    if not glob or any(ch in glob for ch in "\r\n") or not glob.isprintable():
        fail(f"invalid glob (one printable line, no control characters): {glob[:60]!r}")
    if len(glob) > HOOK.MAX_GLOB_CHARS:
        fail(f"glob longer than {HOOK.MAX_GLOB_CHARS} characters")
    return glob


def check_line_value(label, value):
    """A frontmatter value is written verbatim on its own line, so — exactly like
    a glob — it must not smuggle a newline. Without this, `--reinforce
    'never\\nglob: **'` would inject a second `glob:` line and silently widen the
    rule's scope past what the command declared and reported."""
    text = str(value)
    if any(ch in text for ch in "\r\n") or not text.isprintable():
        fail(f"invalid {label} value (one printable line, no control characters): "
             f"{text[:60]!r}")
    return text


def split_submitted(text):
    """(body, fields) for content arriving on stdin.

    `show` prints the whole file, and the skill documents show -> edit ->
    update as the way to change a rule, so stdin routinely arrives WITH the
    frontmatter still attached. Treating it as body text nests one frontmatter
    inside another and the rule stops matching. The block is consumed when it
    declares a `glob`/`globs` key — the unmistakable signature of this plugin's
    own frontmatter — so a rule carrying an extra key the admin preserves (e.g.
    `owner:`) round-trips cleanly, while a body that legitimately starts with
    `---` (which has no glob key) is left alone."""
    fields, body = HOOK.parse_frontmatter(text)
    if fields and ("glob" in fields or "globs" in fields):
        return body.strip(), fields
    return text.strip(), {}


def render_rule(globs, body, reinforce=None, extra=None):
    # The hook ignores globs past MAX_GLOBS_PER_RULE and reads only
    # MAX_FRONTMATTER_BYTES to find the closing `---`, so a rule this tool writes
    # beyond either limit would be one the hook silently never injects. Refuse to
    # write it here instead, so what `add`/`update` confirm is what actually runs.
    if len(globs) > HOOK.MAX_GLOBS_PER_RULE:
        fail(f"a rule may declare at most {HOOK.MAX_GLOBS_PER_RULE} globs "
             f"(got {len(globs)}); split it into separate rules")
    lines = ["---"]
    if len(globs) == 1:
        lines.append(f"glob: {check_glob(globs[0])}")
    else:
        lines.append("glob:")
        lines.extend(f"  - {check_glob(glob)}" for glob in globs)
    if reinforce:
        lines.append(f"reinforce: {check_line_value('reinforce', reinforce)}")
    for key, value in (extra or {}).items():
        if key in ("glob", "globs", "reinforce") or isinstance(value, list):
            continue
        lines.append(f"{key}: {check_line_value(key, value)}")
    lines.append("---")
    lines.append("")
    frontmatter = "\n".join(lines)
    if len(frontmatter.encode("utf-8")) > HOOK.MAX_FRONTMATTER_BYTES:
        fail(f"frontmatter is {len(frontmatter.encode('utf-8'))} bytes, over the "
             f"{HOOK.MAX_FRONTMATTER_BYTES}-byte window the hook reads to find the "
             f"closing '---'; use fewer or shorter globs, or a shorter description")
    return frontmatter + body.strip() + "\n"


def rule_path(scope_dir, name):
    if not HOOK.is_valid_rule_name(name):
        fail(f"invalid rule name: a rule file name may hold only letters, digits "
             f"and '{HOOK.RULE_NAME_EXTRA_CHARS}', and must end in '.md' "
             f"(got {name[:80]!r})")
    return os.path.join(scope_dir, name)


def cmd_init(args):
    scope_dir, _ = scope_for(args)
    os.makedirs(scope_dir, exist_ok=True)
    print(f"ok: scope ready at {scope_dir}")


def cmd_list(args):
    scope_dir, _ = scope_for(args)
    if not os.path.isdir(scope_dir):
        print("(no rules in this scope)")
        return
    rules = rules_in(scope_dir)
    if not rules:
        print("(no rules in this scope)")
    for name, fields, _body in rules:
        globs = HOOK.globs_of(fields)
        shown = ", ".join(globs) if globs else "(NO GLOB — never injected)"
        print(f"{name}  <-  {shown}")
    others = other_markdown_in(scope_dir)
    if others:
        print(f"\n(not rules, no frontmatter: {', '.join(others)})")
    if HOOK.has_legacy_map(scope_dir):
        print("\nWARNING: a legacy rules-map.yml is present and is NOT being used. "
              "Run `migrate` to convert it.")


def cmd_show(args):
    scope_dir, _ = scope_for(args)
    path = rule_path(scope_dir, args.rule)
    if os.path.islink(path) or not os.path.isfile(path):
        fail(f"no such rule in this scope: {args.rule}")
    # Read the file whole: `show` feeds the show -> edit -> update round trip,
    # so truncating here would silently destroy the tail of a long rule.
    # `errors="replace"` matches every other reader in the plugin: under the
    # recommended hardening this is the ONLY way to read a rule, so a rule
    # hand-saved in cp1252 must not turn the sanctioned read path into a
    # traceback while the hook, `list` and `validate` all read it happily.
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    if "�" in text:
        warn(f"{args.rule} is not valid UTF-8; undecodable bytes are shown as "
             f"U+FFFD. An `update` will rewrite the file as UTF-8")
    sys.stdout.write(text)


def cmd_which(args):
    scope_dir, anchor = scope_for(args)
    if args.use_global:
        abs_path = os.path.abspath(args.path)
        rel_path = None
        shown = abs_path
    else:
        abs_path = args.path if os.path.isabs(args.path) else os.path.join(anchor, args.path)
        abs_path = os.path.normpath(abs_path)
        rel_path = os.path.relpath(abs_path, anchor).replace(os.sep, "/")
        if rel_path.startswith(".."):
            fail(f"path outside the root {anchor}: {abs_path}")
        shown = rel_path
    abs_posix = abs_path.replace(os.sep, "/")

    # A folder query must also find globs like 'docs/**', which only match
    # paths INSIDE the folder — probe with a synthetic child.
    looks_like_a_file = "." in os.path.basename(abs_path.rstrip("/"))
    is_dir_query = (os.path.isdir(abs_path) or args.path.endswith("/")
                    or (not os.path.exists(abs_path) and not looks_like_a_file))
    targets = [(rel_path, abs_posix)]
    if is_dir_query:
        targets.append((None if rel_path is None else f"{rel_path.rstrip('/')}/__probe__",
                        f"{abs_posix.rstrip('/')}/__probe__"))

    matches = []
    if os.path.isdir(scope_dir):
        for name, fields, _body in rules_in(scope_dir):
            for glob in HOOK.globs_of(fields):
                if any(HOOK.glob_matches(glob, r, a) for r, a in targets):
                    matches.append(name)
                    break
    for name in matches:
        print(f"match: rule {name}")
    if matches:
        return

    scope_flag = "--global" if args.use_global else "--root <root>"
    if os.path.isdir(abs_path) or args.path.endswith("/"):
        suggestions = [f"{shown.rstrip('/')}/**"]
    elif not os.path.exists(abs_path) and not looks_like_a_file:
        # Could be a folder or an extension-less file; suggesting only the
        # folder form yields a rule that never fires for the file case.
        suggestions = [f"{shown.rstrip('/')}/**", shown]
    else:
        parent = os.path.dirname(shown).replace(os.sep, "/")
        suggestions = [f"{parent}/**" if parent and parent != "/" else shown]
    print(f"no rule covers '{shown}' — to create one:")
    for suggestion in suggestions:
        print(f"  add {scope_flag} --glob '{suggestion}'")


def warn_if_long(name, body):
    if len(body) > HOOK.RULE_WARN_CHARS:
        warn(f"{name} is {len(body)} chars; a rule should state constraints, not "
             f"document behaviour (soft limit {HOOK.RULE_WARN_CHARS}, hard "
             f"truncation at {HOOK.MAX_RULE_CHARS})")


def cmd_add(args):
    scope_dir, _ = scope_for(args)
    body, submitted = split_submitted(sys.stdin.read())
    if not body:
        fail("empty rule content — send the markdown via stdin")
    globs = [g.strip() for g in args.glob if g.strip()] or HOOK.globs_of(submitted)
    if not globs:
        fail("'add' requires at least one --glob")
    name = args.rule or HOOK.derive_rule_name(globs[0])
    if not HOOK.is_valid_rule_name(name):
        source = "invalid rule name" if args.rule else \
            f"the name derived from {globs[0]!r} is not usable"
        fail(f"{source}: {name[:60]!r} — pass a plain one, e.g. --rule csharp.md")
    path = rule_path(scope_dir, name)
    if existing_is_not_a_rule(path):
        fail(f"{name} already exists and is NOT a rule (no frontmatter); refusing "
             f"to overwrite a plain markdown file, even with --force. Remove it "
             f"first, or pass a different --rule")
    if os.path.exists(path) and not args.force:
        fail(f"{name} already exists in this scope; use --force to overwrite, "
             f"`update --rule {name}` to replace its body, or pass another --rule")
    os.makedirs(scope_dir, exist_ok=True)
    reinforce = args.reinforce or submitted.get("reinforce") or None
    if isinstance(reinforce, list):
        reinforce = reinforce[0] if reinforce else None
    atomic_write(path, render_rule(globs, body, reinforce,
                                   {k: v for k, v in submitted.items()
                                    if k not in OWN_KEYS or k == "description"}))
    print(f"ok: {name}  <-  {', '.join(globs)}")
    warn_if_long(name, body)
    validate_scope(scope_dir, quiet=True)


def cmd_update(args):
    scope_dir, _ = scope_for(args)
    body, submitted = split_submitted(sys.stdin.read())
    if not body:
        fail("empty rule content — send the markdown via stdin")
    path = rule_path(scope_dir, args.rule)
    if os.path.islink(path) or not os.path.isfile(path):
        fail(f"no such rule in this scope: {args.rule}")
    result = HOOK.read_rule_file(scope_dir, args.rule)
    if result is None:
        fail(f"cannot read {args.rule}")
    fields = result[0]
    if not fields:
        fail(f"{args.rule} is not a rule (no frontmatter); `update` replaces a "
             f"rule's body. Use `add` to create a rule, choosing a name that does "
             f"not collide with an existing plain markdown file")
    # Precedence: explicit CLI flag, then what was submitted on stdin, then
    # what the rule already had — so a show -> edit -> update round trip keeps
    # everything the user did not deliberately change.
    globs = ([g.strip() for g in args.glob if g.strip()]
             or HOOK.globs_of(submitted) or HOOK.globs_of(fields))
    if not globs:
        fail(f"{args.rule} declares no glob; pass --glob to set one")
    reinforce = (args.reinforce or submitted.get("reinforce")
                 or fields.get("reinforce") or None)
    if isinstance(reinforce, list):
        reinforce = reinforce[0] if reinforce else None
    extra = {k: v for k, v in {**fields, **submitted}.items() if k not in OWN_KEYS}
    if "description" in {**fields, **submitted}:
        extra["description"] = {**fields, **submitted}["description"]
    atomic_write(path, render_rule(globs, body, reinforce, extra))
    print(f"ok: updated {args.rule}")
    warn_if_long(args.rule, body)
    validate_scope(scope_dir, quiet=True)


def cmd_remove(args):
    scope_dir, _ = scope_for(args)
    name = args.rule
    if not name:
        matches = [n for n, fields, _ in rules_in(scope_dir)
                   if args.glob in HOOK.globs_of(fields)]
        if not matches:
            fail(f"no rule declares the glob {args.glob!r}")
        if len(matches) > 1:
            fail(f"{len(matches)} rules declare that glob ({', '.join(matches)}); "
                 f"pick one with --rule")
        name = matches[0]
    path = rule_path(scope_dir, name)
    if os.path.islink(path):
        fail(f"{name} is a symlink; refusing to delete through it")
    if not os.path.isfile(path):
        fail(f"no such rule in this scope: {name}")
    os.unlink(path)
    print(f"ok: removed {name}")


def validate_scope(scope_dir, quiet=False):
    """Print notes and errors; return the number of errors. Notes are advice
    (a long rule, a shared glob); errors mean something will not work."""
    if not os.path.isdir(scope_dir):
        if not quiet:
            print("(no rules in this scope — nothing to validate)")
        return 0
    problems = []
    notes = []
    if HOOK.has_legacy_map(scope_dir):
        problems.append(f"a legacy {LEGACY_MAP_NAME} is present and is NOT used; "
                        f"run `migrate` to convert it")
    rules = rules_in(scope_dir)
    by_glob = {}
    total = 0
    for name, fields, body in rules:
        globs = HOOK.globs_of(fields)
        if not globs:
            problems.append(f"{name}: no glob declared, so it can never be injected")
        for glob in globs:
            by_glob.setdefault(glob, []).append(name)
        if not body:
            problems.append(f"{name}: empty body")
        total += len(body)
        if len(body) > HOOK.RULE_WARN_CHARS:
            notes.append(f"{name}: {len(body)} chars — a rule should state "
                         f"constraints, not document behaviour (soft limit "
                         f"{HOOK.RULE_WARN_CHARS}, truncated at {HOOK.MAX_RULE_CHARS})")
        unknown = set(fields) - {"glob", "globs", "reinforce", "description"}
        if unknown:
            notes.append(f"{name}: unknown frontmatter key(s): "
                         f"{', '.join(sorted(unknown))}")
    others = other_markdown_in(scope_dir)
    if others:
        notes.append(f"ignored (no frontmatter, so not rules): {', '.join(others)}")
    for glob, names in sorted(by_glob.items()):
        if len(names) > 1:
            notes.append(f"{len(names)} rules share the glob {glob!r} "
                         f"({', '.join(names)}) — they all inject together")
    if total > HOOK.MAX_TOTAL_CHARS:
        notes.append(f"rules total {total} chars; one injection is capped at "
                     f"{HOOK.MAX_TOTAL_CHARS}, so a file matching many of them "
                     f"gets the rest on later tool calls")
    for note in notes:
        print(f"note: {note}")
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    if not quiet and not problems:
        print(f"validation ok: {len(rules)} rule(s)")
    return len(problems)


def cmd_validate(args):
    scope_dir, _ = scope_for(args)
    if validate_scope(scope_dir):
        sys.exit(1)


def strip_yaml_comment(line):
    """Remove a trailing YAML comment, honouring quoted spans and YAML's own
    comment rule: a '#' only starts a comment when it follows whitespace (or the
    line start). Two ways a naive version corrupts a migrated glob, both real:
    treating a mid-value '#' as a comment (`build/#tmp/**` -> `build/`), and
    letting an apostrophe inside an unquoted value (`a'b/**  # c`) open a quote
    span that then swallows the genuine trailing comment. A quote is therefore
    only taken as opening a scalar when it too follows whitespace or the ':'."""
    quote = None
    skip = False
    prev = " "  # the line start counts as whitespace for both rules below
    for index, char in enumerate(line):
        if skip:
            skip = False
            prev = char
            continue
        if quote:
            if char == "\\" and quote == '"':
                skip = True
            elif char == quote:
                quote = None
        elif char in "\"'" and prev in " \t:":
            quote = char
        elif char == "#" and prev in " \t":
            return line[:index]
        prev = char
    return line


def read_legacy_rule(legacy_dir, name):
    """Read one legacy rule file without following a symlink, bounded. The
    plain open() this replaced was both a TOCTOU window and an unbounded read.

    Returns (body, over_limit) or None. The overflow is decided on the raw read,
    BEFORE stripping: a rule whose 4001st character is whitespace strips back to
    exactly the limit and would look like a rule that fits. migrate deletes the
    original, so a body silently cut here is a body lost."""
    path = os.path.join(legacy_dir, name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, encoding="utf-8", errors="replace") as handle:
            fd = None
            raw = handle.read(HOOK.MAX_RULE_CHARS + 1)
        return raw.strip(), len(raw) > HOOK.MAX_RULE_CHARS
    except Exception:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def read_legacy_map(map_path):
    """[(glob, rule_name)] from a legacy map. Tolerant of odd formatting by
    design — this runs once, and refusing to migrate a slightly odd map helps
    nobody — but not of an odd *file*.

    Opened exactly like a legacy rule: O_NOFOLLOW, regular file only, bounded.
    The plain open() this replaced followed symlinks, so a cloned repository
    could point rules-map.yml at any file the user can read and have its lines
    come back out through the `skipped <name>` messages — while the hook's own
    legacy notice actively told the agent to run this command."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(map_path, flags)
    except OSError as exc:
        fail(f"cannot read {map_path}: {exc}")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            fd = None
            fail(f"{map_path} is not a regular file; refusing to read it")
        with os.fdopen(fd, encoding="utf-8", errors="replace") as handle:
            fd = None  # fdopen owns it now
            lines = handle.read(MAX_LEGACY_MAP_BYTES).split("\n")
    except AdminError:
        raise
    except Exception as exc:
        fail(f"cannot read {map_path}: {exc}")
    finally:
        if fd is not None:
            os.close(fd)
    entries = []
    pending = None
    for raw in lines:
        stripped = "" if raw.lstrip().startswith("#") else strip_yaml_comment(raw).strip()
        if not stripped or stripped.startswith("rules:"):
            continue
        if stripped.startswith("- glob:"):
            if pending:
                entries.append((pending, HOOK.derive_rule_name(pending)))
            pending = HOOK.unquote(stripped[len("- glob:"):])
        elif stripped.startswith("rule:") and pending:
            entries.append((pending, HOOK.unquote(stripped[len("rule:"):])))
            pending = None
        elif stripped.startswith("- "):
            if pending:
                entries.append((pending, HOOK.derive_rule_name(pending)))
                pending = None
            glob = HOOK.unquote(stripped[2:])
            entries.append((glob, HOOK.derive_rule_name(glob)))
    if pending:
        entries.append((pending, HOOK.derive_rule_name(pending)))
    return entries


def cmd_migrate(args):
    """Convert a legacy `rules-map.yml` + `rules/` scope into one file per rule.

    The old map is parsed here rather than in the hook: keeping a YAML parser
    alive in the injection path for a one-time job would be a permanent cost,
    and two parsers for one format is exactly what this format change removes."""
    scope_dir, _ = scope_for(args)
    map_path = os.path.join(scope_dir, LEGACY_MAP_NAME)
    legacy_dir = os.path.join(scope_dir, LEGACY_RULES_SUBDIR)
    if os.path.islink(map_path):
        fail(f"{map_path} is a symlink; refusing to read the legacy map through it")
    if not os.path.isfile(map_path):
        print("nothing to migrate: no legacy rules-map.yml in this scope")
        return
    # The legacy `rules/` directory is a level the rewrite removed from the
    # hook, so its containment check went with it — and this command brought
    # the directory back. Without this gate a cloned repo shipping
    # `rules -> ~/.claude` makes migrate read and then DELETE the user's files.
    if os.path.exists(legacy_dir):
        expected = os.path.join(os.path.realpath(scope_dir), LEGACY_RULES_SUBDIR)
        if os.path.islink(legacy_dir) or os.path.realpath(legacy_dir) != expected:
            fail(f"{legacy_dir} does not physically live inside {scope_dir} "
                 f"(symlink?); refusing to touch it")
        if not HOOK.is_safely_owned(legacy_dir):
            fail(f"{legacy_dir} is not safely owned; refusing to touch it")
    entries = read_legacy_map(map_path)
    if not entries:
        fail("the legacy map has no readable entries; migrate it by hand")

    by_name = {}
    for glob, name in entries:
        by_name.setdefault(name, [])
        if glob not in by_name[name]:
            by_name[name].append(glob)

    # Every entry is validated and rendered BEFORE anything is written. Rendering
    # can fail (too many globs merged onto one legacy rule file, a glob over the
    # length cap), and failing halfway through the write loop left the scope half
    # converted, reported none of the files already created, and could not be
    # resumed — every re-run died on the same entry.
    prepared, skipped = [], []
    for name, globs in by_name.items():
        short = name[:MAX_ECHOED_NAME_CHARS]
        if not HOOK.is_valid_rule_name(name):
            skipped.append(f"{short!r}: not a usable rule file name")
            continue
        target = os.path.join(scope_dir, name)
        # The same refusal `add` makes: a plain markdown file that merely shares
        # the name is the user's own content, and --force means "replace a rule",
        # never "replace my notes". The legacy map picks this name, so without
        # the guard a repository chooses which of your files gets overwritten.
        if existing_is_not_a_rule(target):
            skipped.append(f"{name}: a plain markdown file (not a rule) already has "
                           f"that name; rename it, or migrate this entry by hand")
            continue
        if os.path.exists(target) and not args.force:
            skipped.append(f"{name}: a rule with that name already exists in the new "
                           f"format (--force replaces it)")
            continue
        legacy = read_legacy_rule(legacy_dir, name)
        if legacy is None:
            skipped.append(f"{name}: rule file missing or unreadable in "
                           f"{LEGACY_RULES_SUBDIR}/")
            continue
        body, over_limit = legacy
        if not body:
            skipped.append(f"{name}: empty")
            continue
        if over_limit:
            skipped.append(f"{name}: longer than the {HOOK.MAX_RULE_CHARS}-char limit; "
                           f"shorten or split it and migrate this entry by hand "
                           f"(converting it would cut the text and then delete the "
                           f"original)")
            continue
        try:
            rendered = render_rule(globs, body)
        except AdminError as exc:
            skipped.append(f"{name}: {exc}")
            continue
        prepared.append((name, target, globs, body, rendered))

    written_names = []
    for name, target, globs, body, rendered in prepared:
        atomic_write(target, rendered)
        written_names.append(name)
        print(f"ok: {name}  <-  {', '.join(globs)}")  # printed as it happens
        warn_if_long(name, body)
    for line in skipped:
        warn(f"skipped {line}")
    if not written_names:
        fail("nothing was migrated; the legacy files were left untouched")
    if skipped and not args.force:
        warn("legacy files kept because some entries were skipped; resolve those, "
             "or re-run with --force to replace rules that already exist in the new "
             "format (--force never overwrites a file that is not a rule)")
        return
    os.unlink(map_path)
    # Only the legacy files we actually migrated may be removed, and each name in
    # written_names already passed is_valid_rule_name (no '/', no '..'), so
    # os.path.join stays inside legacy_dir. Iterating by_name instead would honor
    # an attacker-controlled `rule:` value like '../../victim' or an absolute
    # path — os.path.join would escape the scope and unlink an arbitrary file.
    for name in written_names:
        stale = os.path.join(legacy_dir, name)
        if os.path.isfile(stale) and not os.path.islink(stale):
            os.unlink(stale)
    try:
        os.rmdir(legacy_dir)
    except OSError:
        warn(f"{legacy_dir} is not empty; left in place for you to review")
    print(f"migrated {len(written_names)} rule(s); the legacy map was removed")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["init", "list", "show", "which", "add",
                                            "update", "remove", "validate", "migrate"])
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--root", help="project root (the folder containing .claude/)")
    scope.add_argument("--global", dest="use_global", action="store_true",
                       help="global scope (~/.claude/rules-by-path)")
    parser.add_argument("--glob", action="append", default=[],
                        help="glob the rule applies to; repeat for several")
    parser.add_argument("--rule", help="rule file name")
    parser.add_argument("--reinforce", help="reminder interval in tool calls, or 'never'")
    parser.add_argument("--force", action="store_true", help="overwrite an existing rule")
    parser.add_argument("--path", help="file/folder to resolve (which)")
    args = parser.parse_args()

    if args.command == "add" and not args.glob:
        fail("'add' requires --glob")
    if args.command in ("show", "update") and not args.rule:
        fail(f"'{args.command}' requires --rule")
    if args.command == "remove" and not (args.rule or args.glob):
        fail("'remove' requires --rule or --glob")
    if args.command == "remove" and args.rule and args.glob:
        fail("'remove' takes --rule OR --glob, not both")
    if args.command == "remove" and args.glob:
        args.glob = args.glob[0]
    if args.command == "which" and not args.path:
        fail("'which' requires --path")

    {"init": cmd_init, "list": cmd_list, "show": cmd_show, "which": cmd_which,
     "add": cmd_add, "update": cmd_update, "remove": cmd_remove,
     "validate": cmd_validate, "migrate": cmd_migrate}[args.command](args)


if __name__ == "__main__":
    # Every failure leaves as one line on stderr and exit 1 — including an
    # unexpected one. A traceback tells the model driving this CLI nothing it
    # can act on, and `show` used to emit one for a rule saved in cp1252.
    try:
        main()
    except AdminError as error:
        print(f"rules-by-path-admin: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:  # noqa: BLE001 - deliberate last resort
        print(f"rules-by-path-admin: unexpected error: {error!r}", file=sys.stderr)
        sys.exit(1)
