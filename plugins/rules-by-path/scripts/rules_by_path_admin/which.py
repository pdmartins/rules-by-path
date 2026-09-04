"""`which`: which rules cover a path, answered by the hook's own matcher.

The question people actually bring here is "why is my rule not firing?", so
a rule whose glob covers the path but whose filter takes it back is reported
as such rather than left silent."""

import os

from .common import EXCLUDE_KEY, HOOK, TOOL_KEY, fail, rules_in, scope_for

# The three answers `coverage_of` gives about one rule.
COVERAGE_MATCH = "match"
COVERAGE_EXCLUDED = "excluded"
COVERAGE_FILTERED = "filtered"


def restriction_suffix(fields):
    """The parenthesised kind a rule restricts itself to, empty for a rule that
    restricts itself to nothing — so a `which` listing shows at a glance which
    of its matches are conditional."""
    kinds = HOOK.tools_of(fields)
    if len(kinds) != 1:
        return ""
    return f" ({kinds[0]} only)"


def coverage_of(scope_dir, anchor, use_global, path, tool):
    """([(status, rule name, line)], path as shown) — what the hook would do
    with `path` in this scope. A `match` entry is a rule that injects; an
    `excluded` or `filtered` one is a rule whose glob covers the path but whose
    `exclude:` or `tool:` takes it back."""
    if use_global:
        abs_path = os.path.abspath(path)
        rel_path = None
        shown = abs_path
    else:
        abs_path = path if os.path.isabs(path) else os.path.join(anchor, path)
        abs_path = os.path.normpath(abs_path)
        rel_path = os.path.relpath(abs_path, anchor).replace(os.sep, "/")
        if rel_path.startswith(".."):
            fail(f"path outside the root {anchor}: {abs_path}")
        shown = rel_path
    abs_posix = abs_path.replace(os.sep, "/")

    # A folder query must also find globs like 'docs/**', which only match
    # paths INSIDE the folder — probe with a synthetic child.
    looks_like_a_file = "." in os.path.basename(abs_path.rstrip("/"))
    is_dir_query = (os.path.isdir(abs_path) or path.endswith("/")
                    or (not os.path.exists(abs_path) and not looks_like_a_file))
    # The paths the hook matches a glob against, from the hook's own function.
    # This command answers "what will the injection do with this path", so a
    # second, narrower notion of the path here is a wrong answer: the local copy
    # left out the resolved path, and reported "no rule covers" for any file
    # reached through a directory symlink that the hook does inject into.
    targets = HOOK.path_targets(abs_posix,
                                os.path.realpath(abs_path).replace(os.sep, "/"),
                                None if use_global else anchor)
    if is_dir_query:
        targets.append((None if rel_path is None else f"{rel_path.rstrip('/')}/__probe__",
                        f"{abs_posix.rstrip('/')}/__probe__"))

    # The tool is part of the question now that a rule may restrict itself to
    # one kind of call: `--tool write` asks what fires when this path is
    # WRITTEN. Without it the filters are reported rather than applied, so the
    # answer still lists every rule that covers the path.
    kind = None if tool in HOOK.TOOL_ANY_VALUES else tool
    entries = []
    if not os.path.isdir(scope_dir):
        return entries, shown
    for name, fields, _body in rules_in(scope_dir):
        applied = HOOK.applied_glob(fields, targets, kind)
        if applied is not None:
            entries.append((COVERAGE_MATCH, name,
                            f"match: rule {name}{restriction_suffix(fields)}"))
            continue
        # Not applying is the answer people actually come here with — "why
        # is my rule not firing?" — so a rule the glob DOES cover says
        # which filter took it back rather than staying silent.
        covered = HOOK.first_matching_glob(HOOK.globs_of(fields), targets)
        if covered is None:
            continue
        excluded = HOOK.first_matching_glob(HOOK.excludes_of(fields), targets)
        if excluded is not None:
            entries.append((COVERAGE_EXCLUDED, name,
                            f"excluded: rule {name} — {covered!r} covers this "
                            f"path, {EXCLUDE_KEY}: {excluded!r} takes it back"))
        else:
            entries.append((COVERAGE_FILTERED, name,
                            f"filtered: rule {name} — {covered!r} covers this "
                            f"path, but the rule is {TOOL_KEY}: "
                            f"{', '.join(HOOK.tools_of(fields))} only"))
    return entries, shown


def no_coverage_line(entries, shown):
    """The line that closes a coverage report with no match — or None when
    something matched. "covers" and "injects" part company once filters exist:
    a rule listed above covers this path and still will not fire, and saying
    nothing covers it would contradict the line right before."""
    if any(status == COVERAGE_MATCH for status, _name, _line in entries):
        return None
    if entries:
        return f"no rule injects for '{shown}'"
    return f"no rule covers '{shown}'"


def cmd_which(args):
    scope_dir, anchor = scope_for(args)
    entries, shown = coverage_of(scope_dir, anchor, args.use_global, args.path,
                                 args.tool)
    for _status, _name, line in entries:
        print(line)
    closing = no_coverage_line(entries, shown)
    if closing:
        print(closing)
