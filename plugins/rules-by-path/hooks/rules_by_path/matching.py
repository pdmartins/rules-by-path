"""Which file a tool call touches, and which rules that file matches."""

import os
import time

from .constants import (FILE_PATH_KEYS, MATCH_BUDGET_SECONDS,
                        RULES_DIR_RELPATH, TOOL_KIND_READ, TOOL_KIND_WRITE,
                        WRITE_TOOL_NAMES, warn)
from .frontmatter import excludes_of, globs_of, tools_of
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


def tool_kind(tool_name):
    """Which kind of tool call this is. The hook only ever runs for the five
    file tools, so everything that is not a write is a read."""
    return TOOL_KIND_WRITE if tool_name in WRITE_TOOL_NAMES else TOOL_KIND_READ


def tool_allows(fields, kind):
    """True when a rule's `tool:` filter accepts a tool call of this kind.

    A rule declaring no filter accepts every call, and so does one whose filter
    could not be read (see `tools_of`). `kind` is None when the caller has no
    tool call in hand — the admin CLI's `which`, or a payload that somehow
    arrived without a tool name — and then the filter is reported rather than
    applied: a rule is never lost to a question nobody asked."""
    kinds = tools_of(fields)
    if not kinds or kind is None:
        return True
    return kind in kinds


def first_matching_glob(globs, targets, deadline=None):
    """The first glob that matches any target, or None.

    `deadline` is a `time.monotonic()` value: past it the search stops and
    answers None, which is why every caller re-checks the clock rather than
    trusting the answer — see `collect_candidates`."""
    for glob in globs:
        if deadline is not None and time.monotonic() > deadline:
            return None
        if any(glob_matches(glob, rel, target) for rel, target in targets):
            return glob
    return None


def applied_glob(fields, targets, kind=None, deadline=None):
    """The glob that makes a rule apply to this tool call, or None.

    Every filter a rule declares is restrictive, and they are ANDed: the call
    must be of a kind the rule accepts, one `glob` must match, and no `exclude`
    may. Cheapest first — the tool filter is a tuple lookup, and a rule it
    rules out never spends any of the matching budget."""
    if not tool_allows(fields, kind):
        return None
    matched = first_matching_glob(globs_of(fields), targets, deadline)
    if matched is None:
        return None
    if first_matching_glob(excludes_of(fields), targets, deadline) is not None:
        return None
    return matched


def collect_candidates(abs_path, real_abs, scopes, tool_name=None):
    """(candidates, legacy_scope_labels) for a touched file.

    A candidate is (scope_dir, label, name, glob, fields) — one per applying
    rule, listing the first glob that matched so provenance stays specific.
    Which rules apply is `applied_glob`'s decision, filters included.

    Every scope gets its own slice of the matching budget, and the clock is
    checked per glob rather than per rule. One scope must not be able to spend
    another's time: a nested scope is consulted before the repository root, so a
    shared budget let a vendored directory full of expensive globs starve the
    root's rules on every single tool call — permanently, since the budget is
    recomputed per call."""
    candidates = []
    legacy = []
    budget_hit = False
    # Converted once, here: the rest of the matching reasons in kinds, so a
    # caller with no tool call in hand (the admin CLI) passes a kind directly
    # and nothing has to invent a tool name to stand for one.
    kind = tool_kind(tool_name) if tool_name else None
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
            glob = applied_glob(fields, targets, kind, deadline)
            if time.monotonic() > deadline:
                # The clock ran out while this rule was being matched, so the
                # answer above was reached without consulting every pattern —
                # an `exclude` may simply never have been read. A half-checked
                # rule is worse than an unchecked one: drop it and stop.
                budget_hit = True
                break
            if glob is not None:
                candidates.append((scope_dir, label, name, glob, fields))
    if budget_hit:
        warn(f"glob matching exceeded its {per_scope:.2f}s per-scope budget; the "
             f"remaining rules of that scope were skipped for this tool call")
    return candidates, legacy
