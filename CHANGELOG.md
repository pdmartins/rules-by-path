# Changelog

## 1.0.0

First public release, converted from a standalone hook + skill setup into a
distributable Claude Code plugin.

- `PreToolUse` hook: injects glob-matched markdown rules into context on
  Read/Edit/Write/MultiEdit/NotebookEdit, once per rule per session.
- `SessionStart` hook (`compact|clear`): resets the per-session dedup state so
  rules re-inject after compaction or `/clear`.
- Nested-CLAUDE.md guard: writing a `CLAUDE.md` below the repo root is denied
  with guidance to register a path rule instead.
- `rules-by-path:manage` skill: register/list/update/remove rules through the
  bundled admin CLI.
- `rules-by-path:setup` skill: prerequisites check, hook smoke-test, optional
  permission hardening, migration from manual installs.
- Portability: stdlib-only Python (PyYAML optional — built-in fallback parser
  for admin-written maps), POSIX/Windows file locking.
- Hardening: symlink containment for rule files, bounded reads, map size /
  entry count / glob length caps.
