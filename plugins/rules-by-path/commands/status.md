---
description: Inventory and health check for rules-by-path — environment, both scopes with their findings, which rules cover a path, the configuration in force
argument-hint: "[file or folder to check]"
allowed-tools: ["Bash"]
---

Run this one command and relay its output as a short list of findings, in the
order printed. Read-only: rules are authored with the `rules-by-path:manage`
skill, and problems are fixed with the `rules-by-path:doctor` skill.

```bash
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" status --root "$ROOT" ${ARGUMENTS:+--path "$ARGUMENTS"}
```

Two things that look like bugs and are not: a rule injects once per session
per version, so a rule already delivered stays silent until its repeat distance
is covered; and Bash access (`cat`, `sed`) never triggers injection — only the
five file tools do.
