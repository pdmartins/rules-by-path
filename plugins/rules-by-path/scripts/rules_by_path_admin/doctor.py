"""`doctor`: every check the setup used to walk a model through, run by one
command, each finding naming its fix. `--fix` applies the deterministic ones
(migration, hardening); `--uninstall` undoes what the plugin left behind and
deliberately keeps the user's rules.

Setup and troubleshooting are the same checks at different moments, so they
are one command. The text that only matters when a problem exists is printed
here, when the problem is found, not carried by a skill on every load."""

import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

from .common import (HOOK, HOOK_PATH, INTERVAL_KEY, LEGACY_INTERVAL_KEY,
                     LEGACY_MAP_NAME, rules_in)
from .config import TYPE_SEPARATOR, config_for, split_type_prefix
from .enforce import read_settings_for_sync
from .hardening import (apply_hardening, hardening_state, remove_hardening,
                        user_settings_path)
from .migrate import cmd_migrate
from .status import (HOOK_LAUNCHER_RELPATH, plugin_version,
                     scope_dir_and_anchor, scope_targets)
from .validate import scope_findings

LEVEL_OK = "ok"
LEVEL_INFO = "info"
LEVEL_WARN = "WARN"
LEVEL_ERROR = "ERROR"
PROBE_SESSION_ID = "rbp-doctor-probe"
PROBE_FILE_NAME = "rules-by-path-doctor-probe.txt"
HOOK_TIMEOUT_SECONDS = 15
# What a manual, pre-plugin installation left under ~/.claude.
MANUAL_INSTALL_RELPATHS = (os.path.join("hooks", "rules-by-path.py"),
                           os.path.join("scripts", "rules-by-path-admin.py"),
                           os.path.join("skills", "rules-by-path"))
MANUAL_HOOK_MARKER = "hooks/rules-by-path.py"
PLUGIN_UNINSTALL_COMMAND = "/plugin uninstall rules-by-path@pdmartins"

# ---- user-visible text, in display order ----------------------------------
TITLE = "rules-by-path {version} — doctor"
LINE_FINDING = "{level:<5} {text}"
FIX_AUTO = " — fix: {hint} [--fix applies it]"
FIX_MANUAL = " — fix: {hint} [manual]"
SUMMARY_FIXABLE = ("\n{count} finding(s) can be applied with `doctor --fix`"
                   "{ask}.")
SUMMARY_ASK = " — the hardening edits ~/.claude/settings.json, ask the user first"
SUMMARY_MANUAL = "{count} finding(s) need a human."
SUMMARY_CLEAN = "\nnothing to fix."
APPLYING = "\napplying: {hint}"
RECHECK = "\n--- after fixes ---"
UNINSTALL_DENY = "removed {count} deny entr{plural} from {path}"
UNINSTALL_DENY_NONE = "no deny entries about rules-by-path in {path}"
UNINSTALL_STATE = "removed cached state: {path}"
UNINSTALL_KEPT = "kept (your rules, {count} file(s)): {path}"
UNINSTALL_NEXT = ("\nnext: run {command} in Claude Code. The rule directories "
                  "above are yours; delete them by hand only if you will not "
                  "reinstall.")
# ---------------------------------------------------------------------------


def finding(level, text, hint=None, action=None):
    """One line of the report. `action` is a callable `--fix` runs; a hint
    without an action is advice for a human."""
    return {"level": level, "text": text, "hint": hint, "action": action}


def run_hook(payload, *flags):
    return subprocess.run(
        [sys.executable, HOOK_PATH, *flags], input=json.dumps(payload),
        capture_output=True, text=True, timeout=HOOK_TIMEOUT_SECONDS)


def check_environment():
    hook_path = os.path.join(HOOK.PLUGIN_ROOT, HOOK_LAUNCHER_RELPATH)
    findings = [finding(LEVEL_OK, f"python {sys.version.split()[0]} at {sys.executable}")]
    if os.path.isfile(hook_path):
        findings.append(finding(LEVEL_OK, f"hook launcher present: {hook_path}"))
    else:
        findings.append(finding(LEVEL_ERROR, f"hook launcher missing: {hook_path}",
                                "reinstall the plugin"))
    return findings


def check_hook_smoke(root):
    """Run the injection hook on a file nothing matches: exit 0 and silence
    (or an injection, if a global rule happens to match) is healthy; a
    traceback or a non-zero exit is a broken installation."""
    home = os.path.expanduser("~")
    probe = os.path.join(home, PROBE_FILE_NAME)
    payload = {"tool_name": "Read", "tool_input": {"file_path": probe},
               "session_id": PROBE_SESSION_ID, "cwd": root}
    try:
        proc = run_hook(payload)
    except (OSError, subprocess.SubprocessError) as exc:
        return [finding(LEVEL_ERROR, f"hook smoke test could not run: {exc}",
                        "reinstall the plugin")]
    finally:
        state_path = HOOK.state_file_for(PROBE_SESSION_ID)
        if state_path and os.path.isfile(state_path):
            os.unlink(state_path)
    if proc.returncode != 0 or "Traceback" in proc.stderr:
        return [finding(LEVEL_ERROR, f"hook smoke test failed (exit {proc.returncode}): "
                        f"{proc.stderr.strip()[:300]}", "reinstall the plugin")]
    outcome = "silent (nothing matches a probe file)" if not proc.stdout.strip() \
        else "injected (a global rule matches the probe path)"
    findings = [finding(LEVEL_OK, f"hook smoke test: exit 0, {outcome}")]
    try:
        notice = run_hook({"hook_event_name": "SessionStart", "source": "startup",
                           "cwd": root}, "--session-notice")
    except (OSError, subprocess.SubprocessError) as exc:
        return findings + [finding(LEVEL_ERROR, f"session notice could not run: {exc}")]
    if notice.returncode != 0 or "Traceback" in notice.stderr:
        findings.append(finding(LEVEL_ERROR, "session notice failed: "
                                f"{notice.stderr.strip()[:300]}"))
    else:
        said = "emitted" if notice.stdout.strip() else "silent (no scope near this root)"
        findings.append(finding(LEVEL_OK, f"session notice: {said}"))
    return findings


def check_scope(label, target):
    scope_dir, anchor = scope_dir_and_anchor(target)
    flag = "--global" if target.use_global else f"--root '{anchor}'"
    if not os.path.isdir(scope_dir):
        optional = (" — optional: `init --global` for machine-wide rules"
                    if target.use_global else " — the first `add` creates it")
        return [finding(LEVEL_INFO, f"{label} scope: not created yet ({scope_dir})"
                        + optional)]
    findings = []
    config_path = HOOK.config_path_for(scope_dir)
    if os.path.isfile(config_path) and HOOK.read_config_file(config_path) is None:
        findings.append(finding(LEVEL_ERROR, f"{label} scope: {config_path} is "
                                f"unreadable, so its layer is ignored",
                                "fix the JSON by hand, then `config`"))
    config = config_for(target)
    migrate_action = lambda: cmd_migrate(SimpleNamespace(  # noqa: E731
        use_global=target.use_global, root=target.root, force=False))
    migrate_hint = f"migrate {flag}"
    if HOOK.has_legacy_map(scope_dir):
        findings.append(finding(LEVEL_ERROR, f"{label} scope: legacy {LEGACY_MAP_NAME} "
                                f"present — NOTHING is injected from this scope",
                                migrate_hint, migrate_action))
    legacy_prefixes = {old.lower() for old in
                       (config.get("legacy_type_prefixes") or {})}
    rules = rules_in(scope_dir)
    old_named = [name for name, _f, _b in rules
                 if name.partition(TYPE_SEPARATOR)[0].lower() in legacy_prefixes
                 and name.partition(TYPE_SEPARATOR)[2]]
    if old_named:
        findings.append(finding(LEVEL_WARN, f"{label} scope: {len(old_named)} rule(s) "
                                f"with a pre-0.4.0 type prefix: {', '.join(old_named)}",
                                migrate_hint, migrate_action))
    old_key = [name for name, fields, _b in rules
               if LEGACY_INTERVAL_KEY in fields and INTERVAL_KEY not in fields]
    if old_key:
        findings.append(finding(LEVEL_WARN, f"{label} scope: {len(old_key)} rule(s) "
                                f"still use `{LEGACY_INTERVAL_KEY}:`: {', '.join(old_key)}",
                                migrate_hint, migrate_action))
    untyped = [name for name, _f, _b in rules
               if not split_type_prefix(name, config)[0]
               and name.partition(TYPE_SEPARATOR)[0].lower() not in legacy_prefixes]
    if untyped:
        findings.append(finding(LEVEL_WARN, f"{label} scope: no type prefix on "
                                f"{', '.join(untyped)}",
                                "ask the user which type each is, then "
                                "`remove` + `add --type`"))
    _notes, problems, count = scope_findings(scope_dir, anchor, config,
                                             target.use_global)
    for problem in problems:
        if LEGACY_MAP_NAME in problem:
            continue  # reported above, with its action
        findings.append(finding(LEVEL_ERROR, f"{label} scope: {problem}",
                                f"`update --glob` or `remove` ({flag})"))
    if not findings:
        findings.append(finding(LEVEL_OK, f"{label} scope: {count} rule(s), "
                                f"current format"))
    return findings


def check_hardening():
    state = hardening_state()
    findings = []
    if state["missing"]:
        findings.append(finding(
            LEVEL_WARN, f"hardening: {len(state['missing'])} of "
            f"{len(state['missing']) + len(state['present'])} deny entries missing "
            f"from {state['settings']} — the file tools can still read and edit "
            f"rule files directly", "doctor --fix (edits the user's settings — "
            "ask first)", apply_hardening))
    if state["obsolete"]:
        findings.append(finding(
            LEVEL_WARN, f"hardening: obsolete deny entries (never matched, warn "
            f"at startup): {', '.join(state['obsolete'])}",
            "doctor --fix removes them", apply_hardening))
    if not findings:
        findings.append(finding(LEVEL_OK, f"hardening: all {len(state['present'])} "
                                f"deny entries present in {state['settings']}"))
    return findings


def check_manual_install():
    home = os.path.join(os.path.expanduser("~"), ".claude")
    leftovers = [os.path.join(home, rel) for rel in MANUAL_INSTALL_RELPATHS
                 if os.path.exists(os.path.join(home, rel))]
    findings = []
    settings_path = user_settings_path()
    if os.path.isfile(settings_path):
        try:
            data = read_settings_for_sync(settings_path)
        except Exception:  # noqa: BLE001 - reported by check_hardening's read
            data = {}
        if MANUAL_HOOK_MARKER in json.dumps(data.get("hooks", {})):
            findings.append(finding(
                LEVEL_ERROR, f"pre-plugin hook still registered in {settings_path} "
                f"— rules inject TWICE", f"remove the `hooks` entries that invoke "
                f"{MANUAL_HOOK_MARKER}"))
    if leftovers:
        findings.append(finding(LEVEL_WARN, "pre-plugin manual installation left "
                                "behind: " + ", ".join(leftovers),
                                "delete them (the plugin replaces all three)"))
    if not findings:
        findings.append(finding(LEVEL_OK, "no pre-plugin manual installation"))
    return findings


def state_directories():
    """The directories the hook may have written session state into."""
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    candidates = [os.path.join(plugin_data, "state")] if plugin_data else []
    candidates.append(os.path.join(os.path.expanduser("~"), ".claude", "cache",
                                   "rules-by-path"))
    return [path for path in candidates if os.path.isdir(path)]


def check_state():
    total = 0
    for directory in state_directories():
        try:
            total += sum(1 for entry in os.scandir(directory) if entry.is_file())
        except OSError:
            continue
    return [finding(LEVEL_INFO, f"cached session state: {total} file(s) in "
                    f"{', '.join(state_directories()) or '(no state directory yet)'}")]


def run_checks(args, root):
    findings = check_environment() + check_hook_smoke(root)
    for label, target in scope_targets(args):
        findings += check_scope(label, target)
    return findings + check_hardening() + check_manual_install() + check_state()


def print_findings(findings):
    for entry in findings:
        suffix = ""
        if entry["action"]:
            suffix = FIX_AUTO.format(hint=entry["hint"])
        elif entry["hint"]:
            suffix = FIX_MANUAL.format(hint=entry["hint"])
        print(LINE_FINDING.format(level=entry["level"], text=entry["text"] + suffix))


def print_summary(findings):
    fixable = [entry for entry in findings if entry["action"]]
    manual = [entry for entry in findings if entry["hint"] and not entry["action"]]
    if not fixable and not manual:
        print(SUMMARY_CLEAN)
        return
    if fixable:
        asks = any(entry["action"] is apply_hardening for entry in fixable)
        print(SUMMARY_FIXABLE.format(count=len(fixable), ask=SUMMARY_ASK if asks else ""))
    if manual:
        print(SUMMARY_MANUAL.format(count=len(manual)))


def apply_fixes(findings):
    """Run each distinct fix once — several findings may point at the same
    migration, and it is idempotent anyway."""
    done = set()
    for entry in findings:
        action = entry["action"]
        if action is None or id(action) in done:
            continue
        done.add(id(action))
        print(APPLYING.format(hint=entry["hint"]))
        result = action()
        if action is apply_hardening:
            added, removed = result
            for item in added:
                print(f"  + {item}")
            for item in removed:
                print(f"  - {item}")
    return bool(done)


def cmd_uninstall(args):
    settings_path = user_settings_path()
    removed = remove_hardening()
    if removed:
        print(UNINSTALL_DENY.format(count=len(removed), path=settings_path,
                                    plural="y" if len(removed) == 1 else "ies"))
    else:
        print(UNINSTALL_DENY_NONE.format(path=settings_path))
    for directory in state_directories():
        shutil.rmtree(directory, ignore_errors=True)
        print(UNINSTALL_STATE.format(path=directory))
    for _label, target in scope_targets(args):
        scope_dir, _anchor = scope_dir_and_anchor(target)
        if os.path.isdir(scope_dir):
            print(UNINSTALL_KEPT.format(count=len(rules_in(scope_dir)), path=scope_dir))
    print(UNINSTALL_NEXT.format(command=PLUGIN_UNINSTALL_COMMAND))


def cmd_doctor(args):
    if args.uninstall:
        cmd_uninstall(args)
        return
    root = os.path.expanduser("~") if args.use_global else os.path.abspath(args.root)
    print(TITLE.format(version=plugin_version()))
    findings = run_checks(args, root)
    print_findings(findings)
    if args.fix and apply_fixes(findings):
        print(RECHECK)
        findings = run_checks(args, root)
        print_findings(findings)
    print_summary(findings)
    if any(entry["level"] == LEVEL_ERROR for entry in findings):
        sys.exit(1)
