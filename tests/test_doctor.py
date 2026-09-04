"""`doctor`: the setup checks as one command, `--fix` for the deterministic
repairs, `--uninstall` for what the plugin leaves behind."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

SETTINGS_RELPATH = os.path.join(".claude", "settings.json")
HARDENING_ENTRIES = [
    "Read(**/.claude/rules-by-path/**)",
    "Edit(**/.claude/rules-by-path/**)",
    "Read(~/.claude/rules-by-path/**)",
    "Edit(~/.claude/rules-by-path/**)",
]


class DoctorTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src",)

    def settings_path(self):
        return os.path.join(self.home, SETTINGS_RELPATH)

    def write_settings(self, data):
        util.write_file(self.settings_path(), json.dumps(data))

    def settings(self):
        with open(self.settings_path(), encoding="utf-8") as handle:
            return json.load(handle)

    def doctor(self, *extra):
        return self.admin("doctor", "--root", self.proj, *extra)

    def test_clean_install_reports_ok_and_the_missing_hardening(self):
        proc = self.doctor()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok    hook smoke test: exit 0, silent", proc.stdout)
        self.assertIn("ok    session notice:", proc.stdout)
        self.assertIn("info  project scope: not created yet", proc.stdout)
        self.assertIn("WARN  hardening: 4 of 4 deny entries missing", proc.stdout)
        self.assertIn("ok    no pre-plugin manual installation", proc.stdout)
        self.assertIn("can be applied with `doctor --fix`", proc.stdout)
        self.assertIn("ask the user first", proc.stdout)
        self.assertFalse(os.path.exists(util.state_path(self.home, "rbp-doctor-probe")))

    def test_legacy_map_is_an_error_that_fix_migrates(self):
        util.write_file(os.path.join(self.scope, "rules-map.yml"),
                        "- glob: src/**\n  rule: legacy.md\n")
        util.write_file(os.path.join(self.scope, "rules", "legacy.md"), "LEGACY BODY")
        proc = self.doctor()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERROR project scope: legacy rules-map.yml present", proc.stdout)
        self.assertIn(f"fix: migrate --root '{self.proj}' [--fix applies it]", proc.stdout)
        proc = self.doctor("--fix")
        self.assertIn("applying: migrate --root", proc.stdout)
        self.assertIn("--- after fixes ---", proc.stdout)
        self.assertNotIn("legacy rules-map.yml present", proc.stdout.split("after fixes")[1])
        self.assertFalse(os.path.exists(os.path.join(self.scope, "rules-map.yml")))

    def test_pre_0_4_prefixes_are_a_warning_that_fix_renames(self):
        util.write_rule(self.proj, "Business_no-refunds.md", "src/**", "NO REFUNDS")
        proc = self.doctor()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("WARN  project scope: 1 rule(s) with a pre-0.4.0 type prefix: "
                      "Business_no-refunds.md", proc.stdout)
        self.doctor("--fix")
        self.assertTrue(os.path.isfile(os.path.join(self.scope, "BUSN_no-refunds.md")))
        proc = self.doctor()
        self.assertIn("ok    project scope: 1 rule(s), current format", proc.stdout)

    def test_untyped_rule_needs_a_human(self):
        util.write_rule(self.proj, "no-refunds.md", "src/**", "NO REFUNDS")
        proc = self.doctor()
        self.assertIn("WARN  project scope: no type prefix on no-refunds.md", proc.stdout)
        self.assertIn("[manual]", proc.stdout)
        self.assertIn("1 finding(s) need a human.", proc.stdout)

    def test_fix_writes_the_hardening_and_drops_obsolete_entries(self):
        self.write_settings({"permissions": {"deny": ["Read(**/.env)",
                                                      "Grep(**/.claude/rules-by-path/**)"]},
                             "model": "opus"})
        proc = self.doctor()
        self.assertIn("obsolete deny entries", proc.stdout)
        proc = self.doctor("--fix")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = self.settings()
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["permissions"]["deny"],
                         ["Read(**/.env)"] + HARDENING_ENTRIES)
        self.assertIn("ok    hardening: all 4 deny entries present", proc.stdout)
        self.assertIn("nothing to fix.", proc.stdout)

    def test_pre_plugin_installation_is_reported(self):
        util.write_file(os.path.join(self.home, ".claude", "hooks", "rules-by-path.py"), "#")
        self.write_settings({"hooks": {"PreToolUse": [{"hooks": [
            {"type": "command", "command": "python3 ~/.claude/hooks/rules-by-path.py"}]}]}})
        proc = self.doctor()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERROR pre-plugin hook still registered", proc.stdout)
        self.assertIn("inject TWICE", proc.stdout)
        self.assertIn("WARN  pre-plugin manual installation left behind", proc.stdout)

    def test_uninstall_removes_deny_entries_and_state_but_keeps_rules(self):
        self.write_settings({"permissions": {"deny": ["Read(**/.env)"] + HARDENING_ENTRIES}})
        util.write_rule(self.proj, "CONV_x.md", "src/**", "KEEP ME")
        util.write_state(self.home, "s1", '{"calls": 1, "seen": {}}')
        proc = self.doctor("--uninstall")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.settings()["permissions"]["deny"], ["Read(**/.env)"])
        self.assertFalse(os.path.exists(util.state_dir(self.home)))
        self.assertTrue(os.path.isfile(os.path.join(self.scope, "CONV_x.md")))
        self.assertIn(f"kept (your rules, 1 file(s)): {self.scope}", proc.stdout)
        self.assertIn("/plugin uninstall rules-by-path@pdmartins", proc.stdout)

    def test_fix_and_uninstall_belong_to_doctor(self):
        proc = self.admin("list", "--root", self.proj, "--fix")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--fix", proc.stderr)
        proc = self.doctor("--fix", "--uninstall")
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
