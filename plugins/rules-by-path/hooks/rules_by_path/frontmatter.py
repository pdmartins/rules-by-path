"""The rule file header: parsing it, and reading the settings it declares.

Deliberately the only parser in the plugin — the admin CLI imports this one
rather than carrying a second implementation."""

import os

from .constants import (DEFAULT_REMEMBER_CALLS, DEFAULT_REMEMBER_TOKENS,
                        MAX_GLOB_CHARS, MAX_GLOBS_PER_RULE,
                        MIN_REMEMBER_TOKENS, REMEMBER_ENV_VAR, warn)


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


def globs_of(fields):
    """The globs a rule declares. `glob` may be a single value or a list; the
    plural `globs` is accepted too, because people will write it."""
    raw = fields.get("glob")
    if raw in (None, [], ""):
        raw = fields.get("globs")
    if raw in (None, [], ""):
        return []
    values = raw if isinstance(raw, list) else [raw]
    globs = []
    dropped = 0
    for value in values:
        value = str(value).strip()
        if not value:
            continue
        if len(value) > MAX_GLOB_CHARS:
            warn(f"glob longer than {MAX_GLOB_CHARS} chars ignored: {value[:64]!r}...")
            continue
        if len(globs) >= MAX_GLOBS_PER_RULE:
            dropped += 1  # kept counting so the warning states how many were lost
            continue
        globs.append(value)
    if dropped:
        warn(f"more than {MAX_GLOBS_PER_RULE} globs on one rule; {dropped} ignored "
             f"(these never match — split the rule or remove some globs)")
    return globs


def parse_size(text):
    """An integer with an optional `k`/`M` suffix: `30k`, `1M`, `200000`."""
    text = str(text).strip().lower()
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1_000, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000, text[:-1]
    return int(float(text.strip())) * multiplier


def parse_remember_after(raw, source):
    """(value, unit) for a `remember_after` setting, or None when unset.

    unit is "tokens" or "calls"; a value of 0 means never repeat.

        remember_after: 30k        -> (30000, "tokens")
        remember_after: 30000      -> (30000, "tokens")
        remember_after: 25 calls   -> (25, "calls")
        remember_after: never      -> (0, None)

    Tokens are the default unit because they measure the thing that actually
    causes drift. A bare number below MIN_REMEMBER_TOKENS is refused rather than
    honoured: it is far more likely to be a leftover `remember_after: 25` from
    when the interval was counted in tool calls than a genuine 25-token budget,
    and silently treating it as tokens would repeat the rule on every call.
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
    if text.endswith("calls") or text.endswith("call"):
        unit, unit_stated = "calls", True
        text = text.rsplit("call", 1)[0]
    elif text.endswith("c"):
        unit, unit_stated = "calls", True
        text = text[:-1]
    elif text.endswith("tokens") or text.endswith("token"):
        unit_stated = True
        text = text.rsplit("token", 1)[0]
    try:
        value = parse_size(text)
    except (ValueError, OverflowError):
        # OverflowError is `inf`/`1e400`: int() refuses it, and it is not a
        # ValueError. Letting it escape aborted the whole injection for that
        # tool call — every rule, not just the one carrying the bad value.
        warn(f"{source}: remember_after not understood: {str(raw)[:32]!r}")
        return None
    if value <= 0:
        return (0, None)
    if unit == "tokens" and value < MIN_REMEMBER_TOKENS:
        # Two different mistakes, and guessing wrong at the author's expense is
        # what the flag avoids: a bare number that small is almost certainly a
        # call count from the old format, but `500 tokens` says what it means —
        # it is simply below the floor, and accusing it of being a typo sends
        # the reader looking for a mistake they did not make.
        if unit_stated:
            warn(f"{source}: remember_after of {value} tokens is below the "
                 f"{MIN_REMEMBER_TOKENS}-token minimum (it would repeat the "
                 f"rule on nearly every call); using the default instead")
        else:
            warn(f"{source}: remember_after of {value} tokens looks like a call "
                 f"count from the old format; using the default instead "
                 f"(write '{value} calls' if that is what you meant)")
        return None
    return (value, unit)


def remember_after_of(fields):
    """Per-rule override, or None to use the session default."""
    return parse_remember_after(fields.get("remember_after"), "rule")


def remember_after_default(measured_in_tokens):
    """The interval used by rules that declare none, in the unit the session can
    actually measure."""
    override = parse_remember_after(os.environ.get(REMEMBER_ENV_VAR),
                                    REMEMBER_ENV_VAR)
    if override is not None:
        return override
    if measured_in_tokens:
        return (DEFAULT_REMEMBER_TOKENS, "tokens")
    return (DEFAULT_REMEMBER_CALLS, "calls")
