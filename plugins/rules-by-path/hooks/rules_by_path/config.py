"""The configuration file: the rule taxonomy and the repeat defaults.

`config.json` is read from three layers, each overriding the one before it:

    <plugin>/config.json                        the shipped default
    ~/.claude/rules-by-path/config.json         the user's own
    <project>/.claude/rules-by-path/config.json one per project scope, nearest last

`rule_types` is replaced WHOLE by the nearest layer that declares it — merging
two taxonomies by prefix would produce a hybrid nobody wrote. Everything else
merges key by key.

A project layer arrives with a cloned repository, so it is treated as untrusted
input: its numbers are clamped to the floors in `constants.py` and its texts are
bounded, exactly like a glob or a rule body. Nothing here may break injection —
an unreadable or nonsensical layer is warned about and skipped, and the plugin
keeps running on the layer below it.
"""

import os

from .constants import (DEFAULT_LANGUAGE,
                        DEFAULT_REMEMBER_AGAIN_CALLS,
                        DEFAULT_REMEMBER_AGAIN_TOKENS, LANGUAGE_KEY,
                        LEGACY_REMEMBER_ENV_VAR,
                        MAX_RULE_CHARS, MAX_RULE_TYPES,
                        MAX_TOTAL_CHARS, MAX_TYPE_PREFIX_CHARS,
                        MAX_TYPE_TEXT_CHARS, MIN_CONFIGURABLE_RULE_CHARS,
                        MIN_REMEMBER_AGAIN_CALLS, PLUGIN_CONFIG_PATH,
                        REMEMBER_AGAIN_ENV_VAR, RULE_WARN_CHARS, warn)
from .configfile import config_path_for, read_config_file
from .frontmatter import parse_remember_again_after
from .messages import sanitize_language
from .reinject import sanitize_reinject_budget

REMEMBER_UNITS = ("tokens", "calls")
RULE_SIZE_KEYS = ("max_chars", "warn_chars")


def clean_text(value, label, source):
    """One bounded, printable line, or None. Same shape of check `check_glob`
    makes in the admin: this text is echoed to a terminal and to the model."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not text.isprintable():
        warn(f"{source}: {label} must be one printable line; entry ignored")
        return None
    if len(text) > MAX_TYPE_TEXT_CHARS:
        warn(f"{source}: {label} longer than {MAX_TYPE_TEXT_CHARS} chars; truncated")
        text = text[:MAX_TYPE_TEXT_CHARS]
    return text


def clean_prefix(value, source):
    """A type prefix becomes part of a rule's file name, so it may hold only
    ASCII letters and digits — the allowlist `is_valid_rule_name` enforces one
    level down. Upper-cased so `busn` and `BUSN` are the same type."""
    if not isinstance(value, str):
        return None
    prefix = value.strip().upper()
    if not prefix or len(prefix) > MAX_TYPE_PREFIX_CHARS:
        warn(f"{source}: type prefix must be 1-{MAX_TYPE_PREFIX_CHARS} characters; "
             f"entry ignored")
        return None
    if not prefix.isascii() or not prefix.isalnum() or not prefix[0].isalpha():
        warn(f"{source}: type prefix {value[:16]!r} must be ASCII letters and "
             f"digits, starting with a letter; entry ignored")
        return None
    return prefix


def clean_interval(value, source, label, trusted):
    """A `remember_again_after` value as the string the CLI will write into a
    rule, or None when it is not usable.

    The parser already refuses a token budget below the floor. The call floor is
    applied HERE and only to an untrusted layer: `1 calls` is a legitimate thing
    for the machine's owner to write and a way for a cloned repository to have
    its rules repeated on nearly every tool call."""
    if value is None:
        return None
    text = str(value).strip()
    parsed = parse_remember_again_after(text, f"{source}: {label}")
    if parsed is None:
        return None
    amount, unit = parsed
    if unit == "calls" and not trusted and amount < MIN_REMEMBER_AGAIN_CALLS:
        warn(f"{source}: {label} of {amount} calls is below the "
             f"{MIN_REMEMBER_AGAIN_CALLS}-call floor for a project config; "
             f"using {MIN_REMEMBER_AGAIN_CALLS} calls")
        return f"{MIN_REMEMBER_AGAIN_CALLS} calls"
    return text


def sanitize_rule_types(raw, source, trusted):
    """The taxonomy a layer declares, or None when it declares none usable."""
    if not isinstance(raw, list):
        warn(f"{source}: 'rule_types' must be a list; ignored")
        return None
    types = []
    seen = set()
    for entry in raw:
        if len(types) >= MAX_RULE_TYPES:
            warn(f"{source}: more than {MAX_RULE_TYPES} rule types; the rest ignored")
            break
        if not isinstance(entry, dict):
            warn(f"{source}: a rule type must be an object; entry ignored")
            continue
        prefix = clean_prefix(entry.get("prefix"), source)
        name = clean_text(entry.get("name"), "'name'", source)
        purpose = clean_text(entry.get("purpose"), "'purpose'", source)
        if not prefix or not name or not purpose:
            continue
        if prefix in seen:
            warn(f"{source}: rule type {prefix!r} declared twice; the second is ignored")
            continue
        seen.add(prefix)
        clean = {"prefix": prefix, "name": name, "purpose": purpose}
        interval = clean_interval(entry.get("remember_again_after"), source,
                                  f"rule type {prefix}", trusted)
        if interval:
            clean["remember_again_after"] = interval
        types.append(clean)
    return types or None


def sanitize_legacy_prefixes(raw, source):
    """{old prefix: new prefix} used by `migrate` to rename rule files."""
    if not isinstance(raw, dict):
        warn(f"{source}: 'legacy_type_prefixes' must be an object; ignored")
        return None
    mapping = {}
    for old, new in list(raw.items())[:MAX_RULE_TYPES]:
        if not isinstance(old, str) or not old.strip().isalnum():
            warn(f"{source}: legacy prefix {str(old)[:16]!r} is not a plain word; ignored")
            continue
        target = clean_prefix(new, source)
        if target:
            mapping[old.strip()] = target
    return mapping or None


def sanitize_remember_again_after(raw, source, trusted):
    """The default pair: one value for a session whose context size is
    measurable, one for a session where only tool calls can be counted."""
    if not isinstance(raw, dict):
        warn(f"{source}: 'remember_again_after' must be an object with 'tokens' "
             f"and/or 'calls'; ignored")
        return None
    defaults = {}
    for unit in REMEMBER_UNITS:
        if unit not in raw:
            continue
        value = clean_interval(raw.get(unit), source, f"'{unit}'", trusted)
        if value is None:
            continue
        parsed = parse_remember_again_after(value, source)
        # No conversion between the units exists, so an entry filed under the
        # wrong one is dropped rather than reinterpreted: `"tokens": "25 calls"`
        # would otherwise silently become the token default of a session that
        # can measure tokens perfectly well.
        if parsed and parsed[0] and parsed[1] != unit:
            warn(f"{source}: '{unit}' is set to {value!r}, which is not a "
                 f"{unit} value; ignored")
            continue
        defaults[unit] = value
    return defaults or None


def sanitize_rule_size(raw, source, trusted):
    """How long a rule may be: `max_chars` is the hard cut the hook makes,
    `warn_chars` the soft limit the CLI nags at.

    An untrusted layer may only make a rule SHORTER than the built-in maximum.
    Raising the cut is the one direction that costs the reader: a repository
    could otherwise ship a 20,000-character rule and have all of it repeated
    into the context of everyone who clones it."""
    if not isinstance(raw, dict):
        warn(f"{source}: 'rule_size' must be an object with 'max_chars' and/or "
             f"'warn_chars'; ignored")
        return None
    sizes = {}
    for key in RULE_SIZE_KEYS:
        if key not in raw:
            continue
        try:
            value = int(raw[key])
        except (TypeError, ValueError, OverflowError):
            warn(f"{source}: '{key}' must be a whole number of characters; ignored")
            continue
        ceiling = MAX_TOTAL_CHARS if trusted else MAX_RULE_CHARS
        clamped = max(MIN_CONFIGURABLE_RULE_CHARS, min(value, ceiling))
        if clamped != value:
            warn(f"{source}: '{key}' of {value} is outside "
                 f"{MIN_CONFIGURABLE_RULE_CHARS}-{ceiling}; using {clamped}")
        sizes[key] = clamped
    if sizes.get("warn_chars") and sizes.get("max_chars") \
            and sizes["warn_chars"] > sizes["max_chars"]:
        # A soft limit above the hard cut can never fire: the text is gone
        # before anyone is warned about it.
        sizes["warn_chars"] = sizes["max_chars"]
    return sizes or None


def sanitize_config(raw, source, trusted=True):
    """One layer, validated and bounded.

    A key this function does not know is dropped in silence — the hook must
    never argue with a file it merely reads; `validate` is where an unknown key
    is reported, because that is where a human is listening."""
    layer = {}
    if not isinstance(raw, dict):
        return layer
    if "rule_types" in raw:
        types = sanitize_rule_types(raw.get("rule_types"), source, trusted)
        if types:
            layer["rule_types"] = types
    if "legacy_type_prefixes" in raw:
        mapping = sanitize_legacy_prefixes(raw.get("legacy_type_prefixes"), source)
        if mapping:
            layer["legacy_type_prefixes"] = mapping
    if "remember_again_after" in raw:
        defaults = sanitize_remember_again_after(
            raw.get("remember_again_after"), source, trusted)
        if defaults:
            layer["remember_again_after"] = defaults
    if "rule_size" in raw:
        sizes = sanitize_rule_size(raw.get("rule_size"), source, trusted)
        if sizes:
            layer["rule_size"] = sizes
    if "reinject_budget" in raw:
        budget = sanitize_reinject_budget(raw.get("reinject_budget"), source)
        if budget is not None:
            layer["reinject_budget"] = budget
    if LANGUAGE_KEY in raw:
        chosen = sanitize_language(raw.get(LANGUAGE_KEY), source)
        if chosen:
            layer[LANGUAGE_KEY] = chosen
    return layer


def load_layer(path, trusted):
    """One layer, validated, or {} when there is nothing usable in it.

    The blanket guard is this module's docstring promise made real, and it is
    load-bearing rather than defensive habit: a layer is data that arrived with
    a repository, and the hook decides an `enforce: deny` on the way past here.
    An exception escaping one layer would therefore not merely cost that
    layer's settings — it would cancel the injection AND the denial, turning an
    unreadable file into a way to switch the machine owner's own block off.
    `1e400` is valid JSON, `json` reads it as `float('inf')`, and `int()` of
    that raises OverflowError: a per-key list of expected exception types will
    always be one such case behind, so the last word is taken here."""
    raw = read_config_file(path)
    if raw is None:
        return {}
    try:
        return sanitize_config(raw, path, trusted)
    except Exception as exc:
        warn(f"{path}: unusable configuration ({exc}); ignored")
        return {}


def merge_layer(config, layer, source):
    """Apply one layer over the accumulated config, recording where each value
    came from so `config` can report it. `rule_types` is replaced whole; the
    dict-shaped keys merge key by key."""
    sources = config["sources"]
    for key, value in layer.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            for sub_key, sub_value in value.items():
                config[key][sub_key] = sub_value
                sources[f"{key}.{sub_key}"] = source
        else:
            config[key] = value
            sources[key] = source


def load_config(scope_dirs=(), trusted_count=0):
    """The effective configuration.

    `scope_dirs` are applied in order, after the plugin's own file — pass them
    the way `find_scopes` returns them (global first, then project scopes from
    the outermost down to the file's own), so the nearest scope wins.
    `trusted_count` is how many of the leading entries are the user's own: the
    rest carry a repository's content and are clamped."""
    config = {"rule_types": [], "legacy_type_prefixes": {},
              "remember_again_after": {}, "rule_size": {},
              LANGUAGE_KEY: DEFAULT_LANGUAGE, "sources": {}}
    merge_layer(config, load_layer(PLUGIN_CONFIG_PATH, True), PLUGIN_CONFIG_PATH)
    for index, scope_dir in enumerate(scope_dirs):
        path = config_path_for(scope_dir)
        merge_layer(config, load_layer(path, index < trusted_count), path)
    return config


def rule_types(config):
    return config.get("rule_types") or []


def type_prefixes(config):
    return tuple(entry["prefix"] for entry in rule_types(config))


def find_rule_type(config, prefix):
    """The type declared under `prefix`, matched case-insensitively, or None."""
    if not isinstance(prefix, str):
        return None
    wanted = prefix.strip().upper()
    for entry in rule_types(config):
        if entry["prefix"] == wanted:
            return entry
    return None


def remember_again_after_for_type(config, prefix):
    """What a new rule of this type should declare, as the string to write into
    its frontmatter, or None when the type sets no default of its own."""
    entry = find_rule_type(config, prefix)
    return entry.get("remember_again_after") if entry else None


def remember_again_after_default(config, measured_in_tokens):
    """(value, unit) for rules that declare no interval, in the unit this
    session can actually measure.

    An explicit environment variable wins over every layer: it is the way to
    change the interval for one session without editing anyone's file."""
    for variable in (REMEMBER_AGAIN_ENV_VAR, LEGACY_REMEMBER_ENV_VAR):
        override = parse_remember_again_after(os.environ.get(variable), variable)
        if override is not None:
            return override
    unit = "tokens" if measured_in_tokens else "calls"
    configured = (config or {}).get("remember_again_after", {}).get(unit)
    parsed = parse_remember_again_after(configured, f"config '{unit}'")
    # A configured `never` (0, None) is honoured for either unit — it says "do
    # not repeat", which needs no unit at all. Anything else has to be stated in
    # the unit this session measures in, since no conversion between them exists.
    if parsed is not None and (parsed[0] == 0 or parsed[1] == unit):
        return parsed
    if measured_in_tokens:
        return (DEFAULT_REMEMBER_AGAIN_TOKENS, "tokens")
    return (DEFAULT_REMEMBER_AGAIN_CALLS, "calls")


def max_rule_chars(config):
    """Where a rule body is cut. Everything past this never reaches the model,
    so it is also the number `validate` and `show` reason about."""
    return (config or {}).get("rule_size", {}).get("max_chars") or MAX_RULE_CHARS


def warn_rule_chars(config):
    """Where the CLI starts saying a rule is too long to be repeated cheaply."""
    return (config or {}).get("rule_size", {}).get("warn_chars") or RULE_WARN_CHARS


def language(config):
    """The language rule bodies are written in, and the language the hook
    injects its own text in when it ships a translation of it.

    Always usable: an absent, unreadable or rejected setting leaves the shipped
    default in place, because no answer here may end with the hook injecting
    nothing."""
    return (config or {}).get(LANGUAGE_KEY) or DEFAULT_LANGUAGE
