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
python3 -c "import yaml" 2>/dev/null && echo "PyYAML: available" || echo "PyYAML: missing (built-in fallback parser will be used)"
```

- Launcher failing means no `python3`/`python` on PATH. The hook then stays
  silent instead of erroring on every tool call, but no rules are injected.
  Install Python 3.8+ and re-check. Stop here until fixed.
- PyYAML missing is NOT an error: the plugin ships a fallback parser for the
  map format the CLI writes. Only hand-written exotic YAML needs
  `pip install pyyaml`.

## 2. Smoke-test the hook

```bash
printf '{"tool_name":"Read","tool_input":{"file_path":"%s/setup-probe.txt"},"session_id":"rbp-setup-probe","cwd":"%s"}' "$HOME" "$HOME" \
  | "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path-hook"; echo "exit=$?"
```

Expected: `exit=0` and no output (nothing matches a probe file) — or JSON with
`additionalContext` if a global rule happens to match. A traceback or a
non-zero exit means a broken installation. Clean up afterwards:

```bash
rm -f ~/.claude/cache/rules-by-path/rbp-setup-probe.injected
```

## 3. Initialize the global scope (optional)

Only if the user wants machine-wide rules:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" init --global
```

Project scopes are created implicitly by the first `add --root <root>`.

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

## 5. Migration from a manual (pre-plugin) installation

Only relevant if the user installed rules-by-path by hand before. Check:

```bash
ls ~/.claude/hooks/rules-by-path.py ~/.claude/scripts/rules-by-path-admin.py ~/.claude/skills/rules-by-path 2>/dev/null
grep -n '"command".*rules-by-path' ~/.claude/settings.json 2>/dev/null | head
```

If found, with the user's consent: remove from `~/.claude/settings.json` the
`hooks` entries that invoke `~/.claude/hooks/rules-by-path.py` (otherwise rules
inject twice), then delete the old hook/script/skill files. Existing rule DATA
(`~/.claude/rules-by-path/`, `<project>/.claude/rules-by-path/`) needs no
migration — the plugin reads the same locations and format.

## 6. Uninstall / undo

Removing the plugin (`/plugin uninstall rules-by-path@rules-by-path`) removes
the hook and the skills, but three things linger. Walk the user through them:

1. **Deny rules** in `~/.claude/settings.json` — if the hardening was applied,
   remove the `**/.claude/rules-by-path/**` entries from `permissions.deny`,
   otherwise those paths stay unreadable with nothing left to serve them.
2. **Cached state** — `rm -rf ~/.claude/cache/rules-by-path`.
3. **Rule data** — `~/.claude/rules-by-path/` and each project's
   `.claude/rules-by-path/`. Ask before deleting: this is the user's authored
   content, and it is worth keeping if they may reinstall.

## 7. Final report

Summarize: prerequisites, smoke-test result, global scope initialized or not,
hardening applied/declined/removed, migration performed or not applicable.
Then suggest a first rule: "ask me to add a rule for a folder, e.g. 'when
touching files in src/api, always validate the DTOs'".
