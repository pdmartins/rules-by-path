---
name: setup
description: >
  One-time setup / health-check for the rules-by-path plugin: verify
  prerequisites (python3, optional PyYAML), smoke-test the injection hook,
  optionally apply the recommended permission hardening, and migrate from a
  manual (pre-plugin) installation. Use when the user asks to set up,
  configure, verify, harden or troubleshoot rules-by-path.
---

# rules-by-path — setup & health-check

Run the steps in order and report a short summary at the end. Ask before any
step that edits the user's settings.

## 1. Prerequisites

```bash
python3 --version
python3 -c "import yaml" 2>/dev/null && echo "PyYAML: available" || echo "PyYAML: missing (fallback parser will be used — fine for maps managed by the admin script)"
```

- `python3` missing → the hook cannot run. On Windows, install Python from
  python.org or the Store (both provide `python3`). Stop here until fixed.
- PyYAML missing is NOT an error: the plugin ships a fallback parser for the
  exact map format the admin script writes. Only hand-written exotic YAML
  needs PyYAML (`pip install pyyaml`).

## 2. Smoke-test the hook

```bash
echo '{"tool_name":"Read","tool_input":{"file_path":"'$HOME'/setup-probe.txt"},"session_id":"rbp-setup-probe","cwd":"'$HOME'"}' \
  | python3 "${CLAUDE_PLUGIN_ROOT}/hooks/rules-by-path.py"; echo "exit=$?"
```

Expected: `exit=0` and no output (no rules match a probe file) — or a JSON
with `additionalContext` if a global rule happens to match. Any traceback or
non-zero exit means a broken installation; investigate before continuing.
Clean up the probe state afterwards:

```bash
rm -f ~/.claude/cache/rules-by-path/rbp-setup-probe.injected
```

## 3. Initialize the global scope (optional)

If the user wants machine-wide rules:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules-by-path-admin.py" init --global
```

Project scopes are created implicitly by the first `add --root <root>`.

## 4. Recommended hardening (ASK FIRST — edits user settings)

By default nothing stops the file tools from reading or editing the rule
files directly. That is functional but weaker: rules should reach context
only through the hook, and a model editing its own rules defeats the purpose.
The hardening adds `permissions.deny` entries to `~/.claude/settings.json`:

```json
"deny": [
  "Read(**/.claude/rules-by-path/**)",
  "Edit(**/.claude/rules-by-path/**)",
  "Grep(**/.claude/rules-by-path/**)",
  "Read(~/.claude/rules-by-path/**)",
  "Edit(~/.claude/rules-by-path/**)",
  "Grep(~/.claude/rules-by-path/**)"
]
```

If the user agrees: read `~/.claude/settings.json`, MERGE these entries into
the existing `permissions.deny` array (create it if absent, keep everything
else untouched, no duplicates), and write it back. Show the user the diff.
With the hardening active, every rule write must go through the admin script
— which is what the `rules-by-path:manage` skill does anyway.

## 5. Migration from a manual (pre-plugin) installation

Only relevant if the user previously installed rules-by-path by hand (a
`rules-by-path.py` under `~/.claude/hooks/` plus entries in settings). Check:

```bash
grep -n "rules-by-path" ~/.claude/settings.json | grep -v rules-by-path/ | head
ls ~/.claude/hooks/rules-by-path.py ~/.claude/scripts/rules-by-path-admin.py ~/.claude/skills/rules-by-path 2>/dev/null
```

If found, with the user's consent: remove the old `hooks` entries that invoke
`~/.claude/hooks/rules-by-path.py` from `~/.claude/settings.json` (otherwise
rules inject twice), and delete the old hook/script/skill files. Existing rule
DATA (`~/.claude/rules-by-path/`, `<project>/.claude/rules-by-path/`) needs no
migration — the plugin reads the same locations and format.

## 6. Final report

Summarize: prerequisites status, smoke-test result, global scope initialized
or not, hardening applied or declined, migration performed or not applicable.
Suggest trying it out: "ask me to add a rule for a folder, e.g. 'when
touching files in src/api, always validate the DTOs'".
