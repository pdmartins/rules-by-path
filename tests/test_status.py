"""`status`: one read-only call that reports the environment, both scopes,
their findings, what covers a path, and the repeat unit in use."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402


class StatusTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)

    def status(self, *extra):
        proc = self.admin("status", "--root", self.proj, *extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def test_reports_scopes_that_do_not_exist_yet(self):
        out = self.status().stdout
        self.assertIn("rules-by-path", out)
        self.assertIn("hook:", out)
        self.assertIn("(present)", out)
        self.assertEqual(out.count("not created yet"), 2)
        self.assertIn("no session state yet", out)

    def test_lists_both_scopes_with_their_rules(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        util.write_rule(self.home, "ARCH_global.md", "**/docs/**", "GLOBAL RULE")
        out = self.status().stdout
        self.assertIn("global  ", out)
        self.assertIn("project  ", out)
        self.assertIn("CONV_api.md  <-  src/api/**", out)
        self.assertIn("ARCH_global.md  <-  **/docs/**", out)
        self.assertIn("1 rule(s)", out)

    def test_global_only_reports_one_scope(self):
        util.write_rule(self.home, "ARCH_global.md", "**/docs/**", "GLOBAL RULE")
        proc = self.admin("status", "--global")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ARCH_global.md", proc.stdout)
        self.assertNotIn("project  ", proc.stdout)

    def test_findings_from_validate_are_folded_in(self):
        util.write_rule(self.proj, "CONV_dead.md", [], "NEVER FIRES")
        out = self.status().stdout
        self.assertIn("ERROR: CONV_dead.md: no glob declared", out)

    def test_path_coverage_is_answered_per_scope(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        util.write_rule(self.proj, "CONV_tests.md", "src/**", "TESTS",
                        extra_frontmatter=("exclude: src/api/**",))
        out = self.status("--path", "src/api/x.py").stdout
        self.assertIn("covers 'src/api/x.py'", out)
        self.assertIn("project: match: rule CONV_api.md", out)
        self.assertIn("project: excluded: rule CONV_tests.md", out)
        self.assertIn("global: no rule covers", out)

    def test_json_is_machine_readable(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE",
                        extra_frontmatter=("remember_again_after: 20k",))
        proc = self.status("--json", "--path", "src/api/x.py")
        report = json.loads(proc.stdout)
        self.assertEqual(report["hook"]["present"], True)
        scopes = {scope["scope"]: scope for scope in report["scopes"]}
        self.assertFalse(scopes["global"]["exists"])
        rule = scopes["project"]["rules"][0]
        self.assertEqual(rule["name"], "CONV_api.md")
        self.assertEqual(rule["type"], "CONV")
        self.assertEqual(rule["globs"], ["src/api/**"])
        self.assertEqual(rule["remember_again_after"], "20k")
        self.assertEqual(rule["remember_again_after_parsed"], [20000, "tokens"])
        self.assertNotIn("_fields", rule)
        project_coverage = report["coverage"]["by_scope"]["project"]
        self.assertEqual(project_coverage["entries"][0]["status"], "match")
        self.assertIn("rule_types", report["config"])
        self.assertIsNone(report["repeat"]["unit_in_use"])

    def test_json_belongs_to_status_only(self):
        proc = self.admin("list", "--root", self.proj, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--json", proc.stderr)

    def test_repeat_unit_comes_from_the_newest_state_file(self):
        util.write_state(self.home, "old", json.dumps(
            {"calls": 3, "seen": {"k": [1, None, 0]}}))
        util.write_state(self.home, "new", json.dumps(
            {"calls": 3, "seen": {"k": [1, 45000, 0]}}))
        os.utime(util.state_path(self.home, "old"), (1, 1))
        out = self.status().stdout
        self.assertIn("measured in context tokens", out)
        report = json.loads(self.status("--json").stdout)
        self.assertEqual(report["repeat"]["unit_in_use"], "tokens")

    def test_repeat_unit_falls_back_to_calls_without_a_transcript(self):
        util.write_state(self.home, "only", json.dumps(
            {"calls": 3, "seen": {"k": [1, None, 0]}}))
        out = self.status().stdout
        self.assertIn("measured in file-tool calls", out)

    def test_environment_override_is_reported(self):
        proc = util.run_admin(["status", "--root", self.proj], self.home,
                              env={"RULES_BY_PATH_REMEMBER_AGAIN_AFTER": "50k"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("repeat override: RULES_BY_PATH_REMEMBER_AGAIN_AFTER=50k",
                      proc.stdout)


if __name__ == "__main__":
    unittest.main()
