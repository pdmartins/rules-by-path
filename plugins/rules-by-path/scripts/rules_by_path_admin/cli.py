"""Argument parsing and dispatch: every subcommand this CLI accepts is one
entry of COMMANDS, so the list a user sees and the function that runs cannot
drift apart."""

import argparse
import sys

from .common import HOOK, AdminError, fail
from .config import cmd_config
from .doctor import cmd_doctor
from .enforce import cmd_enforce
from .migrate import cmd_migrate
from .move import ANCHOR_CHOICES, cmd_move
from .rules import (cmd_add, cmd_init, cmd_list, cmd_remove, cmd_show,
                    cmd_update)
from .status import cmd_status
from .validate import cmd_validate
from .which import cmd_which

# Declaration order is what `--help` and the "invalid choice" error list.
COMMANDS = {"init": cmd_init, "list": cmd_list, "show": cmd_show,
            "which": cmd_which, "add": cmd_add, "update": cmd_update,
            "remove": cmd_remove, "validate": cmd_validate,
            "config": cmd_config, "migrate": cmd_migrate,
            "enforce": cmd_enforce, "status": cmd_status,
            "doctor": cmd_doctor, "move": cmd_move}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=list(COMMANDS))
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--root", help="project root (the folder containing .claude/)")
    scope.add_argument("--global", dest="use_global", action="store_true",
                       help="global scope (~/.claude/rules-by-path)")
    parser.add_argument("--glob", action="append", default=[],
                        help="glob the rule applies to; repeat for several")
    parser.add_argument("--exclude", action="append", default=[],
                        help="glob the rule must NOT apply to, even when a "
                             "--glob covers it; repeat for several")
    parser.add_argument("--tool", choices=[*HOOK.TOOL_KINDS, HOOK.TOOL_KIND_ANY],
                        help="restrict the rule to write tool calls "
                             "(Write/Edit/MultiEdit/NotebookEdit) or to reads; "
                             f"'{HOOK.TOOL_KIND_ANY}' clears the restriction. "
                             "On `which`, asks what fires for that kind of call")
    parser.add_argument("--rule", help="rule file name")
    parser.add_argument("--type", dest="type",
                        help="rule type prefix (see `config` for the configured "
                             "ones); required by `add`")
    parser.add_argument("--remember-again-after", dest="remember_again_after",
                        help="how far the context may move before the rule is "
                             "sent again: '30k' (tokens), '25 calls', or 'never'. "
                             "Defaults to what the rule's type declares")
    parser.add_argument("--force", action="store_true", help="overwrite an existing rule")
    parser.add_argument("--path", help="file/folder to resolve (which; optional "
                                       "on status)")
    parser.add_argument("--json", action="store_true",
                        help="status: print the report as JSON")
    parser.add_argument("--to-global", dest="to_global", action="store_true",
                        help="move: destination is the global scope")
    parser.add_argument("--to-root", dest="to_root",
                        help="move: destination is this project root")
    parser.add_argument("--anchor", choices=list(ANCHOR_CHOICES),
                        help="move --to-global: what a root-anchored glob "
                             "should mean there — in any project (**/glob) or "
                             "only in this one (absolute path)")
    parser.add_argument("--fix", action="store_true",
                        help="doctor: apply the deterministic fixes (migration, "
                             "hardening) and re-check")
    parser.add_argument("--uninstall", action="store_true",
                        help="doctor: remove the deny entries and cached state "
                             "the plugin left behind; rule directories are kept")
    parser.add_argument("--list", action="store_true",
                        help="enforce: show enforce: deny rules and their native "
                             "deny equivalents")
    parser.add_argument("--sync", action="store_true",
                        help="enforce: write the native deny entries a project's "
                             "enforce: deny rules need into its settings.json")
    args = parser.parse_args()

    if args.command == "add" and not args.glob:
        fail("'add' requires --glob")
    if args.command in ("show", "update") and not args.rule:
        fail(f"'{args.command}' requires --rule")
    if args.command == "remove":
        if not (args.rule or args.glob):
            fail("'remove' requires --rule or --glob")
        if args.rule and args.glob:
            fail("'remove' takes --rule OR --glob, not both")
        if args.glob:
            args.glob = args.glob[0]
    if args.command == "which" and not args.path:
        fail("'which' requires --path")
    if args.command == "move":
        if not args.rule:
            fail("'move' requires --rule")
        if bool(args.to_global) == bool(args.to_root):
            fail("'move' requires --to-global OR --to-root <project-root>")
        if args.anchor and not args.to_global:
            fail("--anchor only means something with --to-global")
    elif args.to_global or args.to_root or args.anchor:
        fail(f"'{args.command}' takes no --to-global/--to-root/--anchor; they "
             f"belong to `move`")
    # A filter only means something on a file the command actually writes or
    # resolves. Accepting it silently elsewhere would read as "this rule now
    # excludes X" when nothing was written at all.
    if args.command not in ("add", "update", "which", "status"):
        if args.exclude or args.tool:
            fail(f"'{args.command}' takes no --exclude/--tool; they belong to "
                 f"`add`, `update` and `which`")
    if args.command == "status" and args.exclude:
        fail("'status' takes no --exclude")
    if args.json and args.command != "status":
        fail(f"'{args.command}' takes no --json; it belongs to `status`")
    if (args.fix or args.uninstall) and args.command != "doctor":
        fail(f"'{args.command}' takes no --fix/--uninstall; they belong to `doctor`")
    if args.fix and args.uninstall:
        fail("'doctor' takes --fix OR --uninstall, not both")
    if args.command == "enforce":
        if not (args.list or args.sync):
            fail("'enforce' requires --list or --sync")
        if args.list and args.sync:
            fail("'enforce' takes --list OR --sync, not both")

    COMMANDS[args.command](args)


def run():
    """Every failure leaves as one line on stderr and exit 1 — including an
    unexpected one. A traceback tells the model driving this CLI nothing it can
    act on, and `show` used to emit one for a rule saved in cp1252."""
    try:
        main()
    except AdminError as error:
        print(f"rules-by-path-admin: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # noqa: BLE001 - deliberate last resort
        print(f"rules-by-path-admin: unexpected error: {error!r}", file=sys.stderr)
        return 1
    return 0
