---
name: manage
description: >
  Register, list, update or remove path-scoped rules for the rules-by-path
  system — markdown rules auto-injected into context by a PreToolUse hook
  whenever Claude touches a file matching a glob. Use whenever the user asks to
  create/manage a rule tied to a folder or path, in any language, e.g. "add a
  rule for src/api", "when touching X follow Y", "create a folder-scoped
  rule", "list/remove the per-path rules". Rules live in
  .claude/rules-by-path/ (project scope) or ~/.claude/rules-by-path/ (global
  scope).
---

# rules-by-path — managing path-scoped rules

## The one command

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" <subcommand> --root "<project-root>" [...]
```

Use `--global` instead of `--root` for the machine-wide scope.

## What a rule is

One markdown file that declares its own glob in frontmatter:

```markdown
---
glob: src/api/**
---
Every endpoint validates its input and returns ProblemDetails on error.
```

That is the whole format. There is no index file, so nothing can fall out of
sync. A rule may declare several globs, and several rules may share one glob —
they all inject together.

Always write rules through the CLI rather than with a file tool: users who
applied the recommended hardening have those paths deny-listed, and the CLI
validates what it writes.

## How injection works

- The hook reads the rules on every Read/Edit/Write/MultiEdit/NotebookEdit and
  injects the ones whose glob matches the touched file.
- A rule is injected **in full once per session**, then **reinforced with a
  one-line reminder** every N file-tool calls (default 25; `RULES_BY_PATH_REINFORCE_EVERY`
  sets it globally, `reinforce:` in a rule's frontmatter overrides it per rule,
  `never` disables it). Long-context sessions drift away from a rule injected
  hundreds of thousands of tokens earlier.
- Editing a rule re-injects it in full immediately — the dedup key includes the
  content.
- Bash access (`cat`, `sed -i`) does NOT trigger injection; only the five file
  tools do.
- Scopes: the project chain up to the repository root, plus `~/.claude/rules-by-path/`.
  The global scope is budgeted first and the repository-root scope second, so
  neither can be crowded out by rules in nested directories. `<project-root>` is
  the repository root (`git rev-parse --show-toplevel`), not whatever directory
  happens to be the cwd.
- Changes take effect immediately. No restart.

## Adding a rule

1. **Pick the scope.** Project by default; global only when the user says it
   applies everywhere. If ambiguous, ask.

2. **Check what already covers the target:**

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" which --root "<root>" --path '<folder-or-file>'
   ```

   It reports rule *file names*. An existing rule about the same concern should
   be updated (step 4); a different concern is a new rule, even for the same glob.

3. **Create it** — body on stdin, name derived from the glob (`src/api/**` →
   `src--api.md`). A rule name may hold only letters, digits and `._-`, so when
   the derived name would carry anything else (`*.cs` → a metacharacter,
   `src/@types/**` → an `@`), pass one explicitly: `--rule 'csharp.md'`.

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" add --root "<root>" \
     --glob 'src/api/**' <<'EOF'
   <the rule, written by or with the user>
   EOF
   ```

   Repeat `--glob` for several globs. `--reinforce never` for a rule that should
   not be repeated.

4. **Update by name**, never by glob:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" show   --root "<root>" --rule 'src--api.md'
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" update --root "<root>" --rule 'src--api.md' <<'EOF'
   <new body>
   EOF
   ```

   `show` is the sanctioned way to read a rule under the hardening. Read before
   you overwrite: `update` replaces the whole body (it keeps the globs).

   **Single-quote every value that came out of a rule** — `--rule 'src--api.md'`,
   `--glob 'src/api/**'`. Names and globs are repository data that this CLI hands
   back to you: a rule name is restricted to letters, digits and `._-`, so it
   cannot carry shell syntax, but a glob is not restricted at all, and `$(...)`
   and backticks expand inside double quotes just as they do unquoted. Single
   quotes are always safe here, because neither a name nor a rule this CLI wrote
   can contain one.

## Writing a good rule

A rule states a **constraint that changes what you do**, not knowledge you
could get by reading the code. "Endpoints here must return ProblemDetails" is a
rule; a tour of how the module works is not.

Keep it short: the CLI warns above 2,000 characters and the hook truncates at
4,000. A long rule also makes reinforcement expensive, so it gets repeated less
usefully. If a rule is growing, it usually wants to be split into two rules
with narrower globs.

## Glob semantics

| Glob | Matches |
|---|---|
| `src/api/**` | anything under `src/api/` |
| `docs` or `docs/` | the folder `docs/` and everything under it |
| `src/config.json` | that file exactly |
| `*.cs` | any file with that basename, at any depth |
| `**/deploy/**` | a `deploy/` folder at any depth |
| `/repos/x/**` | absolute-path prefix (global scope) |
| `?` | exactly one character; `*` never crosses `/` |

A bare name with no `/` (e.g. `docs`, `Makefile`) matches both the project-root
path AND the file's basename at any depth — so `docs` also matches a file named
`docs` anywhere, not only the root folder. Use `**/docs/**` for a `docs/` folder
wherever it appears.

## Listing, validating, removing

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" list     --root "<root>"
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" validate --root "<root>"
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" remove   --root "<root>" --rule 'src--api.md'
```

`validate` reports rules that can never fire (no glob), empty rules, and notes
long rules, shared globs and a total that exceeds one injection's budget. When
the user asks "which rules exist?", check both scopes.

## Migrating an old installation

A scope still holding `rules-map.yml` injects nothing, and the hook says so in
context. Convert it once:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" migrate --root "<root>"   # or --global
```

## Reminders

- Files inside `.claude/rules-by-path/` never trigger injection themselves.
- Creating a nested CLAUDE.md (below a repo root) is denied by the hook — that
  is intentional; only the project-root CLAUDE.md is a file. One that already
  exists stays editable, and the guard never applies to `~/.claude/CLAUDE.md`
  (the user's own global instructions), whatever their home directory contains.
- The hook never blocks a tool call: on any internal failure it warns on stderr
  and stays silent.
