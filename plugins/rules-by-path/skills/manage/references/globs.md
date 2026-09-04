# Choosing and narrowing globs

Read this when the glob is not obvious, or when a rule needs `exclude`/`tool`.

## Narrowing further: `exclude` and `tool`

A glob answers *where* a rule applies. Two flags narrow it, and every filter is
restrictive — all of them must be satisfied for the rule to reach the model:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" add --root "<root>" \
  --glob 'src/**' --exclude 'src/**/*.test.ts' --tool write \
  --type CONV --rule 'CONV_exported-functions-are-documented.md' <<'EOF'
Every exported function is documented with TSDoc.
EOF
```

- `--exclude` takes paths back out of a glob (repeat it for several). Use it
  instead of enumerating narrower globs when the rule governs "everything under
  X except Y": tests, generated code, a vendored tree.
- `--tool write` restricts the rule to Write/Edit/MultiEdit/NotebookEdit;
  `--tool read` to reads; `--tool any` clears the restriction on an `update`.

**Reach for `--tool write` whenever the rule is about what gets CREATED or
CHANGED** — placement, naming, what to inherit from, what to reuse. Reading a
file is not a moment such a rule can change anything, and a rule spent on a Read
may be gone (dedup is per session) by the time the Write happens. Leave the
filter off for rules that also matter while reading: an invariant that explains
what the code means, a warning about a trap in the data.

A value the hook cannot read is ignored rather than enforced, so a typo leaves
the rule unfiltered instead of silently switching it off — `validate` reports
it. `validate` refuses two shapes outright: an `exclude` of `**`, and an
`exclude` that cancels every glob the rule declares.

Both filters survive a `show` -> edit -> `update` round trip. Deleting the line
from the submitted frontmatter is how you REMOVE one — what stdin declares is
the whole truth when it carries the rule's own frontmatter.

`which` reports them, and explains a rule that covers a path and still will not
fire:

```
match: rule CONV_exported-functions-are-documented.md (write only)
excluded: rule CONV_x.md — 'src/**' covers this path, exclude: 'src/**/*.test.ts' takes it back
filtered: rule CONV_x.md — 'src/**' covers this path, but the rule is tool: write only
```

Add `--tool read` or `--tool write` to `which` to ask what fires for that kind
of call specifically.

## Choosing the glob

**Default to the narrowest glob that still covers every file the rule governs.**
A glob wider than the rule's reach spends context on files the rule cannot
change: touch anything under it and the whole body arrives, relevant or not.
`src/Api/Controllers/**` is better than `src/**` when the constraint is about
controllers; `src/config/database.yml` is better than `src/config/**` when it is
about that one file.

Work it out from the failure, not from the topic: **which file will the agent
have open at the moment it is about to break this rule?** Glob that. A rule
about how DI is registered belongs on the DI file, because that is the file
being edited when it goes wrong.

**The counter-intuitive case: an anti-duplication rule needs its glob on the
WRONG area, not the right one.** A rule globbed at `src/Application/Enums/**`
never fires when the agent creates `Application/Enum/` — it never touches the
canonical path; that IS the bug. The glob has to be `src/Application/**`. Same
for a "handlers inherit from BaseHandler" rule: it must match *any* handler,
including the one that does not inherit from the base. Widening is right exactly
when the failure happens outside the canonical path — and when you widen, say so
in the rule, so the next person does not "fix" the glob back.

Check the reach before you commit to it:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" which --root "<root>" --path '<a file the rule should govern>'
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-path" which --root "<root>" --path '<a file it should NOT>'
```

The second call is the one people skip. A glob that also covers half the
repository is not a rule, it is a tax.

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
