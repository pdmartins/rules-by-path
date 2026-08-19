"""Finding the rule directories that apply to a touched file, and refusing
the ones that are not safe to read: another user's, world-writable, or reached
through a symlinked `.claude`."""

import os
import stat

from .constants import (MAX_ANCESTOR_STEPS, MAX_SCOPES,
                        RULES_DIR_RELPATH, warn)


def is_safely_owned(path):
    """True when `path` is owned by us and not world-writable.

    A rules directory in a world-writable shared parent (say /tmp) would let any
    local user inject instructions into every session below it. Group-writable
    is left alone on purpose: shared-group directories are the norm on team
    machines, and rejecting them would break more than it protects."""
    if os.name == "nt":
        return True  # POSIX ownership bits do not carry over; skip the check
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if info.st_uid != os.geteuid():
        return False
    return not bool(info.st_mode & stat.S_IWOTH)

def scope_is_contained(base_dir, scope_dir):
    """True when `scope_dir` physically lives at `base_dir/.claude/rules-by-path`.

    Validating only what is *inside* the scope is not enough: if `.claude` or
    `.claude/rules-by-path` is itself a symlink, every check below it resolves
    through the same link and passes trivially, so reads, writes and deletes
    land wherever the link points — including the user's global rules."""
    expected = os.path.join(os.path.realpath(base_dir), RULES_DIR_RELPATH)
    return os.path.realpath(scope_dir) == expected

def usable_scope(base_dir, is_global=False):
    """The scope directory for `base_dir`, or None when it is absent or unsafe.

    Physical containment is required for a PROJECT scope, where a symlinked
    `.claude` can arrive inside a cloned repository and redirect everything to
    the attacker's target. It is NOT required for the global scope: `~/.claude`
    is the user's own configuration, symlinking it elsewhere (shared config,
    dotfiles in a repo) is a normal and deliberate choice, and nobody can plant
    that link without already owning the home directory. Ownership of the real
    target is still checked in both cases."""
    scope_dir = os.path.join(base_dir, RULES_DIR_RELPATH)
    if not os.path.isdir(scope_dir):
        return None
    if not is_global and not scope_is_contained(base_dir, scope_dir):
        warn(f"ignoring {scope_dir}: it does not physically live inside {base_dir}")
        return None
    if not is_safely_owned(os.path.realpath(scope_dir)):
        warn(f"ignoring {scope_dir}: not safely owned (world-writable or another user's)")
        return None
    return scope_dir

def find_scopes(start_dir):
    """[(base_dir_or_None, scope_dir, label)] for a touched file: the global
    scope first, then every project scope from the highest ancestor down to the
    touched file's own directory.

    The walk goes all the way to the filesystem root and collects every
    `.claude/rules-by-path` on the way. It used to stop at the first `.git`,
    which silently excluded git submodules: inside one, `.git` is a *file*, so
    `os.path.exists` matched and the walk halted there — a `.cs` under
    `libs/api/src/` received nothing at all, even with a `**/*.cs` rule at the
    parent repository's root.

    What that costs, stated plainly: a rules directory in an ancestor the user
    does not control can now inject into every session below it. The ownership
    and permission filter in `usable_scope` — another user's directory and
    world-writable ones are refused — is what remains of that defence.

    Two orderings, both deliberate, both about who gets served when a budget
    runs out. Global comes first so the user's own rules always get budget
    before rules that arrived with a cloned repository. Among project scopes the
    highest ancestor comes first, and it is the one kept when MAX_SCOPES is
    exceeded: the walk discovers scopes deepest-first, so a naive cap drops it —
    and anyone able to add directories to a repo (a PR into a monorepo, a
    vendored dependency) could bury the outer rules under a chain of nested
    scopes and silently suppress them for that whole subtree.
    """
    scopes = []
    seen = set()
    home = os.path.realpath(os.path.expanduser("~"))

    global_scope = usable_scope(home, is_global=True)
    if global_scope:
        seen.add(os.path.realpath(global_scope))
        scopes.append((None, global_scope, "global"))

    chain = []  # project scopes, deepest first
    directory = start_dir
    steps = 0
    while steps < MAX_ANCESTOR_STEPS:
        steps += 1
        scope_dir = usable_scope(directory)
        if scope_dir:
            real = os.path.realpath(scope_dir)
            if real not in seen:
                seen.add(real)
                chain.append((directory, scope_dir))
        parent = os.path.dirname(directory)
        if parent == directory:
            break  # filesystem root
        directory = parent

    chain.reverse()  # highest ancestor first
    room = max(1, MAX_SCOPES - len(scopes))
    if len(chain) > room:
        warn(f"more than {MAX_SCOPES} scopes apply; keeping the outermost and "
             f"the {room - 1} nearest to the file, ignoring the rest")
        # Keep the outermost scope and the ones closest to the touched file;
        # drop the middle of the chain, which is the part nothing depends on.
        chain = chain[:1] + chain[len(chain) - (room - 1):] if room > 1 else chain[:1]
    for base_dir, scope_dir in chain:
        scopes.append((base_dir, scope_dir, f"project {base_dir}"))
    return scopes
