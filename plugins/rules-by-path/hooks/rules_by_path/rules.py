"""Rule files as files: what a rule may be named, how one is read safely,
and how a scope is indexed without reading every body."""

import os
import re
import stat
import unicodedata

from .constants import (LEGACY_MAP_NAME, MAX_FRONTMATTER_BYTES,
                        MAX_RULE_CHARS, MAX_RULE_NAME_CHARS,
                        MAX_RULES_PER_SCOPE, RULE_NAME_EXTRA_CHARS, warn)
from .frontmatter import parse_frontmatter


def derive_rule_name(glob):
    """Default rule filename when `--rule` is not given. A total function: every
    glob yields a usable name.

    It used to drop wildcard segments only at the ENDS, so the most idiomatic
    globs of all produced names the allowlist then refused — `src/**/*.py` came
    out as `src--**--*.py.md` and `add` simply failed. The forms that broke are
    the ones the docs present as the normal path.

        src/**              -> src.md
        src/**/*.py         -> src-py.md
        docs/**/*.md        -> docs-md.md
        *.cs                -> cs.md
        /repos/_hv/**/*.cs  -> repos-hv-cs.md

    A derived name is only ever a fallback. A good rule name is an assertion —
    `handlers-inherit-base.md` — because it is the name a human reads in `list`
    and `which` when deciding which rule to open."""
    words = []
    for segment in glob.strip().strip("/").split("/"):
        if not segment or set(segment) <= {"*"}:
            continue  # a segment that is only wildcards names nothing
        if segment.startswith("*.") and len(segment) > 2:
            segment = segment[2:]  # `*.py` is about py files, not about `*`
        elif segment.lower().endswith(".md") and len(segment) > 3:
            # A glob naming one markdown file: the rule file is markdown too, so
            # `docs/architecture.md` -> `docs-architecture`, not `-architecture-md`.
            segment = segment[:-3]
        words.append(segment)
    name = re.sub(r"[^a-z0-9]+", "-", "-".join(words).lower()).strip("-")
    return (name or "root") + ".md"

def is_valid_rule_name(rule_name):
    """A rule name must be a bounded `*.md` file name built only from letters,
    digits and `._-`.

    An allowlist, not a blocklist, because this name is repository data that
    reaches three dangerous places: a shell (the manage skill runs the CLI with
    the name it read), a filesystem path, and the authenticated injection
    header. A blocklist of ASCII punctuation let both `$(...)`/backticks
    (command substitution, which expands inside double quotes too) and the
    full-width unicode lookalikes of ':' and '|' (header field forgery) through.
    Length matters as well: an unbounded name reaches the filesystem and raises
    OSError instead of failing cleanly.

    Unicode letters stay allowed — a rule named in the user's own language is
    legitimate — and the name is normalized before the check so a macOS
    filesystem handing back a decomposed form still matches."""
    if not isinstance(rule_name, str) or not rule_name.endswith(".md"):
        return False
    if not rule_name or len(rule_name) > MAX_RULE_NAME_CHARS:
        return False
    stem = unicodedata.normalize("NFC", rule_name[:-len(".md")])
    if not stem:
        return False
    return all(ch.isalnum() or ch in RULE_NAME_EXTRA_CHARS for ch in stem)

def read_rule_file(scope_dir, name, body_limit=MAX_RULE_CHARS):
    """Read a rule file safely: name validated, opened without following
    symlinks, must be a regular file.

    Returns (fields, body, truncated) or None. Truncation is reported as a flag
    rather than by appending a marker to the body: the marker then lives in the
    authenticated header, where rule content cannot produce one. A body that
    simply ended with the marker text was otherwise indistinguishable from a
    body this function had cut."""
    if not is_valid_rule_name(name):
        warn(f"invalid rule name, so it is not injected — a rule file name may "
             f"hold only letters, digits and '{RULE_NAME_EXTRA_CHARS}' and must "
             f"end in '.md': {name[:80]!r}")
        return None
    path = os.path.join(scope_dir, name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    read_limit = MAX_FRONTMATTER_BYTES + body_limit + 1
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        warn(f"cannot open rule '{name}' in {scope_dir}: {exc}")
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            warn(f"rule '{name}' is not a regular file; skipped")
            return None
        with os.fdopen(fd, encoding="utf-8", errors="replace") as handle:
            fd = None  # fdopen owns it now
            text = handle.read(read_limit)
    except Exception as exc:
        warn(f"failed reading {path}: {exc}")
        return None
    finally:
        if fd is not None:
            os.close(fd)
    fields, body = parse_frontmatter(text, name)
    # A file that opens like a rule but yields no fields, and filled the whole
    # read window, has a frontmatter with no closing `---` within the limit. The
    # admin refuses to write one this large, so this only happens to a
    # hand-edited file — say so instead of silently treating it as a non-rule.
    if not fields and text.lstrip("﻿").startswith("---") and len(text) >= read_limit:
        warn(f"rule '{name}': frontmatter exceeds {MAX_FRONTMATTER_BYTES} bytes or "
             f"has no closing '---'; not treated as a rule")
    body = body.strip()
    if body_limit <= 0:
        return fields, "", False  # index pass: the caller only wants the frontmatter
    if len(body) > body_limit:
        warn(f"rule '{name}' truncated at {body_limit} chars")
        return fields, body[:body_limit], True
    return fields, body, False

def scope_index(scope_dir):
    """[(name, fields)] for every rule file in a scope, sorted by name.

    Only the frontmatter is read here; the body is read later, and only for the
    rules that actually match. That keeps the per-tool-call cost proportional
    to the number of rules, not to their size."""
    try:
        with os.scandir(scope_dir) as it:
            names = sorted(entry.name for entry in it
                           if entry.name.endswith(".md")
                           and entry.is_file(follow_symlinks=False))
    except OSError as exc:
        warn(f"cannot list {scope_dir}: {exc}")
        return []
    if len(names) > MAX_RULES_PER_SCOPE:
        warn(f"{scope_dir}: {len(names)} rules exceeds the {MAX_RULES_PER_SCOPE} cap")
        names = names[:MAX_RULES_PER_SCOPE]
    entries = []
    for name in names:
        result = read_rule_file(scope_dir, name, body_limit=0)
        # Frontmatter is what makes a file a rule. A plain markdown file that
        # happens to sit in the directory (a README, notes) is not a broken
        # rule — it is simply not a rule, and must not be reported as one.
        if result is not None and result[0]:
            entries.append((name, result[0]))
    return entries

def has_legacy_map(scope_dir):
    return os.path.isfile(os.path.join(scope_dir, LEGACY_MAP_NAME))
