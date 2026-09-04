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
  context (`additionalContext`) — the body, and nothing else:

  ```
  <rules-by-path>
  Every endpoint must validate its input.
  ---
  Never log the request body.
  </rules-by-path>
  ```

- Injection happens **once per rule version per session**, then the rule is
  **sent again, whole**, once the context has moved on by
  `remember_again_after`, so it does not fade out of a very long context.
  Editing a rule re-injects it immediately.
- Zero context cost for rules that never become relevant.

## vs. native path rules

Claude Code already ships path-scoped guidance of its own, and this plugin
does not replace it: a `paths:` rule under `.claude/rules/*.md`, or a nested
`CLAUDE.md`, both load just-in-time the moment Claude *reads* a matching file,
both reload after a compaction, and both have a user-wide counterpart —
`~/.claude/rules/` — for guidance that should apply everywhere.

Four gaps in that native behaviour are what this plugin is actually for:

1. **It only triggers on `Read`.** A rule about a file never fires the first
   time that file is *created* or edited without first being read — exactly
   the moment a convention is most likely to be broken. This was requested
   upstream and refused: anthropics/claude-code#38487 was closed not-planned.
   The hook here watches all five file tools — `Read`, `Edit`, `Write`,
   `MultiEdit`, `NotebookEdit` — so creation and editing trigger it too, and
   `tool: write` narrows a rule to exactly those (see *Narrowing a rule
   further*).
2. **Nothing resends a rule once it fades.** A rule injected hundreds of
   thousands of tokens ago has effectively left a long session's context;
   nothing native repeats it. `remember_again_after` does.
3. **No lint or audit surface.** `validate`, `which` and the admin CLI answer
   "what would fire here", "which rules are dead weight", and let rules be
   managed at scale from a script — none of which the native mechanism
   exposes.
4. **No policy tied to a human reason.** Native `permissions.deny` blocks
   silently. A **global** rule with `enforce: deny` blocks too, and shows the
   rule's own text as *why* (see *Enforcing a rule* below) — the hook does not
   validate the rule, only the path; the added value is a pedagogical reason
   plus not having to hand-author a permission entry.

On global scope specifically, the honest claim is narrower than "better":
it is that this plugin's global rules are **consistently trusted** — every
rule in `~/.claude/rules-by-path/` is treated as trusted input, uniformly.
The native equivalent has had scope bugs of its own (e.g.
anthropics/claude-code#17204), so "global scope done right" is a differentiator
that can narrow, or disappear outright, as the native implementation matures —
not something to lean on permanently.

**What this buys you, stated plainly: convention adherence and token
economy** — a rule reaches context exactly when its glob matches, and costs
nothing when it does not. It is explicitly **not** a claim about task
correctness: two 2026 ablation studies found that injecting a rule into
context, on its own, moves correctness on the underlying task by close to
nothing. Use this to keep an agent inside a team's conventions cheaply, not to
make it solve harder problems.

## Install

In Claude Code:

```
/plugin marketplace add pdmartins/rules-by-path
/plugin install rules-by-path@pdmartins
```

Then run `/rules-by-path:doctor` once — it checks prerequisites, smoke-tests
the hook, and offers the recommended permission hardening (`doctor --fix`).

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
└── CONV_api-returns-problemdetails.md
```

```markdown
---
glob: src/api/**
remember_again_after: 50k
---
Every endpoint validates its input and returns ProblemDetails on error.
```

The `CONV_` prefix is the rule's **type** — what violating it would cost. Four
types ship (`BUSN` business rules, `ARCH` architecture decisions, `CONV`
conventions, `OTHR` memory pills), each with its own repeat distance, and you
can declare your own (see *Configuration*). Only `BUSN` repeats by default:
prohibition-shaped constraints are the ones measured to decay under long
context, so the other types default to `never` and pay reinforcement's token
cost only when a rule opts back in.

A rule can declare several globs, and several rules can share one glob — they
all inject together.

**Keep a rule to one scope.** Every file a glob matches receives the *whole*
rule, so constraints that govern different paths belong in different rules. A
rule saying "controllers look like X, the DI file looks like Y, no file over 300
lines" under `src/Api/**` tells a controller how DI works and the DI file how
controllers are shaped; as three rules — `src/Api/**`, `src/Api/Controllers/**`,
`src/Api/DependencyInjection.cs` — each file gets only what changes what you do
to it. `add`, `update`, `migrate` and `validate` flag the detectable version of
this: a rule whose text names a file or folder that exists under its own glob.

From then on, whenever Claude touches anything under `src/api/`, the rule
appears in its context — once per session, exactly when it matters.

Other things you can ask for: *"list the path rules"*, *"remove the rule for
docs/"*, *"update the terraform rule"*.

## Scopes

| Scope | Location | Globs match |
|---|---|---|
| **Project** | `<project-root>/.claude/rules-by-path/` | paths relative to the project root |
| **Global** | `~/.claude/rules-by-path/` | absolute paths |

Nested projects work: every `.claude/rules-by-path/` above a touched file
applies, all the way up to the filesystem root. The walk does not stop at a
repository boundary, so a git submodule receives its parent repository's rules.
Your global rules are budgeted first, so rules arriving with a cloned repo can
never crowd them out. Project rules are committed with the repo, so the whole
team shares them.

A rule can change scope — `move --rule <name> --root <root> --to-global`, or
`--global … --to-root <root>` — and the CLI rewrites its globs for the new
frame: bare names and `**/`-floating globs travel as they are, an absolute glob
becomes project-relative, and a root-anchored glob going global is refused
until you say whether it should hold in any project (`--anchor any-project`,
giving `**/src/api/**`) or only in this one (`--anchor this-project`).

## Narrowing a rule further

A glob answers *where*. Two more frontmatter keys answer *which paths inside
it* and *when* — both restrictive, and ANDed with the glob and with each other.
A rule reaches the model when one `glob` matches, no `exclude` matches, and the
tool call is of a kind `tool:` accepts.

```markdown
---
glob: src/**
exclude: src/**/*.test.ts
tool: write
---
Every exported function is documented with TSDoc.
```

- **`exclude:`** takes paths back out of a glob. Same syntax as `glob` (one
  value or a list), same limits. It says the thing a glob alone cannot:
  `src/**` *except* the tests, the generated code, the vendored tree.
- **`tool:`** restricts the rule to `write` calls (Write, Edit, MultiEdit,
  NotebookEdit) or to `read` ones. `any`, or leaving the key out, means both.

`tool: write` is the one that pays for itself twice. Most conventions govern
what you *create*, not what you *read* — and a rule spent on a Read is both a
context cost with no decision attached and, because dedup is per session, a
rule that may no longer be there when the Write finally happens.

A filter can only ever narrow a rule, so a value the hook cannot read is
**ignored, not enforced**: `tool: wirte` leaves the rule unfiltered rather than
silently switching it off. `validate` reports the typo, and refuses outright the
two shapes that disable a rule without saying so — an `exclude` that takes back
every path, and one that cancels every glob the rule declares.

`which` explains the outcome for a concrete path:

```
$ rules-by-path which --root . --path 'src/api/users.test.ts'
excluded: rule CONV_tsdoc.md — 'src/**' covers this path, exclude: 'src/**/*.test.ts' takes it back

$ rules-by-path which --root . --path 'src/api/users.ts' --tool read
filtered: rule CONV_tsdoc.md — 'src/**' covers this path, but the rule is tool: write only
```

## Repeating a rule

A rule is injected the first time it is relevant, then sent again once the
context has moved on by 30k tokens (the shipped default). On a long-context session a rule injected
hundreds of thousands of tokens ago has effectively faded.

The distance is measured in **context tokens**, read from the session
transcript — the count the API itself billed. That is the honest unit: a session
that reads three huge files burns 200k tokens in three tool calls, while one
doing fifty tiny greps burns 20k in fifty. Where the transcript cannot be read,
the hook falls back to counting file-tool calls (default 25) and says so in
`/rules-by-path:status`. There is no conversion between the two units.

There is no short form of a repeat: with no header in the emitted text, there is
no way to mark a fragment as one, so the whole body is resent. **A short rule is
a cheap rule.**

A rule is only ever repeated when its glob matches the file being touched. A
rule governing a folder nobody opens again is never repeated, however long the
session runs.

The default comes from the rule's type, then from `config.json` (see below);
`RULES_BY_PATH_REMEMBER_AGAIN_AFTER` overrides it for one session, and
`remember_again_after:` in a rule's own frontmatter overrides everything —
tokens (`30k`, `1M`), calls (`25 calls`), or `never`:

```markdown
---
glob: infra/**
remember_again_after: 50k
---
```

## Usage stats, and improving rules with them

Every injection is counted, per rule, in one small file beside the session
state (`usage-stats.json`): injections, repeats, distinct sessions, first and
last date, the directories the rule fired under and the glob that matched —
every collection bounded, session ids never shown. `status` prints it next to
each rule and derives two notes from it: a rule **never injected** since stats
began, and a rule that fires often but **always under one subfolder** of a
wider glob, with the narrower glob to use.

The `rules-by-path:improve` skill turns that, plus the validator's notes, into
proposals — prune, narrow, split, reword — and harvests path-bound
instructions out of `CLAUDE.md` files and native `.claude/rules/*.md` into
rules that also fire on writes. Its second input, `digest --root <root>`,
lists those sources and distills your own turns from this project's most
recent sessions (harness noise dropped, everything bounded), pairing each
session with the rules injected in it, so a correction that followed an
injection is visible as a rule that did not land. It reads only this
project's transcripts, and only when asked.

## Configuration

The rule taxonomy, the repeat defaults, the size limits and the language rules
are written in all live in a `config.json` read from three layers, each
overriding the one before it:

| Layer | Where | Trusted |
|---|---|---|
| Plugin | `<plugin>/config.json` — the shipped default | yes |
| User | `~/.claude/rules-by-path/config.json` | yes |
| Project | `<project>/.claude/rules-by-path/config.json` | **no** |

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

`rules-by-path config --root <root>` prints the effective result and names the
layer each value came from. `rule_types` is replaced whole by the nearest layer
that declares it — merging two taxonomies by prefix would produce a hybrid
nobody wrote; the other keys merge key by key.

A project layer arrives with whatever repository is checked out, so it is
treated like any other repository content: its intervals are clamped to a floor
(a clone cannot ask for a repeat on nearly every tool call), its type texts are
bounded to one printable line, its prefixes must be ASCII letters and digits,
and it may **shorten** `max_chars` but never lengthen it. A layer that cannot be
parsed is skipped with a warning; nothing about a config can stop injection.

### `language`

`language` is what the manage skill writes rule **bodies** in, so the choice
stops being re-made from the language of each conversation. It is also the
language of the text the hook injects around them — the session notice, the
supersede and truncation notices, the reason an `enforce: deny` gives —
whenever the plugin ships a translation of it. Shipped: `en` (the default) and
`pt-BR`; `pt_br` and `PT-BR` select the same one.

Any other language is a perfectly good value: the rules are written in it, and
only that surrounding text falls back to English — `config` and `validate` both
say so rather than letting it be a surprise. The scaffolding is never taken
from configuration, only selected by it: a layer arrives with a cloned
repository, and supplying the wording of the text the model trusts most is not
something a clone may do. For the same reason the value itself is bounded to 32
characters of visible letters, digits, spaces and `-_()`, normalized to NFKC so
a lookalike of `en` is `en`, and anything else is warned about and ignored. The
allowlist buys exactly one thing — the value cannot forge a delimiter, a
frontmatter key or a second line — so the CLI quotes it rather than reading it
out as prose. The `enforce: deny` reason is the one exception to the project
winning: that sentence speaks for you against the repository being blocked, so
only your own layers choose the language it arrives in.

Rule file names, type prefixes (`BUSN`, `ARCH`, …) and frontmatter keys are
identifiers, not prose, and never translate.

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

The same syntax reads an `exclude:` entry — it is a glob like any other, only
matched to take a path back rather than to cover it.

A bare name with no `/` (like `docs` or `Makefile`) is matched two ways: against
the project-root path (so `docs` covers the root `docs` entry and everything
under it) **and** against the file's basename at any depth (so `Makefile` catches
every `Makefile`, and `docs` also matches a file literally named `docs`
anywhere). To target a `docs/` folder wherever it appears, use `**/docs/**`.

## Design guarantees

- **Never blocks work by accident**: any internal hook failure goes to stderr
  and the tool call proceeds untouched. The hook denies a tool call only
  through one deliberate, narrow path — a **global** rule with `enforce: deny`
  matching a write (see *Enforcing a rule* below) — never as a side effect of a
  failure. The recommended hardening's own `permissions.deny` entries (see
  *Security model*) are a second, independent way to deny, which the hook has
  no part in enforcing.
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
- **Bounded trust**: a rules directory owned by another user, or in a
  world-writable parent (a shared `/tmp`, say), is ignored. Since the upward
  search runs to the filesystem root, this ownership check is what stands
  between you and a rules directory you do not control. It relies on POSIX
  permission bits and is not enforced on Windows.
- **The outermost rules always apply**: your global scope is consulted first
  and the outermost project scope next, and both keep their slot when the
  8-scope cap is reached. Nested `.claude/rules-by-path/` directories — which
  anyone opening a PR can add — cannot crowd out the rules declared above them.
- **No forgeable provenance, because none is emitted**: the injected text is the
  rule bodies between a pair of tags. Nothing states a rule's name, glob or
  scope, so there is no authority claim for content to forge. What content is
  stopped from doing is closing the block early or impersonating the harness
  (`<system-reminder>`, `<function_calls>`) — those markers are defanged inside
  rule bodies. A rule file carries the authority any file in your repository
  carries; treat one arriving in a clone the way you would treat its
  `CLAUDE.md`.
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

`/rules-by-path:doctor` offers deny-list entries for your
`~/.claude/settings.json` (`doctor --fix` writes them, after you agree):

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

### Enforcing a rule (`enforce: deny`)

Native `permissions.deny` blocks a tool call with no explanation attached. A
rule can ask for the same block, plus one thing native deny does not offer: its
own body as the reason a human or model actually reads.

```markdown
---
glob: infra/prod/**
enforce: deny
---
Production infrastructure is changed through the deploy pipeline only, never
by hand. Open a PR against `infra/` instead.
```

The hook still does not read a rule for CORRECTNESS — it only ever matches a
path — so `enforce: deny` is exactly the native deny, with the rule's
(defanged) text attached as `permissionDecisionReason`. It fires only for
`Write`, `Edit`, `MultiEdit` and `NotebookEdit`; `Read` is never denied.

**Trust gate: honoured from the GLOBAL scope only.** A project's
`.claude/rules-by-path/` arrives with whatever repository is checked out —
exactly as untrusted as its `CLAUDE.md` — so a project rule that declares
`enforce: deny` is inert to the hook, silently, no matter how it is worded.
There is no config, environment variable or project layer that widens this: it
is keyed to which scope actually matched, not to anything a repository could
set. `validate` still points it out, with the way around it:

```bash
"<plugin>/bin/rules-by-path" enforce --root <project-root> --list   # what would fire, and what it maps to
"<plugin>/bin/rules-by-path" enforce --root <project-root> --sync   # write the native deny entries for real
```

`--sync` writes one `Edit(<glob>)` entry per glob into that project's own
`.claude/settings.json` — `Edit(...)` alone, because it already covers every
file-editing tool (see *Recommended hardening* above); idempotent, and it
creates a minimal `settings.json` if the project has none yet. A global rule
needs no such sync: the hook already enforces it directly, so `--sync --global`
is refused.

## Uninstalling

`/plugin uninstall rules-by-path@pdmartins` removes the hook and the
skills. Three things outlive it. `doctor --uninstall` removes the first two —
the deny-list entries above (otherwise those paths stay unreadable) and the
cached state at `~/.claude/cache/rules-by-path` — and deliberately keeps the
third, your authored rules in `~/.claude/rules-by-path/` and each project's
`.claude/rules-by-path/`, listing them so you can decide.

## Troubleshooting

Two commands answer nearly everything; `/rules-by-path:status` and the
`rules-by-path:doctor` skill run them for you:

```bash
# both scopes, their findings, what covers a path, the config in force, usage
"<plugin>/bin/rules-by-path" status --root <root> [--path <file>] [--json]
# every setup check, each finding naming its fix; --fix applies the safe ones
"<plugin>/bin/rules-by-path" doctor --root <root> [--fix]
```

- **Rule not injecting?** Each rule version injects once per session. The
  state lives in `$CLAUDE_PLUGIN_DATA/state/` for a plugin install (falling
  back to `~/.claude/cache/rules-by-path/`); delete `<state-dir>/<session_id>.json`
  to force re-injection. Check that a scope containing the rule is actually on
  the path from the touched file up to the filesystem root — the walk does not
  stop at a repository boundary, so this is rarely the cause — and that
  `validate` reports it.
- **Upgrading from the `rules-map.yml` format, or from pre-0.4.0 names?**
  `doctor` reports it and `doctor --fix` runs `migrate` for you. Until then,
  a scope holding a `rules-map.yml` injects nothing and the hook says so.
- **Nothing happens at all?** `doctor` smoke-tests the hook and reports
  whether Python was found.
- **Hook errors** are printed to stderr (visible in verbose mode) and never
  block the tool call.

## Repository layout

The plugin is one directory. Everything else in this repository is the
marketplace that publishes it, or scaffolding that builds and tests it — none
of which is installed on a user's machine.

```
.claude-plugin/marketplace.json   the marketplace (this repo is one)
plugins/
└── rules-by-path/                THE PLUGIN — this, and only this, is installed
    ├── .claude-plugin/plugin.json
    ├── hooks/                    PreToolUse injection + SessionStart
    ├── bin/                      launchers (POSIX + .cmd), on PATH when installed
    ├── scripts/                  the management CLI the skills drive
    ├── skills/                   manage, doctor, improve
    └── commands/                 /rules-by-path:status
tests/                            development only
publish.sh                        development only
README.md  CHANGELOG.md  LICENSE
```

If it is not under `plugins/rules-by-path/`, Claude Code never sees it.

## Development

```bash
python3 -m unittest discover -s tests    # the suite, standard library only
claude plugin validate . --strict        # both manifests
bash publish.sh --local                  # install the working tree on this machine
```

`--local` reinstalls rather than updating on purpose: the version is
`MAJOR.MINOR.REVISION` and changes **only** on a release, so `claude plugin
update` would compare two identical version strings and keep serving the cached
copy.

The mode also decides where the install comes from, and the script repoints the
marketplace to match: a release installs from **GitHub** — exactly what it just
published, exactly what a user gets — while `--local` installs from **this
directory**, the only way to run code that is not released yet. The marketplace
name never changes, so the install id stays `rules-by-path@pdmartins` either way
and the two can never both be installed.

`bash publish.sh --minor` (or `--major` / `--revision`) is the release. It
refuses on a dirty tree, a failing suite or invalid manifests; then it bumps
both manifests, merges `develop` into `main`, pushes, points GitHub's default
branch at `main` — `/plugin marketplace add` reads that branch — and refreshes
the local install. `--dry-run` prints the plan without touching anything.

## Roadmap

- Adapters for other agents that support pre-tool hooks or rule injection
  (Codex, Antigravity, …) — the core is plain Python with no Claude-specific
  logic beyond the hook I/O envelope.

## License

[MIT](LICENSE)
