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

## How the system works

- `rules-map.yml` maps globs to markdown rule files in `rules/`.
- The plugin's PreToolUse hook reads the maps on every
  Read/Edit/Write/MultiEdit/NotebookEdit and injects matching rules into
  context via `additionalContext` — at most once per rule per session.
- File access through Bash (`cat`, `sed -i`, heredocs) does NOT trigger
  injection — only the five file tools above do. This is deliberate: parsing
  paths out of arbitrary shell commands would be fragile.
- Two scopes:
  - **Project**: `<project-root>/.claude/rules-by-path/` — globs are relative to
    the project root (the directory containing `.claude/`). All ancestor
    projects of a file apply (nested projects work). `<project-root>` is the
    repository root (`git rev-parse --show-toplevel` from the target directory)
    unless the user names a different root — never a subdirectory just because
    it is the cwd.
  - **Global**: `~/.claude/rules-by-path/` — globs match the absolute path.
- Changes take effect immediately (the hook re-reads the map on every tool
  call). No restart needed.

## File access constraints

**Every write goes through the admin script** — never edit `rules-map.yml` or
the files under `rules/` directly (with the file tools or ad-hoc `sed`): a
partial edit leaves an orphan entry the hook fails on silently, and users who
applied the recommended hardening (see the `rules-by-path:setup` skill) have
the file tools deny-listed on those paths anyway.

The admin script (its command line carries only `--root`/`--global`, never a
rules path):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules-by-path-admin.py"
```

Reading for management purposes is fine via Bash (`cat`, `ls`). If a file tool
is denied on those paths, do not work around the deny by other means; the
admin script is the sanctioned channel.

## Registering a new rule

1. **Determine the scope.** Default to project scope; use global only when the
   user says the rule applies everywhere (or the target is outside any
   project). If ambiguous, ask.

2. **Discover or choose the glob.** First check whether an entry already covers
   the target — `which` uses the hook's own matching, so the answer is exact:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules-by-path-admin.py" which \
     --root "<project-root>" --path <folder-or-file>
   ```

   A match means update that exact glob with `--force` — a different glob
   string would create a duplicate entry (the admin refuses when the derived
   filename would collide with another glob's rule). No match: pick a new glob
   (see semantics below) — project globs are relative to the project root
   (`src/api/**`); global globs match absolute paths (`**/terraform/**`,
   `*.tf`, `/repos/x/**`).

3. **Register** — rule content on stdin; the script derives the filename from
   the glob (leading/trailing `*`/`**` segments dropped, `/` → `--`, `.md` appended
   unless already present: `src/api/**` → `src--api.md`), refuses derived names
   containing metacharacters (pass a clean `--rule csharp-files.md` for globs
   like `*.cs`), appends the map entry and validates everything:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules-by-path-admin.py" add \
     --root "<project-root>" --glob 'src/api/**' <<'EOF'
   <markdown content of the rule, written by or with the user>
   EOF
   ```

   Global scope: replace `--root "<project-root>"` with `--global`.
   Keep rules short and directive (the hook truncates a rule at 16k chars and
   caps one injection at 48k).

4. If the glob is already registered the script refuses; re-run with `--force`
   to overwrite the rule content (that is also the update flow).

## Glob semantics (as implemented by the hook)

| Glob | Matches |
|---|---|
| `src/api/**` | anything under `src/api/` |
| `docs` or `docs/` | the folder `docs/` and everything under it |
| `src/config.json` | that file exactly |
| `*.cs` | any file with that basename shape, at any depth (no-`/` globs also match the basename) |
| `**/deploy/**` | a `deploy/` folder at any depth |
| `/repos/x/**` | absolute-path prefix (global scope) |
| `?` | exactly one character; `*` never crosses `/` |

## Listing and validating

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules-by-path-admin.py" list --root "<project-root>"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules-by-path-admin.py" list --global
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules-by-path-admin.py" validate --root "<project-root>"
```

When the user asks "which rules exist?", check both scopes (project and
`--global`). `validate` exits non-zero on orphan entries or missing rule files
— run it whenever something looks off.

## Updating / removing a rule

Always resolve the exact registered glob first (`which --path ...`) — updating
with a different glob string silently creates a duplicate entry.

```bash
# update = re-register with --force (content on stdin)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules-by-path-admin.py" add \
  --root "<project-root>" --glob 'src/api/**' --force <<'EOF'
<new content>
EOF

# remove entry + rule file (kept if another glob references the same file)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules-by-path-admin.py" remove \
  --root "<project-root>" --glob 'src/api/**'
```

## Reminders

- Each rule is injected once per session. After a context compaction or
  `/clear`, a SessionStart hook resets that state automatically, so rules are
  re-injected on the next touch. To force it manually in a live session:
  `rm ~/.claude/cache/rules-by-path/<session_id>.injected`.
- Files inside `.claude/rules-by-path/` never trigger injection themselves.
- Creating/editing a nested CLAUDE.md (below a repo root) is denied by the hook
  with a message pointing here — that is intentional; only the project-root
  CLAUDE.md is a file.
- The hook never blocks a tool call: on any internal failure it warns on
  stderr and stays silent.
- First rule in a fresh scope? `init` creates the skeleton (add also does it
  implicitly): `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules-by-path-admin.py" init --global`
