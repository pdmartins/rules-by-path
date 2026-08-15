# rules-by-path

**Path-scoped rules for Claude Code.** Markdown rules are injected into
context automatically — by a `PreToolUse` hook — the moment Claude touches a
file matching a glob. Nothing loads until it is relevant; each rule loads at
most once per session.

A scalable replacement for nested `CLAUDE.md` files.

## Why

`CLAUDE.md` is all-or-nothing: everything in it is in context on every
session, whether or not the work touches that part of the tree. Scattering
nested `CLAUDE.md` files through subfolders sort of works, but pollutes the
repo, and the guidance still isn't tied to what the agent actually touches.

`rules-by-path` inverts this:

- You register a **glob → markdown rule** mapping (`src/api/** → src--api.md`).
- A `PreToolUse` hook watches `Read`/`Edit`/`Write`/`MultiEdit`/`NotebookEdit`.
- The first time Claude touches a matching file, the rule's markdown is
  injected into context (`additionalContext`) — clearly labeled with its glob
  and scope.
- Injection happens **once per rule per session** (state resets automatically
  after `/clear` and context compaction, so rules survive both).
- Zero context cost for rules that never become relevant.

The hook also **blocks the creation of nested `CLAUDE.md` files** inside a
repo (only the project-root `CLAUDE.md` is allowed) and redirects the agent to
register a path rule instead — the system enforces its own convention.

## Install

In Claude Code:

```
/plugin marketplace add pdmartins/rules-by-path
/plugin install rules-by-path@rules-by-path
```

Then run `/rules-by-path:setup` once — it checks prerequisites, smoke-tests
the hook, and offers the recommended permission hardening.

**Requirements:** `python3` on `PATH` (any 3.8+; stdlib only). PyYAML is
optional — without it a built-in parser handles the map format the plugin
itself writes.

## Quick start

Just ask Claude, in any language:

> "Add a rule for `src/api`: every endpoint needs input validation and must
> return ProblemDetails on errors."

The `rules-by-path:manage` skill registers it:

```
.claude/rules-by-path/
├── rules-map.yml          # - glob: "src/api/**"
│                          #   rule: "src--api.md"
└── rules/
    └── src--api.md        # your markdown rule
```

From then on, whenever Claude touches anything under `src/api/`, the rule
appears in its context — once per session, exactly when it matters.

Other things you can ask for: *"list the path rules"*, *"remove the rule for
docs/"*, *"update the terraform rule"*.

## Scopes

| Scope | Location | Globs match |
|---|---|---|
| **Project** | `<project-root>/.claude/rules-by-path/` | paths relative to the project root |
| **Global** | `~/.claude/rules-by-path/` | absolute paths |

Nested projects work: all ancestor projects of a touched file apply, nearest
first, then the global scope. Project rules are committed with the repo, so
the whole team shares them.

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

## Design guarantees

- **Never blocks work**: any internal hook failure goes to stderr and the tool
  call proceeds untouched. The only deliberate block is the nested-CLAUDE.md
  guard.
- **Bounded**: a rule is truncated at 16k chars, one injection is capped at
  48k, hostile/huge maps (>256 KiB, >512 entries, globs >256 chars) are
  skipped with a warning.
- **Symlink-safe**: a rule file that resolves outside its `rules/` directory
  is refused, so a hostile map cannot pull arbitrary readable files (keys,
  tokens) into context.
- **Concurrency-safe dedup**: parallel tool calls serialize on a per-session
  lock file, so a simultaneous first touch still injects a rule exactly once.
- **Bash is out of scope by design**: `cat`/`sed` via Bash don't trigger
  injection — parsing paths out of arbitrary shell commands would be fragile
  and easy to spoof. The five file tools are the reliable signal.

## Security model

Project rules ride with the repo — they have exactly the same trust level as
a repo's `CLAUDE.md`: cloning a repository means trusting its rule content.
Review `.claude/rules-by-path/` in code review like any other instruction
file. Injected blocks are always labeled with their source scope and glob, so
provenance is visible in context.

### Recommended hardening

`/rules-by-path:setup` offers deny-list entries for your
`~/.claude/settings.json`:

```json
"permissions": {
  "deny": [
    "Read(**/.claude/rules-by-path/**)",
    "Edit(**/.claude/rules-by-path/**)",
    "Grep(**/.claude/rules-by-path/**)",
    "Read(~/.claude/rules-by-path/**)",
    "Edit(~/.claude/rules-by-path/**)",
    "Grep(~/.claude/rules-by-path/**)"
  ]
}
```

With this, rule files reach context **only** through the hook, and the agent
cannot quietly rewrite its own rules — every change goes through the bundled
admin CLI (`scripts/rules-by-path-admin.py`), which validates the map on
every write. Optional, but it is how the system is meant to run.

## Troubleshooting

- **Rule not injecting?** `python3 "<plugin>/scripts/rules-by-path-admin.py"
  which --root <root> --path <file>` shows exactly which entries match — it
  uses the hook's own matching code. Remember each rule injects only once per
  session; delete `~/.claude/cache/rules-by-path/<session_id>.injected` to
  force re-injection.
- **Map looks broken?** `... validate --root <root>` (or `--global`) reports
  orphan entries and missing rule files.
- **Hook errors** are printed to stderr (visible in verbose mode) and never
  block the tool call.

## Roadmap

- Adapters for other agents that support pre-tool hooks or rule injection
  (Codex, Antigravity, …) — the core is plain Python with no Claude-specific
  logic beyond the hook I/O envelope.

## License

[MIT](LICENSE)
