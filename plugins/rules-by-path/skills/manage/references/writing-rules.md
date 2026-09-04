# Writing a good rule

Read this before drafting a body, and before storing anything longer than a paragraph.

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

Where such a rule's glob goes is not obvious — see *Choosing the glob* in
`globs.md`,
which is where that decision is made.

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

Keep it short. The limits are configured (`rules-by-path config` prints them;
2,000 characters soft and 4,000 hard by default), and a repeat resends the whole
body, so length is paid again every time the rule is refreshed. If a rule is
growing, it usually wants to be split — see below.

## One rule, one scope — and one type

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

Do not over-split either: constraints that govern the *same* paths **and** are
of the *same* type belong in one rule. The test is the path set and the type,
never the topic.

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

## When a memory arrives whole

Users do not hand you one constraint at a time. They paste a page of hard-won
knowledge — "here is everything about our handlers, our enums and our release
process" — and expect it to be remembered. **Do not store that as one rule.**
Decompose it first, then write N small rules.

Split along three axes, in this order:

1. **By type.** Business invariants, architecture decisions and conventions
   answer to different prefixes and carry different repeat distances. Two
   sentences of different types are two rules even when they govern the same
   folder — the type is part of the identity of a rule, not decoration on it.
2. **By path set.** Within one type, the section above applies: two constraints
   that govern different paths are two rules.
3. **By size.** A rule is resent *whole* every time it is repeated, so length is
   paid again and again. Two limits, both in `config.json` and both printed by
   `rules-by-path config`: a soft one the CLI warns at (2,000 characters by
   default) and a hard one the hook truncates at (4,000). Treat the soft limit
   as the ceiling, not the target — if a fragment is still past half of it after
   the first two cuts, look for the seam again: a rule that long is usually a
   procedure with an inventory bolted onto it, and the inventory is the part
   that rots.

Then:

- **Each fragment must stand alone.** A rule may not say "as in the rule about
  handlers" — rules are injected independently and in no guaranteed order, and
  the reader may have only this one.
- **Name each one for what it asserts**, and confirm the type of each with the
  user when it is not obvious. One paste can produce a `BUSN`, an `ARCH` and a
  `CONV`; asking once, about all three, costs one exchange — one question per
  fragment in a single `AskUserQuestion` call.
- **Run `validate` at the end** and read its notes: they catch the split you
  missed.

If the user is watching, say what you split and why in one line per rule — they
are the one who will read these file names in six months.
