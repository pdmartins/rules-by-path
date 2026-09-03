---
name: doctor
description: >
  Setup, health-check, repair and removal for the rules-by-path plugin. Use
  when the user asks to set up, configure, verify, harden, troubleshoot,
  migrate or uninstall rules-by-path, or reports that a rule is not being
  injected.
---

# rules-by-path — doctor

One command runs every check; each finding names its fix.

```bash
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" doctor --root "$ROOT"
```

- A finding marked `[--fix applies it]` is applied by re-running with
  `--fix`. When the summary says the hardening edits the user's settings, ASK
  first and state the trade-off in one line: with it, every rule read and
  write goes through this CLI (the manage skill does that anyway); it
  constrains the file tools, not subprocesses. Show what changed.
- A finding marked `[manual]` needs a decision only the user can make (a
  rule's type, an unreadable file). Bring it to them with the command named.
- "Rule not firing?" — `status --root "$ROOT" --path '<file>'` runs the
  hook's own matcher on that path and says which rule covers it, or which
  filter took it back.
- Uninstall: `doctor --root "$ROOT" --uninstall` removes the deny entries and
  the cached state and lists the rule directories it keeps. Then the user runs
  `/plugin uninstall rules-by-path@pdmartins`. Never delete rule directories
  without asking: they are the user's authored content.
- After a first setup, offer the first rule: "ask me to add a rule for a
  folder, e.g. 'when touching src/api, always validate the DTOs'".
