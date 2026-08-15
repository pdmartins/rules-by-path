# Changelog

## 1.0.0

First public release, converted from a standalone hook + skill setup into a
distributable Claude Code plugin, then hardened against the findings of a
multi-agent security audit (see `tests/test_security.py` — every fix below has
a regression test).

### Features

- `PreToolUse` hook: injects glob-matched markdown rules into context on
  Read/Edit/Write/MultiEdit/NotebookEdit, once per rule version per session.
- `SessionStart` hook (`compact|clear`): resets the per-session dedup state so
  rules re-inject after compaction or `/clear`.
- Nested-CLAUDE.md guard: writing a `CLAUDE.md` below the repo root is denied
  with guidance to register a path rule instead.
- `rules-by-path:manage` skill: register/list/show/update/remove rules through
  the bundled CLI.
- `rules-by-path:setup` skill: prerequisites check, hook smoke-test, optional
  permission hardening, migration from manual installs, uninstall walkthrough.
- `bin/` launchers resolve `python3`/`python`/`py`, so the plugin does not
  depend on an interpreter name a platform may not have.

### Security

- **Arbitrary file read via a symlinked `rules/` directory** — containment is
  now anchored on the map's own directory, rule files are opened with
  `O_NOFOLLOW` and must be regular files, and rule names must be plain `*.md`
  names. Previously a cloned repo could inject `/proc/self/environ`, a private
  key, or any readable file into context.
- **The same hole in the CLI**, which wrote and unlinked through a symlinked
  `rules/`; both now share the hook's containment check.
- **Context spoofing** — injected blocks carry a random per-invocation marker
  and the header states how many blocks legitimately carry it, so rule content
  can no longer forge a block claiming a more trusted scope.
- **ReDoS** — glob matching no longer compiles to a regex. A 44-character glob
  used to hang the hook for minutes on every tool call; the replacement
  segment matcher handles the same input in 0.05 ms.
- **Unbounded trust in ancestor directories** — the upward search now stops at
  the repository root, ignores maps in world-writable directories, and is
  capped at 8 maps per tool call.
- **Budget starvation** — global rules are processed before project rules, so
  rules arriving with a cloned repo cannot push the user's own guardrails out
  of the injection budget.

### Correctness

- **Data loss**: the CLI no longer rewrites a map it could not fully parse (it
  used to silently discard every entry), and map writes are atomic.
- **Stale rules**: the dedup key now includes the rule's content, so editing a
  rule re-injects it instead of leaving Claude on the superseded text.
- Globs containing `#`, quotes or backslashes round-trip intact.
- The nested-CLAUDE.md guard is case-insensitive (macOS/Windows).
- Dedup state prefers `${CLAUDE_PLUGIN_DATA}`, falls back to `~/.claude/cache`
  then the temp dir, and warns instead of silently re-injecting forever.
- Sessions that match no rule no longer leave state files behind.
- `derive_rule_name` drops leading `*`/`**` segments (`**/deploy/**` produced
  the unusable name `**--deploy.md`).

### Second audit round

A verification pass over the fixes above found that two of them had opened new
holes; both are closed and covered by regression tests.

- **Arbitrary file overwrite through the atomic write** — the new map/rule
  writes used a predictable `<name>.tmp`, so a symlink planted at that exact
  path in a cloned repo redirected the write to any file the user could write
  (a shell rc file, the user's global CLAUDE.md). Writes now go through
  `mkstemp` (random name, `O_EXCL`, mode 0600).
- **Arbitrary file deletion via a rule name** — `add --force` unlinked the
  previous rule name taken verbatim from repo-controlled map data; an absolute
  name made `os.path.join` discard the scope entirely. Rule names are now
  validated at parse time for every writing command, and every unlink is gated.
- **YAML alias expansion DoS** — a tiny map with anchors expands to billions of
  nodes, and `repr()`-ing a parsed value walked all of them on every tool call.
  Parsed values are no longer repr'd.
- **Non-ASCII globs were silently corrupted** — `json.dumps` escaped them to
  `\uXXXX`, which PyYAML decodes but the fallback parser does not, so an
  accented glob stopped matching on machines without PyYAML.
- **`show` truncated at 16k**, so the show → edit → update round trip the manage
  skill mandates destroyed the tail of a long rule.
- `hooks.json` uses exec form (the shell form breaks under PowerShell on
  Windows); the `.cmd` launchers no longer swallow the exit code in a `for`
  body; the shell launchers resolve `$0` through symlinks.
- The CLAUDE.md guard matches case-insensitively only on case-insensitive
  filesystems — blocking `claude.md` on Linux was over-reach.
- Read-only CLI commands (`list`, `which`, `validate`) no longer refuse to run
  on a map with one unparseable entry; only writing commands are strict.
- The temp-dir state fallback is per-uid, mode 0700, and rejects a symlink.
- Rule content that impersonates the plugin's own header is defanged, so a rule
  cannot claim the authenticity marker was rotated.

### Portability

- Stdlib-only Python; PyYAML optional, with a fallback parser for the map
  format the CLI writes. The CLI is strict about maps it cannot parse; the
  hook stays lenient so one bad line never disables every rule.
- POSIX/Windows file locking.
