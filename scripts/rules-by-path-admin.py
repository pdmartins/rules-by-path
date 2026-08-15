#!/usr/bin/env python3
"""rules-by-path-admin — management CLI for the rules-by-path plugin.

The recommended hardening deny-lists the rules directories for Claude's file
tools, and Bash write commands whose command line contains their literal path
are denied as well. This script is the sanctioned management channel: it takes
`--root`/`--global` plus the entry data (rule content on stdin) and performs
all file I/O internally, so no denied path ever appears on a command line.
Used by the `rules-by-path:manage` skill.

Subcommands:
  init                       create/refresh the skeleton (keeps existing entries)
  list                       print the map and the rule files
  show  --rule N             print one rule's content (the sanctioned read path)
  which --path P             show which entries cover a path (hook's own matching)
  add   --glob G [--rule N]  register a rule; markdown content read from stdin
        [--force]            overwrite an existing entry for the same glob
  update --rule N            replace an existing rule's content (stdin), by file
                             name — never requires pasting a glob back
  remove --glob G | --rule N drop the entry and its rule file
  validate                   check map shape, names, containment and rule files

Scope: --root <project-root> (project) or --global (~/.claude).

Every write is refused when the map cannot be parsed, and the map is replaced
atomically — a management tool that silently truncates a user's rules is worse
than one that refuses to run.
"""

import argparse
import json
import os
import sys
import tempfile

RULES_DIR_RELPATH = os.path.join(".claude", "rules-by-path")
MAP_FILE_NAME = "rules-map.yml"
RULES_SUBDIR_NAME = "rules"
HOOK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "hooks", "rules-by-path.py")

HEADER_PROJECT = """\
# rules-by-path — glob-to-rule map (scope: project).
# Injected by the rules-by-path plugin's PreToolUse hook whenever Claude
# touches a file matching a glob. Manage it via the `rules-by-path:manage`
# skill — never by hand-editing this file.
#
# Project scope: globs are relative to the project root (the folder containing .claude/).
#   - "src/api/**"     -> anything under src/api/
#   - "docs"           -> the docs/ folder and everything below
#   - "*.tf"           -> a glob without '/' also matches the basename
# Entry format:
#   - glob: "src/api/**"        # required
#     rule: "src--api.md"       # optional; default: glob with '/' -> '--' (+ '.md' unless already present)
rules:
"""

HEADER_GLOBAL = """\
# rules-by-path — GLOBAL glob-to-rule map (applies to every project).
# Injected by the rules-by-path plugin's PreToolUse hook whenever Claude
# touches a file matching a glob. Manage it via the `rules-by-path:manage`
# skill — never by hand-editing this file.
#
# Global scope: globs match the file's ABSOLUTE path.
#   - "**/terraform/**"  -> any terraform folder anywhere
#   - "*.tf"             -> a glob without '/' also matches the basename
#   - "/repos/x/**"      -> absolute-path prefix
# Entry format:
#   - glob: "**/deploy/**"      # required
#     rule: "deploy.md"         # optional; default: glob with '/' -> '--' (+ '.md' unless already present)
rules:
"""


def fail(message):
    print(f"rules-by-path-admin: {message}", file=sys.stderr)
    sys.exit(1)


def load_hook_module():
    """Import the plugin's hook so glob matching, map parsing, name derivation
    and containment are the exact code the injection uses — a second
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


def derive_rule_name(glob):
    return HOOK.derive_rule_name(glob)


def is_valid_rule_name(rule_name):
    return HOOK.is_valid_rule_name(rule_name)


def paths_for(args):
    if args.use_global:
        anchor = os.path.expanduser("~")
        header = HEADER_GLOBAL
    else:
        anchor = os.path.abspath(args.root)
        if not os.path.isdir(anchor):
            fail(f"project root does not exist: {anchor}")
        header = HEADER_PROJECT
    base = os.path.join(anchor, RULES_DIR_RELPATH)
    # The scope directory must physically live under the root the caller named.
    # Without this, a cloned repo shipping `.claude/rules-by-path` as a symlink
    # redirects every read, write and delete — a project-scoped `add` would
    # land in the user's GLOBAL rules and apply to every project forever.
    if not HOOK.scope_is_contained(anchor, base):
        fail(f"{base} does not physically live inside {anchor} (symlink?); refusing to touch it")
    return base, os.path.join(base, MAP_FILE_NAME), os.path.join(base, RULES_SUBDIR_NAME), header, anchor


def safe_rules_dir(map_path, rules_dir, anchor):
    """The rules directory, verified to be a real directory inside the scope.
    Refusing here is what stops a cloned repo from steering writes and unlinks
    through a symlinked `rules/`."""
    if not os.path.exists(rules_dir):
        return rules_dir  # not created yet; makedirs will create a real dir
    resolved = HOOK.resolve_rules_dir(map_path, anchor)
    if resolved is None:
        fail(f"{rules_dir} is not a real directory inside the scope (symlink?); refusing to touch it")
    return resolved


def load_entries(map_path):
    """Entries for a command that WRITES. Aborts on anything not fully
    understood, so a read-modify-write cannot wipe or mangle the map, and so a
    hostile rule name never reaches a filesystem call."""
    if not os.path.isfile(map_path):
        return []
    try:
        return HOOK.load_raw_entries(map_path, strict=True)
    except HOOK.MapParseError as exc:
        fail(f"{exc}\nrefusing to write: fix or delete the map first, "
             f"otherwise this command would discard every entry in it")


def load_entries_reporting(map_path):
    """(entries, parse_error). `validate` exists to detect breakage, so it must
    surface a map it could not parse rather than treating it as empty."""
    if not os.path.isfile(map_path):
        return [], None
    # `validate` exists to detect breakage, so it uses the strict posture: a
    # line this code could not understand is a problem to report, not to skip.
    try:
        return HOOK.load_raw_entries(map_path, strict=True), None
    except HOOK.MapParseError as exc:
        return [], str(exc)


def load_entries_lenient(map_path):
    """Entries for a read-only command. Reports what it could not parse instead
    of refusing — a user whose map has one bad line still needs to be able to
    list it and find out what is wrong."""
    if not os.path.isfile(map_path):
        return []
    try:
        return HOOK.load_raw_entries(map_path, strict=False)
    except HOOK.MapParseError as exc:
        print(f"rules-by-path-admin: {exc}", file=sys.stderr)
        return []


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


def write_map(map_path, header, entries):
    lines = [header]
    for entry in entries:
        # json.dumps produces a valid YAML double-quoted scalar, so a glob
        # containing a quote, backslash or '#' round-trips intact.
        # ensure_ascii=False keeps non-ASCII globs literal: the \uXXXX escapes
        # json.dumps emits by default are decoded by PyYAML but not by the
        # fallback parser, so an accented glob would stop matching without it.
        lines.append(f"  - glob: {json.dumps(entry['glob'], ensure_ascii=False)}\n")
        if entry["rule"]:
            lines.append(f"    rule: {json.dumps(entry['rule'], ensure_ascii=False)}\n")
    atomic_write(map_path, "".join(lines))


def resolved_rule(entry):
    return entry["rule"] or derive_rule_name(entry["glob"])


def cmd_init(args):
    base, map_path, rules_dir, header, anchor = paths_for(args)
    os.makedirs(rules_dir, exist_ok=True)
    write_map(map_path, header, load_entries(map_path))
    print(f"ok: skeleton ready at {base}")


def cmd_which(args):
    base, map_path, rules_dir, _, anchor = paths_for(args)
    if args.use_global:
        abs_path = os.path.abspath(args.path)
        rel_path = None
        shown = abs_path
    else:
        root = os.path.abspath(args.root)
        abs_path = args.path if os.path.isabs(args.path) else os.path.join(root, args.path)
        abs_path = os.path.normpath(abs_path)
        rel_path = os.path.relpath(abs_path, root).replace(os.sep, "/")
        if rel_path.startswith(".."):
            fail(f"path outside the root {root}: {abs_path}")
        shown = rel_path
    abs_posix = abs_path.replace(os.sep, "/")
    entries = load_entries_lenient(map_path)
    # A folder query ("--path docs") must also find entries like 'docs/**',
    # which only match paths INSIDE the folder — probe with a synthetic child.
    # A path that does not exist yet and has no file extension is treated as a
    # folder: asking about a folder you are about to create is the normal case
    # when registering a rule, and it used to report a spurious "no match".
    looks_like_a_file = "." in os.path.basename(abs_path.rstrip("/"))
    is_dir_query = (os.path.isdir(abs_path) or args.path.endswith("/")
                    or (not os.path.exists(abs_path) and not looks_like_a_file))
    targets = [(rel_path, abs_posix)]
    if is_dir_query:
        child = "__probe__"
        targets.append((None if rel_path is None else f"{rel_path.rstrip('/')}/{child}",
                        f"{abs_posix.rstrip('/')}/{child}"))
    matches = [e for e in entries
               if any(HOOK.glob_matches(e["glob"], r, a) for r, a in targets)]

    if args.json:
        print(json.dumps({
            "path": shown,
            "matches": [{"glob": e["glob"], "rule": resolved_rule(e),
                         "present": os.path.isfile(os.path.join(rules_dir, resolved_rule(e)))}
                        for e in matches],
        }, ensure_ascii=False, indent=2))
        return

    for entry in matches:
        rule_name = resolved_rule(entry)
        gone = "" if os.path.isfile(os.path.join(rules_dir, rule_name)) else "  (rule MISSING)"
        print(f"match: rule {rule_name}{gone}")
    if not matches:
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
        print(f"no entry covers '{shown}' — to create one:")
        for suggestion in suggestions:
            print(f"  add {scope_flag} --glob '{suggestion}'")


def cmd_list(args):
    _, map_path, rules_dir, _, anchor = paths_for(args)
    if not os.path.isfile(map_path):
        print("(no rules-map.yml in this scope)")
        return
    entries = load_entries_lenient(map_path)
    if not entries:
        print("(no rules registered in this scope)")
    for entry in entries:
        rule_name = resolved_rule(entry)
        gone = "" if os.path.isfile(os.path.join(rules_dir, rule_name)) else "  (rule MISSING)"
        print(f"{entry['glob']}  ->  {rule_name}{gone}")


def cmd_show(args):
    base, map_path, rules_dir, _, anchor = paths_for(args)
    if not is_valid_rule_name(args.rule):
        fail(f"invalid rule name: {args.rule!r}")
    rules_dir = HOOK.resolve_rules_dir(map_path, anchor)
    if rules_dir is None:
        fail("the rules/ directory is not a real directory inside the scope")
    path = os.path.join(rules_dir, args.rule)
    if os.path.islink(path) or not os.path.isfile(path):
        fail(f"cannot read rule {args.rule!r} in this scope")
    # Read the file whole. The hook truncates at 16k for context budget, but
    # `show` feeds the show -> edit -> update round trip: truncating here would
    # silently destroy the tail of a long rule on the next update.
    with open(path, encoding="utf-8") as handle:
        sys.stdout.write(handle.read())


def cmd_add(args):
    base, map_path, rules_dir, header, anchor = paths_for(args)
    content = sys.stdin.read().strip()
    if not content:
        fail("empty rule content — send the markdown via stdin")
    rule_name = args.rule or derive_rule_name(args.glob)
    if not is_valid_rule_name(rule_name):
        fail(f"invalid rule name ({rule_name!r}); pass a clean name via --rule, "
             f"e.g. --rule csharp-files.md")
    entries = load_entries(map_path)
    existing = next((e for e in entries if e["glob"] == args.glob), None)
    if existing and not args.force:
        fail(f"glob already registered (rule '{resolved_rule(existing)}'); use --force to overwrite")
    collision = next((e for e in entries
                      if e["glob"] != args.glob and resolved_rule(e) == rule_name), None)
    if collision:
        fail(f"the file {rule_name} already belongs to another glob — "
             f"update it with `update --rule {rule_name}`, or pass a different --rule")
    rules_dir = safe_rules_dir(map_path, rules_dir, anchor)
    os.makedirs(rules_dir, exist_ok=True)
    old_name = None
    if existing:
        old_name = resolved_rule(existing)
        existing["rule"] = rule_name
    else:
        entries.append({"glob": args.glob, "rule": rule_name})
    # Write the replacement and commit the map BEFORE removing the old file:
    # unlinking first destroys content whenever a later step refuses to run.
    write_rule_file(rules_dir, rule_name, content)
    write_map(map_path, header, entries)
    if old_name and old_name != rule_name:
        unlink_rule_file(rules_dir, old_name)
    print(f"ok: registered -> {rule_name}")
    validate(base, map_path, anchor)


def cmd_update(args):
    base, map_path, rules_dir, header, anchor = paths_for(args)
    content = sys.stdin.read().strip()
    if not content:
        fail("empty rule content — send the markdown via stdin")
    if not is_valid_rule_name(args.rule):
        fail(f"invalid rule name: {args.rule!r}")
    entries = load_entries(map_path)
    if not any(resolved_rule(e) == args.rule for e in entries):
        fail(f"no entry uses the rule file {args.rule!r} — register it with `add --glob ...`")
    rules_dir = safe_rules_dir(map_path, rules_dir, anchor)
    write_rule_file(rules_dir, args.rule, content)
    print(f"ok: updated {args.rule}")
    validate(base, map_path, anchor)


def write_rule_file(rules_dir, rule_name, content):
    if not is_valid_rule_name(rule_name):
        fail(f"invalid rule name: {rule_name!r}")
    atomic_write(os.path.join(rules_dir, rule_name), content + "\n")


def unlink_rule_file(rules_dir, rule_name):
    """Delete a rule file, but only when the name is one we would have written.

    Rule names come out of the map, which is repository data. An absolute name
    makes os.path.join discard rules_dir entirely, and '../' walks out of the
    scope — either one turns a rename into arbitrary file deletion."""
    if not is_valid_rule_name(rule_name):
        warn(f"refusing to delete {rule_name!r}: not a plain '*.md' file name")
        return
    path = os.path.join(rules_dir, rule_name)
    if os.path.islink(path):
        warn(f"refusing to delete {rule_name}: it is a symlink")
        return
    if os.path.isfile(path):
        os.unlink(path)


def warn(message):
    print(f"rules-by-path-admin: {message}", file=sys.stderr)


def cmd_remove(args):
    base, map_path, rules_dir, header, anchor = paths_for(args)
    entries = load_entries(map_path)
    # Removing by rule NAME is the safe path when the glob came out of the map
    # (repository data); removing by glob is for a glob the user just named.
    if args.rule:
        target = next((e for e in entries if resolved_rule(e) == args.rule), None)
        if target is None:
            fail(f"no entry uses the rule file {args.rule!r}")
    else:
        target = next((e for e in entries if e["glob"] == args.glob), None)
        if target is None:
            fail(f"glob not registered: {args.glob!r}")
    kept = [e for e in entries if e is not target]
    rule_name = resolved_rule(target)
    rules_dir = safe_rules_dir(map_path, rules_dir, anchor)
    write_map(map_path, header, kept)
    still_used = any(resolved_rule(e) == rule_name for e in kept)
    if not still_used:
        unlink_rule_file(rules_dir, rule_name)
    print(f"ok: removed entry (rule {rule_name}"
          f"{' kept, used by another glob' if still_used else ' deleted'})")
    validate(base, map_path, anchor)


def validate(base, map_path, anchor):
    problems = []
    entries, parse_error = load_entries_reporting(map_path)
    if parse_error:
        problems.append(f"map cannot be parsed: {parse_error}")
    rules_dir = HOOK.resolve_rules_dir(map_path, anchor)
    if rules_dir is None and entries:
        fail("the rules/ directory is not a real directory inside the scope (symlink?)")
    for entry in entries:
        rule_name = resolved_rule(entry)
        if not is_valid_rule_name(rule_name):
            problems.append(f"invalid rule name for a registered glob: {rule_name!r}")
            continue
        path = os.path.join(base, RULES_SUBDIR_NAME, rule_name)
        if not os.path.isfile(path):
            problems.append(f"missing rule file: {rule_name}")
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        sys.exit(1)
    print(f"validation ok: {len(entries)} entrie(s)")


def cmd_validate(args):
    base, map_path, _, _, anchor = paths_for(args)
    if not os.path.isfile(map_path):
        print("(no rules-map.yml in this scope — nothing to validate)")
        return
    validate(base, map_path, anchor)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["init", "list", "show", "which", "add",
                                            "update", "remove", "validate"])
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--root", help="project root (the folder containing .claude/)")
    scope.add_argument("--global", dest="use_global", action="store_true",
                       help="global scope (~/.claude/rules-by-path)")
    parser.add_argument("--glob", help="rule glob (add/remove)")
    parser.add_argument("--rule", help="rule file name (add/update/show)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing entry (add)")
    parser.add_argument("--path", help="file/folder to resolve (which)")
    parser.add_argument("--json", action="store_true", help="machine-readable output (which)")
    args = parser.parse_args()
    if args.command == "add" and not args.glob:
        fail("'add' requires --glob")
    if args.command == "remove" and not (args.glob or args.rule):
        fail("'remove' requires --glob or --rule")
    if args.command == "remove" and args.glob and args.rule:
        fail("'remove' takes --glob OR --rule, not both")
    if args.command in ("show", "update") and not args.rule:
        fail(f"'{args.command}' requires --rule")
    if args.command == "which" and not args.path:
        fail("'which' requires --path")
    {"init": cmd_init, "list": cmd_list, "show": cmd_show, "which": cmd_which,
     "add": cmd_add, "update": cmd_update, "remove": cmd_remove,
     "validate": cmd_validate}[args.command](args)


if __name__ == "__main__":
    main()
