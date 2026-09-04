# Changelog

Versions are `MAJOR.MINOR.REVISION` and change only on a release — that is,
only when `develop` is merged into `main` by `publish.sh`. Between releases the
version in `develop` is the last published one, and `0.0.0` means never
published. To run the current working tree on your own machine you do not need
a version at all: `bash publish.sh --local` reinstalls it.

## Unreleased

**One deterministic command per job; the skills shrink to what needs judgement.**

- `status` — environment, both scopes with their rules and findings, what
  covers a path (`--path`), the configuration in force, the repeat unit in
  use, and per-rule usage; `--json` for machine consumers. The `status`
  command is now one line instead of nine commands for the model to run.
- `doctor` — every setup check in one call (Python, hook launcher, hook and
  session-notice smoke tests, each scope's format, the recommended hardening,
  pre-plugin leftovers, cached state), each finding naming its fix.
  `doctor --fix` applies migration and hardening; `doctor --uninstall`
  removes deny entries and cached state and keeps rule directories. Replaces
  the `setup` skill; the new `doctor` skill is a page.
- **Usage stats.** The hook records, per rule and across sessions,
  injections, repeats, distinct sessions, first/last date, the directories
  matched and the glob that matched — bounded, fail-open, off the critical
  path, exempt from the stale-state sweep. `status` derives two notes: never
  injected since stats began, and always injected under one subfolder of a
  wider glob (with the narrower glob to use).
- `move` — carries a rule between scopes and rewrites its globs for the new
  frame; the one ambiguous shape (a root-anchored glob going global) asks for
  `--anchor any-project|this-project`. Type checked against the destination
  taxonomy; language and `enforce: deny` differences warned about.
- `digest` and the `improve` skill — harvest sources (CLAUDE.md files,
  native rules with their `paths:`) and the user's own turns from this
  project's recent sessions, paired with the rules injected in each; the
  skill proposes prune/narrow/split/harvest/reword/new-rule changes with
  evidence and the exact command, and applies only what the user picks.
- `manage` skill: 491 lines down to 125; the reference material lives in
  `references/` and is read on demand.
- The three answers only the user has — a rule's type, an ambiguous scope, and
  the anchor of a glob going global — are asked as options to pick instead of
  prose, and the type options are built from what `config` prints, so a
  replaced taxonomy travels into the question. Splitting a paste asks for every
  fragment's type in one round, and so does `doctor`'s untyped-rule finding.
  More than four types, or nobody to answer (a `-p` run, a subagent), falls
  back to prose and to the CLI's own refusal.

**A rule can narrow itself past its glob: `exclude:` and `tool:`.**

```markdown
---
glob: src/**
exclude: src/**/*.test.ts
tool: write
---
Every exported function is documented with TSDoc.
```

Both are restrictive and ANDed with the glob and with each other: a rule
reaches the model when one `glob` matches, no `exclude` matches, and the tool
call is of a kind `tool:` accepts. `exclude:` reads exactly like `glob:` (one
value or a list, same length and count limits). `tool:` takes `write` (Write,
Edit, MultiEdit, NotebookEdit), `read`, or `any`.

`tool: write` is the one with a measurable payoff. Most conventions govern what
gets created, not what gets read — and before this, a Read spent the rule: dedup
is per session, so the Write that was actually about to break the convention
could arrive to find the rule already delivered and gone.

A filter only ever narrows a rule, so **a value the hook cannot read is ignored,
not enforced** — `tool: wirte` leaves the rule unfiltered rather than silently
switching it off. `validate` reports the typo, and now errors on the two shapes
that disable a rule without saying so: an `exclude` of `**`, and an `exclude`
that cancels every glob the rule declares.

Around it:

- `add`/`update` take `--exclude` (repeatable) and `--tool`; `--tool any`
  clears the restriction. Both filters survive a `show` -> edit -> `update`
  round trip, and deleting the line from the submitted frontmatter removes the
  filter.
- `which` takes `--tool read|write`, marks a restricted match `(write only)`,
  and — the reason it exists — explains a rule whose glob covers the path but
  which still will not fire, as `excluded:` or `filtered:`.
- `enforce --list` warns that a native `permissions.deny` entry cannot express
  an `exclude`, so a synced entry denies more than the rule does. `validate`
  reports `enforce: deny` on a `tool: read` rule as inert — a deny only ever
  acts on a write.
- `migrate` carries the filters through the rewrite it does of the pre-0.4.0
  interval key.

**Breaking: the injected text is now the rule bodies and nothing else.**

```
<rules-by-path>
Every endpoint must validate its input.
---
Never log the request body.
</rules-by-path>
```

Gone from what reaches the model: the preamble, the per-rule JSON header, the
per-invocation marker, and every field that stated where a rule came from
(`name`, `glob`, `scope`, `reminder`, `truncated`). Measured on one rule in a
real project, a repeat went from 801 characters to 248 — 33 of them framing.

The reason is not only cost. The marker-and-header scheme authenticated one rule
block against another, defending against content forging a `scope: global` claim
to look more trustworthy than its neighbour. That attack only had something to
win because the plugin emitted authority metadata in the first place. Without
it, a forged block claims exactly the authority a real one has — which is the
authority any file in the repository already has when the harness injects its
`CLAUDE.md`. What is still defended is the boundary: rule content cannot close
the block early, nor impersonate the harness (`<system-reminder>`,
`<function_calls>`), nor forge the separator between two rules.

### Also changed

- **Repeats are measured in context tokens**, read from the session transcript —
  the count the API itself billed. The call count measured the wrong thing: a
  session that reads three huge files burns 200k tokens in three calls and was
  never reminded, while fifty tiny greps burned 20k and were reminded twice.
  Where the transcript cannot be read, the hook falls back to counting file-tool
  calls and reports which unit is in use. There is no conversion between them.
- **`reinforce:` becomes `remember_again_after:`**, taking tokens (`30k`, `1M`),
  calls (`25 calls`) or `never`; `RULES_BY_PATH_REINFORCE_EVERY` becomes
  `RULES_BY_PATH_REMEMBER_AGAIN_AFTER`. Units may be mixed in one session, so the
  state records both measures per rule. The intermediate spelling
  `remember_after` is still honoured wherever it appears — frontmatter key and
  environment variable alike — and `migrate` rewrites it in rule files.
- **A repeat still requires the glob to match again.** Distance covered is
  necessary, not sufficient: a rule governing a folder nobody reopens is never
  repeated.
- **A repeat resends the whole rule.** With no header there is no way to mark a
  fragment as one, so the one-line summary is gone — and with it a quiet flaw:
  only the first line of a rule ever survived a session, so everything after it
  was seen exactly once. A short rule is now a cheap rule.
- **The scope walk no longer stops at a repository boundary**, so a git
  submodule receives its parent repository's rules. Inside a submodule `.git` is
  a *file*, which halted the walk: a `.cs` under `libs/api/src/` received nothing
  at all, even with a `**/*.cs` rule at the parent root. The walk now runs to the
  filesystem root, which makes the ownership and permission check on a scope
  directory the only thing standing between a session and a rules directory the
  user does not control.
- **The nested-`CLAUDE.md` guard is removed.** Whether folder-scoped guidance
  belongs in a `CLAUDE.md` or in a rule is the user's policy, not the hook's to
  enforce with a `PreToolUse` deny.
- **`derive_rule_name` is a total function.** `add --glob` without `--rule` used
  to fail on `src/**/*.py`, `docs/**/*.md` and `*.cs` — the forms the docs
  present as the normal path — because wildcards in the middle of a glob reached
  the name allowlist. Names now join with a single `-`: `src/api/**` derives
  `src-api.md`, not `src--api.md`.
- **`which` no longer suggests an `add` command** when nothing matches.
- **`validate` notes rule names outside the `TYPE_what-it-asserts.md`
  convention** — the prefix says what violating the rule costs, the rest asserts
  what the rule requires. A note, never an error, and only in the CLI: the hook
  never refuses a rule over its file name.
- **`validate` compares a rule's own wording against its repeat schedule** — a
  note when the body reads like a prohibition (`never`, `must not`,
  `forbidden`, `proibido`...) but `remember_again_after` is `never`, and a note
  the other way when a rule with no prohibition language repeats tighter than
  10k tokens / 10 calls. Advice only, and only in the CLI: the hook never reads
  a rule's own text this way.

### Configuration

- **`config.json`, in three layers.** The rule taxonomy and the repeat defaults
  moved out of the code and into a file: the plugin ships one, `~/.claude/rules-
  by-path/config.json` overrides it, and a project's own
  `.claude/rules-by-path/config.json` overrides that. `rules-by-path config`
  prints the effective result and names the layer each value came from.
- **A project layer is untrusted**, because it arrives with whatever repository
  is checked out: its intervals are clamped to a floor (a repo cannot ask for a
  repeat on nearly every tool call), its type texts are bounded to one printable
  line, its prefixes must be ASCII letters and digits, and an unreadable or
  nonsensical layer is skipped rather than allowed to break injection.
- **Four rule types by default** — `BUSN` (business rules), `ARCH` (architecture
  decisions), `CONV` (conventions and definitions), `OTHR` (memory pills) — each
  with a name, a purpose and its own repeat distance. Any project or user may
  declare a different taxonomy; nothing else in the plugin carries a second copy
  of it.
- **Only `BUSN` defaults to active reinforcement.** Only prohibition-shaped
  constraints are known to decay under long context (arXiv:2604.20911) —
  requirements and conventions hold up without being repeated, and every
  repeat adds one more instruction competing for the model's attention
  regardless of type (arXiv:2608.02639) — so `ARCH`, `CONV` and `OTHR` ship
  with `remember_again_after: never`, and a rule opts back into repetition
  individually when it needs to.
- **`reinject_budget`** (config key, default 3, clamped to 0-20 in every layer
  alike, including the user's own) caps how many times ONE rule may be
  re-injected in a session regardless of how far the context has moved on —
  the first delivery is always free, only the repeats that follow it spend the
  budget, so no rule can reinject for the rest of a very long session.
- **`language`** (config key, default `en`) is what the manage skill writes rule
  bodies in — the choice stops being re-decided from the language of each
  conversation — and also the language of the text the hook injects around them:
  the session notice, the supersede and truncation notices and the reason an
  `enforce: deny` gives. Translations ship with the plugin (`en`, `pt-BR`) and
  are never taken from a config layer, only selected by one: a layer arrives
  with a cloned repository, and a language it names but the plugin does not
  ship leaves that surrounding text in English, which `config` and `validate`
  both report. The project layer wins over the global one, so a rule written
  inside a repository comes out in that repository's language — except for the
  `enforce: deny` reason, which speaks for you against the repository being
  blocked and therefore takes its language from your layers alone. The value is
  NFKC-normalized and refuses the alphanumerics that render as nothing, so what
  a human approves in the file is what the code selects. Rule file names, type
  prefixes and frontmatter keys are identifiers and never translate.
- **No config layer can take the hook down with it.** `load_layer` now answers
  any failure while validating a layer with a warning and an empty layer, and
  the numeric coercions accept `OverflowError` (`1e400` is valid JSON, and
  `json` reads it as `float('inf')`) as the deep-nesting `RecursionError` is
  now caught where the document is parsed. This is a security fix, not tidying:
  the `enforce: deny` decision runs on the same path, so an exception escaping
  one unreadable file cancelled the machine owner's own block — silently, with
  exit code 0. The denial is now decided before any config is read at all.
- **`add` now requires a type**, via `--type` or a name that already carries the
  prefix, and lists the configured types when it is missing. It is the only
  moment in the system when a human is present to judge what violating the rule
  would cost.
- **A type's repeat distance is written into the rule** at `add` time rather
  than resolved at injection time, so a rule file states its own schedule and
  the hook never has to know what a type is.
- **`migrate` became "bring this scope up to the current format"**: it renames
  pre-0.4.0 type prefixes (`Business_x.md` -> `BUSN_x.md`), rewrites
  `remember_after:` as `remember_again_after:`, and still converts a legacy
  `rules-map.yml`. Rules with no type prefix are reported, never guessed at.
- **The admin CLI is a package**, `scripts/rules_by_path_admin/`, mirroring the
  hook: one concern per module, none over 400 lines.
  `scripts/rules-by-path-admin.py` stays as the executable facade that `bin/`
  and the test suite address by path.

### Enforcement

- **`enforce: deny` on a GLOBAL rule now blocks the write tools it matches**
  (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`): the tool call is denied with
  the rule's own (defanged) body as `permissionDecisionReason`. The hook still
  validates only the PATH, never the rule's content — native
  `permissions.deny` already blocks by path; what this adds is the rule's own
  text as the pedagogical reason and not having to hand-author a permission
  entry. The identical setting on a PROJECT-scope rule is inert: a project
  scope arrives with whatever repository is checked out, and honouring
  `enforce:` there would let a cloned repository deny the user's own tool
  calls.
- **`enforce --list`/`--sync`** show the native `permissions.deny` entries an
  `enforce: deny` rule implies and write them into the project's own
  `.claude/settings.json`, idempotently — the way to turn a project-scope
  `enforce: deny` (which the hook always ignores) into one that actually
  blocks. `validate` notes when a project rule needs this.

### Reliability

- **A stale `seen` no longer survives a lost compaction race.**
  `SessionStart(compact|clear)`'s reset runs asynchronously and can lose the
  race against the very next `PreToolUse` call, leaving `seen` entries
  recorded against a much larger context than the one that follows — exactly
  the moment a rule's text has just been summarized out of the transcript. The
  hook now compares the current context-token count against the highest one
  recorded in `seen`; a drop bigger than `TOKEN_REGRESSION_SLACK` (4096 tokens)
  clears `seen` in place (`calls` survives) and the rule re-injects on that
  same call, without waiting on the async reset. The async reset is still the
  primary path; this is only the fallback for when it loses the race.
- **Editing a rule mid-session now marks the next delivery as superseding the
  old one.** The dedup key already hashes the body, so an edited rule
  re-injects on its own — but the earlier wording used to stay in the
  transcript as a stale, contradictory instruction. The fresh delivery is now
  prefixed with a one-line notice that it supersedes any earlier occurrence,
  and the stale entry is dropped from `seen` instead of leaving one dead entry
  behind per edit for the rest of the session — conflicting duplicate
  instructions are themselves a driver of long-context collapse
  (arXiv:2608.02639).

### Docs

- **Two stale claims corrected.** "Design guarantees" no longer cites a
  nested-`CLAUDE.md` block removed earlier in this cycle, and Troubleshooting
  no longer says the scope walk "stops at the repo root" — it runs to the
  filesystem root, as the Scopes section already said.
- **`/rules-by-path:status` step 5 points at the real state-file locations** —
  `$CLAUDE_PLUGIN_DATA/state/*.json`, falling back to
  `~/.claude/cache/rules-by-path/*.json` — instead of a glob that matched
  neither.
- **New "vs. native path rules" section** in the README: what Claude Code's
  own `paths:` rules and nested `CLAUDE.md` already do, the four gaps this
  plugin closes (Write/Edit/new-file triggers, decay-resistant reinforcement,
  validation/audit tooling, `enforce: deny` as policy with a pedagogical
  reason), and an explicit value claim — convention adherence and token
  economy, not task correctness.

## 0.2.0

Packaging, no behaviour change. The plugin became one directory,
`plugins/rules-by-path/`, so everything Claude Code installs lives under it and
everything outside it (the test suite, `publish.sh`) is development scaffolding
that never ships. A release now installs from GitHub; `publish.sh --local`
installs the working tree, and returns to the branch it started on even when a
step fails.

## 0.1.0

First release, on `main` but not yet public: the repository stays private while
the plugin is used and validated in real work. `1.0.0` is the version that goes
out when it does.

Everything below is what that release contains. `rules-by-path` started as
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
