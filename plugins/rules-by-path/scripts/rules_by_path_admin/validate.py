"""`validate`: everything that can be said about a scope without changing it.

Notes are advice — a long rule, a shared glob, a rule that looks like it wants
to be split. Errors mean something will not work at all."""

import os
import re
import sys

from .common import (HOOK, LEGACY_INTERVAL_KEY, INTERVAL_KEY, LEGACY_MAP_NAME,
                     MAX_SCANNED_CHILDREN, MAX_SPLIT_SUGGESTIONS,
                     MIN_MENTION_CHARS, OWN_KEYS, other_markdown_in, rules_in,
                     scope_for)
from .config import TYPE_SEPARATOR, config_for, name_convention, split_type_prefix

# Case-insensitive: a rule stating a prohibition needs the opposite
# reinforcement default from one stating a requirement or convention — only
# prohibition-shaped constraints are known to decay under long context
# (arXiv:2604.20911). Advice for a human reading `validate`, never a judgement
# injected by the hook, which never looks at a rule's own text this way.
PROHIBITION_PATTERN = re.compile(
    r"never|do not|don't|must not|forbidden|nunca|não (deve|pode)|proibido",
    re.IGNORECASE)
# A repeat this tight, on a rule with no prohibition language at all, is more
# often a copy-pasted interval than a deliberate choice.
AGGRESSIVE_INTERVAL_TOKENS = 10_000
AGGRESSIVE_INTERVAL_CALLS = 10


def glob_base_dir(glob, anchor):
    """The deepest directory a glob is rooted at, or None.

    `src/Api/**` -> <anchor>/src/Api. Everything from the first segment carrying
    a metacharacter onwards is dropped, because that is where the glob stops
    naming a place and starts describing a set."""
    text = glob.strip()
    segments = []
    for segment in text.strip("/").split("/"):
        if not segment or any(ch in segment for ch in "*?"):
            break
        segments.append(segment)
    if not segments:
        return None
    if text.startswith("/"):
        return "/" + os.path.join(*segments)
    return os.path.join(anchor, *segments) if anchor else None


def targets_one_file(glob):
    """True when the glob names a single file rather than a set of them."""
    text = glob.strip().rstrip("/")
    if any(ch in text for ch in "*?"):
        return False
    return "." in os.path.basename(text)


def split_candidates(name, globs, body, anchor):
    """Notes about constraints that look narrower than the rule that carries them.

    The premise of this plugin is that nothing reaches the context until it is
    relevant, and a rule is the unit of that decision: every file its glob
    matches receives ALL of it. So a rule that mixes "controllers look like X",
    "the DI file looks like Y" and "no file over 300 lines" under `src/Api/**`
    hands two thirds of itself to files that cannot act on it — the first two
    belong in their own rules, with their own globs.

    Deciding that needs judgement, so this only raises the question, and only on
    the signal that is actually checkable: the rule's own text naming a path
    that exists UNDER its glob and is narrower than it. Directory listings stay
    out of the hook — this runs in the CLI, never in the injection path."""
    if not body:
        return []
    notes = []
    for glob in globs:
        if targets_one_file(glob):
            continue
        base = glob_base_dir(glob, anchor)
        if not base or not os.path.isdir(base):
            continue
        declared = {segment.lower() for segment in glob.strip("/").split("/")}
        found = []  # [(name mentioned in the body, glob that would target it)]
        try:
            with os.scandir(base) as entries:
                children = sorted(entries, key=lambda entry: entry.name)[:MAX_SCANNED_CHILDREN]
        except OSError:
            continue
        prefix = glob.strip().split("*")[0].rstrip("/")
        for child in children:
            if child.name.startswith("."):
                continue
            stem = os.path.splitext(child.name)[0]
            if len(stem) < MIN_MENTION_CHARS or stem.lower() in declared:
                continue
            if not re.search(rf"\b{re.escape(stem)}\b", body, re.IGNORECASE):
                continue
            if child.is_dir(follow_symlinks=False):
                found.append((child.name, f"{prefix}/{child.name}/**"))
            else:
                found.append((child.name, f"{prefix}/{child.name}"))
            if len(found) >= MAX_SPLIT_SUGGESTIONS:
                break
        if found:
            notes.append(
                f"{name}: mentions {', '.join(repr(mention) for mention, _ in found)}, "
                f"which live under {glob!r} but are narrower than it. Every file "
                f"matched by a rule receives the WHOLE rule, so a constraint that "
                f"only governs those belongs in its own rule: "
                f"{' / '.join(f'--glob {suggestion!r}' for _, suggestion in found)}")
    return notes


def effective_interval(name, fields, config):
    """(value, unit) this rule would actually repeat at, following the same
    precedence the hook applies: the rule's own `remember_again_after`, else
    its type's default. Returns None when neither says anything — the
    session/global default then applies, and that is a property of the
    session, not of this rule, so there is nothing here worth a note about."""
    own = HOOK.remember_again_after_of(fields)
    if own is not None:
        return own
    prefix, _rest = split_type_prefix(name, config)
    type_default = HOOK.remember_again_after_for_type(config, prefix) if prefix else None
    return HOOK.parse_remember_again_after(type_default, name) if type_default else None


def enforce_notes(name, fields, is_global):
    """Notes about a rule's `enforce:` setting: a value the hook does not
    recognise, or a `deny` declared somewhere the hook will never honour it.

    `enforce: deny` only ever binds from the GLOBAL scope (see
    `HOOK.enforce_denial`): a project rule arrives with whatever repository is
    checked out, and letting it deny the user's own tool calls would be an
    escalation. This is where that trust gate is explained to a human, with
    the way around it — a native deny via `enforce --sync` — spelled out."""
    raw = fields.get("enforce")
    if raw in (None, [], ""):
        return []
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if HOOK.enforce_of(fields) is None:
        return [f"{name}: enforce: {str(raw)[:32]!r} is not understood — only "
                f"'deny' is honoured; ignored"]
    if not is_global:
        return [f"{name}: enforce: deny only takes effect from the GLOBAL "
                f"scope (project rules are untrusted input); the hook ignores "
                f"it here. Run `enforce --sync` to write an equivalent native "
                f"deny into this project's permissions instead"]
    return []


def reinforcement_notes(name, body, fields, config):
    """Notes about a mismatch between what a rule's text asks for and how
    often it is set to repeat (own frontmatter or inherited type default):
    a prohibition with reinforcement off, or a non-prohibition reinforced as
    tightly as one — never an error, since both are legitimate choices."""
    if not body:
        return []
    interval = effective_interval(name, fields, config)
    if interval is None:
        return []
    value, unit = interval
    prohibits = bool(PROHIBITION_PATTERN.search(body))
    if prohibits and not value:
        return [f"{name}: reads like a prohibition but remember_again_after "
                f"is 'never' — only prohibition constraints are known to decay "
                f"under long context; consider giving it a repeat distance"]
    aggressive = (unit == "tokens" and value < AGGRESSIVE_INTERVAL_TOKENS) or \
                 (unit == "calls" and value < AGGRESSIVE_INTERVAL_CALLS)
    if not prohibits and value and aggressive:
        return [f"{name}: repeats every {value} {unit} with no prohibition "
                f"language in its body — requirements and conventions hold up "
                f"without reinforcement; this may be over-treatment"]
    return []


def validate_scope(scope_dir, anchor=None, quiet=False, config=None, is_global=False):
    """Print notes and errors; return the number of errors. Notes are advice
    (a long rule, a shared glob, a rule that looks like it should be split);
    errors mean something will not work.

    `is_global` decides whether an `enforce: deny` rule here would actually be
    honoured by the hook — see `enforce_notes` — and defaults to False so a
    caller that has not been updated to pass it merely loses that one note
    rather than misreporting a global scope as a project one."""
    if not os.path.isdir(scope_dir):
        if not quiet:
            print("(no rules in this scope — nothing to validate)")
        return 0
    problems = []
    notes = []
    if HOOK.has_legacy_map(scope_dir):
        problems.append(f"a legacy {LEGACY_MAP_NAME} is present and is NOT used; "
                        f"run `migrate` to convert it")
    soft_limit = HOOK.warn_rule_chars(config)
    hard_limit = HOOK.max_rule_chars(config)
    rules = rules_in(scope_dir, hard_limit)
    by_glob = {}
    total = 0
    for name, fields, body in rules:
        globs = HOOK.globs_of(fields)
        if not globs:
            problems.append(f"{name}: no glob declared, so it can never be injected")
        for glob in globs:
            by_glob.setdefault(glob, []).append(name)
        if not body:
            problems.append(f"{name}: empty body")
        total += len(body)
        if len(body) > soft_limit:
            notes.append(f"{name}: {len(body)} chars — a rule should state "
                         f"constraints, not document behaviour (soft limit "
                         f"{soft_limit}, truncated at {hard_limit})")
        unknown = set(fields) - OWN_KEYS
        if unknown:
            notes.append(f"{name}: unknown frontmatter key(s): "
                         f"{', '.join(sorted(unknown))}")
        if LEGACY_INTERVAL_KEY in fields and INTERVAL_KEY not in fields:
            notes.append(f"{name}: uses `{LEGACY_INTERVAL_KEY}:`, renamed to "
                         f"`{INTERVAL_KEY}:` in 0.4.0. It is still honoured; "
                         f"`migrate` rewrites it")
        notes.extend(split_candidates(name, globs, body, anchor))
        notes.extend(reinforcement_notes(name, body, fields, config or {}))
        notes.extend(enforce_notes(name, fields, is_global))
    convention = name_convention(config or {})
    off_convention = [name for name, _f, _b in rules
                      if convention and not convention.match(name)]
    if off_convention:
        notes.append(f"name(s) outside the `TYPE{TYPE_SEPARATOR}what-it-asserts.md` "
                     f"convention: {', '.join(off_convention)} — the type prefix "
                     f"({'/'.join(HOOK.type_prefixes(config or {}))}) says what "
                     f"violating the rule costs, and the rest should assert what "
                     f"the rule requires. These still load; renaming is a "
                     f"curation choice")
    others = other_markdown_in(scope_dir)
    if others:
        notes.append(f"ignored (no frontmatter, so not rules): {', '.join(others)}")
    for glob, names in sorted(by_glob.items()):
        if len(names) > 1:
            notes.append(f"{len(names)} rules share the glob {glob!r} "
                         f"({', '.join(names)}) — they all inject together")
    if total > HOOK.MAX_TOTAL_CHARS:
        notes.append(f"rules total {total} chars; one injection is capped at "
                     f"{HOOK.MAX_TOTAL_CHARS}, so a file matching many of them "
                     f"gets the rest on later tool calls")
    for note in notes:
        print(f"note: {note}")
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    if not quiet and not problems:
        print(f"validation ok: {len(rules)} rule(s)")
    return len(problems)


def cmd_validate(args):
    scope_dir, anchor = scope_for(args)
    if validate_scope(scope_dir, anchor, config=config_for(args),
                      is_global=args.use_global):
        sys.exit(1)
