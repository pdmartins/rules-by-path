# Changelog

## 1.0.0

First public release. `rules-by-path` started as a personal hook + skill and was
converted into a distributable Claude Code plugin, then put through several
rounds of multi-agent security review before publication. Every guarantee below
is covered by a test in `tests/` — `tests/test_security.py` holds one regression
per issue those reviews found.

### What it does

- **One file per rule.** A rule is a markdown file in `.claude/rules-by-path/`
  declaring its glob in frontmatter — no index file, so nothing can fall out of
  sync and no operation rewrites shared state. A rule may declare several
  globs, and several rules may share one glob.
- **`PreToolUse` hook** — injects glob-matched rules into context on
  Read/Edit/Write/MultiEdit/NotebookEdit, once per rule version per session.
  Editing a rule re-injects it immediately.
- **Reinforcement** — after the full injection, a rule is repeated as a
  one-line reminder every N file-tool calls (default 25, `RULES_BY_PATH_REINFORCE_EVERY`
  or per-rule `reinforce:`; `never` opts out). A rule injected hundreds of
  thousands of tokens ago has faded, and a long-context session never compacts,
  so it never gets the SessionStart reset either.
- **`SessionStart` hook** (`compact|clear`) — resets the per-session dedup
  state, so rules survive compaction and `/clear`.
- **Nested-CLAUDE.md guard** — writing a `CLAUDE.md` below the repository root
  is denied, with guidance to register a path rule instead.
- **`rules-by-path:manage` skill** — register, list, show, update and remove
  rules through the bundled CLI.
- **`rules-by-path:setup` skill** — prerequisites check, hook smoke-test,
  optional permission hardening, migration from a manual install, and an
  uninstall walkthrough.

### Security properties

Rule content is trusted at the level of a repository's `CLAUDE.md` — cloning a
repo means trusting its rules. What the plugin enforces mechanically is that a
rule can only inject *its own text*:

- **Containment** — a scope directory must physically live inside the root it
  claims (so a symlinked `.claude` or `.claude/rules-by-path` cannot redirect
  reads, writes or deletes into your global rules); its `rules/` must be a real
  directory inside it; rule files are opened with `O_NOFOLLOW` and must be
  regular files; rule names must be plain, bounded `*.md` names. A hostile map
  cannot reach a private key, `/etc`, or `/proc/self/environ`.
- **Safe writes** — the CLI writes through `mkstemp` (random name, `O_EXCL`,
  mode 0600) and `os.replace`, so no planted symlink can redirect a write; every
  unlink is gated on a validated rule name, so no map entry can steer a delete.
- **Unforgeable provenance** — each injection carries a random per-call marker
  and the header declares how many blocks legitimately carry it. Content that
  impersonates the plugin's own framing is defanged, and every untrusted value
  interpolated into a block header (rule name, glob, scope, path) is stripped
  of control and formatting characters.
- **Bounded trust** — the upward search stops at the repository root, ignores
  maps in world-writable directories (POSIX only), and is capped at 8 maps per
  tool call.
- **No pathological input** — glob matching uses a non-backtracking segment
  matcher rather than a regex, and total match time per tool call is bounded, so
  neither one crafted glob nor a scope full of them can stall it. Frontmatter has
  one small parser with no comment syntax, no anchors and no optional YAML
  dependency, so there is no second parser to disagree with it and no alias
  expansion to weaponise. A leading UTF-8 BOM is tolerated.
- **Fair budget** — global rules are budgeted before project rules, so rules
  arriving with a cloned repo cannot crowd out your own guardrails.

### Reliability

- Rules are independent files, so no command rewrites a shared index and a
  broken rule never hides the others. Writes are atomic.
- Globs containing `#`, quotes, backslashes or non-ASCII characters round-trip
  intact.
- `validate` reports rules that can never fire, empty rules, long rules, globs
  shared by several rules, and a total that exceeds one injection's budget.
- Dedup state prefers `${CLAUDE_PLUGIN_DATA}`, falls back to `~/.claude/cache`
  and then a per-uid temp directory, and warns rather than silently
  re-injecting on every call.
- Sessions that match no rule leave no state behind.

### Portability

- Standard library only, Python 3.8+. No YAML dependency at all.
- `bin/` launchers resolve `python3`, `python` or the Windows `py` launcher, and
  work when installed via a symlink on `PATH`.
- POSIX and Windows file locking. Tested on Linux and macOS; Windows support is
  implemented but not yet verified by the author.
