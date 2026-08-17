# Changelog

Versions are `MAJOR.MINOR.REVISION` and change only on a release — that is,
only when `develop` is merged into `main` by `publish.sh`. Between releases the
version in `develop` is the last published one, and `0.0.0` means never
published. To run the current working tree on your own machine you do not need
a version at all: `bash publish.sh --local` reinstalls it.

## Unreleased (0.0.0)

Everything below ships in the first public release. `rules-by-path` started as
a personal hook + skill and was converted into a distributable Claude Code
plugin, then put through several rounds of multi-agent security review before
publication. Every guarantee below is covered by a test in `tests/` —
`tests/test_security.py` holds one regression per issue those reviews found.

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
- **`SessionStart` hooks** — on `compact|clear`, resets the per-session dedup
  state so rules survive compaction and `/clear`. On any session start, states
  once that the rules directory is managed by the plugin and names the CLI, so
  the agent does not learn that from a permission denial in every session. Both
  are silent when no scope exists.
- **Nested-CLAUDE.md guard** — *creating* a `CLAUDE.md` below the repository
  root is denied, with guidance to register a path rule instead. One that
  already exists stays editable, and the guard never applies above your home
  directory, so `~/.claude/CLAUDE.md` is safe from it.
- **`rules-by-path:manage` skill** — register, list, show, update and remove
  rules through the bundled CLI.
- **`rules-by-path:setup` skill** — prerequisites check, hook smoke-test,
  optional permission hardening, migration from a manual install, and an
  uninstall walkthrough.
- **`/rules-by-path:status` command** — read-only diagnosis: whether the
  launcher runs, what each scope holds, what `validate` flags, and which rules
  cover a given path.

### Security properties

Rule content is trusted at the level of a repository's `CLAUDE.md` — cloning a
repo means trusting its rules. What the plugin enforces mechanically is that a
rule can only inject *its own text*:

- **Containment** — a scope directory must physically live inside the root it
  claims (so a symlinked `.claude` or `.claude/rules-by-path` cannot redirect
  reads, writes or deletes into your global rules); during migration both the
  legacy `rules/` directory and the legacy `rules-map.yml` must be real, in
  place and unlinked; every file the plugin reads or writes — rules, legacy
  files, and the session state — is opened with `O_NOFOLLOW` and must be a
  regular file. A hostile repository cannot reach a private key, `/etc`,
  `/proc/self/environ`, or your global rules.
- **Rule names are an allowlist** — letters, digits and `._-`, bounded. A name
  is repository data that reaches a shell, a filesystem path and the injection
  header, so `$(...)`, backticks and the full-width lookalikes of `:` and `|`
  are rejected rather than escaped.
- **Safe writes** — the CLI writes through `mkstemp` (random name, `O_EXCL`,
  mode 0600) and `os.replace`, so no planted symlink can redirect a write; every
  unlink is gated on a validated rule name, so no map entry can steer a delete.
- **Unforgeable provenance** — each injection carries a random per-call marker,
  declared *before* any repository-controlled text, and states how many blocks
  legitimately carry it. Block headers are emitted as JSON, so no rule name,
  glob, scope label or path can close a field and open another; values are also
  normalized and stripped of control and formatting characters. Content that
  impersonates the plugin's own framing is defanged wherever it appears in a
  line, not only at the start.
- **Bounded trust** — the upward search stops at the repository root, ignores
  rules directories in world-writable parents (POSIX only), and consults at
  most 8 scopes per tool call. When that cap bites, the scopes that survive are
  the global one and the repository root — never only the deepest ones.
- **No pathological input** — glob matching uses a non-backtracking segment
  matcher rather than a regex, and match time is bounded by a wall-clock budget
  split evenly across the scopes, so a scope full of expensive globs can stall
  neither the tool call nor the rules of the scopes above it. Frontmatter has
  one small parser with no comment syntax, no anchors and no optional YAML
  dependency, so there is no second parser to disagree with it and no alias
  expansion to weaponise. A leading UTF-8 BOM is tolerated.
- **Fair budget** — global rules are budgeted first and repository-root rules
  second, so rules arriving inside a cloned repo — at any nesting depth —
  cannot crowd out your own guardrails.

### Reliability

- Rules are independent files, so no command rewrites a shared index and a
  broken rule never hides the others. Writes are atomic.
- `migrate` never loses text: it validates and renders every entry before
  writing any, refuses to overwrite a markdown file that is not a rule (even
  with `--force`), and skips a legacy rule that would not fit under the size
  cap rather than converting a cut copy and deleting the original.
- A file reached through a directory symlink gets the same rules as the file
  itself, so a monorepo alias neither loses a rule nor borrows one from
  outside the project.
- `show` reads a rule that is not valid UTF-8 instead of failing — under the
  recommended hardening it is the only way to read one — and no command exits
  with a traceback.
- Globs containing `#`, quotes, backslashes or non-ASCII characters round-trip
  intact.
- `validate` reports rules that can never fire, empty rules, long rules, globs
  shared by several rules, and a total that exceeds one injection's budget.
- **Split suggestions.** A rule hands its whole text to every file its glob
  matches, so `add`, `update`, `migrate` and `validate` flag a rule whose own
  text names a file or folder living under its glob — "controllers do X, the DI
  file does Y, nothing over 300 lines" on `src/Api/**` is three rules, and each
  file should receive only the ones that change what you do to it. The check
  runs in the CLI, never in the injection path, and only reports names that
  exist on disk; the manage skill carries the judgement half.
- Dedup state prefers `${CLAUDE_PLUGIN_DATA}`, falls back to `~/.claude/cache`
  and then a per-uid temp directory, and warns rather than silently
  re-injecting on every call.
- State files expire after 14 days, and the sweep runs on every invocation —
  including the far more common ones that inject nothing, which is how a
  machine that rarely matches a rule used to accumulate one file per session.

### Portability

- Standard library only, Python 3.8+. No YAML dependency at all.
- `bin/` launchers resolve `python3`, `python` or the Windows `py` launcher —
  including the POSIX scripts, which are what git-bash runs on Windows — and
  each candidate has to execute a trivial program before it is used, so a
  Microsoft Store alias stub named `python3` is skipped rather than trusted.
  They work when installed via a symlink on `PATH`.
- POSIX and Windows file locking. Tested on Linux and macOS; Windows support is
  implemented but not yet verified by the author.
