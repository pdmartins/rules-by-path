"""`status`: the whole picture in one call — environment, both scopes with
their rules and findings, what covers a path, the configuration in force and
the repeat distance actually in use.

Everything here is read-only and deterministic. It exists so the `status`
command and the `improve` skill need ONE command instead of a script of nine:
instruction text that only matters when a problem exists is paid for here,
when the problem is found, rather than in a skill file on every load."""

import json
import os
import sys
import time
from types import SimpleNamespace

from .common import HOOK, read_regular_file, rules_in, other_markdown_in
from .config import cmd_config, config_for, split_type_prefix
from .rules import filters_label
from .usage import (public_usage, usage_label, usage_notes, usage_of,
                    usage_since)
from .validate import scope_findings
from .which import coverage_of, no_coverage_line

SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
PLUGIN_MANIFEST_RELPATH = os.path.join(".claude-plugin", "plugin.json")
HOOK_LAUNCHER_RELPATH = os.path.join("bin", "rules-by-path-hook")
# A state file is a few KB; this is a ceiling, not an expectation.
MAX_STATE_BYTES = 1024 * 1024
UNIT_TOKENS = "tokens"
UNIT_CALLS = "calls"

# ---- user-visible text, in display order ----------------------------------
TITLE = "rules-by-path {version} — status"
LINE_PYTHON = "python: {version} ({executable})"
LINE_HOOK_OK = "hook: {path} (present)"
LINE_HOOK_MISSING = "hook: {path} MISSING — nothing can be injected; reinstall the plugin"
LINE_SCOPE = "\n{label}  {directory}  — {count} rule(s)"
LINE_SCOPE_ABSENT = "\n{label}  {directory}  — not created yet (the first `add` creates it)"
LINE_RULE = "  {name}  <-  {globs}{filters}  ({chars} chars{repeat}{usage})"
LINE_USAGE_SINCE = "usage stats since {since} ({path})"
LINE_USAGE_NONE = "usage stats: nothing recorded yet ({path})"
LINE_NOT_RULES = "  (not rules, no frontmatter: {names})"
LINE_NOTE = "  note: {text}"
LINE_PROBLEM = "  ERROR: {text}"
LINE_COVERS = "\ncovers '{shown}':"
LINE_COVERS_ENTRY = "  {label}: {line}"
LINE_CONFIG = "\nconfig (nearest layer wins):"
LINE_REPEAT_ENV = ("\nrepeat override: {variable}={value} (environment, beats "
                   "every layer for this session)")
LINE_REPEAT_UNIT = ("repeat distance measured in {unit} — from the most recent "
                    "session state ({state_file})")
LINE_REPEAT_UNKNOWN = ("repeat distance: no session state yet — tokens when the "
                       "transcript can be read, tool calls otherwise")
UNIT_LABELS = {UNIT_TOKENS: "context tokens (transcript readable)",
               UNIT_CALLS: "file-tool calls (transcript not readable)"}
# ---------------------------------------------------------------------------


def plugin_version():
    path = os.path.join(HOOK.PLUGIN_ROOT, PLUGIN_MANIFEST_RELPATH)
    try:
        return json.loads(read_regular_file(path, MAX_STATE_BYTES)).get(
            "version", "?")
    except (OSError, ValueError):
        return "?"


def environment_report():
    hook_path = os.path.join(HOOK.PLUGIN_ROOT, HOOK_LAUNCHER_RELPATH)
    return {
        "version": plugin_version(),
        "python": {"version": ".".join(str(part) for part in sys.version_info[:3]),
                   "executable": sys.executable},
        "hook": {"path": hook_path, "present": os.path.isfile(hook_path)},
    }


def rule_entry(name, fields, body, config, usage):
    prefix, _rest = split_type_prefix(name, config)
    interval = HOOK.remember_again_after_of(fields)
    raw_interval = fields.get("remember_again_after")
    if isinstance(raw_interval, list):
        raw_interval = raw_interval[0] if raw_interval else None
    return {
        "name": name,
        "type": prefix,
        "globs": HOOK.globs_of(fields),
        "excludes": HOOK.excludes_of(fields),
        "tools": HOOK.tools_of(fields),
        "remember_again_after": raw_interval,
        "remember_again_after_parsed": list(interval) if interval else None,
        "enforce": HOOK.enforce_of(fields),
        "chars": len(body),
        "usage": public_usage(usage),
        "_fields": fields,
        "_usage": usage,
    }


def scope_report(label, scope_dir, anchor, config, is_global, stats):
    report = {"scope": label, "directory": scope_dir,
              "exists": os.path.isdir(scope_dir), "rules": [], "not_rules": [],
              "legacy_map": False, "notes": [], "problems": []}
    if not report["exists"]:
        return report
    for name, fields, body in rules_in(scope_dir, HOOK.max_rule_chars(config)):
        report["rules"].append(rule_entry(name, fields, body, config,
                                          usage_of(stats, scope_dir, name)))
    report["not_rules"] = other_markdown_in(scope_dir)
    report["legacy_map"] = HOOK.has_legacy_map(scope_dir)
    notes, problems, _count = scope_findings(scope_dir, anchor, config, is_global)
    notes.extend(usage_notes(stats, scope_dir,
                             [(rule["name"], rule["globs"]) for rule in report["rules"]]))
    report["notes"], report["problems"] = notes, problems
    return report


def scope_targets(args):
    """[(label, args namespace)] — the global scope always, the project scope
    when --root was given. Each namespace is what the other commands take, so
    `config_for` and `scope_for` see exactly the scope they expect."""
    targets = [(SCOPE_GLOBAL, SimpleNamespace(use_global=True, root=None))]
    if not args.use_global:
        targets.append((SCOPE_PROJECT, SimpleNamespace(use_global=False,
                                                       root=args.root)))
    return targets


def scope_dir_and_anchor(target):
    if target.use_global:
        anchor = os.path.expanduser("~")
    else:
        anchor = os.path.abspath(target.root)
    return os.path.join(anchor, HOOK.RULES_DIR_RELPATH), anchor


def newest_state_file():
    directory = HOOK.state_dir()
    if directory is None:
        return None
    newest, newest_mtime = None, 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False) and entry.name.endswith(".json"):
                    mtime = entry.stat().st_mtime
                    if mtime > newest_mtime:
                        newest, newest_mtime = entry.path, mtime
    except OSError:
        return None
    return newest


def repeat_report():
    """Which unit the repeat distance is being measured in, taken from the
    most recent session state: a `seen` entry holds [call number, context
    tokens, reinjections], and a null second slot means the transcript could
    not be read, so distance fell back to counting tool calls."""
    override = os.environ.get(HOOK.REMEMBER_AGAIN_ENV_VAR)
    state_file = newest_state_file()
    unit = None
    if state_file:
        try:
            data = json.loads(read_regular_file(state_file, MAX_STATE_BYTES))
            seen = data.get("seen") if isinstance(data, dict) else None
        except (OSError, ValueError):
            seen = None
        entries = [HOOK.coerce_seen_entry(value) for value in (seen or {}).values()]
        entries = [entry for entry in entries if entry is not None]
        if entries:
            unit = UNIT_TOKENS if any(e[1] is not None for e in entries) else UNIT_CALLS
    return {"env_override": override or None, "unit_in_use": unit,
            "state_file": state_file, "state_age_seconds":
                int(time.time() - os.path.getmtime(state_file)) if state_file else None}


def collect(args):
    report = environment_report()
    stats = HOOK.load_stats()
    report["usage"] = {"since": usage_since(stats), "path": HOOK.stats_path()}
    report["scopes"] = []
    coverage = {"path": args.path, "by_scope": {}} if args.path else None
    for label, target in scope_targets(args):
        scope_dir, anchor = scope_dir_and_anchor(target)
        config = config_for(target)
        report["scopes"].append(scope_report(label, scope_dir, anchor, config,
                                             target.use_global, stats))
        if coverage is not None:
            entries, shown = coverage_of(scope_dir, anchor, target.use_global,
                                         args.path, args.tool)
            coverage["by_scope"][label] = {
                "shown": shown,
                "entries": [{"status": status, "rule": name, "line": line}
                            for status, name, line in entries]}
    report["coverage"] = coverage
    config = config_for(args)
    report["config"] = {key: value for key, value in config.items()}
    report["repeat"] = repeat_report()
    return report


def print_scope(scope):
    if not scope["exists"]:
        print(LINE_SCOPE_ABSENT.format(label=scope["scope"],
                                       directory=scope["directory"]))
        return
    print(LINE_SCOPE.format(label=scope["scope"], directory=scope["directory"],
                            count=len(scope["rules"])))
    for rule in scope["rules"]:
        globs = ", ".join(rule["globs"]) if rule["globs"] else "(NO GLOB — never injected)"
        repeat = f", repeat {rule['remember_again_after']}" if rule["remember_again_after"] else ""
        label = usage_label(rule["_usage"])
        print(LINE_RULE.format(name=rule["name"], globs=globs,
                               filters=filters_label(rule["_fields"]),
                               chars=rule["chars"], repeat=repeat,
                               usage=f"; {label}" if label else ""))
    if scope["not_rules"]:
        print(LINE_NOT_RULES.format(names=", ".join(scope["not_rules"])))
    for note in scope["notes"]:
        print(LINE_NOTE.format(text=note))
    for problem in scope["problems"]:
        print(LINE_PROBLEM.format(text=problem))


def print_report(report, args):
    print(TITLE.format(version=report["version"]))
    print(LINE_PYTHON.format(**report["python"]))
    hook = report["hook"]
    print((LINE_HOOK_OK if hook["present"] else LINE_HOOK_MISSING).format(path=hook["path"]))
    usage = report["usage"]
    print((LINE_USAGE_SINCE if usage["since"] else LINE_USAGE_NONE).format(**usage))
    for scope in report["scopes"]:
        print_scope(scope)
    coverage = report["coverage"]
    if coverage is not None:
        print(LINE_COVERS.format(shown=coverage["path"]))
        for label, answer in coverage["by_scope"].items():
            entries = answer["entries"]
            for entry in entries:
                print(LINE_COVERS_ENTRY.format(label=label, line=entry["line"]))
            closing = no_coverage_line(
                [(e["status"], e["rule"], e["line"]) for e in entries],
                answer["shown"])
            if closing:
                print(LINE_COVERS_ENTRY.format(label=label, line=closing))
    print(LINE_CONFIG)
    cmd_config(args)
    repeat = report["repeat"]
    if repeat["env_override"]:
        print(LINE_REPEAT_ENV.format(variable=HOOK.REMEMBER_AGAIN_ENV_VAR,
                                     value=repeat["env_override"]))
    else:
        print()
    if repeat["unit_in_use"]:
        print(LINE_REPEAT_UNIT.format(unit=UNIT_LABELS[repeat["unit_in_use"]],
                                      state_file=repeat["state_file"]))
    else:
        print(LINE_REPEAT_UNKNOWN)


def strip_private(value):
    """The JSON view drops the keys that exist only for the text printer."""
    if isinstance(value, dict):
        return {key: strip_private(item) for key, item in value.items()
                if not key.startswith("_")}
    if isinstance(value, list):
        return [strip_private(item) for item in value]
    return value


def cmd_status(args):
    report = collect(args)
    if args.json:
        print(json.dumps(strip_private(report), indent=2, ensure_ascii=False))
        return
    print_report(report, args)
