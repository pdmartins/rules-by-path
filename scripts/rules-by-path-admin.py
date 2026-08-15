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
import sys
import tempfile

RULES_DIR_RELPATH = os.path.join(".claude", "rules-by-path")
LEGACY_MAP_NAME = "rules-map.yml"
LEGACY_RULES_SUBDIR = "rules"
HOOK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "hooks", "rules-by-path.py")


def fail(message):
    print(f"rules-by-path-admin: {message}", file=sys.stderr)
    sys.exit(1)


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


HOOK = load_hook_module()


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


OWN_KEYS = {"glob", "globs", "reinforce", "description"}


def check_glob(glob):
    """A glob is written verbatim into frontmatter, so it must not be able to
    add lines to it."""
    if not glob or any(ch in glob for ch in "\r\n") or not glob.isprintable():
        fail(f"invalid glob (one printable line, no control characters): {glob[:60]!r}")
    if len(glob) > HOOK.MAX_GLOB_CHARS:
        fail(f"glob longer than {HOOK.MAX_GLOB_CHARS} characters")
    return glob


def split_submitted(text):
    """(body, fields) for content arriving on stdin.

    `show` prints the whole file, and the skill documents show -> edit ->
    update as the way to change a rule, so stdin routinely arrives WITH the
    frontmatter still attached. Treating it as body text nests one frontmatter
    inside another and the rule stops matching. The block is only consumed when
    it parses to this plugin's own keys, so a rule whose body legitimately
    starts with `---` is left alone."""
    fields, body = HOOK.parse_frontmatter(text)
    if fields and set(fields) <= OWN_KEYS:
        return body.strip(), fields
    return text.strip(), {}


def render_rule(globs, body, reinforce=None, extra=None):
    lines = ["---"]
    if len(globs) == 1:
        lines.append(f"glob: {check_glob(globs[0])}")
    else:
        lines.append("glob:")
        lines.extend(f"  - {check_glob(glob)}" for glob in globs)
    if reinforce:
        lines.append(f"reinforce: {reinforce}")
    for key, value in (extra or {}).items():
        if key in ("glob", "globs", "reinforce") or isinstance(value, list):
            continue
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body.strip() + "\n"


def rule_path(scope_dir, name):
    if not HOOK.is_valid_rule_name(name):
        fail(f"invalid rule name (plain, short '*.md' file names only): {name[:80]!r}")
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
    with open(path, encoding="utf-8") as handle:
        sys.stdout.write(handle.read())


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
    """Remove a trailing YAML comment, honouring quoted spans. Cutting at the
    first '#' corrupts a glob that legitimately contains one — the exact bug
    the old parser shipped, reintroduced here if this is naive."""
    quote = None
    skip = False
    for index, char in enumerate(line):
        if skip:
            skip = False
            continue
        if quote:
            if char == "\\" and quote == '"':
                skip = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            return line[:index]
    return line


def read_legacy_rule(legacy_dir, name):
    """Read one legacy rule file without following a symlink, bounded. The
    plain open() this replaced was both a TOCTOU window and an unbounded read."""
    import stat as stat_module
    path = os.path.join(legacy_dir, name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat_module.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, encoding="utf-8", errors="replace") as handle:
            fd = None
            return handle.read(HOOK.MAX_RULE_CHARS + 1).strip()
    except Exception:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def read_legacy_map(map_path):
    """[(glob, rule_name)] from a legacy map. Tolerant by design: this runs
    once, and refusing to migrate a slightly odd map helps nobody."""
    try:
        with open(map_path, encoding="utf-8") as handle:
            lines = handle.read().split("\n")
    except OSError as exc:
        fail(f"cannot read {map_path}: {exc}")
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

    written, skipped = [], []
    for name, globs in by_name.items():
        if not HOOK.is_valid_rule_name(name):
            skipped.append(f"{name[:60]!r}: not a usable rule file name")
            continue
        body = read_legacy_rule(legacy_dir, name)
        if body is None:
            skipped.append(f"{name}: rule file missing or unreadable in "
                           f"{LEGACY_RULES_SUBDIR}/")
            continue
        if not body:
            skipped.append(f"{name}: empty")
            continue
        target = os.path.join(scope_dir, name)
        if os.path.exists(target) and not args.force:
            skipped.append(f"{name}: already exists in the new format (use --force)")
            continue
        atomic_write(target, render_rule(globs, body))
        written.append(f"{name}  <-  {', '.join(globs)}")

    for line in written:
        print(f"ok: {line}")
    for line in skipped:
        warn(f"skipped {line}")
    if not written:
        fail("nothing was migrated; the legacy files were left untouched")
    if skipped and not args.force:
        warn("legacy files kept because some entries were skipped; "
             "re-run with --force once you have reviewed them")
        return
    os.unlink(map_path)
    for name in by_name:
        stale = os.path.join(legacy_dir, name)
        if os.path.isfile(stale) and not os.path.islink(stale):
            os.unlink(stale)
    try:
        os.rmdir(legacy_dir)
    except OSError:
        warn(f"{legacy_dir} is not empty; left in place for you to review")
    print(f"migrated {len(written)} rule(s); the legacy map was removed")


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
    main()
