"""The commands that read and write rule files: init, list, show, which, add,
update, remove.

`add` is the one command that refuses to guess: a rule needs a type, and this is
the only moment in the whole system when a human is present to choose it."""

import os
import sys

from .common import (HOOK, INTERVAL_KEY, LEGACY_INTERVAL_KEY,
                     MAX_ECHOED_NAME_CHARS, RENDERED_KEYS, atomic_write,
                     check_glob, check_line_value, existing_is_not_a_rule,
                     existing_rule_path, fail, other_markdown_in,
                     preserved_fields, rule_path, rules_in, scope_for, warn,
                     warn_if_long)
from .config import (TYPE_SEPARATOR, check_remember_again_after, config_for,
                     resolve_type)
from .validate import validate_scope


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


def render_rule(globs, body, remember_again_after=None, extra=None):
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
    if remember_again_after:
        lines.append(f"{INTERVAL_KEY}: "
                     f"{check_line_value(INTERVAL_KEY, remember_again_after)}")
    for key, value in (extra or {}).items():
        if key in RENDERED_KEYS or isinstance(value, list):
            continue
        lines.append(f"{key}: {check_line_value(key, value)}")
    lines.append("---")
    lines.append("")
    frontmatter = "\n".join(lines)
    size = len(frontmatter.encode("utf-8"))
    if size > HOOK.MAX_FRONTMATTER_BYTES:
        fail(f"frontmatter is {size} bytes, over the "
             f"{HOOK.MAX_FRONTMATTER_BYTES}-byte window the hook reads to find the "
             f"closing '---'; use fewer or shorter globs, or a shorter description")
    return frontmatter + body.strip() + "\n"


def submitted_interval(submitted):
    """The interval a body arriving on stdin already declares, under either the
    current key or the one it replaced."""
    value = submitted.get(INTERVAL_KEY) or submitted.get(LEGACY_INTERVAL_KEY) or None
    if isinstance(value, list):
        value = value[0] if value else None
    return value


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
    path = existing_rule_path(scope_dir, args.rule)
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
    if not matches:
        print(f"no rule covers '{shown}'")
        return
    for name in matches:
        print(f"match: rule {name}")


def cmd_add(args):
    scope_dir, anchor = scope_for(args)
    config = config_for(args)
    body, submitted = split_submitted(sys.stdin.read())
    if not body:
        fail("empty rule content — send the markdown via stdin")
    globs = [g.strip() for g in args.glob if g.strip()] or HOOK.globs_of(submitted)
    if not globs:
        fail("'add' requires at least one --glob")
    name = args.rule or HOOK.derive_rule_name(globs[0])
    prefix, name = resolve_type(config, args.type, name)
    if not HOOK.is_valid_rule_name(name):
        source = "invalid rule name" if args.rule else \
            f"the name derived from {globs[0]!r} is not usable"
        fail(f"{source}: {name[:MAX_ECHOED_NAME_CHARS]!r} — pass a plain one, e.g. "
             f"--rule {prefix}{TYPE_SEPARATOR}handlers-inherit-base.md")
    path = rule_path(scope_dir, name)
    if existing_is_not_a_rule(path):
        fail(f"{name} already exists and is NOT a rule (no frontmatter); refusing "
             f"to overwrite a plain markdown file, even with --force. Remove it "
             f"first, or pass a different --rule")
    if os.path.exists(path) and not args.force:
        fail(f"{name} already exists in this scope; use --force to overwrite, "
             f"`update --rule {name}` to replace its body, or pass another --rule")
    os.makedirs(scope_dir, exist_ok=True)
    # Precedence: the flag, then what the body already declared, then the
    # default this type carries in the config. The type default is WRITTEN into
    # the rule rather than resolved at injection time, so the file states its
    # own schedule and the hook never has to know what a type is.
    from_type = HOOK.remember_again_after_for_type(config, prefix)
    from_body = submitted_interval(submitted)
    interval = args.remember_again_after or from_body or from_type
    check_remember_again_after(interval)
    atomic_write(path, render_rule(globs, body, interval,
                                   preserved_fields(submitted)))
    print(f"ok: {name}  <-  {', '.join(globs)}")
    if interval:
        from_type_only = (interval == from_type and not args.remember_again_after
                          and not from_body)
        origin = f" (default for {prefix})" if from_type_only else ""
        print(f"    {INTERVAL_KEY}: {interval}{origin}")
    warn_if_long(name, body, config)
    validate_scope(scope_dir, anchor, quiet=True, config=config,
                   is_global=args.use_global)


def cmd_update(args):
    scope_dir, anchor = scope_for(args)
    body, submitted = split_submitted(sys.stdin.read())
    if not body:
        fail("empty rule content — send the markdown via stdin")
    path = existing_rule_path(scope_dir, args.rule)
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
    interval = (args.remember_again_after or submitted_interval(submitted)
                or submitted_interval(fields) or None)
    check_remember_again_after(interval)
    merged = {**fields, **submitted}
    atomic_write(path, render_rule(globs, body, interval,
                                   preserved_fields(merged, owned_last=True)))
    print(f"ok: updated {args.rule}")
    config = config_for(args)
    warn_if_long(args.rule, body, config)
    validate_scope(scope_dir, anchor, quiet=True, config=config,
                   is_global=args.use_global)


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
