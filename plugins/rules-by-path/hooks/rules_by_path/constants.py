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

MAX_RULE_CHARS = 4_000  # a rule states constraints; it is not documentation
RULE_WARN_CHARS = 2_000  # `validate` nags above this
MAX_TOTAL_CHARS = 24_000  # ceiling for one injection
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

# How far the context may move on before an already-injected rule is repeated.
# Long-context models drift away from a rule injected hundreds of thousands of
# tokens ago, and a session that never compacts never gets the SessionStart
# reset. `remember_after: never` disables the repeat for one rule.
#
# Tokens are the honest unit: a session that reads three huge files burns 200k
# tokens in 3 tool calls, while one doing 50 tiny greps burns 20k in 50 — the
# call count measures the wrong thing. Calls remain the fallback for when the
# transcript cannot be read, and there is no conversion between the two: no
# faithful tokens-per-call rate exists, and faking precision is worse than
# losing it.
DEFAULT_REMEMBER_TOKENS = 30_000
DEFAULT_REMEMBER_CALLS = 25
REMEMBER_ENV_VAR = "RULES_BY_PATH_REMEMBER_AFTER"
# A bare number below this is read as a leftover from the call-counting era
# (`remember_after: 25`) rather than as an absurdly small token budget.
MIN_REMEMBER_TOKENS = 1_000
# Only the tail of the transcript is read to find the last usage record.
TRANSCRIPT_TAIL_BYTES = 64 * 1024

# The characters a rule file name may carry besides letters and digits. This is
# an allowlist on purpose — see is_valid_rule_name.
RULE_NAME_EXTRA_CHARS = "._-"

LEGACY_NOTICE = (
    "This scope still uses the old rules-map.yml format, so NO rules are being "
    "injected from it. Migrate it by running: "
    f"\"{ADMIN_COMMAND}\" migrate --root <project-root> (or --global). "
    "Tell the user this happened."
)

SESSION_NOTICE = (
    "[rules-by-path] This session has path-scoped rules available. They are "
    "markdown files under `.claude/rules-by-path/` (project) and "
    "`~/.claude/rules-by-path/` (global), and they reach you AUTOMATICALLY: the "
    "moment you touch a file whose glob matches, the rule is injected into your "
    "context. So there is never a reason to open, list, grep or edit those files "
    "yourself — and the recommended setup deny-lists them, so an attempt is "
    "refused rather than answered. To read or change a rule, use the CLI: "
    f"\"{ADMIN_COMMAND}\" list|show|which|add|update, with --root '<repo-root>' "
    "or --global — or the rules-by-path:manage skill, which drives it for you."
)

TRUNCATION_NOTICE = "\n[...rule truncated by the rules-by-path size limit...]"

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
FORGED_FRAMING_TOKENS = (
    RULES_OPEN_TAG,
    RULES_CLOSE_TAG,
    TRUNCATION_NOTICE.strip(),
    "[rules-by-path]",
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
