---
name: setup
description: >
  One-time setup, health-check or removal for the rules-by-path plugin: verify
  prerequisites, smoke-test the injection hook, optionally apply (or undo) the
  recommended permission hardening, and migrate from a manual pre-plugin
  installation. Use when the user asks to set up, configure, verify, harden,
  troubleshoot or uninstall rules-by-path.
---

# rules-by-path — setup, health-check & removal

Run the steps in order and report a short summary at the end. Ask before any
step that edits the user's settings. If the user asked to **uninstall**, skip
to section 6.

## 1. Prerequisites

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" --help >/dev/null 2>&1 && echo "launcher: ok" || echo "launcher: FAILED — no Python on PATH"
```

- Launcher failing means no `python3`/`python` on PATH. The hook then stays
  silent instead of erroring on every tool call, but no rules are injected.
  Install Python 3.8+ and re-check. Stop here until fixed.
- The plugin is standard-library only — there is no YAML or other dependency to
  install. Frontmatter (and, during `migrate`, a legacy map) is parsed by a
  small built-in parser.

## 2. Smoke-test the hook

```bash
printf '{"tool_name":"Read","tool_input":{"file_path":"%s/setup-probe.txt"},"session_id":"rbp-setup-probe","cwd":"%s"}' "$HOME" "$HOME" \
  | "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path-hook"; echo "exit=$?"
```

Expected: `exit=0` and no output (nothing matches a probe file) — or JSON with
`additionalContext` if a global rule happens to match. A traceback or a
non-zero exit means a broken installation. Clean up afterwards — per-session
state is one `<session_id>.json` file in `$CLAUDE_PLUGIN_DATA/state` when that
variable is set (the normal case for a plugin install), otherwise in
`~/.claude/cache/rules-by-path`:

```bash
rm -f ~/.claude/cache/rules-by-path/rbp-setup-probe.json
[ -n "$CLAUDE_PLUGIN_DATA" ] && rm -f "$CLAUDE_PLUGIN_DATA/state/rbp-setup-probe.json"
```

## 3. Initialize the global scope (optional)

Only if the user wants machine-wide rules:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" init --global
```

Project scopes are created implicitly by the first `add --root <root>`.

## 3b. Reinforcement interval (optional)

A rule is injected in full once per session, then repeated as a one-line
reminder every 25 file-tool calls. On very long contexts a shorter interval keeps
rules present; on short sessions it is noise. To change it globally, add to
`~/.claude/settings.json`:

```json
"env": { "RULES_BY_PATH_REINFORCE_EVERY": "25" }
```

`0` disables reinforcement. A single rule can override it with `reinforce:` in
its frontmatter (a number, or `never`).

## 4. Recommended hardening (ASK FIRST — edits user settings)

By default nothing stops the file tools from reading or editing rule files
directly. That works, but it is weaker: rules should reach context only through
the hook, and a model editing its own rules defeats the purpose. The hardening
adds these to `permissions.deny` in `~/.claude/settings.json`:

```json
"Read(**/.claude/rules-by-path/**)",
"Edit(**/.claude/rules-by-path/**)",
"Grep(**/.claude/rules-by-path/**)"
```

Only these three, and not `Write(...)`/`MultiEdit(...)`: Claude Code's file
permission matcher honors `Read` for reads, `Grep` for greps, and a single
`Edit` rule for *every* file-editing tool — Write, Edit, MultiEdit and
NotebookEdit alike. A separate `Write(...)` or `MultiEdit(...)` deny entry is
not matched and makes Claude Code print a warning at startup, so do not add one.

If the user agrees: read `~/.claude/settings.json`, MERGE these entries into the
existing `permissions.deny` array (create it if absent, keep everything else
untouched, no duplicates), and write it back. Show the user the diff.

Be explicit about the trade-off so it is an informed choice:

- With the hardening, all rule reads and writes go through
  `rules-by-path show` / `add` / `update`, which is what the manage skill uses
  anyway.
- It constrains the *file tools*, not arbitrary subprocesses. It raises the
  bar; it is not a sandbox.
- Removing the plugin later without removing these entries leaves those paths
  unreadable — section 6 undoes it.

## 5. Migrating existing rules

**Rule format.** Rules used to live in a `rules-map.yml` index plus a `rules/`
folder; now each rule is a single markdown file declaring its own glob. A scope
still in the old shape injects NOTHING, and the hook says so in context. Check
and convert both scopes:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" migrate --global
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" migrate --root "<project-root>"
```

`migrate` converts every entry, then removes the legacy map — unless something
could not be converted, in which case it keeps everything and tells you what to
resolve. Run `validate` afterwards.

**Manual (pre-plugin) installation.** Only if the user installed rules-by-path
by hand before there was a plugin:

```bash
ls ~/.claude/hooks/rules-by-path.py ~/.claude/scripts/rules-by-path-admin.py ~/.claude/skills/rules-by-path 2>/dev/null
grep -n '"command".*rules-by-path' ~/.claude/settings.json 2>/dev/null | head
```

If found, with the user's consent: remove from `~/.claude/settings.json` the
`hooks` entries that invoke `~/.claude/hooks/rules-by-path.py` (otherwise rules
inject twice), then delete the old hook/script/skill files.

## 6. Uninstall / undo

Removing the plugin (`/plugin uninstall rules-by-path@rules-by-path`) removes
the hook and the skills, but three things linger. Walk the user through them:

1. **Deny rules** in `~/.claude/settings.json` — if the hardening was applied,
   remove the `**/.claude/rules-by-path/**` entries from `permissions.deny`,
   otherwise those paths stay unreadable with nothing left to serve them.
2. **Cached state** — `rm -rf ~/.claude/cache/rules-by-path` and, if
   `CLAUDE_PLUGIN_DATA` was in play, that plugin's data directory under
   `~/.claude/plugins/data/`.
3. **Rule data** — `~/.claude/rules-by-path/` and each project's
   `.claude/rules-by-path/`. Ask before deleting: this is the user's authored
   content, and it is worth keeping if they may reinstall.

## 7. Final report

Summarize: prerequisites, smoke-test result, global scope initialized or not,
hardening applied/declined/removed, migration performed or not applicable.
Then suggest a first rule: "ask me to add a rule for a folder, e.g. 'when
touching files in src/api, always validate the DTOs'".
