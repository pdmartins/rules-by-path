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
  which  --path P [--tool T]  which rules cover a path (the hook's own matching)
  add    --glob G [--glob G]  create a rule; markdown body read from stdin
         --type T [--rule N] [--force] [--remember-again-after 30k|25 calls|never]
         [--exclude G] [--tool read|write]  narrow it past its glob
  update --rule N             replace a rule's body (stdin), keeping its globs
  remove --rule N | --glob G  delete a rule file
  move   --rule N --to-global [--anchor any-project|this-project] | --to-root R
                              carry a rule to the other scope, rewriting its globs
  validate                    check every rule: frontmatter, globs, size, safety
  config                      the effective config.json: rule types and defaults
  migrate                     bring a scope up to the current format
  enforce --list              enforce: deny rules and their native deny equivalents
  enforce --sync              write those equivalents into a project's settings.json
  status [--path P] [--json]  environment, both scopes, findings, coverage, config
  doctor [--fix|--uninstall]  setup checks, each finding naming its fix

Scope: --root <project-root> (project) or --global (~/.claude).
"""

import os
import sys

# The package sits beside this file. Prepending its directory is what lets the
# CLI run as a plain script from any working directory, which is how `bin/` and
# the manage skill invoke it — there is no installed distribution to import from.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from rules_by_path_admin import run  # noqa: E402 - after the sys.path fix

if __name__ == "__main__":
    sys.exit(run())
