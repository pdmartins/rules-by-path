"""The rule file header: parsing it, and reading the settings it declares.

Deliberately the only parser in the plugin — the admin CLI imports this one
rather than carrying a second implementation."""

from .constants import (MAX_GLOB_CHARS, MAX_GLOBS_PER_RULE,
                        MIN_REMEMBER_AGAIN_TOKENS, TOOL_ANY_VALUES,
                        TOOL_KINDS, warn)

# The frontmatter keys that carry a rule's filters. Each is accepted in the
# singular and the plural, because people write both.
GLOB_KEYS = ("glob", "globs")
EXCLUDE_KEYS = ("exclude", "excludes")
TOOL_KEYS = ("tool", "tools")


def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text, source="rule"):
    """Parse the leading `---` block of a rule file.

    Deliberately tiny and strict: `key: value` lines plus `  - item` lines
    under a key. No comments (so a `#` in a glob is literal), no nesting, no
    anchors. The whole point is that there is exactly one parser, with no
    optional dependency that could behave differently — two parsers for the
    same file is how this plugin previously shipped two corruption bugs.

    Returns (fields, body). `fields` maps a key to a string or list of strings.
    """
    # A leading UTF-8 BOM (Notepad, "UTF-8 with BOM", PowerShell Out-File) would
    # make the file not start with `---`, so a perfectly good rule would parse to
    # nothing and be silently ignored. Strip it before the delimiter check.
    if text[:1] == "﻿":
        text = text[1:]
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text

    fields = {}
    current_key = None
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        stripped = raw.strip()
        if stripped.startswith("- ") and current_key is not None:
            item = unquote(stripped[2:])
            if item:
                if not isinstance(fields.get(current_key), list):
                    fields[current_key] = []
                fields[current_key].append(item)
            continue
        if ":" not in stripped:
            warn(f"{source}: frontmatter line not understood: {stripped[:80]!r}")
            current_key = None
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = unquote(value)
        if key in fields:
            # The last one wins, as in YAML — but silently, and that is the
            # problem: two `glob:` lines in a hand-edited rule look like two
            # covered paths and are one.
            warn(f"{source}: frontmatter key {key!r} appears more than once; "
                 f"only the last is used")
        current_key = key
        fields[key] = value if value else []
    return fields, "\n".join(lines[end + 1:])


def declared_values(fields, keys):
    """The raw values a rule declares under the first of `keys` that carries
    any, always as a list. A missing key and an empty one answer the same."""
    for key in keys:
        raw = fields.get(key)
        if raw not in (None, [], ""):
            return raw if isinstance(raw, list) else [raw]
    return []


def glob_list(fields, keys, label, overflow_consequence):
    """The globs a rule declares under `keys`, bounded.

    `glob:` and `exclude:` are both lists of globs answering to the same two
    limits, so they share one reader: a second copy is how one of them
    silently loses a bound. Only the wording differs, because dropping a glob
    and dropping an exclude fail in opposite directions — one rule stops
    reaching a path, the other keeps reaching one it was told to leave alone.
    """
    globs = []
    dropped = 0
    for value in declared_values(fields, keys):
        value = str(value).strip()
        if not value:
            continue
        if len(value) > MAX_GLOB_CHARS:
            warn(f"{label} longer than {MAX_GLOB_CHARS} chars ignored: "
                 f"{value[:64]!r}...")
            continue
        if len(globs) >= MAX_GLOBS_PER_RULE:
            dropped += 1  # kept counting so the warning states how many were lost
            continue
        globs.append(value)
    if dropped:
        warn(f"more than {MAX_GLOBS_PER_RULE} {label} patterns on one rule; "
             f"{dropped} ignored ({overflow_consequence})")
    return globs


def globs_of(fields):
    """The globs a rule declares. `glob` may be a single value or a list; the
    plural `globs` is accepted too, because people will write it."""
    return glob_list(fields, GLOB_KEYS, "glob",
                     "these never match — split the rule or remove some globs")


def excludes_of(fields):
    """The globs that take a rule back. A rule applies when one of its `glob`
    entries matches and NONE of these do — every filter a rule declares is
    restrictive, and they are ANDed.

    Declared like `glob`: one value or a list, singular or plural key."""
    return glob_list(fields, EXCLUDE_KEYS, "exclude",
                     "the rule still injects for the paths they name")


def tool_values_of(fields):
    """Everything a rule's `tool:` key declares, lowercased, in order.

    `tools_of` narrows this to what the hook acts on; `validate` and the admin
    CLI need the raw list so that a value neither of them recognises is
    reported and preserved rather than quietly dropped on the next rewrite."""
    return [str(value).strip().lower()
            for value in declared_values(fields, TOOL_KEYS)
            if str(value).strip()]


def tools_of(fields):
    """The tool kinds a rule restricts itself to — a subset of TOOL_KINDS — or
    () when it declares no restriction at all.

    A value this function does not recognise yields (), exactly like an absent
    key: a filter can only ever NARROW a rule, so a typo in one must not be the
    reason a rule silently stops arriving. `validate` reports it instead, where
    a human is listening. `any`/`all` mean the same thing on purpose, said out
    loud rather than by omission."""
    values = tool_values_of(fields)
    if any(value in TOOL_ANY_VALUES for value in values):
        return ()
    return tuple(kind for kind in TOOL_KINDS if kind in values)


def parse_size(text):
    """An integer with an optional `k`/`M` suffix: `30k`, `1M`, `200000`."""
    text = str(text).strip().lower()
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1_000, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000, text[:-1]
    return int(float(text.strip())) * multiplier


def parse_remember_again_after(raw, source):
    """(value, unit) for a `remember_again_after` setting, or None when unset.

    unit is "tokens" or "calls"; a value of 0 means never repeat.

        remember_again_after: 30k        -> (30000, "tokens")
        remember_again_after: 30000      -> (30000, "tokens")
        remember_again_after: 25 calls   -> (25, "calls")
        remember_again_after: never      -> (0, None)

    Tokens are the default unit because they measure the thing that actually
    causes drift. A bare number below MIN_REMEMBER_AGAIN_TOKENS is refused rather
    than honoured: it is far more likely to be a leftover `remember_again_after:
    25` from when the interval was counted in tool calls than a genuine 25-token
    budget, and silently treating it as tokens would repeat it on every call.
    """
    if raw in (None, [], ""):
        return None
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in ("never", "no", "off", "0"):
        return (0, None)
    unit = "tokens"
    unit_stated = False  # whether the author wrote the unit or left it implied
    if text.endswith(("calls", "call")):
        unit, unit_stated = "calls", True
        text = text.rsplit("call", 1)[0]
    elif text.endswith("c"):
        unit, unit_stated = "calls", True
        text = text[:-1]
    elif text.endswith(("tokens", "token")):
        unit_stated = True
        text = text.rsplit("token", 1)[0]
    try:
        value = parse_size(text)
    except (ValueError, OverflowError):
        # OverflowError is `inf`/`1e400`: int() refuses it, and it is not a
        # ValueError. Letting it escape aborted the whole injection for that
        # tool call — every rule, not just the one carrying the bad value.
        warn(f"{source}: remember_again_after not understood: {str(raw)[:32]!r}")
        return None
    if value <= 0:
        return (0, None)
    if unit == "tokens" and value < MIN_REMEMBER_AGAIN_TOKENS:
        # Two different mistakes, and guessing wrong at the author's expense is
        # what the flag avoids: a bare number that small is almost certainly a
        # call count from the old format, but `500 tokens` says what it means —
        # it is simply below the floor, and accusing it of being a typo sends
        # the reader looking for a mistake they did not make.
        if unit_stated:
            warn(f"{source}: remember_again_after of {value} tokens is below the "
                 f"{MIN_REMEMBER_AGAIN_TOKENS}-token minimum (it would repeat the "
                 f"rule on nearly every call); using the default instead")
        else:
            warn(f"{source}: remember_again_after of {value} tokens looks like a call "
                 f"count from the old format; using the default instead "
                 f"(write '{value} calls' if that is what you meant)")
        return None
    return (value, unit)


def enforce_of(fields):
    """The rule's `enforce:` setting, or None when it declares none, or one
    this function does not recognise.

    Only `deny` is ever recognised, and any other value returns None exactly
    like an absent key — this function never warns, unlike the rest of this
    module. It runs on the hook's hot path (shared with the CLI, per this
    module's own docstring), and a bogus `enforce:` value is `validate`'s to
    report, where a human is actually listening, not something the hook should
    complain about on every single tool call.

    This says only what the frontmatter DECLARES. Whether the declaring scope
    is trusted enough to act on it — the hook only ever honours `deny` from the
    global scope — is a decision the caller makes, not this function."""
    raw = fields.get("enforce")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not isinstance(raw, str):
        return None
    return "deny" if raw.strip().lower() == "deny" else None


def remember_again_after_of(fields):
    """Per-rule override, or None to use the session default.

    `remember_after:` is the name this key carried until 0.4.0 and is still
    honoured, silently: dropping a setting because a hand-written rule uses the
    old spelling would change behaviour for someone who changed nothing. The
    admin's `validate` is where the rename is pointed out, and `migrate`
    rewrites it."""
    raw = fields.get("remember_again_after")
    if raw in (None, [], ""):
        raw = fields.get("remember_after")
    return parse_remember_again_after(raw, "rule")
