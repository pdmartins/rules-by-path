---
name: improve
description: >
  Review and improve path-scoped rules with evidence: prune rules that never
  fire, narrow rules that always fire under one folder, split or reword rules
  the validator flags, harvest path-bound instructions out of CLAUDE.md and
  native .claude/rules into rules-by-path, and (opt-in) mine recent sessions
  for repeated instructions and corrections. Use when the user asks to
  review, improve, optimize, audit, tune or clean up their rules, or to move
  CLAUDE.md content into rules-by-path.
---

# rules-by-path — improve

Evidence first, proposals second, edits only for what the user picks.

## 1. Gather

```bash
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" status --root "$ROOT" --json
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" digest --root "$ROOT"
```

`status --json` is the inventory: every rule with its globs, size, type,
validator notes and usage (injections, sessions, last date, the directories
it fired under). `digest` lists the harvest sources (CLAUDE.md files, native
rules with their `paths:`) and the user's own turns from the most recent
sessions of this project, each paired with the rules injected in it. Read a
harvest source with the Read tool only when its name or `paths:` suggests
folder-bound content. Sessions are the user's conversations: if they did not
ask for them to be mined, say what `digest` reads and ask before using that
part.

## 2. Propose

One numbered list, each item = the evidence, the change, the exact command.
Order by payoff; skip categories with nothing to say.

- **Prune** — usage note "never injected since …": the glob matches nothing
  here, or nobody needs the rule. Prove it with `which --path` on a file it
  should govern; then `remove`, or fix the glob with `update --glob`.
- **Narrow** — usage note "always under X while its glob reaches wider":
  `update --rule '<name>' --glob '<the narrower glob it prints>'`.
- **Split** — validator note "mentions … narrower than it", or a body over
  the soft limit: `show`, `add` one rule per fragment, `remove` the original
  (see the manage skill's `references/writing-rules.md`).
- **Harvest** — an instruction in a CLAUDE.md or native rule that only
  matters for some folder or file type: `add` it with that glob (native
  `paths:` translate directly; `**/*.py` stays `**/*.py`), then tell the user
  which lines to delete from the source. Native rules fire on Read only;
  rules-by-path also fire on Write/Edit, which is the point of moving them.
- **Reword** — a rule injected in a session where the user then corrected
  the same topic is a rule that did not land: rewrite it as a procedure that
  names the failure, not an inventory of facts.
- **New rule** — the same instruction given in two or more sessions about
  the same path: draft it, glob it where the file is open when it breaks,
  confirm the type with the user.
- **Cadence** — validator notes about repeat distance (a prohibition set to
  `never`, an aggressive interval on a convention) are one
  `update --remember-again-after` each.

## 3. Apply

Only the items the user chose, through the manage skill's commands. Finish
with `validate --root "$ROOT"` and one line per change.
