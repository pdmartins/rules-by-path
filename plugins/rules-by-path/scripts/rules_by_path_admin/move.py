"""`move`: carry a rule between the project scope and the global one.

A glob changes meaning when it changes scope. In a project it is matched
against the path relative to the project root; in the global scope against
the absolute path (minus its leading `/`), so `src/api/**` moved to the global
scope verbatim would never match anything again. The rewrites below are
deterministic for every shape but one — a root-anchored glob going global can
mean "in any project" or "in this one" — and that one is asked for, with
`--anchor`, rather than guessed."""

import os
from types import SimpleNamespace

from .common import (HOOK, MAX_ECHOED_NAME_CHARS, atomic_write,
                     existing_rule_path, fail, read_regular_file, rule_path,
                     scope_for, warn)
from .config import config_for, describe_types, split_type_prefix
from .rules import preserved_fields, render_rule, submitted_interval
from .validate import validate_scope

ANCHOR_ANY_PROJECT = "any-project"
ANCHOR_THIS_PROJECT = "this-project"
ANCHOR_CHOICES = (ANCHOR_ANY_PROJECT, ANCHOR_THIS_PROJECT)
ANY_DEPTH_PREFIX = "**/"
CURRENT_DIR_PREFIX = "./"
# A rule file is read whole here, like `show` does: a move must not truncate.
MAX_MOVED_RULE_BYTES = 1024 * 1024

# ---- user-visible text ------------------------------------------------------
AMBIGUOUS = ("{glob!r} is anchored at the project root, which means nothing in "
             "the global scope. Say what the move should mean:\n"
             "  --anchor {any}   ->  {any_glob!r}  (the rule holds in every project)\n"
             "  --anchor {this}  ->  {this_glob!r}  (only in this one)")
OUTSIDE_ROOT = ("{glob!r} points outside {root} and cannot become a project "
                "glob there; narrow or drop it first (`update --glob`)")
TYPE_MISSING = ("the type prefix of {name!r} is not in the destination's "
                "taxonomy — rename first (`remove` + `add --type`). Types "
                "configured there:\n{types}")
LANGUAGE_DIFFERS = ("the destination writes rules in {dest!r}, the source in "
                    "{source!r}; the body was moved as is, not translated")
ENFORCE_TO_GLOBAL = ("this rule carries `enforce: deny`: in the global scope the "
                     "hook honours it, so matching writes will be BLOCKED from now on")
ENFORCE_TO_PROJECT = ("this rule carries `enforce: deny`: a project scope cannot "
                      "enforce it — run `enforce --sync` there to write the "
                      "native deny entry")
MOVED = "ok: moved {name}  {source} -> {dest}"
REWRITTEN = "    {key}: {before!r} -> {after!r}"
PROVE = ("check the reach: `which --{flag} --path '<a file it should govern>'` "
         "and one it should not")
# -----------------------------------------------------------------------------


def is_root_anchored(glob):
    """A glob that names a place from the project root: has a `/`, is not
    absolute, and does not already float with `**/`."""
    text = glob.strip()
    if text.startswith(CURRENT_DIR_PREFIX):
        text = text[len(CURRENT_DIR_PREFIX):]
    return ("/" in text.rstrip("/") and not text.startswith("/")
            and not text.startswith(ANY_DEPTH_PREFIX))


def strip_current_dir(glob):
    text = glob.strip()
    return text[len(CURRENT_DIR_PREFIX):] if text.startswith(CURRENT_DIR_PREFIX) else text


def to_global(glob, source_root, anchor):
    """The glob as the global scope must read it, or None when `anchor` is
    needed and missing."""
    if not is_root_anchored(glob):
        return glob
    text = strip_current_dir(glob)
    if anchor == ANCHOR_ANY_PROJECT:
        return ANY_DEPTH_PREFIX + text
    if anchor == ANCHOR_THIS_PROJECT:
        return source_root.replace(os.sep, "/").rstrip("/") + "/" + text
    return None


def to_project(glob, dest_root):
    """The glob as a project scope reads it. An absolute glob under the root
    becomes relative to it; one outside the root cannot move; anything else
    (`**/x/**`, a bare name) already reads the same in both scopes."""
    text = glob.strip()
    if not text.startswith("/"):
        return text
    root = dest_root.replace(os.sep, "/").rstrip("/") + "/"
    if text.startswith(root):
        return text[len(root):] or text
    fail(OUTSIDE_ROOT.format(glob=glob, root=dest_root))


def rewrite_all(values, key, rewrite):
    """[(before, after)] for every glob-like value, via `rewrite`; a None from
    `rewrite` means the value is ambiguous, collected and reported together."""
    pairs = []
    ambiguous = []
    for value in values:
        after = rewrite(value)
        if after is None:
            ambiguous.append(value)
        pairs.append((value, after))
    if ambiguous:
        fail("\n".join(AMBIGUOUS.format(
            glob=glob, any=ANCHOR_ANY_PROJECT, this=ANCHOR_THIS_PROJECT,
            any_glob=to_global(glob, rewrite.source_root, ANCHOR_ANY_PROJECT),
            this_glob=to_global(glob, rewrite.source_root, ANCHOR_THIS_PROJECT))
            for glob in ambiguous))
    return pairs


def destination_of(args):
    """(namespace, label, flag) for the scope the rule goes to."""
    if args.to_global:
        if args.use_global:
            fail("the rule is already in the global scope")
        return SimpleNamespace(use_global=True, root=None), "global", "global"
    if not args.use_global and os.path.abspath(args.to_root) == os.path.abspath(args.root):
        fail("--to-root names the scope the rule is already in")
    root = os.path.abspath(args.to_root)
    return SimpleNamespace(use_global=False, root=root), f"project {root}", f"root '{root}'"


def read_whole_rule(scope_dir, name):
    path = existing_rule_path(scope_dir, name)
    fields, body = HOOK.parse_frontmatter(read_regular_file(path, MAX_MOVED_RULE_BYTES))
    if not fields:
        fail(f"{name} is not a rule (no frontmatter); nothing to move")
    return path, fields, body.strip()


def cmd_move(args):
    source_dir, source_anchor = scope_for(args)
    dest, dest_label, dest_flag = destination_of(args)
    dest_dir, dest_anchor = scope_for(dest)
    name = args.rule
    source_path, fields, body = read_whole_rule(source_dir, name)
    dest_config = config_for(dest)
    if not split_type_prefix(name, dest_config)[0]:
        fail(TYPE_MISSING.format(name=name[:MAX_ECHOED_NAME_CHARS],
                                 types=describe_types(dest_config)))
    target = rule_path(dest_dir, name)
    if os.path.exists(target) and not args.force:
        fail(f"{name} already exists in the {dest_label} scope; --force replaces it")

    if dest.use_global:
        rewrite = lambda glob: to_global(glob, source_anchor, args.anchor)  # noqa: E731
    else:
        rewrite = lambda glob: to_project(glob, dest_anchor)  # noqa: E731
    rewrite.source_root = source_anchor
    globs = rewrite_all(HOOK.globs_of(fields), HOOK.GLOB_KEYS[0], rewrite)
    excludes = rewrite_all(HOOK.excludes_of(fields), HOOK.EXCLUDE_KEYS[0], rewrite)
    if not globs:
        fail(f"{name} declares no glob; give it one with `update --glob` before moving")

    source_language = HOOK.language(config_for(args))
    dest_language = HOOK.language(dest_config)
    os.makedirs(dest_dir, exist_ok=True)
    atomic_write(target, render_rule(
        [after for _before, after in globs], body, submitted_interval(fields),
        preserved_fields(fields, owned_last=True),
        excludes=[after for _before, after in excludes], tool=HOOK.tools_of(fields)))
    os.unlink(source_path)

    source_label = "global" if args.use_global else f"project {source_anchor}"
    print(MOVED.format(name=name, source=source_label, dest=dest_label))
    for key, pairs in ((HOOK.GLOB_KEYS[0], globs), (HOOK.EXCLUDE_KEYS[0], excludes)):
        for before, after in pairs:
            if before != after:
                print(REWRITTEN.format(key=key, before=before, after=after))
    if source_language != dest_language:
        warn(LANGUAGE_DIFFERS.format(dest=dest_language, source=source_language))
    if HOOK.enforce_of(fields) == "deny":
        warn(ENFORCE_TO_GLOBAL if dest.use_global else ENFORCE_TO_PROJECT)
    print(PROVE.format(flag=dest_flag))
    validate_scope(dest_dir, dest_anchor, quiet=True, config=dest_config,
                   is_global=dest.use_global)
