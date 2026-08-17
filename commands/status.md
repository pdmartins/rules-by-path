---
description: Health check and inventory for rules-by-path — whether the hook can run, which scopes exist, and which rules cover a path
argument-hint: "[file or folder to check]"
allowed-tools: ["Bash"]
---

# rules-by-path — status

Read-only diagnosis. Do not edit settings or rules here: if something needs
fixing, point the user at `/rules-by-path:setup` (installation, hardening,
migration) or at the `rules-by-path:manage` skill (authoring rules).

Run the checks below, then report **one short block** — a list of findings, not
a transcript of commands.

## 1. Can the plugin run at all?

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" --help >/dev/null 2>&1 \
  && echo "launcher: ok" \
  || echo "launcher: FAILED (no working python3/python/py on PATH — nothing can be injected)"
```

## 2. Which scopes exist, and what is in them

The project scope is anchored at the repository root, not the cwd:

```bash
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" list --global
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" list --root "$ROOT"
```

`(no rules in this scope)` means the directory does not exist yet — normal, not
an error.

## 3. Would anything actually fire

`validate` reports rules that can never inject (no glob, empty body), rules over
the size limits, globs shared by several rules, and a legacy `rules-map.yml`:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" validate --global
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" validate --root "$ROOT"
```

## 4. What covers this path

Use `$ARGUMENTS` when the user named a file or folder, otherwise the current
directory. This runs the hook's own matcher, so it settles "why is my rule not
firing?" definitively. Single-quote the path:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" which --root "$ROOT" --path '<target>'
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" which --global --path '<target>'
```

## 5. Reinforcement interval

```bash
echo "RULES_BY_PATH_REINFORCE_EVERY=${RULES_BY_PATH_REINFORCE_EVERY:-25 (default)}"
```

## Report

In this order: launcher ok or broken; rule count per scope; anything `validate`
flagged; which rules cover the queried path (or that none do — pass on the `add`
suggestion the tool printed); the reinforcement interval.

Two things worth saying when they apply, because they look like bugs and are
not:

- a rule injects **once per session per version**, so a correctly configured
  rule that was already delivered will not appear again until it changes or the
  interval reinforces it;
- a scope still holding `rules-map.yml` injects nothing at all until
  `/rules-by-path:setup` migrates it.
