# Changelog

## 1.0.0

First public release. `rules-by-path` started as a personal hook + skill and was
converted into a distributable Claude Code plugin, then put through two rounds
of multi-agent security review before publication. Every guarantee below is
covered by a test in `tests/` — `tests/test_security.py` holds one regression
per issue those reviews found.

### What it does

- **`PreToolUse` hook** — injects glob-matched markdown rules into context on
  Read/Edit/Write/MultiEdit/NotebookEdit, once per rule version per session.
  Editing a rule re-injects it immediately.
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

- **Containment** — the `rules/` directory must be a real directory inside the
  map's own folder; rule files are opened with `O_NOFOLLOW` and must be regular
  files; rule names must be plain `*.md` names. A hostile map cannot reach a
  private key, `/etc`, or `/proc/self/environ`.
- **Safe writes** — the CLI writes through `mkstemp` (random name, `O_EXCL`,
  mode 0600) and `os.replace`, so no planted symlink can redirect a write; every
  unlink is gated on a validated rule name, so no map entry can steer a delete.
- **Unforgeable provenance** — each injection carries a random per-call marker
  and the header declares how many blocks legitimately carry it. Content that
  impersonates the plugin's own framing is defanged.
- **Bounded trust** — the upward search stops at the repository root, ignores
  maps in world-writable directories (POSIX only), and is capped at 8 maps per
  tool call.
- **No pathological input** — glob matching uses a non-backtracking segment
  matcher rather than a regex, and parsed map values are never `repr`'d, so
  neither a crafted glob nor a YAML alias bomb can stall a tool call.
- **Fair budget** — global rules are budgeted before project rules, so rules
  arriving with a cloned repo cannot crowd out your own guardrails.

### Reliability

- The CLI refuses to rewrite a map it cannot fully parse, and replaces it
  atomically — it will never silently discard your rules. Read-only commands
  stay usable on a partly broken map.
- Globs containing `#`, quotes, backslashes or non-ASCII characters round-trip
  intact, with or without PyYAML.
- Dedup state prefers `${CLAUDE_PLUGIN_DATA}`, falls back to `~/.claude/cache`
  and then a per-uid temp directory, and warns rather than silently
  re-injecting on every call.
- Sessions that match no rule leave no state behind.

### Portability

- Stdlib-only Python 3.8+; PyYAML optional, with a built-in parser for the map
  format the CLI writes.
- `bin/` launchers resolve `python3`, `python` or the Windows `py` launcher, and
  work when installed via a symlink on `PATH`.
- POSIX and Windows file locking. Tested on Linux and macOS; Windows support is
  implemented but not yet verified by the author.
