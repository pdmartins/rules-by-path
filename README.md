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

**Requirements:** Python 3.8+ on `PATH` as `python3`, `python` or (Windows)
the `py` launcher — stdlib only, no packages to install. PyYAML is optional:
without it a built-in parser handles the map format the plugin itself writes.
Tested on Linux and macOS; Windows support is implemented but not yet verified
by the author.

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

Nested projects work: all ancestor projects of a touched file apply, up to the
repository root. Your global rules are budgeted first, so rules arriving with a
cloned repo can never crowd them out. Project rules are committed with the
repo, so the whole team shares them.

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
  skipped with a warning, and at most 8 ancestor maps are consulted.
- **No pathological matching**: globs are matched by a non-backtracking
  segment matcher, not a regex, so no glob can burn CPU or stall a tool call.
- **Stays inside the scope**: `.claude/rules-by-path` must physically live
  inside the project it claims to belong to, its `rules/` must be a real
  directory inside it, rule files are opened without following symlinks and
  must be regular files, and rule names must be plain, bounded `*.md` names. A
  hostile map cannot point at a private key, `/etc`, `/proc/self/environ`, or
  your global rules.
- **Bounded trust**: the upward search stops at the repository root, and a map
  in a world-writable directory (a shared `/tmp`, say) is ignored — a
  directory you don't control cannot inject instructions into your session.
  The ownership half of that check relies on POSIX permission bits and is not
  enforced on Windows.
- **Unforgeable provenance**: each injection carries a random per-call marker,
  and the header states how many blocks legitimately carry it. Rule content
  cannot forge a block claiming to come from a more trusted scope.
- **Concurrency-safe dedup**: parallel tool calls serialize on a per-session
  lock file, so a simultaneous first touch still injects a rule exactly once.
  The dedup key includes the rule's content, so editing a rule re-injects it
  in the same session.
- **Never loses your rules**: the CLI refuses to rewrite a map it cannot fully
  parse, and replaces it atomically.
- **Bash is out of scope by design**: `cat`/`sed` via Bash don't trigger
  injection — parsing paths out of arbitrary shell commands would be fragile
  and easy to spoof. The five file tools are the reliable signal.

## Security model

**Rule content is trusted input, at the same level as a repository's
`CLAUDE.md`.** Project rules ride with the repo, so cloning a repository means
trusting whatever instructions its rules contain, and you should review
`.claude/rules-by-path/` in code review like any other instruction file. What
the plugin guarantees is narrower and mechanical: a rule can only ever inject
*its own text*, it cannot read other files, impersonate a more trusted scope,
or hang your session.

That boundary is enforced by the containment, provenance and matching
guarantees listed above, each covered by a regression test in
`tests/test_security.py`.

### Recommended hardening

`/rules-by-path:setup` offers deny-list entries for your
`~/.claude/settings.json`:

```json
"permissions": {
  "deny": [
    "Read(**/.claude/rules-by-path/**)",
    "Edit(**/.claude/rules-by-path/**)",
    "Grep(**/.claude/rules-by-path/**)"
  ]
}
```

With this, the *file tools* can no longer read or rewrite rule files, so rules
reach context through the hook and changes go through the bundled CLI, which
validates the map on every write. Reading and updating a rule stay available
through `rules-by-path show` and `rules-by-path update`.

It raises the bar; it is not a sandbox — it constrains Claude's file tools,
not arbitrary subprocesses. Optional, but it is how the system is meant to run.

## Uninstalling

`/plugin uninstall rules-by-path@rules-by-path` removes the hook and the
skills. Three things outlive it, and `/rules-by-path:setup` walks you through
them: the deny-list entries above (remove them, or those paths stay
unreadable), the cache at `~/.claude/cache/rules-by-path`, and your authored
rules in `~/.claude/rules-by-path/` and each project's
`.claude/rules-by-path/`.

## Troubleshooting

Just ask Claude — the `rules-by-path:manage` skill runs these for you. Directly:

```bash
# which rules cover this file? (uses the hook's own matching)
"<plugin>/bin/rules-by-path" which --root <root> --path <file> --json
# orphan entries, missing rule files, unsafe rules dir
"<plugin>/bin/rules-by-path" validate --root <root>
```

- **Rule not injecting?** Each rule version injects once per session. The
  state lives in `$CLAUDE_PLUGIN_DATA/state/` for a plugin install (falling
  back to `~/.claude/cache/rules-by-path/`); delete
  `<state-dir>/<session_id>.injected` to force re-injection. Check the rule is
  inside the repository — the search stops at the repo root.
- **Nothing happens at all?** Run `/rules-by-path:setup`, which smoke-tests the
  hook and reports whether Python was found.
- **Hook errors** are printed to stderr (visible in verbose mode) and never
  block the tool call.

## Roadmap

- Adapters for other agents that support pre-tool hooks or rule injection
  (Codex, Antigravity, …) — the core is plain Python with no Claude-specific
  logic beyond the hook I/O envelope.

## License

[MIT](LICENSE)
