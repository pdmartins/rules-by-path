"""Per-rule usage, kept across sessions: how often a rule was injected, in how
many sessions, when last, under which directories and through which glob.

Session state answers "was this rule already delivered?" and is thrown away;
this file answers "is this rule earning its place?" and is kept. It is what
lets `status` say a rule has never fired, or has fired forty times but always
under one subfolder of the glob it declares — the two signals a human needs to
prune or narrow a rule with evidence instead of a hunch.

Written only on the calls that actually inject, after the payload has been
flushed, under its own lock, and every failure degrades to "no usage recorded"
— never to a blocked or delayed tool call."""

import json
import os
import stat
import time

from .constants import (MAX_STATS_DIRS_PER_RULE, MAX_STATS_GLOBS_PER_RULE,
                        MAX_STATS_RECENT_SESSIONS, MAX_STATS_RULES,
                        STATS_FILE_NAME, STATS_READ_LIMIT_BYTES, warn)
from .state import lock_exclusive, state_dir

STATS_VERSION = 1


def stats_path():
    directory = state_dir()
    return os.path.join(directory, STATS_FILE_NAME) if directory else None


def rule_key(scope_dir, name):
    return f"{os.path.realpath(scope_dir)}::{name}"


def empty_stats():
    return {"version": STATS_VERSION, "since": int(time.time()), "rules": {}}


def empty_entry():
    return {"injections": 0, "reinjections": 0, "sessions": 0,
            "recent_sessions": [], "first": None, "last": None,
            "dirs": {}, "globs": {}}


def matched_dir(abs_path, base_dir):
    """The directory of the touched file, relative to the scope's base for a
    project scope and absolute for the global one — the same frame the rule's
    own glob is written in, so the two can be compared later."""
    if base_dir is None:
        return os.path.dirname(abs_path).replace(os.sep, "/") or "/"
    relative = os.path.relpath(abs_path, base_dir).replace(os.sep, "/")
    return os.path.dirname(relative) or "."


def bump_bounded(counter, key, cap):
    """Count `key`, keeping at most `cap` keys: a newcomer past the cap evicts
    the least-counted key, so what survives is the frequent set, not the first
    set seen."""
    if key in counter:
        counter[key] += 1
        return
    if len(counter) >= cap:
        smallest = min(counter, key=counter.get)
        if counter[smallest] > 1:
            return  # the newcomer has 1 and would only evict something rarer
        del counter[smallest]
    counter[key] = 1


def record_entry(entry, session_id, now, directory, glob, repeat):
    entry["injections"] += 1
    if repeat:
        entry["reinjections"] += 1
    entry["first"] = entry["first"] or now
    entry["last"] = now
    if session_id and session_id not in entry["recent_sessions"]:
        entry["sessions"] += 1
        entry["recent_sessions"].append(session_id)
        del entry["recent_sessions"][:-MAX_STATS_RECENT_SESSIONS]
    bump_bounded(entry["dirs"], directory, MAX_STATS_DIRS_PER_RULE)
    if glob:
        bump_bounded(entry["globs"], glob, MAX_STATS_GLOBS_PER_RULE)


def coerce_entry(value):
    """A stored entry with every field present and of the right type, so a
    hand-edited or half-written file cannot crash the arithmetic above."""
    entry = empty_entry()
    if not isinstance(value, dict):
        return entry
    for key in ("injections", "reinjections", "sessions"):
        if isinstance(value.get(key), int) and not isinstance(value.get(key), bool):
            entry[key] = value[key]
    for key in ("first", "last"):
        if isinstance(value.get(key), int):
            entry[key] = value[key]
    recent = value.get("recent_sessions")
    if isinstance(recent, list):
        entry["recent_sessions"] = [s for s in recent if isinstance(s, str)]
    for key in ("dirs", "globs"):
        counter = value.get(key)
        if isinstance(counter, dict):
            entry[key] = {k: v for k, v in counter.items()
                          if isinstance(k, str) and isinstance(v, int)}
    return entry


def read_stats(raw):
    try:
        data = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("rules"), dict):
        return empty_stats()
    since = data.get("since")
    return {"version": STATS_VERSION,
            "since": since if isinstance(since, int) else int(time.time()),
            "rules": {key: coerce_entry(value)
                      for key, value in data["rules"].items() if isinstance(key, str)}}


def evict_oldest(rules):
    """Keep the file bounded: past the cap, the rules injected longest ago go."""
    while len(rules) > MAX_STATS_RULES:
        oldest = min(rules, key=lambda key: rules[key]["last"] or 0)
        del rules[oldest]


def record_injections(session_id, deliveries, abs_path):
    """Count one injection per delivered rule. `deliveries` is
    [(scope_dir, base_dir, name, glob, repeat)], in the order they were sent."""
    if not deliveries:
        return
    path = stats_path()
    if path is None:
        return
    fd = None
    try:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            warn(f"usage stats path {path} is not a regular file; ignoring it")
            return
        lock_exclusive(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, STATS_READ_LIMIT_BYTES)
        stats = read_stats(raw)
        now = int(time.time())
        for scope_dir, base_dir, name, glob, repeat in deliveries:
            entry = stats["rules"].setdefault(rule_key(scope_dir, name), empty_entry())
            record_entry(entry, session_id, now, matched_dir(abs_path, base_dir),
                         glob, repeat)
        evict_oldest(stats["rules"])
        payload = json.dumps(stats).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.truncate(fd, 0)
        os.write(fd, payload)
    except Exception as exc:  # noqa: BLE001 - usage is never worth a failed call
        warn(f"usage stats not recorded: {exc}")
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def load_stats():
    """The stored usage, or an empty structure when there is none — for the
    admin CLI, which reads it and never writes it."""
    path = stats_path()
    if path is None or not os.path.isfile(path) or os.path.islink(path):
        return empty_stats()
    try:
        with open(path, "rb") as handle:
            return read_stats(handle.read(STATS_READ_LIMIT_BYTES))
    except OSError as exc:
        warn(f"usage stats unreadable: {exc}")
        return empty_stats()
