---
name: manage
description: >
  Register, list, update or remove path-scoped rules for the rules-by-path
  system — markdown rules auto-injected into context by a PreToolUse hook
  whenever Claude touches a file matching a glob. Use whenever the user asks to
  create/manage a rule tied to a folder or path, in any language, e.g. "add a
  rule for src/api", "when touching X follow Y", "create a folder-scoped
  rule", "list/remove the per-path rules". Rules live in
  .claude/rules-by-path/ (project scope) or ~/.claude/rules-by-path/ (global
  scope).
---

# rules-by-path — managing path-scoped rules

## The one command

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" <subcommand> --root "<project-root>" [...]
```

Use `--global` instead of `--root` for the machine-wide scope.

## What a rule is

One markdown file that declares its own glob in frontmatter:

```markdown
---
glob: src/api/**
---
Every endpoint validates its input and returns ProblemDetails on error.
```

That is the whole format. There is no index file, so nothing can fall out of
sync. A rule may declare several globs, and several rules may share one glob —
they all inject together.

Always write rules through the CLI rather than with a file tool: users who
applied the recommended hardening have those paths deny-listed, and the CLI
validates what it writes.

## How injection works

- The hook reads the rules on every Read/Edit/Write/MultiEdit/NotebookEdit and
  injects the ones whose glob matches the touched file.
- What reaches the model is the rule bodies and nothing else — an opening tag,
  the bodies separated by a `---` line, a closing tag. No preamble, no rule
  name, no glob, no scope: nothing about a rule's origin is emitted.
- A rule is injected **once per session**, then **sent again, whole**, once the
  context has moved on by `remember_after` — and only when the rule's glob
  matches again, so a rule for a folder nobody reopens is never repeated.
  Default 30k tokens, or 25 file-tool calls when the token count cannot be read.
  The value takes tokens (`30k`, `1M`), calls (`25 calls`), or `never`.
  `RULES_BY_PATH_REMEMBER_AFTER` sets the default; `remember_after:` in a rule's
  frontmatter overrides it per rule.
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

## Adding a rule

1. **Pick the scope.** Project by default; global only when the user says it
   applies everywhere. If ambiguous, ask.

2. **Check what already covers the target:**

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" which --root "<root>" --path '<folder-or-file>'
   ```

   It reports rule *file names*. An existing rule about the same concern should
   be updated (step 4); a different concern is a new rule, even for the same glob.

3. **Name it** — always pass `--rule` (see *Naming a rule* below). The name
   derived from the glob is a fallback for when you have nothing better, not the
   normal path.

4. **Create it** — body on stdin:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" add --root "<root>" \
     --glob 'src/Application/**/*Handler.cs' \
     --rule 'Architecture_handlers-inherit-base.md' <<'EOF'
   <the rule, written by or with the user>
   EOF
   ```

   Repeat `--glob` for several globs. `--remember-after` takes tokens (`30k`,
   `1M`), calls (`25 calls`), or `never` for a rule that should not be
   repeated.

5. **Update by name**, never by glob:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" show   --root "<root>" --rule 'Architecture_handlers-inherit-base.md'
   "${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" update --root "<root>" --rule 'Architecture_handlers-inherit-base.md' <<'EOF'
   <new body>
   EOF
   ```

   `show` is the sanctioned way to read a rule under the hardening. Read before
   you overwrite: `update` replaces the whole body (it keeps the globs).

   **Single-quote every value that came out of a rule** — `--rule 'Architecture_handlers-inherit-base.md'`,
   `--glob 'src/api/**'`. Names and globs are repository data that this CLI hands
   back to you: a rule name is restricted to letters, digits and `._-`, so it
   cannot carry shell syntax, but a glob is not restricted at all, and `$(...)`
   and backticks expand inside double quotes just as they do unquoted. Single
   quotes are always safe here, because neither a name nor a rule this CLI wrote
   can contain one.

## Naming a rule

A rule file name is **`Type_what-it-asserts.md`**, and the type prefix is
mandatory. Three types, chosen by what a violation costs:

| Prefix | Violating it means |
|---|---|
| `Business_` | the software is **wrong** — an invariant of the domain broke |
| `Architecture_` | code went to the **wrong place or shape** — wrong folder, wrong base class, a duplicate of something that exists |
| `Convention_` | **consistency** was lost — formatting, naming, a house style |

```
Architecture_handlers-inherit-base.md
Architecture_application-enums-single-folder.md
Business_order-cannot-be-cancelled-after-invoicing.md
Convention_api-returns-problemdetails.md
```

After the prefix: lowercase words joined by `-`, ASCII only, no other
punctuation — `^(Business|Architecture|Convention)_[a-z0-9]+(-[a-z0-9]+)*\.md$`.
`_` separates the type, `-` separates words, and nothing else appears, so the
boundary is unambiguous to the eye, to `ls` and to a regex.

Two things to get right:

- **The name is an assertion, not a coordinate.** `Architecture_src-application.md`
  says where; `Architecture_handlers-inherit-base.md` says *what*, and that is
  what a human reads in `list` and `which` when choosing which rule to open.
  `validate` prints a note for names outside this convention — a note, never an
  error: a rule with any name still loads and still injects.
- **If you cannot tell which type it is, ASK the user.** Do not guess and do not
  default to `Convention_`. The type is a judgement about what breaking the rule
  costs, and only the person who owns the codebase knows that.

The type does not travel to the model — nothing about a rule's origin is
injected. It is there so a human can see, from a directory listing, what kind of
rules a project has accumulated. When the *distinction* matters to whoever reads
the rule in context, say it in the rule's own first line, where it costs ten
characters and is unambiguous:

```markdown
Business: an order cannot be cancelled after it has been invoiced.
```

## Writing a good rule

A rule states a **constraint that changes what you do**. What that excludes is
*descriptive* text — a tour of how a module works. What it INCLUDES, and this is
the part people get wrong: **prescriptive placement**. Where new things go, what
they inherit from, what to reuse before creating something new. "Enums live in
`Application/Enums`" is knowledge you could get by reading the code, and it is
still a rule, because the failure it prevents is real:

> A session created `Application/Enum/` next to the existing
> `Application/Enums/`, and wrote a method into a handler instead of inheriting
> from the `BaseHandler` that already had it.

**The counter-intuitive part: an anti-duplication rule needs its glob on the
WRONG area, not the right one.** A rule globbed at `src/Application/Enums/**`
never fires when the agent creates `Application/Enum/` — it never touches the
canonical path; that IS the bug. The glob has to be `src/Application/**`. Same
for the handler: the rule must match *any* handler, including the one that does
not inherit from the base.

Prefer a procedure with the failure mode named over an inventory of facts. An
inventory ("enums live in X") costs nothing and rots silently at every refactor;
a procedure does not:

```markdown
---
glob: src/Application/**
---
Before creating a folder here, list `src/Application/` and reuse the one that
exists. Near-homonym folders (`Enums`/`Enum`) have been created by mistake; the
canonical enum folder is `Enums`.
```

Keep it short: the CLI warns above 2,000 characters and the hook truncates at
4,000. A repeat resends the whole body, so length is paid again every time the
rule is refreshed. If a rule is growing, it usually wants to be split — see
below.

## One rule, one scope

Every file a glob matches receives the **whole** rule. So before writing, ask of
each constraint: *which paths does this actually govern?* Constraints that
answer differently belong in different rules. This is the plugin's entire
premise applied one level down — nothing should reach the context until it is
relevant, and "relevant" is decided per rule.

A worked example. This is three rules wearing one coat:

```markdown
---
glob: src/Api/**
---
Controllers follow pattern X.
DependencyInjection.cs follows pattern Y.
No file may exceed 300 lines.
```

Touch a controller and you are told how DI registration works; touch the DI file
and you are told how controllers are shaped. Neither can act on the other's
constraint, and both pay for it on every session. Split by the paths each
constraint governs:

| Glob | Constraint |
|---|---|
| `src/Api/**` | no file over 300 lines |
| `src/Api/Controllers/**` | controllers follow pattern X |
| `src/Api/DependencyInjection.cs` | DI follows pattern Y |

A controller now receives exactly two rules, and both change what you do to it.

Do not over-split either: constraints that govern the *same* paths belong in one
rule. The test is the path set, never the topic.

`add`, `update`, `migrate` and `validate` flag the obvious version of this — a
rule whose text names a file or folder that exists under its own glob:

```
note: src--Api.md: mentions 'Controllers', 'DependencyInjection.cs', which live
under 'src/Api/**' but are narrower than it. ... belongs in its own rule:
--glob 'src/Api/Controllers/**' / --glob 'src/Api/DependencyInjection.cs'
```

That check only sees names that exist on disk, so it catches the common case and
nothing else — the judgement stays yours. Apply it when updating too: a new
constraint that governs a narrower path is a new rule, not another bullet on an
existing one.

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

A bare name with no `/` (e.g. `docs`, `Makefile`) matches both the project-root
path AND the file's basename at any depth — so `docs` also matches a file named
`docs` anywhere, not only the root folder. Use `**/docs/**` for a `docs/` folder
wherever it appears.

## Listing, validating, removing

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" list     --root "<root>"
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" validate --root "<root>"
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" remove   --root "<root>" --rule 'Architecture_handlers-inherit-base.md'
```

`validate` reports rules that can never fire (no glob), empty rules, and notes
long rules, shared globs and a total that exceeds one injection's budget. When
the user asks "which rules exist?", check both scopes.

## Migrating an old installation

A scope still holding `rules-map.yml` injects nothing, and the hook says so in
context. Convert it once:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" migrate --root "<root>"   # or --global
```

## Reminders

- Files inside `.claude/rules-by-path/` never trigger injection themselves.
- Rule content is untrusted input and is not dressed up as anything more
  trustworthy than it is: a rule file carries exactly the authority any file in
  the repository carries. What the hook does defend is the boundary — content
  cannot close the block early nor impersonate the harness.
- The hook never blocks a tool call: on any internal failure it warns on stderr
  and stays silent.
