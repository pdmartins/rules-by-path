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

- A rule is one markdown file that declares the glob it applies to, in its own
  frontmatter. There is no index to keep in sync.
- A `PreToolUse` hook watches `Read`/`Edit`/`Write`/`MultiEdit`/`NotebookEdit`.
- The first time Claude touches a matching file, the rule is injected into
  context (`additionalContext`) — labeled with its glob and scope.
- Injection happens **once per rule version per session**, then a one-line
  **reminder** every N file-tool calls, so a rule does not fade out of a very
  long context. Editing a rule re-injects it in full immediately.
- Zero context cost for rules that never become relevant.

The hook also **blocks the creation of nested `CLAUDE.md` files** inside a
repo (only the project-root `CLAUDE.md` is allowed) and redirects the agent to
register a path rule instead — the system enforces its own convention. A nested
`CLAUDE.md` that already exists stays editable, and the guard never applies
above your home directory, so `~/.claude/CLAUDE.md` is untouched even if your
dotfiles live in a git repository.

## Install

In Claude Code:

```
/plugin marketplace add pdmartins/rules-by-path
/plugin install rules-by-path@rules-by-path
```

Then run `/rules-by-path:setup` once — it checks prerequisites, smoke-tests
the hook, and offers the recommended permission hardening.

**Requirements:** Python 3.8+ on `PATH` as `python3`, `python` or (Windows)
the `py` launcher. Standard library only — nothing to install. Tested on Linux
and macOS; Windows support is implemented but not yet verified by the author.

## Quick start

Just ask Claude, in any language:

> "Add a rule for `src/api`: every endpoint needs input validation and must
> return ProblemDetails on errors."

The `rules-by-path:manage` skill writes one file:

```
.claude/rules-by-path/
└── src--api.md
```

```markdown
---
glob: src/api/**
---
Every endpoint validates its input and returns ProblemDetails on error.
```

A rule can declare several globs, and several rules can share one glob — they
all inject together.

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

## Reinforcement

A rule is injected in full the first time it is relevant, then repeated as a
one-line reminder every 25 file-tool calls. On a long-context session a rule
injected hundreds of thousands of tokens ago has effectively faded; a short
reminder costs little and keeps it live.

Set the interval with `RULES_BY_PATH_REINFORCE_EVERY` (`0` disables it), or per
rule with `reinforce:` in its frontmatter:

```markdown
---
glob: infra/**
reinforce: never
---
```

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

A bare name with no `/` (like `docs` or `Makefile`) is matched two ways: against
the project-root path (so `docs` covers the root `docs` entry and everything
under it) **and** against the file's basename at any depth (so `Makefile` catches
every `Makefile`, and `docs` also matches a file literally named `docs`
anywhere). To target a `docs/` folder wherever it appears, use `**/docs/**`.

## Design guarantees

- **Never blocks work**: any internal hook failure goes to stderr and the tool
  call proceeds untouched. The only deliberate block is the nested-CLAUDE.md
  guard.
- **Bounded**: a rule is truncated at 4k chars (the CLI warns above 2k), one
  injection is capped at 24k, a scope is capped at 256 rules and a glob at 256
  chars, and at most 8 scopes are consulted per tool call.
- **No pathological matching**: globs are matched by a non-backtracking
  segment matcher, not a regex, so no single glob can blow up. Aggregate cost
  is bounded by a wall-clock budget *divided among the scopes*, so a scope full
  of expensive globs can only ever spend its own share — not the repository
  root's, and not your global rules'. Frontmatter is parsed by one small parser
  with no comment syntax, anchors or optional YAML dependency — there is no
  second parser to disagree with.
- **Symlinks do not change which rule applies**: a file reached through a
  directory link is matched on both the literal and the resolved path (the
  resolved one only while it stays inside the same project), so a monorepo
  alias neither loses a rule nor borrows one.
- **Stays inside the scope**: `.claude/rules-by-path` must physically live
  inside the project it claims to belong to, rule files are opened without
  following symlinks and must be regular files, and rule names must be plain,
  bounded `*.md` names. A hostile repository cannot reach a private key,
  `/etc`, `/proc/self/environ`, or your global rules.
- **Bounded trust**: the upward search stops at the repository root, and a
  rules directory in a world-writable parent (a shared `/tmp`, say) is ignored
  — a directory you don't control cannot inject instructions into your session.
  The ownership half of that check relies on POSIX permission bits and is not
  enforced on Windows.
- **The outermost rules always apply**: your global scope is consulted first
  and the repository-root scope is next, and both keep their slot when the
  8-scope cap is reached. Nested `.claude/rules-by-path/` directories — which
  anyone opening a PR can add — cannot crowd out the rules the repository
  itself declares.
- **Unforgeable provenance**: each injection carries a random per-call marker,
  and the header states how many blocks legitimately carry it. Rule content
  cannot forge a block claiming to come from a more trusted scope.
- **Concurrency-safe dedup**: parallel tool calls serialize on a per-session
  lock file, so a simultaneous first touch still injects a rule exactly once.
  The dedup key includes the rule's content, so editing a rule re-injects it
  in the same session.
- **Never loses your rules**: each rule is an independent file, so no operation
  rewrites a shared index; writes go through `mkstemp` + `os.replace`, so a
  planted symlink cannot redirect one.
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
    "Read(~/.claude/rules-by-path/**)",
    "Edit(~/.claude/rules-by-path/**)"
  ]
}
```

Two tools, two anchors. Both halves are counter-intuitive enough to be worth
stating, and both were verified against Claude Code 2.1.233:

- **`Read` and `Edit` only.** `Read(...)` governs reads *and greps* — the Grep
  tool checks its `path` argument as a read — so a `Grep(...)` entry is never
  consulted by anything and sits dead in your settings. `Edit(...)` governs
  every file-editing tool (Write, Edit, NotebookEdit alike); a separate
  `Write(...)` entry is not matched and makes Claude Code warn at startup.
- **Both anchors.** A pattern that does not start with `/` or `~/` is resolved
  against the current working directory, so the `**/...` pair only covers the
  project you have open. The `~/`-anchored pair is what protects your **global**
  rules whenever Claude Code runs in a project outside your home directory.

With this, the *file tools* can no longer read or rewrite rule files, so rules
reach context through the hook and changes go through the bundled CLI, which
validates what it writes. Reading and updating a rule stay available through
`rules-by-path show` and `rules-by-path update`.

So that the deny-list is not something Claude discovers the hard way, a
`SessionStart` hook says it once, up front: the rules directory is managed by
the plugin, its contents arrive automatically, and the CLI is the way in.
Without that, the agent meets the directory by listing or reading it and
collects a permission denial — which explains nothing, so the attempt repeats
next session. The notice is emitted only when a scope actually exists.

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
"<plugin>/bin/rules-by-path" which --root <root> --path <file>
# rules that can never fire, empty rules, long rules, shared globs
"<plugin>/bin/rules-by-path" validate --root <root>
```

- **Rule not injecting?** Each rule version injects once per session. The
  state lives in `$CLAUDE_PLUGIN_DATA/state/` for a plugin install (falling
  back to `~/.claude/cache/rules-by-path/`); delete `<state-dir>/<session_id>.json`
  to force re-injection. Check the rule is inside the repository — the search
  stops at the repo root — and that `validate` reports it.
- **Upgrading from the `rules-map.yml` format?** Run
  `"<plugin>/bin/rules-by-path" migrate --root <root>` (and `--global`). Until
  you do, that scope injects nothing and the hook says so in context.
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
