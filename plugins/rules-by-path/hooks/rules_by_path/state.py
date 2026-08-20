"""Per-session state: what has already been injected, how far the context
has moved since, and when a rule is due to be sent again.

Every failure in here degrades to "stateless but still injecting" — never to
a blocked tool call."""

import hashlib
import json
import os
import re
import stat
import tempfile
import time

from .constants import (DEFAULT_REMEMBER_AGAIN_CALLS, MAX_SESSION_ID_CHARS,
                        STATE_MAX_AGE_SECONDS, TOKEN_REGRESSION_SLACK,
                        TRANSCRIPT_TAIL_BYTES, warn)
from .discovery import is_safely_owned


def lock_exclusive(fd):
    """Best-effort exclusive lock on fd. POSIX flock, msvcrt on Windows;
    silently degrades to no lock (dedup then tolerates a rare double
    injection instead of ever blocking the tool call)."""
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    except ImportError:
        pass
    except Exception as exc:
        warn(f"flock failed: {exc}")
        return
    try:
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    except Exception as exc:
        warn(f"lock unavailable ({exc}); proceeding without it")

def state_dir():
    """Where per-session state lives. Prefers the plugin's own data directory,
    falls back to ~/.claude/cache, then to a per-uid temp directory."""
    candidates = []
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        candidates.append(os.path.join(plugin_data, "state"))
    candidates.append(os.path.join(os.path.expanduser("~"), ".claude", "cache",
                                   "rules-by-path"))
    suffix = f"-{os.getuid()}" if hasattr(os, "getuid") else ""
    candidates.append(os.path.join(tempfile.gettempdir(), f"rules-by-path-state{suffix}"))
    for candidate in candidates:
        try:
            os.makedirs(candidate, mode=0o700, exist_ok=True)
            if os.path.islink(candidate) or not os.path.isdir(candidate):
                continue
            if not is_safely_owned(candidate):
                warn(f"ignoring state directory {candidate}: not safely owned")
                continue
            if os.access(candidate, os.W_OK):
                return candidate
        except Exception:
            continue
    warn("no writable state directory; rules will re-inject on every tool call")
    return None


def state_file_for(session_id):
    """The state file for a session id, which arrives as JSON from another
    process and is therefore not to be trusted as a string.

    Everything else in this area degrades to "stateless but still injecting";
    this used to be the one line that could do worse. `re.sub` raises TypeError
    on a non-string, and this call sits outside main()'s try, so a numeric or
    absent-typed id took the whole injection down — the user's global rules
    included — instead of costing only the dedup. An over-long id had the
    mirror-image effect: ENAMETOOLONG on every save, so every rule re-injected
    in full on every single tool call."""
    directory = state_dir()
    if directory is None:
        return None
    raw = session_id if isinstance(session_id, str) and session_id.strip() else "default"
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", raw)
    if len(safe_id) > MAX_SESSION_ID_CHARS:
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]
        safe_id = safe_id[:MAX_SESSION_ID_CHARS - len(digest) - 1] + "-" + digest
    return os.path.join(directory, (safe_id or "default") + ".json")


def coerce_seen_entry(value):
    """[call number, context tokens or None, reinjections already sent] from
    whatever is on disk, or None when the entry is unusable. Accepts the bare
    integer written by earlier versions (call number only) and the
    two-element list that predates the reinjection budget — both are missing
    the third slot, which is filled with 0: an entry written before the
    budget existed has spent none of it."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return [value, None, 0]
    if isinstance(value, list) and value:
        try:
            calls = int(value[0])
        except (TypeError, ValueError):
            return None
        tokens = value[1] if len(value) > 1 else None
        if tokens is not None:
            try:
                tokens = int(tokens)
            except (TypeError, ValueError):
                tokens = None
        try:
            reinjections = int(value[2]) if len(value) > 2 else 0
        except (TypeError, ValueError):
            reinjections = 0
        return [calls, tokens, reinjections]
    return None


def context_size(payload):
    """Tokens of context in this session, or None when it cannot be measured.

    The count is read from the transcript the harness already writes: the last
    `usage` record is what the API itself billed, not an estimate from character
    counts. Only the tail of the file is read — a transcript reaches several
    megabytes, and reading one per tool call would cost more than every other
    thing this hook does put together.

    Two known imprecisions, both acceptable against a threshold of tens of
    thousands: the record describes the *previous* request, so it lags by one
    turn; and after a compaction the number drops, which is exactly when
    SessionStart(compact) already clears the state.

    Returns None when there is no transcript, it cannot be read, or no usage
    record is found — the caller then falls back to counting tool calls.
    This is a capability, not a dependency: losing it costs precision, not
    function.
    """
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > TRANSCRIPT_TAIL_BYTES:
                handle.seek(size - TRANSCRIPT_TAIL_BYTES)
                handle.readline()  # drop the partial line the seek landed in
            tail = handle.read()
    except OSError as exc:
        warn(f"transcript not readable ({exc}); counting tool calls instead")
        return None
    total = None
    for line in tail.decode("utf-8", "replace").splitlines():
        if '"usage"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        message = record.get("message")
        message = message if isinstance(message, dict) else {}
        usage = message.get("usage")
        if not isinstance(usage, dict):
            usage = record.get("usage")
        if not isinstance(usage, dict):
            continue
        counted = 0
        for key in ("input_tokens", "cache_creation_input_tokens",
                    "cache_read_input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and value > 0:
                counted += value
        if counted:
            total = counted
    return total


def open_state(state_path):
    """Open the session state under an exclusive lock: (fd, state).

    state = {"calls": int,
             "seen": {dedup_key: [call number, context tokens or None,
                                  reinjections already sent]}}.

    Both measures are recorded because rules choose their own unit: one rule may
    ask to be repeated every 30k tokens and another every 25 calls, in the same
    session. Storing only the session's preferred unit would silently ignore
    whichever rule disagreed with it.

    Parallel tool calls each spawn a hook process, so the read-decide-write
    cycle is serialized; on any failure the hook proceeds statelessly rather
    than blocking the tool call."""
    empty = {"calls": 0, "seen": {}}
    if state_path is None:
        return None, empty
    try:
        # O_NOFOLLOW: this is the one file the hook opens for WRITING, and
        # save_state truncates it. A symlink planted at that path would have its
        # target destroyed and replaced with the hook's JSON. The ELOOP lands in
        # the except below, which degrades to stateless — the fail-open contract.
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(state_path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            warn(f"state path {state_path} is not a regular file; ignoring it")
            os.close(fd)
            return None, empty
        lock_exclusive(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        raw = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw += chunk
        if not raw.strip():
            return fd, empty
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            # Unreadable state (a crash mid-write, or an older format): start
            # over and KEEP the fd so the next save overwrites it. Returning
            # without the fd would leave it broken for the whole session, and
            # every rule would re-inject in full on every single tool call.
            warn(f"state file {state_path} unreadable; starting a fresh one")
            return fd, empty
        if not isinstance(data, dict):
            return fd, empty
        # Coerce the shape while the fd is still held, so a value of the wrong
        # type repairs on the next save instead of dropping the fd (which would
        # make every tool call re-parse the same corrupt file all session). A
        # non-int `calls` must not spam full re-injections, and a malformed
        # `seen` entry must not crash the arithmetic in main() — that crash
        # aborts the whole injection, taking the user's global rules with it, on
        # every single tool call until the session ends.
        try:
            calls = int(data.get("calls") or 0)
        except (TypeError, ValueError):
            calls = 0
        raw_seen = data.get("seen")
        seen = {}
        if isinstance(raw_seen, dict):
            for entry_key, entry_value in raw_seen.items():
                entry = coerce_seen_entry(entry_value)
                if entry is not None:
                    seen[entry_key] = entry
        return fd, {"calls": calls, "seen": seen}
    except Exception as exc:
        warn(f"failed reading state {state_path}: {exc}")
        return None, empty


def save_state(fd, state):
    if fd is None:
        return
    try:
        payload = json.dumps(state).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.truncate(fd, 0)
        os.write(fd, payload)
    except Exception as exc:
        warn(f"failed writing state: {exc}")


def close_state(fd):
    if fd is None:
        return
    try:
        os.close(fd)  # releases the lock
    except Exception as exc:
        warn(f"failed closing state: {exc}")


def cleanup_stale_state():
    directory = state_dir()
    if directory is None:
        return
    try:
        cutoff = time.time() - STATE_MAX_AGE_SECONDS
        with os.scandir(directory) as it:
            for entry in it:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    os.unlink(entry.path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        warn(f"state cleanup failed: {exc}")

def detect_context_regression(state, current_tokens):
    """Fallback for when SessionStart(compact|clear)'s async reset loses the
    race against the very next PreToolUse: the reset's `--reset-session`
    delete has not landed yet, so `seen` still carries the pre-compaction
    high-water mark, and a rule whose text just got summarized out of context
    reads as "already delivered" and stays silent exactly when it needs to be
    repeated (this is the failure the reset exists to prevent; here it is
    caught late instead of not at all).

    Clears `seen` in place — `calls` is untouched — and returns True when
    `current_tokens` has fallen more than TOKEN_REGRESSION_SLACK below the
    highest token count recorded on any seen entry: a drop that size is
    compaction or /clear, not the ordinary jitter of which turn the
    transcript's last usage record happens to describe. `current_tokens is
    None` (no readable transcript) or no seen entry with a recorded token
    count both mean there is nothing to compare against, so nothing is
    cleared — a regression is never guessed at, only measured.
    """
    if current_tokens is None:
        return False
    seen = state.get("seen")
    if not isinstance(seen, dict):
        return False
    max_recorded = None
    for entry_value in seen.values():
        entry = coerce_seen_entry(entry_value)
        if entry is None:
            continue
        tokens = entry[1]
        if tokens is not None and (max_recorded is None or tokens > max_recorded):
            max_recorded = tokens
    if max_recorded is None:
        return False
    if current_tokens + TOKEN_REGRESSION_SLACK < max_recorded:
        warn(f"context tokens dropped from {max_recorded} to {current_tokens}; "
             "compaction/clear likely won the race against the async reset, "
             "clearing seen rules so they re-inject on this call")
        seen.clear()
        return True
    return False


def pop_superseded_entries(seen, scope_dir, name, digest):
    """Remove and report whether `seen` held an entry for this same rule — same
    scope directory and name — under a DIFFERENT content hash. A hit means the
    rule was edited since that entry was recorded: the dedup key hashes the
    body, so the new text is injected on its own regardless, but the earlier
    wording stays in the transcript as a stale, contradictory instruction
    unless something clears it out. This is that cleanup.

    Only called once the caller has committed to injecting this delivery, so
    an edit that gets deferred (the char budget was full this call, say) is
    left alone — the caller detects the same edit again on the next attempt
    instead of losing the record of it. Left in place, a stale entry no longer
    drives any scheduling decision of its own (nothing keys off a digest that
    stopped matching), but it also never goes away: a rule edited several
    times in one session would otherwise leave one dead entry behind per edit
    for the rest of the session.
    """
    prefix = f"{os.path.realpath(scope_dir)}::{name}::"
    current_key = prefix + digest
    stale = [key for key in seen if key.startswith(prefix) and key != current_key]
    for key in stale:
        del seen[key]
    return bool(stale)


def is_due(last_seen, call_number, tokens, interval, budget):
    """Whether a rule already delivered this session should be sent again.

    The question is only ever asked when the rule's glob matched the file being
    touched, so covering the distance is necessary but not sufficient: a rule
    governing a folder nobody opens again is never repeated, however long the
    session runs. Nor is it sufficient once `budget` reinjections have already
    been spent on this rule this session — only prohibition-style constraints
    are known to decay under long context (arXiv:2604.20911), and every
    reinjection adds one more instruction competing for the model's attention
    regardless of type (arXiv:2608.02639), so repetition is capped rather than
    left to run for the rest of a very long session.

    `interval` is (value, unit) as parsed from `remember_again_after`; a value of 0
    means never. A token distance in a session that cannot count tokens falls
    back to the default call count, which prefers a coarser schedule to silence.
    Converting between tokens and calls is never attempted — there is no
    faithful rate, and inventing one would misreport precision.
    """
    value, unit = interval
    if not value:
        return False
    last_calls, last_tokens, reinjections = last_seen
    if reinjections >= budget:
        return False
    if unit == "calls":
        return call_number - last_calls >= value
    if tokens is None or last_tokens is None:
        return call_number - last_calls >= DEFAULT_REMEMBER_AGAIN_CALLS
    return tokens - last_tokens >= value
