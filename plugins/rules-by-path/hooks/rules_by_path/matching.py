"""Which file a tool call touches, and which rules that file matches."""

import os
import time

from .constants import (FILE_PATH_KEYS, MATCH_BUDGET_SECONDS,
                        RULES_DIR_RELPATH, warn)
from .frontmatter import globs_of
from .globbing import glob_matches
from .rules import has_legacy_map, scope_index


def extract_file_path(payload):
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in FILE_PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def is_inside_rules_dir(abs_path, real_abs):
    """True when the path is inside a rules directory — those files must never
    trigger injection. Checked on the resolved path too, so an in-repo symlink
    aliasing the rules directory does not slip past a textual comparison.

    `real_abs` is passed in rather than resolved here: `collect_candidates`
    needs the same value on the same tool call, and resolving a path walks
    every component of it."""
    needle = f"/{RULES_DIR_RELPATH.replace(os.sep, '/')}/"
    return any(needle in candidate + "/" for candidate in (abs_path, real_abs))


def path_targets(abs_path, real_abs, base_dir):
    """[(rel_path, abs_path)] — the paths a glob is matched against.

    The literal path the tool named, plus the resolved one when it still lives
    inside the same project. Matching only the literal text means the same file
    reached through a directory symlink does not get the rule that governs it:
    monorepos routinely carry convenience links (`packages/app/shared ->
    ../../shared`), and a hostile repo could alias a directory precisely to dodge
    a rule. The resolved path is dropped when it leaves the project, so a link
    pointing outside cannot pull in globs from a scope it does not belong to."""
    if base_dir is None:
        targets = [(None, abs_path)]
        if real_abs != abs_path:
            targets.append((None, real_abs))
        return targets
    targets = [(os.path.relpath(abs_path, base_dir).replace(os.sep, "/"), abs_path)]
    if real_abs != abs_path:
        try:
            rel_real = os.path.relpath(real_abs, os.path.realpath(base_dir))
        except ValueError:
            # Windows: `relpath` raises rather than answering '..' when the two
            # paths sit on different drives, which is what a junction pointing
            # at another volume produces. That is the same answer as any other
            # path resolving out of the project — drop the resolved target — and
            # it must not escape: this runs before anything is written, so the
            # exception took the whole injection with it, global rules included.
            return targets
        rel_real = rel_real.replace(os.sep, "/")
        if rel_real != ".." and not rel_real.startswith("../"):
            targets.append((rel_real, real_abs))
    return targets


def collect_candidates(abs_path, real_abs, scopes):
    """(candidates, legacy_scope_labels) for a touched file.

    A candidate is (scope_dir, label, name, glob, fields) — one per matching
    rule, listing the first glob that matched so provenance stays specific.

    Every scope gets its own slice of the matching budget, and the clock is
    checked per glob rather than per rule. One scope must not be able to spend
    another's time: a nested scope is consulted before the repository root, so a
    shared budget let a vendored directory full of expensive globs starve the
    root's rules on every single tool call — permanently, since the budget is
    recomputed per call."""
    candidates = []
    legacy = []
    budget_hit = False
    per_scope = MATCH_BUDGET_SECONDS / max(1, len(scopes))
    for base_dir, scope_dir, label in scopes:
        deadline = time.monotonic() + per_scope
        if has_legacy_map(scope_dir):
            legacy.append(label)
        targets = path_targets(abs_path, real_abs, base_dir)
        for name, fields in scope_index(scope_dir):
            if time.monotonic() > deadline:
                budget_hit = True
                break
            for glob in globs_of(fields):
                if time.monotonic() > deadline:
                    budget_hit = True
                    break
                if any(glob_matches(glob, rel, target) for rel, target in targets):
                    candidates.append((scope_dir, label, name, glob, fields))
                    break
    if budget_hit:
        warn(f"glob matching exceeded its {per_scope:.2f}s per-scope budget; the "
             f"remaining rules of that scope were skipped for this tool call")
    return candidates, legacy
