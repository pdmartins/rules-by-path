#!/usr/bin/env python3
"""rules-by-path-admin — management CLI for the rules-by-path plugin.

The recommended hardening config deny-lists the rules directories for Claude's
file tools, and Bash write commands whose command line contains their literal
path are denied as well. This script is the sanctioned management channel: it
takes `--root`/`--global` plus the entry data (rule content on stdin) and
performs all file I/O internally, so no denied path ever appears on a command
line. Used by the `rules-by-path:manage` skill.

Subcommands:
  init                       create/refresh the skeleton (keeps existing entries)
  list                       print the map and the rule files
  which --path P             show which entries cover a path (hook's own matching)
  add   --glob G [--rule N]  register a rule; markdown content read from stdin
        [--force]            overwrite an existing entry for the same glob
  remove --glob G            drop the entry and its rule file
  validate                   check map shape, names and rule-file existence

Scope: --root <project-root> (project) or --global (~/.claude).
"""

import argparse
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
    """Import the plugin's hook so glob matching, map parsing and name
    derivation are the exact code the injection uses — no duplicated (and
    driftable) logic here."""
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


def is_flat_name(rule_name):
    return not (os.path.isabs(rule_name) or "/" in rule_name or "\\" in rule_name
                or rule_name in (".", ".."))


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


def load_entries(map_path):
    """Return entries as [{'glob': ..., 'rule': ... or None}] preserving order.
    Uses the hook's own parser (PyYAML when available, fallback otherwise)."""
    if not os.path.isfile(map_path):
        return []
    return HOOK.load_raw_entries(map_path)


def write_map(map_path, header, entries):
    with open(map_path, "w", encoding="utf-8") as handle:
        handle.write(header)
        for entry in entries:
            handle.write(f'  - glob: "{entry["glob"]}"\n')
            if entry["rule"]:
                handle.write(f'    rule: "{entry["rule"]}"\n')


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
    for entry in matches:
        rule_name = resolved_rule(entry)
        gone = "" if os.path.isfile(os.path.join(rules_dir, rule_name)) else "  (rule MISSING)"
        print(f"match: '{entry['glob']}' -> rules/{rule_name}{gone}")
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
    with open(map_path, encoding="utf-8") as handle:
        print(handle.read().rstrip())
    files = sorted(os.listdir(rules_dir)) if os.path.isdir(rules_dir) else []
    print(f"\n# rules/: {', '.join(files) if files else '(empty)'}")


def cmd_add(args):
    base, map_path, rules_dir, header = paths_for(args)
    content = sys.stdin.read().strip()
    if not content:
        fail("empty rule content — send the markdown via stdin")
    rule_name = args.rule or derive_rule_name(args.glob)
    if not is_flat_name(rule_name):
        fail(f"invalid rule name (flat names only, no '/'): {rule_name!r}")
    if any(ch in rule_name for ch in "*?[]"):
        fail(f"derived name contains a metacharacter ({rule_name!r}); pass a clean name via --rule")
    entries = load_entries(map_path)
    existing = next((e for e in entries if e["glob"] == args.glob), None)
    if existing and not args.force:
        fail(f"glob already registered (rule '{resolved_rule(existing)}'); use --force to overwrite")
    collision = next((e for e in entries
                      if e["glob"] != args.glob and resolved_rule(e) == rule_name), None)
    if collision:
        fail(f"the file rules/{rule_name} already belongs to glob '{collision['glob']}' — "
             f"update THAT glob with --force, or pass a different name via --rule")
    os.makedirs(rules_dir, exist_ok=True)
    if existing:
        old_name = resolved_rule(existing)
        if old_name != rule_name and os.path.isfile(os.path.join(rules_dir, old_name)):
            os.unlink(os.path.join(rules_dir, old_name))
        existing["rule"] = rule_name
    else:
        entries.append({"glob": args.glob, "rule": rule_name})
    with open(os.path.join(rules_dir, rule_name), "w", encoding="utf-8") as handle:
        handle.write(content + "\n")
    write_map(map_path, header, entries)
    print(f"ok: '{args.glob}' -> rules/{rule_name}")
    validate(base, map_path)


def cmd_remove(args):
    base, map_path, rules_dir, header = paths_for(args)
    entries = load_entries(map_path)
    target = next((e for e in entries if e["glob"] == args.glob), None)
    if target is None:
        fail(f"glob not registered: {args.glob!r}")
    kept = [e for e in entries if e is not target]
    rule_name = resolved_rule(target)
    write_map(map_path, header, kept)
    still_used = any(resolved_rule(e) == rule_name for e in kept)
    rule_path = os.path.join(rules_dir, rule_name)
    if not still_used and is_flat_name(rule_name) and os.path.isfile(rule_path):
        os.unlink(rule_path)
    print(f"ok: removed '{args.glob}' (rule {rule_name}"
          f"{' kept, used by another glob' if still_used else ' deleted'})")
    validate(base, map_path)


def validate(base, map_path):
    problems = []
    entries = load_entries(map_path)
    for entry in entries:
        rule_name = resolved_rule(entry)
        if not is_flat_name(rule_name):
            problems.append(f"invalid rule name for '{entry['glob']}': {rule_name!r}")
            continue
        path = os.path.join(base, RULES_SUBDIR_NAME, rule_name)
        if not os.path.isfile(path):
            problems.append(f"missing rule for '{entry['glob']}': rules/{rule_name}")
        else:
            print(f"ok: {entry['glob']} -> rules/{rule_name}")
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
    parser.add_argument("command", choices=["init", "list", "which", "add", "remove", "validate"])
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--root", help="project root (the folder containing .claude/)")
    scope.add_argument("--global", dest="use_global", action="store_true",
                       help="global scope (~/.claude/rules-by-path)")
    parser.add_argument("--glob", help="rule glob (add/remove)")
    parser.add_argument("--rule", help="rule file name (add; default derived from the glob)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing entry (add)")
    parser.add_argument("--path", help="file/folder to resolve (which)")
    args = parser.parse_args()
    if args.command in ("add", "remove") and not args.glob:
        fail(f"'{args.command}' requires --glob")
    if args.command == "which" and not args.path:
        fail("'which' requires --path")
    {"init": cmd_init, "list": cmd_list, "which": cmd_which, "add": cmd_add,
     "remove": cmd_remove, "validate": cmd_validate}[args.command](args)


if __name__ == "__main__":
    main()
