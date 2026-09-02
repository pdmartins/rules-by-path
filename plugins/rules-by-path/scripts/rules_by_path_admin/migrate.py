"""`migrate`: bring a scope up to the current format.

Three idempotent steps — rename pre-0.4.0 type prefixes, rewrite the frontmatter
key that was renamed with them, and convert a legacy `rules-map.yml` if one is
still there. The old map is parsed here rather than in the hook: keeping a YAML
parser alive in the injection path for a one-time job would be a permanent
cost."""

import os

from .common import (HOOK, INTERVAL_KEY, LEGACY_INTERVAL_KEY, LEGACY_MAP_NAME,
                     MAX_ECHOED_NAME_CHARS, AdminError, NotARegularFile,
                     atomic_write, existing_is_not_a_rule, fail,
                     preserved_fields, read_regular_file, rules_in, scope_for,
                     warn, warn_if_long)
from .config import (TYPE_SEPARATOR, config_for, describe_types,
                     split_type_prefix)
from .rules import render_rule, submitted_interval
from .validate import validate_scope

LEGACY_RULES_SUBDIR = "rules"
# A legacy map is a list of globs, never a document. The bound matters because
# the file is repository data and used to be read whole.
MAX_LEGACY_MAP_BYTES = 256 * 1024


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


def read_legacy_rule(legacy_dir, name, limit):
    """Read one legacy rule file without following a symlink, bounded. The
    plain open() this replaced was both a TOCTOU window and an unbounded read.

    Returns (body, over_limit) or None. The overflow is decided on the raw read,
    BEFORE stripping: a rule whose 4001st character is whitespace strips back to
    exactly the limit and would look like a rule that fits. migrate deletes the
    original, so a body silently cut here is a body lost."""
    try:
        raw = read_regular_file(os.path.join(legacy_dir, name), limit + 1)
    except OSError:
        return None  # missing, a symlink, not a regular file: `skipped` reports it
    return raw.strip(), len(raw) > limit


def read_legacy_map(map_path):
    """[(glob, rule_name)] from a legacy map. Tolerant of odd formatting by
    design — this runs once, and refusing to migrate a slightly odd map helps
    nobody — but not of an odd *file*.

    Opened exactly like a legacy rule: O_NOFOLLOW, regular file only, bounded.
    The plain open() this replaced followed symlinks, so a cloned repository
    could point rules-map.yml at any file the user can read and have its lines
    come back out through the `skipped <name>` messages — while the hook's own
    legacy notice actively told the agent to run this command."""
    try:
        lines = read_regular_file(map_path, MAX_LEGACY_MAP_BYTES).split("\n")
    except NotARegularFile:
        fail(f"{map_path} is not a regular file; refusing to read it")
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


def migrate_rule_names(scope_dir, config, force):
    """Rename rule files carrying a pre-0.4.0 type prefix (`Business_x.md` ->
    `BUSN_x.md`), using the mapping the config declares.

    A rule with NO type prefix is not renamed and not guessed at: choosing the
    type is a judgement about what violating the rule costs, and only the person
    who owns the code can make it. It is reported instead."""
    mapping = {old.lower(): new for old, new in
               (config.get("legacy_type_prefixes") or {}).items()}
    if not mapping:
        return 0
    renamed = 0
    for name, _fields, _body in rules_in(scope_dir):
        head, separator, rest = name.partition(TYPE_SEPARATOR)
        target_prefix = mapping.get(head.lower()) if separator and rest else None
        if not target_prefix:
            continue
        new_name = f"{target_prefix}{TYPE_SEPARATOR}{rest}"
        if new_name == name or not HOOK.is_valid_rule_name(new_name):
            continue
        source_path = os.path.join(scope_dir, name)
        target_path = os.path.join(scope_dir, new_name)
        if os.path.islink(source_path):
            warn(f"skipped {name}: it is a symlink; refusing to rename through it")
            continue
        if existing_is_not_a_rule(target_path):
            warn(f"skipped {name}: {new_name} already exists and is not a rule")
            continue
        if os.path.exists(target_path) and not force:
            warn(f"skipped {name}: {new_name} already exists (--force replaces it)")
            continue
        os.replace(source_path, target_path)
        renamed += 1
        print(f"ok: renamed {name} -> {new_name}")
    return renamed


def migrate_interval_key(scope_dir):
    """Rewrite `remember_after:` as `remember_again_after:`. The hook honours
    both, so this is tidying, not repair — but leaving the old spelling in place
    means every rule file disagrees with every document that describes it."""
    rewritten = 0
    for name, fields, body in rules_in(scope_dir):
        if LEGACY_INTERVAL_KEY not in fields or INTERVAL_KEY in fields:
            continue
        globs = HOOK.globs_of(fields)
        if not globs or not body:
            continue  # `validate` already reports these; rewriting would not help
        # Every setting `render_rule` writes from an argument has to be handed
        # back to it: the filters are in RENDERED_KEYS, so `preserved_fields`
        # drops them, and a rewrite that did not pass them would silently widen
        # the rule it was only supposed to retitle a key on.
        try:
            rendered = render_rule(globs, body, submitted_interval(fields),
                                   preserved_fields(fields, owned_last=True),
                                   excludes=HOOK.excludes_of(fields),
                                   tool=HOOK.tool_values_of(fields))
        except AdminError as exc:
            # A rule this tool would refuse to write today (a filter that
            # cancels its own glob, say) is one `validate` reports. Renaming a
            # key in it is not worth aborting the whole migration for.
            warn(f"skipped {name}: {exc}")
            continue
        atomic_write(os.path.join(scope_dir, name), rendered)
        rewritten += 1
        print(f"ok: {name}: {LEGACY_INTERVAL_KEY} -> {INTERVAL_KEY}")
    return rewritten


def report_untyped_rules(scope_dir, config):
    """Rules whose name carries no configured type. Said once, here, because
    `migrate` is when someone is looking at the scope as a whole."""
    untyped = [name for name, _f, _b in rules_in(scope_dir)
               if not split_type_prefix(name, config)[0]]
    if not untyped:
        return
    print(f"\nthese rules carry no type prefix: {', '.join(untyped)}")
    print("A type is not guessable from the text — it says what violating the "
          "rule costs. Ask the user which one each is, then rename with "
          "`remove` + `add --type`. Configured types:")
    print(describe_types(config))


def migrate_legacy_map(args, scope_dir, anchor, config):
    """Convert a legacy `rules-map.yml` + `rules/` scope into one file per rule.

    The old map is parsed here rather than in the hook: keeping a YAML parser
    alive in the injection path for a one-time job would be a permanent cost,
    and two parsers for one format is exactly what this format change removes."""
    map_path = os.path.join(scope_dir, LEGACY_MAP_NAME)
    legacy_dir = os.path.join(scope_dir, LEGACY_RULES_SUBDIR)
    if os.path.islink(map_path):
        fail(f"{map_path} is a symlink; refusing to read the legacy map through it")
    if not os.path.isfile(map_path):
        return 0
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
    body_limit = HOOK.max_rule_chars(config)
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
        legacy = read_legacy_rule(legacy_dir, name, body_limit)
        if legacy is None:
            skipped.append(f"{name}: rule file missing or unreadable in "
                           f"{LEGACY_RULES_SUBDIR}/")
            continue
        body, over_limit = legacy
        if not body:
            skipped.append(f"{name}: empty")
            continue
        if over_limit:
            skipped.append(f"{name}: longer than the "
                           f"{body_limit}-char limit; "
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
        warn_if_long(name, body, config)
    for line in skipped:
        warn(f"skipped {line}")
    if not written_names:
        fail("nothing was migrated; the legacy files were left untouched")
    if skipped and not args.force:
        warn("legacy files kept because some entries were skipped; resolve those, "
             "or re-run with --force to replace rules that already exist in the new "
             "format (--force never overwrites a file that is not a rule)")
        return len(written_names)
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
    return len(written_names)


def cmd_migrate(args):
    """Bring a scope up to the current format, in idempotent steps: rename type
    prefixes, rewrite the renamed frontmatter key, convert a legacy
    `rules-map.yml` if one is still there.

    The renames run FIRST on purpose. The map conversion is the step that can
    refuse to finish (an entry too long, a name already taken), and when it does
    it stops the command — a scope would then never get the cheap, safe part of
    the migration it was asked for."""
    scope_dir, anchor = scope_for(args)
    config = config_for(args)
    if not os.path.isdir(scope_dir):
        print("nothing to migrate: this scope has no rules directory")
        return
    changed = migrate_rule_names(scope_dir, config, args.force)
    changed += migrate_interval_key(scope_dir)
    changed += migrate_legacy_map(args, scope_dir, anchor, config)
    if not changed:
        print("nothing to migrate: this scope is already in the current format")
    report_untyped_rules(scope_dir, config)
    # Legacy rules are free-form documents written before globs were per-rule,
    # so they are the population most likely to need splitting. Say it here,
    # while the conversion is fresh, rather than leaving it for a `validate`
    # nobody runs.
    validate_scope(scope_dir, anchor, quiet=True, config=config,
                   is_global=args.use_global)
