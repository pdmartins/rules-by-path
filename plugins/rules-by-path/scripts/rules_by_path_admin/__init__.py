"""rules-by-path-admin — management CLI for the rules-by-path plugin.

The recommended hardening deny-lists the rules directory for Claude's file
tools, so this package is the sanctioned management channel: it takes `--root`
or `--global` plus the rule content on stdin and does all file I/O internally.
Used by the `rules-by-path:manage` skill.

Layout — one concern per module, none over 400 lines:

    common.py    the scope, the rule-file vocabulary, safe read and write
    config.py    config.json: which layers apply, the rule taxonomy, `config`
    validate.py  everything that can be said about a scope without changing it
    rules.py     init, list, show, add, update, remove
    which.py     which — what covers a path, by the hook's own matcher
    status.py    status — the whole picture in one read-only call
    doctor.py    doctor — setup checks, --fix, --uninstall
    hardening.py the recommended permissions.deny entries: check, apply, remove
    migrate.py   bringing a scope up to the current format
    move.py      carrying a rule between scopes, globs rewritten
    enforce.py   enforce: deny rules <-> permissions.deny in settings.json
    cli.py       argument parsing and dispatch

`scripts/rules-by-path-admin.py` is the executable facade that `bin/` and the
test suite address by path.
"""

from .cli import run  # noqa: F401 - what the executable facade calls
