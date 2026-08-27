"""rules-by-path — PreToolUse hook for Claude Code.

When Claude touches a file (Read/Edit/Write/MultiEdit/NotebookEdit), this hook
collects the rules that apply to it and injects them into context via
`hookSpecificOutput.additionalContext`.

A rule is a single markdown file in `.claude/rules-by-path/` that declares the
glob it applies to in its own frontmatter:

    ---
    glob: src/api/**
    remember_again_after: 30k
    ---
    Every endpoint must validate its input.

Scopes: every `.claude/rules-by-path/` from the touched file's directory up to
the filesystem root, plus the global scope, `~/.claude/rules-by-path/`.

What reaches the model is the rule bodies and nothing else:

    <rules-by-path>
    Every endpoint must validate its input.
    ---
    Never log the request body.
    </rules-by-path>

Design constraints:
- Never blocks the tool call: any internal failure goes to stderr and the hook
  exits 0 with no stdout.
- Each rule *version* is injected at most once per session (the dedup key
  includes a hash of the content, so editing a rule re-injects it), then
  repeated in full once the context has moved on by `remember_again_after`.
- Files inside `.claude/rules-by-path/` never trigger injection.
- Rule content is untrusted input, and is not dressed up as anything more
  trustworthy than it is. The emitted text carries no provenance and no
  authentication: a rule file is exactly as trusted as the repository's
  CLAUDE.md, which the harness already injects with no ceremony at all. What
  the plugin does defend is the boundary — content cannot close the block early
  nor impersonate the harness itself (see `neutralize`).
- Glob matching is a non-backtracking segment matcher — no regex, hence no
  catastrophic backtracking on a hostile glob.
- Standard library only. Frontmatter is parsed by a small parser here, so the
  plugin has no YAML dependency and no second parser to drift from.

Rules are managed by the `rules-by-path:manage` skill through the companion
script `scripts/rules-by-path-admin.py` in this plugin.

Layout — one concern per module, none over 400 lines:

    constants.py    every tunable, plus `warn`
    messages.py     the injected text, in every language the plugin ships
    configfile.py   reading one config.json off disk, untrusted
    config.py       config.json: the rule taxonomy and the repeat defaults
    reinject.py     the re-injection budget: its config key and its clamp
    frontmatter.py  the rule header: parsing, globs, remember_again_after
    globbing.py     non-backtracking glob matching
    discovery.py    which scopes apply, and which are safe to read
    rules.py        rule names, reading a rule file, indexing a scope
    matching.py     the touched path, and the rules it matches
    state.py        per-session dedup, context size, repeat scheduling
    context.py      assembling the injected text, defanging forged framing
    main.py         the three entry points Claude Code calls

This module re-exports the surface the admin CLI and the test suite import.
`hooks/rules-by-path.py` is the executable facade that Claude Code runs; it
exists because `hooks.json`, the `bin/` launchers, the admin and the tests all
address the hook by that path.
"""

from .constants import (ADMIN_COMMAND, BRAZILIAN_PORTUGUESE,
                        CONFIG_FILE_NAME, DEFAULT_LANGUAGE,
                        DEFAULT_REMEMBER_AGAIN_CALLS,
                        DEFAULT_REMEMBER_AGAIN_TOKENS,
                        ENFORCE_DENY_REASON_TEMPLATE, FILE_PATH_KEYS,
                        FORGED_FRAMING_TOKENS, HARNESS_MARKER,
                        LANGUAGE_EXTRA_CHARS,
                        LANGUAGE_FORBIDDEN_CHARS, LANGUAGE_KEY,
                        LEGACY_MAP_NAME,
                        LEGACY_NOTICE,
                        LEGACY_REMEMBER_ENV_VAR, MATCH_BUDGET_SECONDS,
                        MAX_ANCESTOR_STEPS, MAX_CONFIG_BYTES,
                        MAX_CONFIGURABLE_REINJECT_BUDGET,
                        MAX_FRONTMATTER_BYTES, MAX_GLOB_CHARS,
                        MAX_GLOBS_PER_RULE, MAX_LANGUAGE_CHARS,
                        MAX_REINJECTIONS_PER_RULE,
                        MAX_RULE_CHARS, MAX_RULE_NAME_CHARS, MAX_RULE_TYPES,
                        MAX_RULES_PER_SCOPE, MAX_SCOPES, MAX_SESSION_ID_CHARS,
                        MAX_TOTAL_CHARS, MAX_TYPE_PREFIX_CHARS,
                        MAX_TYPE_TEXT_CHARS, MIN_CONFIGURABLE_RULE_CHARS,
                        MIN_REMEMBER_AGAIN_CALLS,
                        MIN_REMEMBER_AGAIN_TOKENS, PLUGIN_CONFIG_PATH,
                        PLUGIN_ROOT, REMEMBER_AGAIN_ENV_VAR,
                        RULE_NAME_EXTRA_CHARS, RULE_SEPARATOR, RULE_WARN_CHARS,
                        RULES_CLOSE_TAG, RULES_DIR_RELPATH, RULES_OPEN_TAG,
                        SESSION_NOTICE, STATE_MAX_AGE_SECONDS,
                        STATE_READ_CHUNK_BYTES, SUPERSEDE_NOTICE,
                        TOKEN_REGRESSION_SLACK, TRANSCRIPT_TAIL_BYTES,
                        TRUNCATION_NOTICE, TRUNCATION_NOTICES,
                        WRITE_TOOL_NAMES, warn)
from .messages import (ENFORCE_DENY_REASON_TEMPLATE_KEY,
                       LANGUAGE_NORMAL_FORM, LEGACY_NOTICE_KEY,
                       MESSAGE_KEYS, MESSAGES, SESSION_NOTICE_KEY,
                       SHIPPED_LANGUAGES, SUPERSEDE_NOTICE_KEY,
                       TRUNCATION_NOTICE_KEY, canonical_language,
                       has_translation, messages_for, normalize_language,
                       sanitize_language)
from .frontmatter import (enforce_of, globs_of, parse_frontmatter,
                          parse_remember_again_after, parse_size,
                          remember_again_after_of, unquote)
from .configfile import config_path_for, read_config_file
from .config import (find_rule_type, language, load_config,
                     load_layer, max_rule_chars,
                     remember_again_after_default,
                     remember_again_after_for_type, rule_types, sanitize_config,
                     type_prefixes, warn_rule_chars)
from .reinject import reinject_budget, sanitize_reinject_budget
from .globbing import (glob_matches, glob_matches_path, match_path,
                       match_segment)
from .discovery import (find_scopes, is_safely_owned, scope_is_contained,
                        usable_scope)
from .rules import (derive_rule_name, has_legacy_map, is_valid_rule_name,
                    read_rule_file, scope_index)
from .matching import (collect_candidates, extract_file_path,
                       is_inside_rules_dir, path_targets)
from .state import (cleanup_stale_state, close_state, coerce_int,
                    coerce_seen_entry, context_size, detect_context_regression,
                    is_due, lock_exclusive, open_state, pop_superseded_entries,
                    save_state, state_dir, state_file_for)
from .context import build_context, defang, neutralize
from .main import (build_blocks, cli, config_for_scopes, enforce_denial,
                   main, messages_for_scopes, reset_session, session_notice,
                   trusted_scopes)
