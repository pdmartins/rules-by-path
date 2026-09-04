---
glob: CHANGELOG.md
tool: write
remember_again_after: 50k
---
Entries go under `## Unreleased`, never under a version number: `publish.sh`
renames that heading to the version it computes, dated, and refuses to release
an empty one.

Each version reads as a summary of at most two lines, then the Keep a Changelog
categories that have something in them — Added, Changed, Deprecated, Removed,
Fixed, Security — in that order, the empty ones omitted. An entry that requires
action on upgrade starts with `**Breaking:**`.

An entry says what changed and why it changed; the reason is the part a reader
cannot reconstruct from the diff. Write it for a human reading in six months,
wrapped at 80 columns like the rest of the file.
