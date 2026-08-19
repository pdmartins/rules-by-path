"""The three entry points Claude Code calls: the PreToolUse injection
(`main`), the SessionStart notice, and the state reset on compact/clear."""

import hashlib
import json
import os
import sys
import time

from .constants import (FILE_PATH_KEYS, LEGACY_NOTICE, MATCH_BUDGET_SECONDS,
                        MAX_TOTAL_CHARS, RULES_DIR_RELPATH, SESSION_NOTICE,
                        warn)
from .context import build_context
from .discovery import find_scopes
from .frontmatter import globs_of, remember_after_default, remember_after_of
from .globbing import glob_matches
from .rules import has_legacy_map, read_rule_file, scope_index
from .state import (cleanup_stale_state, close_state, context_size, is_due,
                    open_state, save_state, state_file_for)


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
    for candidate in (abs_path, os.path.realpath(abs_path).replace(os.sep, "/")):
        if needle in candidate + "/":
            return True
    return False


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

    tokens = context_size(payload)
    default_interval = remember_after_default(tokens is not None)
    state_path = state_file_for(payload.get("session_id"))
    state_fd, state = open_state(state_path)
    try:
        state["calls"] = state.get("calls", 0) + 1
        call_number = state["calls"]
        seen = state["seen"]

        blocks = []
        total_chars = 0
        for scope_dir, label, name, glob, fields in candidates:
            result = read_rule_file(scope_dir, name)
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
                text, truncated = body, was_truncated
            elif is_due(last_seen, call_number, tokens,
                        remember_after_of(fields) or default_interval):
                # Repeating means sending the rule again, whole: with no header
                # there is no way to mark a fragment as one. Short rules are
                # what keeps this cheap.
                text, truncated = body, was_truncated
            else:
                continue

            if total_chars + len(text) > MAX_TOTAL_CHARS:
                warn(f"injection budget of {MAX_TOTAL_CHARS} chars reached; "
                     f"rule '{name}' left for the next tool call")
                continue
            total_chars += len(text)
            blocks.append({"name": name, "text": text, "truncated": truncated})
            seen[key] = [call_number, tokens]

        # The legacy notice is told once per scope per session. Repeating it on
        # every tool call would be noise the user cannot silence except by
        # migrating, which is exactly what they may not be ready to do yet.
        for label in legacy_scopes:
            key = f"legacy::{label}"
            if key in seen:
                continue
            blocks.append({"name": "legacy-format", "text": LEGACY_NOTICE})
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
                    "additionalContext": build_context(blocks),
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
    if not find_scopes(os.path.abspath(cwd)):
        return  # no rules anywhere near this session: say nothing at all
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": SESSION_NOTICE,
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
