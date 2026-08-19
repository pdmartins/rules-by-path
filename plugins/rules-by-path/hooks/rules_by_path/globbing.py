"""Glob matching, non-backtracking by construction: a two-pointer matcher
for one path segment and a bottom-up DP across segments. No regex, so no
input — however hostile — can make matching blow up."""

import os


def match_segment(pattern, text):
    """Match one path segment: '*' matches any run of characters within the
    segment, '?' exactly one. Two-pointer algorithm, O(len(pattern)*len(text))
    worst case — never exponential."""
    p = t = 0
    star = -1
    mark = 0
    while t < len(text):
        if p < len(pattern) and (pattern[p] == "?" or pattern[p] == text[t]):
            p += 1
            t += 1
        elif p < len(pattern) and pattern[p] == "*":
            star = p
            p += 1
            mark = t
        elif star >= 0:
            p = star + 1
            mark += 1
            t = mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


def match_path(glob_segments, path_segments):
    """Match '/'-split sequences. A '**' segment matches zero or more segments,
    except as the LAST segment where it requires at least one — `src/api/**`
    means "inside src/api", not "src/api itself".

    Bottom-up DP over (glob index, path index): O(G*T) time, O(T) memory, with
    no backtracking, so no input can make it blow up."""
    n_glob = len(glob_segments)
    n_path = len(path_segments)
    row = [False] * (n_path + 1)  # row[t] == "glob[g:] can match path[t:]"
    row[n_path] = True
    for g in range(n_glob - 1, -1, -1):
        prev = row
        row = [False] * (n_path + 1)
        segment = glob_segments[g]
        if segment == "**":
            if g == n_glob - 1:
                for t in range(n_path + 1):
                    row[t] = t < n_path  # trailing '**' needs at least one segment
            else:
                for t in range(n_path, -1, -1):
                    row[t] = prev[t] or (t < n_path and row[t + 1])
        else:
            for t in range(n_path):
                row[t] = match_segment(segment, path_segments[t]) and prev[t + 1]
            row[n_path] = False
    return row[0]


def glob_matches_path(glob, path):
    """Full match of `path` against `glob`, with the documented conveniences:
    a trailing '/' means the whole directory, and a glob with no metacharacter
    matches itself and anything under it."""
    g = glob.strip()
    if g.startswith("./"):
        g = g[2:]
    if g.endswith("/"):
        g = g.rstrip("/") + "/**"
    segments = [s for s in g.split("/") if s]
    targets = [s for s in path.split("/") if s]
    if not any(ch in g for ch in "*?"):  # plain path: itself or anything under it
        return len(targets) >= len(segments) and targets[:len(segments)] == segments
    return match_path(segments, targets)


def glob_matches(glob, rel_path, abs_path):
    """Check `glob` against a file. `rel_path` is None for the global scope.

    Non-absolute globs match the project-relative path (or the absolute path
    minus the leading '/' in the global scope); globs without '/' also match
    the file's basename, so `*.cs` catches any C# file at any depth.
    """
    g = glob.strip()
    if g.startswith("/"):
        targets = [abs_path]
    else:
        targets = [rel_path if rel_path is not None else abs_path.lstrip("/")]
        if "/" not in g.rstrip("/"):
            targets.append(os.path.basename(abs_path))
    return any(glob_matches_path(g, t) for t in targets)
