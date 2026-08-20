"""`enforce`: bridging `enforce: deny` rules to Claude Code's own
`permissions.deny`.

The hook only ever honours `enforce: deny` from the GLOBAL scope (see
`HOOK.enforce_denial` — a project rule is untrusted repository content, and
letting it deny the user's own tool calls would be an escalation). A project
rule that declares `enforce: deny` therefore needs a NATIVE deny entry to
actually block anything: `--list` shows what that entry would be, `--sync`
writes it into the project's own `.claude/settings.json`, reusing the same
merge-don't-duplicate approach `/rules-by-path:setup` uses for the user's
`~/.claude/settings.json`.
"""

import json
import os

from .common import HOOK, atomic_write, fail, rules_in, scope_for

SETTINGS_RELPATH = os.path.join(".claude", "settings.json")


def enforce_rules(scope_dir):
    """[(name, globs)] for every rule in the scope that declares
    `enforce: deny`, in the order `rules_in` already sorts them."""
    return [(name, HOOK.globs_of(fields)) for name, fields, _body in rules_in(scope_dir)
            if HOOK.enforce_of(fields) == "deny"]


def deny_entry_for(glob):
    """The native `permissions.deny` entry one glob translates to: `Edit(...)`
    alone, not a separate `Write(...)`/`MultiEdit(...)` pair.
    `/rules-by-path:setup` already established (verified against Claude Code
    2.1.233, see the README's *Recommended hardening*) that `Edit(...)` is
    consulted for every file-editing tool — Write, Edit, MultiEdit,
    NotebookEdit alike — and a separate `Write(...)` entry is simply never
    matched, only printing a startup warning."""
    return f"Edit({glob})"


def deny_entries(rules):
    """The native `permissions.deny` entries these rules translate to,
    deduplicated but order-preserving."""
    entries = []
    for _name, globs in rules:
        for glob in globs:
            entry = deny_entry_for(glob)
            if entry not in entries:
                entries.append(entry)
    return entries


def read_settings_for_sync(settings_path):
    """The project's `.claude/settings.json` as a dict ready to be mutated in
    place, or `{}` when there is none yet — `--sync` creates a minimal one.

    Unlike `--list`'s read-only `existing_deny_entries`, a file this function
    cannot make sense of is a reason to STOP rather than to proceed as if it
    were empty: silently treating unreadable JSON as "no entries yet" would
    have `--sync` overwrite whatever the human had there with a minimal file
    that has lost it."""
    if not os.path.isfile(settings_path):
        return {}
    if os.path.islink(settings_path):
        fail(f"{settings_path} is a symlink; refusing to write through it")
    try:
        with open(settings_path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        fail(f"cannot read {settings_path}: {exc}")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        fail(f"{settings_path} is not valid JSON ({exc}); fix it by hand, then "
             f"re-run --sync")
    if not isinstance(data, dict):
        fail(f"{settings_path} does not hold a JSON object; refusing to touch it")
    return data


def existing_deny_entries(settings_path):
    """The `permissions.deny` array already on disk, read-only and tolerant:
    `--list` reports status, it must never fail just because the file a human
    hand-edited is momentarily not quite valid JSON."""
    if not os.path.isfile(settings_path):
        return []
    try:
        with open(settings_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    deny = (data or {}).get("permissions", {}).get("deny")
    return deny if isinstance(deny, list) else []


def cmd_enforce_list(scope_dir, anchor, is_global):
    rules = enforce_rules(scope_dir)
    if not rules:
        print("(no enforce: deny rules in this scope)")
        return
    settings_path = os.path.join(anchor, SETTINGS_RELPATH)
    existing = set(existing_deny_entries(settings_path))
    for name, globs in rules:
        print(f"{name}  enforce: deny")
        if not globs:
            print("  (no glob declared — never matches, so never denies)")
            continue
        for glob in globs:
            entry = deny_entry_for(glob)
            if is_global:
                status = "already active via the hook (global scope)"
            elif entry in existing:
                status = f"synced in {settings_path}"
            else:
                status = "NOT synced — run `enforce --sync` to add it"
            print(f"  {entry}  [{status}]")


def cmd_enforce_sync(scope_dir, anchor, is_global):
    if is_global:
        # A global `enforce: deny` rule is already honoured directly by the
        # hook (see HOOK.enforce_denial) — syncing it would write a SECOND,
        # redundant mechanism into a file (~/.claude/settings.json) that has
        # nothing to do with any one project. --sync exists for the case the
        # hook cannot cover: a project rule, whose scope is untrusted.
        fail("'enforce --sync' is for a project scope (--root); a global "
             "enforce: deny rule is already honoured by the hook directly, so "
             "there is nothing to sync. Pass --root <project-root> for the "
             "project whose enforce rules need a native deny of their own")
    rules = enforce_rules(scope_dir)
    entries = deny_entries(rules)
    if not entries:
        print("(no enforce: deny rules to sync)")
        return
    settings_path = os.path.join(anchor, SETTINGS_RELPATH)
    data = read_settings_for_sync(settings_path)
    permissions = data.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        fail(f"{settings_path}: 'permissions' is not an object; fix it by hand")
    deny = permissions.setdefault("deny", [])
    if not isinstance(deny, list):
        fail(f"{settings_path}: 'permissions.deny' is not an array; fix it by hand")
    added = [entry for entry in entries if entry not in deny]
    deny.extend(added)
    if not added:
        print(f"ok: {settings_path} already has every deny entry these rules need")
        return
    atomic_write(settings_path, json.dumps(data, indent=2) + "\n")
    plural = "y" if len(added) == 1 else "ies"
    print(f"ok: {len(added)} new deny entr{plural} written to {settings_path}")
    for entry in added:
        print(f"  {entry}")


def cmd_enforce(args):
    scope_dir, anchor = scope_for(args)
    if args.sync:
        cmd_enforce_sync(scope_dir, anchor, args.use_global)
    else:
        cmd_enforce_list(scope_dir, anchor, args.use_global)
