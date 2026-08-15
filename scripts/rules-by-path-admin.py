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
  remove --glob G            drop the entry and its rule file
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
    return bool(HOOK.RULE_NAME_RE.match(rule_name))


def paths_for(args):
    if args.use_global:
        base = os.path.join(os.path.expanduser("~"), RULES_DIR_RELPATH)
        header = HEADER_GLOBAL
    else:
        root = os.path.abspath(args.root)
        if not os.path.isdir(root):
            fail(f"project root does not exist: {root}")
        base = os.path.join(root, RULES_DIR_RELPATH)
        header = HEADER_PROJECT
    return base, os.path.join(base, MAP_FILE_NAME), os.path.join(base, RULES_SUBDIR_NAME), header


def safe_rules_dir(map_path, rules_dir):
    """The rules directory, verified to be a real directory inside the scope.
    Refusing here is what stops a cloned repo from steering writes and unlinks
    through a symlinked `rules/`."""
    if not os.path.exists(rules_dir):
        return rules_dir  # not created yet; makedirs will create a real dir
    resolved = HOOK.resolve_rules_dir(map_path)
    if resolved is None:
        fail(f"{rules_dir} is not a real directory inside the scope (symlink?); refusing to touch it")
    return resolved


def load_entries(map_path):
    """Return entries as [{'glob': ..., 'rule': ... or None}] preserving order.
    Aborts on an unreadable map so a read-modify-write cannot wipe it."""
    if not os.path.isfile(map_path):
        return []
    try:
        return HOOK.load_raw_entries(map_path, strict=True)
    except HOOK.MapParseError as exc:
        fail(f"{exc}\nrefusing to write: fix or delete the map first, "
             f"otherwise this command would discard every entry in it")


def write_map(map_path, header, entries):
    """Replace the map atomically — no window where it sits truncated."""
    directory = os.path.dirname(map_path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = map_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(header)
        for entry in entries:
            # json.dumps produces a valid YAML double-quoted scalar, so a glob
            # containing a quote, backslash or '#' round-trips intact.
            handle.write(f"  - glob: {json.dumps(entry['glob'])}\n")
            if entry["rule"]:
                handle.write(f"    rule: {json.dumps(entry['rule'])}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, map_path)


def resolved_rule(entry):
    return entry["rule"] or derive_rule_name(entry["glob"])


def cmd_init(args):
    base, map_path, rules_dir, header = paths_for(args)
    os.makedirs(rules_dir, exist_ok=True)
    write_map(map_path, header, load_entries(map_path))
    print(f"ok: skeleton ready at {base}")


def cmd_which(args):
    base, map_path, rules_dir, _ = paths_for(args)
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
    entries = load_entries(map_path)
    # A folder query ("--path docs") must also find entries like 'docs/**',
    # which only match paths INSIDE the folder — probe with a synthetic child.
    is_dir_query = os.path.isdir(abs_path) or args.path.endswith("/")
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
        if is_dir_query:
            suggestion = f"{shown.rstrip('/')}/**"
        else:
            parent = os.path.dirname(shown).replace(os.sep, "/")
            suggestion = f"{parent}/**" if parent and parent != "/" else shown
        print(f"no entry covers '{shown}' — to create one: "
              f"add {'--global' if args.use_global else '--root <root>'} --glob '{suggestion}'")


def cmd_list(args):
    _, map_path, rules_dir, _ = paths_for(args)
    if not os.path.isfile(map_path):
        print("(no rules-map.yml in this scope)")
        return
    entries = load_entries(map_path)
    if not entries:
        print("(no rules registered in this scope)")
    for entry in entries:
        rule_name = resolved_rule(entry)
        gone = "" if os.path.isfile(os.path.join(rules_dir, rule_name)) else "  (rule MISSING)"
        print(f"{entry['glob']}  ->  {rule_name}{gone}")


def cmd_show(args):
    base, map_path, rules_dir, _ = paths_for(args)
    if not is_valid_rule_name(args.rule):
        fail(f"invalid rule name: {args.rule!r}")
    content = HOOK.read_rule_content(map_path, args.rule)
    if content is None:
        fail(f"cannot read rule {args.rule!r} in this scope")
    print(content)


def cmd_add(args):
    base, map_path, rules_dir, header = paths_for(args)
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
    rules_dir = safe_rules_dir(map_path, rules_dir)
    os.makedirs(rules_dir, exist_ok=True)
    if existing:
        old_name = resolved_rule(existing)
        if old_name != rule_name and os.path.isfile(os.path.join(rules_dir, old_name)):
            os.unlink(os.path.join(rules_dir, old_name))
        existing["rule"] = rule_name
    else:
        entries.append({"glob": args.glob, "rule": rule_name})
    write_rule_file(rules_dir, rule_name, content)
    write_map(map_path, header, entries)
    print(f"ok: registered -> {rule_name}")
    validate(base, map_path)


def cmd_update(args):
    base, map_path, rules_dir, header = paths_for(args)
    content = sys.stdin.read().strip()
    if not content:
        fail("empty rule content — send the markdown via stdin")
    if not is_valid_rule_name(args.rule):
        fail(f"invalid rule name: {args.rule!r}")
    entries = load_entries(map_path)
    if not any(resolved_rule(e) == args.rule for e in entries):
        fail(f"no entry uses the rule file {args.rule!r} — register it with `add --glob ...`")
    rules_dir = safe_rules_dir(map_path, rules_dir)
    write_rule_file(rules_dir, args.rule, content)
    print(f"ok: updated {args.rule}")
    validate(base, map_path)


def write_rule_file(rules_dir, rule_name, content):
    """Write a rule file without following a symlink at the destination."""
    path = os.path.join(rules_dir, rule_name)
    if os.path.islink(path):
        fail(f"{rule_name} is a symlink; refusing to write through it")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(content + "\n")
    os.replace(tmp_path, path)


def cmd_remove(args):
    base, map_path, rules_dir, header = paths_for(args)
    entries = load_entries(map_path)
    target = next((e for e in entries if e["glob"] == args.glob), None)
    if target is None:
        fail(f"glob not registered: {args.glob!r}")
    kept = [e for e in entries if e is not target]
    rule_name = resolved_rule(target)
    rules_dir = safe_rules_dir(map_path, rules_dir)
    write_map(map_path, header, kept)
    still_used = any(resolved_rule(e) == rule_name for e in kept)
    rule_path = os.path.join(rules_dir, rule_name)
    if not still_used and is_valid_rule_name(rule_name) \
            and os.path.isfile(rule_path) and not os.path.islink(rule_path):
        os.unlink(rule_path)
    print(f"ok: removed entry (rule {rule_name}"
          f"{' kept, used by another glob' if still_used else ' deleted'})")
    validate(base, map_path)


def validate(base, map_path):
    problems = []
    entries = load_entries(map_path)
    rules_dir = HOOK.resolve_rules_dir(map_path)
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
    base, map_path, _, _ = paths_for(args)
    if not os.path.isfile(map_path):
        print("(no rules-map.yml in this scope — nothing to validate)")
        return
    validate(base, map_path)


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
    if args.command in ("add", "remove") and not args.glob:
        fail(f"'{args.command}' requires --glob")
    if args.command in ("show", "update") and not args.rule:
        fail(f"'{args.command}' requires --rule")
    if args.command == "which" and not args.path:
        fail("'which' requires --path")
    {"init": cmd_init, "list": cmd_list, "show": cmd_show, "which": cmd_which,
     "add": cmd_add, "update": cmd_update, "remove": cmd_remove,
     "validate": cmd_validate}[args.command](args)


if __name__ == "__main__":
    main()
