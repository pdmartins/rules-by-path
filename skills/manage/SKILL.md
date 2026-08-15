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

Use `--global` instead of `--root` for the machine-wide scope. Every write goes
through this CLI — never edit `rules-map.yml` or the files under `rules/`
directly, with a file tool or with `sed`. A partial edit leaves an orphan entry,
and users who applied the recommended hardening have those paths deny-listed
anyway. The CLI refuses to rewrite a map it cannot fully parse, so it will tell
you rather than quietly drop entries.

## How the system works

- `rules-map.yml` maps globs to markdown rule files in `rules/`.
- The plugin's PreToolUse hook reads the maps on every
  Read/Edit/Write/MultiEdit/NotebookEdit and injects matching rules into
  context — at most once per rule *version* per session. Editing a rule makes
  it inject again immediately.
- File access through Bash (`cat`, `sed -i`, heredocs) does NOT trigger
  injection — only the five file tools above do. This is deliberate: parsing
  paths out of arbitrary shell commands would be fragile.
- Two scopes:
  - **Project**: `<project-root>/.claude/rules-by-path/` — globs are relative to
    the project root. `<project-root>` is the repository root
    (`git rev-parse --show-toplevel` from the target directory) unless the user
    names a different root — never a subdirectory just because it is the cwd.
  - **Global**: `~/.claude/rules-by-path/` — globs match the absolute path.
- The hook stops looking upward at the repository root, so a map outside the
  project never applies.
- Changes take effect immediately. No restart needed.

## Registering a new rule

1. **Determine the scope.** Default to project scope; use global only when the
   user says the rule applies everywhere (or the target is outside any
   project). If ambiguous, ask.

2. **Check whether a rule already covers the target.** `which` uses the hook's
   own matching, so the answer is exact:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" which --root "<project-root>" \
     --path <folder-or-file> --json
   ```

   It reports the rule *file name* for each match. If there is a match, update
   that rule by name (step 4) — registering a similar-but-different glob would
   create a duplicate entry.

3. **Register a new rule** — content on stdin. The file name is derived from
   the glob (leading/trailing `*`/`**` segments dropped, `/` → `--`, `.md`
   appended: `src/api/**` → `src--api.md`). For globs like `*.cs` the derived
   name would contain a metacharacter, so pass a clean `--rule csharp.md`:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" add --root "<project-root>" \
     --glob 'src/api/**' <<'EOF'
   <markdown content of the rule, written by or with the user>
   EOF
   ```

   Keep rules short and directive (a rule is truncated at 16k chars and one
   injection is capped at 48k).

4. **Update an existing rule** — by file name, never by glob:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" show   --root "<root>" --rule src--api.md
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" update --root "<root>" --rule src--api.md <<'EOF'
   <new content>
   EOF
   ```

   `show` is the sanctioned way to read a rule — the hardening blocks `Read`
   and `cat` on those paths. Read before you overwrite: `update` replaces the
   whole file.

   Never paste a glob you read out of a map back onto a command line — use the
   rule file name instead (`update --rule`, `remove --rule`). Globs are repo
   data; rule file names are validated, glob strings are not. A glob on the
   command line should only ever be one the user just gave you.

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

## Listing, validating, removing

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" list     --root "<root>"
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" validate --root "<root>"
# remove by rule file name (safe: names are validated, globs are repo data)
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" remove   --root "<root>" --rule src--api.md
# or by a glob the USER just named
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" remove   --root "<root>" --glob 'src/api/**'
```

When the user asks "which rules exist?", check both scopes (project and
`--global`). `validate` exits non-zero on orphan entries, missing rule files or
an unsafe rules directory.

## Reminders

- Each rule version is injected once per session. After a compaction or
  `/clear` a SessionStart hook resets that state automatically.
- Files inside `.claude/rules-by-path/` never trigger injection themselves.
- Creating/editing a nested CLAUDE.md (below a repo root) is denied by the hook
  — that is intentional; only the project-root CLAUDE.md is a file.
- The hook never blocks a tool call: on any internal failure it warns on
  stderr and stays silent.
- First rule in a fresh scope? `add` creates the skeleton implicitly; `init`
  does it explicitly.
