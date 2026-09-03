# How injection works, and the configuration behind it

Read this when a question is about timing, repeats, scopes or `config.json`.

## How injection works

- The hook reads the rules on every Read/Edit/Write/MultiEdit/NotebookEdit and
  injects the ones whose glob matches the touched file.
- What reaches the model is the rule bodies and nothing else — an opening tag,
  the bodies separated by a `---` line, a closing tag. No preamble, no rule
  name, no glob, no scope: nothing about a rule's origin is emitted.
- A rule is injected **once per session**, then **sent again, whole**, once the
  context has moved on by `remember_again_after` — and only when the rule's glob
  matches again, so a rule for a folder nobody reopens is never repeated.
  The value takes tokens (`30k`, `1M`), calls (`25 calls`), or `never`. Each
  rule type carries its own default, which `add` writes into the rule; the
  session-wide default lives in `config.json` and `rules-by-path config` prints
  it. `remember_again_after:` in a rule's frontmatter overrides everything, and
  `RULES_BY_PATH_REMEMBER_AGAIN_AFTER` overrides it for one session.
- There is no short form of a repeat: with no header there is no way to mark a
  fragment as one, so the whole body is resent. **A short rule is therefore a
  cheap rule** — this is the practical reason to keep one constraint per file.
- Editing a rule re-injects it in full immediately — the dedup key includes the
  content.
- Bash access (`cat`, `sed -i`) does NOT trigger injection; only the five file
  tools do.
- Scopes: every `.claude/rules-by-path/` from the touched file's directory up to
  the filesystem root, plus `~/.claude/rules-by-path/`. The walk does not stop
  at a repository boundary, so a git submodule receives its parent repository's
  rules. The global scope is budgeted first and the outermost scope second, so
  neither can be crowded out by rules in nested directories. `<project-root>` is
  the repository root (`git rev-parse --show-toplevel`), not whatever directory
  happens to be the cwd.
- Changes take effect immediately. No restart.

## Changing the configuration

The taxonomy, the repeat defaults and the size limits live in `config.json`,
in three layers — the plugin's own, then `~/.claude/rules-by-path/config.json`,
then `<project>/.claude/rules-by-path/config.json`, nearest wins:

```json
{
  "rule_types": [
    {"prefix": "BUSN", "name": "Business Rules",
     "purpose": "Domain invariants — violating one makes the software wrong",
     "remember_again_after": "20k"}
  ],
  "remember_again_after": {"tokens": "30k", "calls": "25 calls"},
  "rule_size": {"max_chars": 4000, "warn_chars": 2000},
  "language": "pt-BR"
}
```

`language` is what you write rule bodies in, and — when the plugin ships a
translation of it (`en`, `pt-BR`) — also the language of the text the hook
injects around them. Any other language is a fine value: the rules come out in
it and only that surrounding text falls back to English, which `config` and
`validate` both say out loud. The project layer wins over the global one on
purpose: a rule written inside a repository should come out in that
repository's language.

`rule_types` is replaced whole by the nearest layer that declares it; the other
keys merge key by key. A project layer is treated as untrusted (it arrives with
whatever repository is checked out): its intervals have a floor, its texts are
bounded, and it may shorten `max_chars` but never lengthen it.

No subcommand writes this file, and it lives **inside** the rules directory —
which the recommended hardening deny-lists for Read and Edit alike. So under
hardening you cannot edit it yourself: write out the exact JSON the user should
put in which layer, let them save it, then run `config` to confirm what took
effect. Without hardening, edit the file directly and run `config` afterwards.

## Reminders

- Files inside `.claude/rules-by-path/` never trigger injection themselves.
- Rule content is untrusted input and is not dressed up as anything more
  trustworthy than it is: a rule file carries exactly the authority any file in
  the repository carries. What the hook does defend is the boundary — content
  cannot close the block early nor impersonate the harness.
- The hook never blocks a tool call: on any internal failure it warns on stderr
  and stays silent.
