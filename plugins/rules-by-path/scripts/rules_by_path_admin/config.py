"""The configuration the CLI runs under: which layers apply, the rule taxonomy
they declare, and the `config` command that prints the result.

The taxonomy lives in `config.json` — the plugin's default, the user's own, and
the project's — so it is data, not code, and the manage skill reads it from here
rather than carrying a second copy of it."""

import os
import re

from .common import (HOOK, INTERVAL_KEY, RULES_DIR_RELPATH, check_line_value,
                     fail)


# A rule file name is `TYPE_what-it-asserts.md`. The type is chosen by what a
# violation costs, and the taxonomy itself lives in `config.json` — plugin
# default, overridable per user and per project — so changing it is editing one
# file, not this script and the skill's documentation of it. The type never
# reaches the model: it is there so a human reading `list` can see what kind of
# rules a project has accumulated.
#
# Enforced HERE and nowhere else, deliberately. The CLI is strict because there
# is a human to tell; the hook stays permissive because dropping a rule someone
# wrote by hand, over its file name, would be the worst possible behaviour.
TYPE_SEPARATOR = "_"

# The units a `remember_again_after` default can be expressed in, and what each
# one means — printed by `config`, in this order.
INTERVAL_UNIT_LABELS = {"tokens": "when the context size can be measured",
                        "calls": "when only tool calls can be counted"}

# What `config` says about `language`. The second line is the one that earns
# its space: a language the plugin ships no translation of is a legitimate
# setting — the rules are written in it — and only the text the hook wraps
# them in falls back to English. A user should learn that from the CLI rather
# than by noticing English scaffolding around their own rules.
LANGUAGE_LABEL = "the language rule bodies are written in"
LANGUAGE_TRANSLATED = ("the text the hook injects around them is translated "
                       "to it as well")
LANGUAGE_FALLBACK = ("the text the hook injects around them falls back to "
                     "{fallback} — translations shipped: {shipped}")


def config_layers(args):
    """(scope dirs whose config applies, how many of them are trusted).

    A project layer is treated as untrusted — it arrives with whatever
    repository is checked out — so its numbers are clamped. The global layer is
    the user's own configuration and is not."""
    layers = []
    global_scope = os.path.join(os.path.expanduser("~"), RULES_DIR_RELPATH)
    if os.path.isdir(global_scope):
        layers.append(global_scope)
    trusted = len(layers)
    if not args.use_global and args.root:
        layers.append(os.path.join(os.path.abspath(args.root), RULES_DIR_RELPATH))
    return layers, trusted


def config_for(args):
    """The effective config for the scope this command targets: the plugin's
    defaults, then the user's global file, then the project's own."""
    layers, trusted = config_layers(args)
    return HOOK.load_config(layers, trusted)


def name_convention(config):
    """The regex a rule file name should match, or None when the config
    declares no types at all (nothing to check against)."""
    prefixes = HOOK.type_prefixes(config)
    if not prefixes:
        return None
    return re.compile(r"^(?:%s)%s[a-z0-9]+(?:-[a-z0-9]+)*\.md$"
                      % ("|".join(re.escape(p) for p in prefixes),
                         re.escape(TYPE_SEPARATOR)))


def describe_types(config):
    """The configured types, one per line — printed whenever the CLI has to ask
    a human to choose one."""
    lines = []
    for entry in HOOK.rule_types(config):
        interval = entry.get(INTERVAL_KEY)
        suffix = f" [repeat: {interval}]" if interval else ""
        lines.append(f"  {entry['prefix']}  {entry['name']} — "
                     f"{entry['purpose']}{suffix}")
    return "\n".join(lines) or "  (no rule types configured)"


def split_type_prefix(name, config):
    """(canonical prefix, rest) for a rule name that starts with a configured
    type, else (None, name). Matched case-insensitively so `busn_x.md` is
    recognised, and reported canonically so the file is written `BUSN_x.md`."""
    head, separator, rest = name.partition(TYPE_SEPARATOR)
    if not separator or not rest:
        return None, name
    entry = HOOK.find_rule_type(config, head)
    if not entry:
        return None, name
    return entry["prefix"], rest


def resolve_type(config, requested, name):
    """(prefix, name) for a rule being created: the type is taken from --type,
    from the name's own prefix, or — when neither says — refused.

    Refusing is the point. The type is a judgement about what violating the rule
    costs, and this is the only moment in the whole system when a human is
    present to make it; guessing here is what produced scopes full of rules
    nobody can triage."""
    from_name, rest = split_type_prefix(name, config)
    prefix = None
    if requested:
        entry = HOOK.find_rule_type(config, requested)
        if not entry:
            fail(f"unknown rule type {requested[:16]!r}. Configured types:\n"
                 f"{describe_types(config)}")
        prefix = entry["prefix"]
    if from_name and prefix and from_name != prefix:
        fail(f"--type {prefix} contradicts the name {name!r}, which already "
             f"declares {from_name}; pass one or the other")
    prefix = prefix or from_name
    if not prefix:
        fail(f"a rule needs a type: pass --type, or name the file "
             f"TYPE{TYPE_SEPARATOR}what-it-asserts.md. Configured types:\n"
             f"{describe_types(config)}\n"
             f"The type says what violating the rule costs — ask the user which "
             f"one it is rather than guessing.")
    return prefix, f"{prefix}{TYPE_SEPARATOR}{rest}"


def check_remember_again_after(value):
    """Refuse a `remember_again_after` the hook would not honour.

    The CLI is strict where the hook is permissive: a value written by hand into
    a rule file still loads (silently dropping someone's rule over a typo in an
    optional field would be the worst outcome), but a value passed to `add` or
    `update` is checked here, while there is still a human to tell."""
    if not value:
        return
    check_line_value(INTERVAL_KEY, value)  # no smuggled frontmatter line
    if HOOK.parse_remember_again_after(value, INTERVAL_KEY) is None:
        fail(f"{INTERVAL_KEY} not understood: {str(value)[:40]!r} — use tokens "
             f"('30k', '30000'), calls ('25 calls'), or 'never'")


def cmd_config(args):
    """Print the configuration in force for this scope, and where each part of
    it came from. This is how the manage skill learns the rule types: they are
    configuration, so nothing may carry a second copy of them."""
    config = config_for(args)
    sources = config.get("sources", {})
    plugin_config = HOOK.PLUGIN_CONFIG_PATH

    print("rule types:")
    print(describe_types(config))
    origin = sources.get("rule_types", plugin_config)
    print(f"  (from {origin})")

    print(f"\n{INTERVAL_KEY} defaults, for rules that declare none:")
    for unit, label in INTERVAL_UNIT_LABELS.items():
        value = config.get(INTERVAL_KEY, {}).get(unit)
        shown = value if value else "(built-in fallback)"
        where = sources.get(f"{INTERVAL_KEY}.{unit}", "built in")
        print(f"  {unit}: {shown}  — {label} (from {where})")

    print("\nrule size:")
    for key, label in (("max_chars", "hard cut: text past this never reaches the model"),
                       ("warn_chars", "soft limit: the CLI says a rule is getting long")):
        value = config.get("rule_size", {}).get(key)
        shown = value if value else "(built-in fallback)"
        where = sources.get(f"rule_size.{key}", "built in")
        print(f"  {key}: {shown}  — {label} (from {where})")

    chosen = HOOK.language(config)
    print(f"\n{HOOK.LANGUAGE_KEY}:")
    # Quoted, like `validate` quotes it: the value can come from a project
    # layer that arrived with a cloned repository, and 32 allowlisted characters
    # are enough to word a short imperative. Quoting keeps it visibly a value
    # being reported rather than a sentence in the CLI's own voice.
    print(f"  {chosen!r}  — {LANGUAGE_LABEL} "
          f"(from {sources.get(HOOK.LANGUAGE_KEY, plugin_config)})")
    if HOOK.has_translation(chosen):
        print(f"  {LANGUAGE_TRANSLATED}")
    else:
        print("  " + LANGUAGE_FALLBACK.format(
            fallback=HOOK.DEFAULT_LANGUAGE,
            shipped=", ".join(HOOK.SHIPPED_LANGUAGES)))

    print("\nlayers, nearest last:")
    print(f"  {plugin_config}")
    layers, _trusted = config_layers(args)
    for scope_dir in layers:
        path = HOOK.config_path_for(scope_dir)
        print(f"  {path}" if os.path.isfile(path) else f"  {path}  (absent)")
