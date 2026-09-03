"""Every tunable of the hook, plus `warn` — the one helper the whole
plugin uses. Kept in one module so a limit is read and changed in a single
place, and so no other module has to import a sibling just to complain."""

import os
import sys


# hooks/rules_by_path/constants.py -> the plugin root is three levels up.
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
ADMIN_COMMAND = os.path.join(PLUGIN_ROOT, "bin", "rules-by-path")

RULES_DIR_RELPATH = os.path.join(".claude", "rules-by-path")
LEGACY_MAP_NAME = "rules-map.yml"
FILE_PATH_KEYS = ("file_path", "notebook_path", "path")
# The only tools `enforce: deny` ever acts on. Read/Grep never write, so an
# enforce rule has nothing to deny them from doing.
WRITE_TOOL_NAMES = ("Write", "Edit", "MultiEdit", "NotebookEdit")
# The two kinds of tool call a rule's `tool:` filter can name. The hook only
# ever runs for the five file tools, so everything that is not a write is a
# read — there is no third kind to grow into.
TOOL_KIND_READ = "read"
TOOL_KIND_WRITE = "write"
TOOL_KINDS = (TOOL_KIND_READ, TOOL_KIND_WRITE)
# Values that say "no restriction" out loud. Recognised so that writing one by
# hand is not reported as a typo, and so the admin CLI has a word for clearing
# a filter (`--tool any`) instead of a magic empty string.
TOOL_KIND_ANY = "any"
TOOL_ANY_VALUES = (TOOL_KIND_ANY, "all")

# How long a rule may be. Both are defaults: `config.json` may set `rule_size`
# per user and per project (see config.py). A rule is resent WHOLE every time it
# is repeated, so length is paid again at every reminder — which is why the hard
# cut exists at all, and why the soft one nags well below it.
MAX_RULE_CHARS = 4_000  # a rule states constraints; it is not documentation
RULE_WARN_CHARS = 2_000  # `validate` nags above this
MAX_TOTAL_CHARS = 24_000  # ceiling for one injection
# A configured limit is clamped to this range. The ceiling is one injection's
# budget: a single rule allowed to exceed it could never be delivered whole. The
# floor is small enough for a one-line rule and large enough not to be a trap.
MIN_CONFIGURABLE_RULE_CHARS = 200
MAX_RULES_PER_SCOPE = 256
# The hook only reads this many bytes to find a rule's closing `---`, so the
# admin must refuse to write a frontmatter larger than this (otherwise a rule it
# accepts becomes invisible here). Sized to hold the maximum a rule may legally
# declare: MAX_GLOBS_PER_RULE globs of up to MAX_GLOB_CHARS each, plus keys.
MAX_FRONTMATTER_BYTES = 8_192
MAX_GLOB_CHARS = 256
MAX_GLOBS_PER_RULE = 16
MAX_RULE_NAME_CHARS = 128
MAX_SESSION_ID_CHARS = 120  # keeps <id>.json inside every filesystem's name limit
MAX_SCOPES = 8  # scopes consulted per tool call
MAX_ANCESTOR_STEPS = 64
# Total wall-clock a single tool call may spend matching globs, divided evenly
# among the scopes that apply. Each glob is polynomial on its own, but a scope
# may declare thousands of them; this bounds the aggregate so a hostile repo
# cannot stall every tool call, and the per-scope split stops one scope from
# spending another's share. Fail-open: when a scope exhausts its slice, its
# remaining rules are simply not consulted for this call.
MATCH_BUDGET_SECONDS = 2.0
STATE_MAX_AGE_SECONDS = 14 * 24 * 3600
# Per-rule usage lives beside the session state, in one file that the stale
# sweep never touches: it is the record that outlives sessions on purpose.
# Every collection in it is capped so it stays a few KB however long it lives.
STATS_FILE_NAME = "usage-stats.json"
STATS_READ_LIMIT_BYTES = 4 * 1024 * 1024
MAX_STATS_RULES = 512
MAX_STATS_DIRS_PER_RULE = 20
MAX_STATS_GLOBS_PER_RULE = 16
MAX_STATS_RECENT_SESSIONS = 16
STATE_READ_CHUNK_BYTES = 64 * 1024  # one read normally swallows the file

# How far the context may move on before an already-injected rule is repeated.
# Long-context models drift away from a rule injected hundreds of thousands of
# tokens ago, and a session that never compacts never gets the SessionStart
# reset. `remember_again_after: never` disables the repeat for one rule.
#
# Tokens are the honest unit: a session that reads three huge files burns 200k
# tokens in 3 tool calls, while one doing 50 tiny greps burns 20k in 50 — the
# call count measures the wrong thing. Calls remain the fallback for when the
# transcript cannot be read, and there is no conversion between the two: no
# faithful tokens-per-call rate exists, and faking precision is worse than
# losing it.
#
# These two are the LAST-RESORT fallback, not the shipped default: the default
# lives in the plugin's config.json, which the user's and the project's own
# config may override (see config.py). They are what the plugin falls back to
# when that file is missing or unreadable, so the hook keeps working with no
# configuration at all.
DEFAULT_REMEMBER_AGAIN_TOKENS = 30_000
DEFAULT_REMEMBER_AGAIN_CALLS = 25
REMEMBER_AGAIN_ENV_VAR = "RULES_BY_PATH_REMEMBER_AGAIN_AFTER"
# The name this setting carried until 0.4.0, still honoured so an installation
# that exported it keeps the behaviour it configured.
LEGACY_REMEMBER_ENV_VAR = "RULES_BY_PATH_REMEMBER_AFTER"
# Floors for the repeat interval. A bare number below the token floor is read as
# a leftover from the call-counting era (`remember_again_after: 25`) rather than
# as an absurdly small token budget; the call floor exists so a config arriving
# with a cloned repository cannot ask for a repeat on nearly every tool call.
MIN_REMEMBER_AGAIN_TOKENS = 1_000
MIN_REMEMBER_AGAIN_CALLS = 5
# Only the tail of the transcript is read to find the last usage record.
TRANSCRIPT_TAIL_BYTES = 64 * 1024

# A token drop bigger than this is compaction/clear having won the race
# against the async SessionStart reset, not ordinary reporting jitter.
TOKEN_REGRESSION_SLACK = 4_096

# How many times a rule may be RE-injected in one session, after its first
# delivery — the first injection is free, only repeats spend this budget. Each
# re-injection adds one more instruction to the pile competing for the model's
# attention, which is itself the variable long-context collapse tracks
# (arXiv:2608.02639), so a rule that would otherwise repeat for the rest of a
# very long session is cut off instead. This is the LAST-RESORT fallback, not
# the shipped default: `config.json`'s `reinject_budget` (see reinject.py) is
# what actually ships, and may be overridden per user and per project.
MAX_REINJECTIONS_PER_RULE = 3
# The hard ceiling `reinject_budget` may be configured to, in EITHER layer.
# Unlike rule_size, there is no direction in which raising this number is safe
# to leave unclamped even for the user's own file — a session repeating one
# rule without limit is exactly the failure this budget exists to prevent — so
# both layers are held to the same range.
MAX_CONFIGURABLE_REINJECT_BUDGET = 20

# The configuration file, looked up in three layers: this plugin's own (the
# shipped default), then `~/.claude/rules-by-path/`, then each project scope.
# It sits beside the rules, and the hook only ever reads `*.md` as a rule, so it
# cannot be mistaken for one.
CONFIG_FILE_NAME = "config.json"
PLUGIN_CONFIG_PATH = os.path.join(PLUGIN_ROOT, CONFIG_FILE_NAME)
MAX_CONFIG_BYTES = 32 * 1024
MAX_RULE_TYPES = 16
# `name` and `purpose` are echoed to a terminal and to the model (by `config`,
# and by the error `add` prints when the type is missing), so they are bounded
# and kept to one printable line — the same treatment a glob gets.
MAX_TYPE_TEXT_CHARS = 120
MAX_TYPE_PREFIX_CHARS = 8

# The characters a rule file name may carry besides letters and digits. This is
# an allowlist on purpose — see is_valid_rule_name.
RULE_NAME_EXTRA_CHARS = "._-"

# The language rules are written in, and the language the text the hook injects
# around them is emitted in. It is a `config.json` key like any other, so a
# project may set it and have its own rules come out in its own language.
#
# The value reaches the model, and arrives from a layer that came with a cloned
# repository, so it is bounded and allowlisted the way a glob is: no newline, no
# colon, no backtick, no angle bracket, and a low ceiling on top of that. What
# the allowlist buys is that the value cannot forge a delimiter, a header line
# or a second line — NOT that 32 characters are too few to word an imperative,
# which they are not, so every place that echoes the value quotes it. Comparison
# ignores case and reads `_` as `-`, so `pt_br` and `PT-BR` select `pt-BR`, and
# the value is NFKC-normalized first so `ｅｎ` is `en` (see messages.py).
LANGUAGE_KEY = "language"
MAX_LANGUAGE_CHARS = 32
LANGUAGE_EXTRA_CHARS = " -_()"
# The alphanumerics that render as nothing at all. `str.isalnum()` is true for
# Unicode category Lo, and `str.isprintable()` is true for them as well, so the
# allowlist above admits them on both counts while a reader sees an empty gap —
# a value that LOOKS like `en` and does not select English. These four are the
# complete set: no other codepoint is alphanumeric, printable, and invisible,
# and NFKC folds every wider spelling of them into one of these.
LANGUAGE_FORBIDDEN_CHARS = "\u115f\u1160\u3164\uffa0"
# The languages the plugin ships a translation of the injected text in, spelled
# canonically. An unlisted language is still a legitimate value — the rules
# themselves are written in it — and only the scaffolding falls back to English.
DEFAULT_LANGUAGE = "en"
BRAZILIAN_PORTUGUESE = "pt-BR"

# How the harness labels this plugin's own output. A marker, not prose: every
# translation of SESSION_NOTICE opens with these exact bytes, and rule content
# is defanged from emitting them (see FORGED_FRAMING_TOKENS).
HARNESS_MARKER = "[rules-by-path]"

LEGACY_NOTICE = (
    "This scope still uses the old rules-map.yml format, so NO rules are being "
    "injected from it. Migrate it by running: "
    f"\"{ADMIN_COMMAND}\" migrate --root <project-root> (or --global). "
    "Tell the user this happened."
)

SESSION_NOTICE = (
    f"{HARNESS_MARKER} This session has path-scoped rules available. They are "
    "markdown files under `.claude/rules-by-path/` (project) and "
    "`~/.claude/rules-by-path/` (global), and they reach you AUTOMATICALLY: the "
    "moment you touch a file whose glob matches, the rule is injected into your "
    "context. So there is never a reason to open, list, grep or edit those files "
    "yourself — and the recommended setup deny-lists them, so an attempt is "
    "refused rather than answered. To read or change a rule, use the CLI: "
    f"\"{ADMIN_COMMAND}\" list|show|which|add|update, with --root '<repo-root>' "
    "or --global — or the rules-by-path:manage skill, which drives it for you."
)

# The truncation notice of every shipped language, indexed by language code.
# The other translated texts live in messages.py; this one lives here, beside
# FORGED_FRAMING_TOKENS, because that list has to defang EVERY language's
# variant regardless of which one is active (see the note there) and is built
# before any configuration has been read. messages.py indexes this mapping, so
# there is still exactly one copy of each string.
TRUNCATION_NOTICES = {
    DEFAULT_LANGUAGE: "\n[...rule truncated by the rules-by-path size limit...]",
    BRAZILIAN_PORTUGUESE:
        "\n[...regra truncada pelo limite de tamanho do rules-by-path...]",
}
TRUNCATION_NOTICE = TRUNCATION_NOTICES[DEFAULT_LANGUAGE]

# Prefixed onto a rule's body when it is injected because the rule was EDITED
# mid-session: the dedup key hashes the body, so a changed rule is injected
# again on its own, but the earlier wording is still sitting in the transcript
# as a now-contradictory instruction — pairwise conflicts between injected
# instructions are a driver of long-context collapse (arXiv:2608.02639), so the
# fresh copy says outright which one to follow.
SUPERSEDE_NOTICE = (
    "This version supersedes any earlier occurrence of this rule in the "
    "conversation."
)

# The reason shown for an `enforce: deny` block. The hook does not validate
# the rule, only the path — the added value is (a) the rule's own text as the
# pedagogical reason a human or model reads for WHY, and (b) not having to
# hand-author a `permissions.deny` entry. `{body}` is filled in already
# defanged (see `neutralize`), so this template itself carries none of the
# rule's untrusted content directly.
ENFORCE_DENY_REASON_TEMPLATE = (
    "rules-by-path: this tool call is blocked by the enforced rule {name!r} "
    "(global scope). Its own text is the reason:\n\n{body}"
)

# The whole of the emitted framing: an opening tag, a closing tag, and a line
# between rules. The tags are not decoration — another injector's document can
# land in the same message right after this one, so without a closing tag there
# is no way to tell where the rules end. The separator is a line rather than a
# blank line because rule bodies contain blank lines.
RULES_OPEN_TAG = "<rules-by-path>"
RULES_CLOSE_TAG = "</rules-by-path>"
RULE_SEPARATOR = "---"

# Framing that rule content must never be able to emit verbatim. Two kinds, and
# the second is the one that matters: this plugin's own tags (content that
# closes the block early would put its text outside it, where it reads as the
# harness talking), and the harness's own markers. Impersonating a rule buys
# the authority of a rule; impersonating Claude Code buys the authority the
# CLAUDE.md is injected with. Only the second is an escalation.
#
# Every shipped truncation notice is listed, not just the active language's:
# defanging only the variant in force would leave the others usable as forged
# framing by a rule that guessed which languages exist. A substitution that
# never matches costs nothing, so the cheap answer is also the safe one.
FORGED_FRAMING_TOKENS = (
    RULES_OPEN_TAG,
    RULES_CLOSE_TAG,
    *(notice.strip() for notice in TRUNCATION_NOTICES.values()),
    HARNESS_MARKER,
    "<system-reminder",
    "</system-reminder",
    "<function_results",
    "<function_calls",
    # How the harness labels a hook's additionalContext when it hands it to the
    # model — observed live: `PreToolUse:Read hook additional context: ...`.
    # Content that emits this claims to be the harness introducing a new block.
    "hook additional context",
)


def warn(message):
    print(f"rules-by-path: {message}", file=sys.stderr)


def coerce_int(value, fallback):
    """`value` as an int, or `fallback` when it is not a number.

    Every number this plugin reads comes from a file it does not control — a
    state file that may have been hand-edited or half-written, a `config.json`
    that arrived with a cloned repository. `OverflowError` is the load-bearing
    member of that triple: `1e400` is valid JSON, `json` reads it as
    `float('inf')`, and `int()` refuses it."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
