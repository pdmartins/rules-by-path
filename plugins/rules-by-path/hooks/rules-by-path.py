#!/usr/bin/env python3
"""Executable facade for the rules-by-path hook. The implementation lives in
the `rules_by_path` package next to this file — see its docstring.

This file stays because four independent things address the hook by this exact
path and must keep working unchanged: `hooks/hooks.json`, the `bin/` launchers,
the admin CLI (which imports it to share the plugin's one glob matcher and one
frontmatter parser) and the test suite. Importing the package here re-exports
that whole surface, so `HOOK.parse_frontmatter` and friends resolve as before.

A hyphen in the name is why this indirection exists at all: `rules-by-path` is
not a legal module name, so the package it loads has to be named differently.
"""

import os
import sys

# The package sits beside this file. Prepending its directory is what lets the
# hook run as a plain script from any working directory, which is how Claude
# Code invokes it — there is no installed distribution to import from.
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from rules_by_path import *  # noqa: E402,F401,F403 - the public surface
from rules_by_path import cli  # noqa: E402

if __name__ == "__main__":
    sys.exit(cli())
