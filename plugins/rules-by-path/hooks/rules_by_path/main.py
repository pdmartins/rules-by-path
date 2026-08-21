"""The three entry points Claude Code calls: the PreToolUse injection
(`main`), the SessionStart notice, and the state reset on compact/clear."""

import hashlib
import json
import os
import sys
import time

from .constants import (DEFAULT_LANGUAGE, FILE_PATH_KEYS,
                        MATCH_BUDGET_SECONDS, MAX_TOTAL_CHARS,
                        RULES_DIR_RELPATH, WRITE_TOOL_NAMES, warn)
from .config import (language, load_config, max_rule_chars,
                     remember_again_after_default)
from .context import build_context, neutralize
from .messages import (ENFORCE_DENY_REASON_TEMPLATE_KEY, LEGACY_NOTICE_KEY,
                       SESSION_NOTICE_KEY, messages_for)
from .discovery import find_scopes
from .frontmatter import enforce_of, globs_of, remember_again_after_of
from .globbing import glob_matches
from .reinject import reinject_budget
from .rules import has_legacy_map, read_rule_file, scope_index
from .state import (cleanup_stale_state, close_state, context_size,
                    detect_context_regression, is_due, open_state,
                    pop_superseded_entries, save_state, state_file_for)


def extract_file_path(payload):
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in FILE_PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def is_inside_rules_dir(abs_path):
    """True when the path is inside a rules directory — those files must never
    trigger injection. Checked on the resolved path too, so an in-repo symlink
    aliasing the rules directory does not slip past a textual comparison."""
    needle = f"/{RULES_DIR_RELPATH.replace(os.sep, '/')}/"
    candidates = (abs_path, os.path.realpath(abs_path).replace(os.sep, "/"))
    return any(needle in candidate + "/" for candidate in candidates)


def path_targets(abs_path, real_abs, base_dir):
    """[(rel_path, abs_path)] — the paths a glob is matched against.

    The literal path the tool named, plus the resolved one when it still lives
    inside the same project. Matching only the literal text means the same file
    reached through a directory symlink does not get the rule that governs it:
    monorepos routinely carry convenience links (`packages/app/shared ->
    ../../shared`), and a hostile repo could alias a directory precisely to dodge
    a rule. The resolved path is dropped when it leaves the project, so a link
    pointing outside cannot pull in globs from a scope it does not belong to."""
    if base_dir is None:
        targets = [(None, abs_path)]
        if real_abs != abs_path:
            targets.append((None, real_abs))
        return targets
    targets = [(os.path.relpath(abs_path, base_dir).replace(os.sep, "/"), abs_path)]
    if real_abs != abs_path:
        rel_real = os.path.relpath(real_abs, os.path.realpath(base_dir))
        rel_real = rel_real.replace(os.sep, "/")
        if rel_real != ".." and not rel_real.startswith("../"):
            targets.append((rel_real, real_abs))
    return targets


def collect_candidates(abs_path, scopes):
    """(candidates, legacy_scope_labels) for a touched file.

    A candidate is (scope_dir, label, name, glob, fields) — one per matching
    rule, listing the first glob that matched so provenance stays specific.

    Every scope gets its own slice of the matching budget, and the clock is
    checked per glob rather than per rule. One scope must not be able to spend
    another's time: a nested scope is consulted before the repository root, so a
    shared budget let a vendored directory full of expensive globs starve the
    root's rules on every single tool call — permanently, since the budget is
    recomputed per call."""
    candidates = []
    legacy = []
    budget_hit = False
    real_abs = os.path.realpath(abs_path).replace(os.sep, "/")
    per_scope = MATCH_BUDGET_SECONDS / max(1, len(scopes))
    for base_dir, scope_dir, label in scopes:
        deadline = time.monotonic() + per_scope
        if has_legacy_map(scope_dir):
            legacy.append(label)
        targets = path_targets(abs_path, real_abs, base_dir)
        for name, fields in scope_index(scope_dir):
            if time.monotonic() > deadline:
                budget_hit = True
                break
            for glob in globs_of(fields):
                if time.monotonic() > deadline:
                    budget_hit = True
                    break
                if any(glob_matches(glob, rel, target) for rel, target in targets):
                    candidates.append((scope_dir, label, name, glob, fields))
                    break
    if budget_hit:
        warn(f"glob matching exceeded its {per_scope:.2f}s per-scope budget; the "
             f"remaining rules of that scope were skipped for this tool call")
    return candidates, legacy


def config_for_scopes(scopes):
    """The configuration in force for a tool call reaching these scopes.

    The global scope, when there is one, is the first entry and the only
    trusted layer: everything after it is a project scope, whose config arrives
    with whatever repository the touched file belongs to."""
    trusted_count = 1 if scopes and scopes[0][0] is None else 0
    return load_config([scope_dir for _base, scope_dir, _label in scopes],
                       trusted_count)


def messages_for_scopes(scopes):
    """The injected text, in the language these scopes configure.

    Never raises: every caller here emits something the reader needs more than
    they need it in their own language, so an unreadable configuration costs
    the translation and nothing else. `load_layer` already refuses to let one
    layer fail; this is the second belt, on the paths where losing the message
    is the expensive outcome."""
    try:
        return messages_for(language(config_for_scopes(scopes)))
    except Exception as exc:
        warn(f"configuration unreadable ({exc}); falling back to "
             f"{DEFAULT_LANGUAGE}")
        return messages_for(DEFAULT_LANGUAGE)


def trusted_scopes(scopes):
    """The scopes whose `config.json` is the machine owner's own — the global
    one, which `find_scopes` marks by having no base directory.

    Used for the deny reason and nothing else. A project deliberately wins
    `language` everywhere else, so its rules come out in its own language; the
    block reason is the one sentence the plugin speaks on the owner's behalf
    AGAINST a repository, and that repository does not choose the language it
    is refused in."""
    return [scope for scope in scopes if scope[0] is None]


def enforce_denial(tool_name, scopes, candidates):
    """(rule name, rule body) for the first `enforce: deny` rule that should
    block this tool call, or None when nothing should.

    The hook never validates a rule's content, only its path, so `enforce:` is
    not a policy engine — it is "the recommended hardening's `permissions.deny`,
    with the rule's own text as the reason a human or model reads for WHY, and
    without hand-authoring a permission entry".

    Trust gate, deliberately narrower than what merely MATCHED: only a rule
    from the GLOBAL scope may ever deny. A project scope's rules arrive with
    whatever repository is checked out, and honouring `enforce:` there would
    let a cloned repository deny the user's own tool calls — an escalation the
    hook must never grant no matter how the frontmatter is worded. `enforce:`
    on a project-scope rule is simply inert here, silently (no warn on this hot
    path); `validate` is where it is pointed out, with `enforce --sync` as the
    way to turn it into an actual native deny for that project."""
    if tool_name not in WRITE_TOOL_NAMES:
        return None
    if not (scopes and scopes[0][0] is None):
        return None  # no global scope in play this call; nothing to trust
    trusted_scope = scopes[0][1]
    for scope_dir, _label, name, _glob, fields in candidates:
        if scope_dir != trusted_scope or enforce_of(fields) != "deny":
            continue
        result = read_rule_file(scope_dir, name)
        if result is None or not result[1]:
            continue
        return name, result[1]
    return None


def build_blocks(candidates, config, seen, call_number, tokens,
                 default_interval):
    """The deliveries this tool call should inject, in candidate order.

    A candidate is delivered when this session has not seen this exact version
    of it, or when `is_due` says the context has moved far enough since it last
    did. Each delivery is recorded in `seen` as it is appended, so a rule left
    out by the injection budget is retried on the next tool call instead of
    counting as already delivered.
    """
    body_limit = max_rule_chars(config)
    budget = reinject_budget(config)
    blocks = []
    total_chars = 0
    for scope_dir, _label, name, _glob, fields in candidates:
        result = read_rule_file(scope_dir, name, body_limit)
        if result is None:
            continue
        body, was_truncated = result[1], result[2]
        if not body:
            continue
        # The content hash is part of the key so an edited rule counts as a
        # new rule and is injected again, rather than being treated as
        # already delivered for the rest of the session.
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        key = f"{os.path.realpath(scope_dir)}::{name}::{digest}"
        last_seen = seen.get(key)

        if last_seen is None:
            # The first delivery is free: it never counts against the
            # reinjection budget, only the repeats that follow it do.
            text, truncated, reinjections = body, was_truncated, 0
        elif is_due(last_seen, call_number, tokens,
                    remember_again_after_of(fields) or default_interval,
                    budget):
            # Repeating means sending the rule again, whole: with no header
            # there is no way to mark a fragment as one. Short rules are
            # what keeps this cheap.
            text, truncated, reinjections = body, was_truncated, last_seen[2] + 1
        else:
            continue

        if total_chars + len(text) > MAX_TOTAL_CHARS:
            warn(f"injection budget of {MAX_TOTAL_CHARS} chars reached; "
                 f"rule '{name}' left for the next tool call")
            continue
        total_chars += len(text)
        # A fresh digest with an older entry still on file under the same
        # scope+name means the rule was edited: the stale copy is still
        # sitting in the transcript as a contradictory instruction, so the
        # delivery says so and the dead entry is dropped rather than
        # accumulating for the rest of the session. Only checked on a
        # first-time digest — a plain repeat of an already-delivered
        # version is not an edit.
        superseded = last_seen is None and pop_superseded_entries(
            seen, scope_dir, name, digest)
        blocks.append({"name": name, "text": text, "truncated": truncated,
                       "superseded": superseded})
        seen[key] = [call_number, tokens, reinjections]
    return blocks


def main():
    payload = json.load(sys.stdin)
    raw_path = extract_file_path(payload)
    if not raw_path:
        return
    cwd = payload.get("cwd") or os.getcwd()
    abs_path = raw_path if os.path.isabs(raw_path) else os.path.join(cwd, raw_path)
    abs_path = os.path.normpath(abs_path).replace(os.sep, "/")
    if is_inside_rules_dir(abs_path):
        return

    scopes = find_scopes(os.path.dirname(abs_path))
    if not scopes:
        return
    candidates, legacy_scopes = collect_candidates(abs_path, scopes)

    # The denial is decided before any configuration is read, and its wording
    # is then resolved from the trusted layers alone. Both halves are the same
    # rule: whether the machine owner's block fires, and what it says, may not
    # depend on a file that arrived with the repository being blocked.
    denial = enforce_denial(payload.get("tool_name"), scopes, candidates)
    if denial is not None:
        name, body = denial
        template = messages_for_scopes(
            trusted_scopes(scopes))[ENFORCE_DENY_REASON_TEMPLATE_KEY]
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason":
                    template.format(name=name, body=neutralize(body)),
            },
        }))
        return

    # The language is resolved once, here, and travels down by parameter — no
    # module keeps it.
    config = config_for_scopes(scopes)
    messages = messages_for(language(config))

    tokens = context_size(payload)
    default_interval = remember_again_after_default(config, tokens is not None)
    state_path = state_file_for(payload.get("session_id"))
    state_fd, state = open_state(state_path)
    try:
        # Catches the compaction/clear the async SessionStart reset lost the
        # race against: a token count that dropped hard since the last
        # recorded injection means `seen` still thinks the summarized-away
        # text is fresh in context. Must run before any dedup decision below.
        detect_context_regression(state, tokens)
        state["calls"] = state.get("calls", 0) + 1
        call_number = state["calls"]
        seen = state["seen"]

        blocks = build_blocks(candidates, config, seen, call_number, tokens,
                              default_interval)

        # The legacy notice is told once per scope per session. Repeating it on
        # every tool call would be noise the user cannot silence except by
        # migrating, which is exactly what they may not be ready to do yet.
        for label in legacy_scopes:
            key = f"legacy::{label}"
            if key in seen:
                continue
            blocks.append({"name": "legacy-format",
                           "text": messages[LEGACY_NOTICE_KEY]})
            seen[key] = [call_number, tokens]

        if blocks:
            # Emit the injection and flush it BEFORE recording the rules as
            # seen: if the process dies in the window, the worst case is
            # re-injecting a rule (a harmless duplicate) rather than marking it
            # delivered when the model never received it. The design prefers a
            # rare double injection to loss.
            payload_out = json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": build_context(blocks, messages),
                },
                "suppressOutput": True,
            })
            sys.stdout.write(payload_out)
            sys.stdout.flush()
        save_state(state_fd, state)  # advances the call counter either way
    finally:
        close_state(state_fd)
    # Best-effort maintenance, kept off the critical path: it runs after the
    # payload is delivered so a slow directory sweep can never delay or drop it.
    # It must be reached on the far more common no-injection path too — an
    # early `return` there meant the sweep only ever ran as a side effect of a
    # successful injection, so sessions that never matched a rule left their
    # state files behind forever.
    cleanup_stale_state()


def session_notice():
    """SessionStart: say up front that the rules directory is the plugin's
    business, not the agent's.

    Without this the agent meets the directory the only way it can — by listing,
    reading or grepping it — and collects a permission denial for every attempt,
    in every session, because the recommended hardening deny-lists exactly those
    paths. A denial explains nothing, so the attempt repeats in the next session.
    Saying it once, before anything is tried, costs about eighty tokens and only
    in sessions that actually have a scope; the denials cost more than that and
    teach nothing."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    scopes = find_scopes(os.path.abspath(cwd))
    if not scopes:
        return  # no rules anywhere near this session: say nothing at all
    # This path did not read any configuration before the notice had a language
    # to be written in. The cost is one small file per layer, once per session,
    # and only in sessions that have a scope at all — and the notice still goes
    # out in English if that read turns out to be unusable, because a repository
    # silencing this warning is exactly the outcome it exists to prevent.
    messages = messages_for_scopes(scopes)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": messages[SESSION_NOTICE_KEY],
        },
    }))


def reset_session():
    """SessionStart (source compact|clear) mode: drop the session's state so
    rules are re-injected on the next touch — compaction may have summarized
    the injected text away, and /clear discards it entirely."""
    payload = json.load(sys.stdin)
    state_path = state_file_for(payload.get("session_id"))
    if state_path is None:
        return
    try:
        os.unlink(state_path)
    except FileNotFoundError:
        pass


def cli(argv=None):
    """Dispatch by flag, and never exit non-zero: a hook that fails must leave
    the tool call alone."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        if "--reset-session" in argv:
            reset_session()
        elif "--session-notice" in argv:
            session_notice()
        else:
            main()
    except Exception as exc:  # never break the tool call because of this hook
        warn(f"unexpected error: {exc}")
    return 0
