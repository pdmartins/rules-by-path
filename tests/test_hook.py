"""Unit and end-to-end tests for hooks/rules-by-path.py."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


class GlobMatchingTest(unittest.TestCase):
    def check(self, glob, rel, expected, abs_path=None):
        abs_path = abs_path or f"/proj/{rel}"
        self.assertEqual(HOOK.glob_matches(glob, rel, abs_path), expected,
                         f"glob={glob!r} rel={rel!r}")

    def test_double_star_matches_any_depth(self):
        self.check("src/api/**", "src/api/users.py", True)
        self.check("src/api/**", "src/api/v1/deep/users.py", True)
        self.check("src/api/**", "src/apix/users.py", False)
        self.check("src/api/**", "src/api", False)  # the dir itself, not inside

    def test_plain_path_matches_itself_and_below(self):
        self.check("docs", "docs", True)
        self.check("docs", "docs/guide.md", True)
        self.check("docs", "docsx/guide.md", False)
        self.check("src/config.json", "src/config.json", True)
        self.check("src/config.json", "src/config.jsonx", False)

    def test_trailing_slash_means_directory(self):
        self.check("docs/", "docs/guide.md", True)
        self.check("docs/", "docs", False)

    def test_single_star_stays_within_segment(self):
        self.check("src/*.py", "src/a.py", True)
        self.check("src/*.py", "src/sub/a.py", False)

    def test_no_slash_glob_matches_basename(self):
        self.check("*.cs", "deep/nested/Program.cs", True)
        self.check("*.cs", "deep/nested/Program.cshtml", False)

    def test_question_mark(self):
        self.check("v?", "v1", True)
        self.check("v?", "v12", False)

    def test_absolute_glob_matches_abs_path(self):
        self.assertTrue(HOOK.glob_matches("/repos/x/**", None, "/repos/x/a/b.py"))
        self.assertFalse(HOOK.glob_matches("/repos/x/**", None, "/repos/y/a.py"))

    def test_double_star_dir_at_any_depth(self):
        self.check("**/deploy/**", "infra/deploy/main.tf", True)
        self.check("**/deploy/**", "deploy/main.tf", True)
        self.check("**/deploy/**", "src/deployment/main.tf", False)

    def test_bracket_is_literal_not_a_character_class(self):
        self.check("[", "src/a.py", False)
        self.check("a[b].py", "a[b].py", True)


class DeriveRuleNameTest(unittest.TestCase):
    def test_derivations(self):
        cases = {
            "src/api/**": "src--api.md",
            "docs": "docs.md",
            "docs/": "docs.md",
            "src/config.json": "src--config.json.md",
            "**/deploy/**": "deploy.md",
            "/repos/x/**": "repos--x.md",
            "**": "root.md",
        }
        for glob, expected in cases.items():
            self.assertEqual(HOOK.derive_rule_name(glob), expected, glob)


class FrontmatterTest(unittest.TestCase):
    def test_single_glob_and_body(self):
        fields, body = HOOK.parse_frontmatter("---\nglob: src/**\n---\nrule text\n")
        self.assertEqual(HOOK.globs_of(fields), ["src/**"])
        self.assertEqual(body.strip(), "rule text")

    def test_glob_list(self):
        text = "---\nglob:\n  - src/**\n  - lib/**\n---\nbody"
        fields, body = HOOK.parse_frontmatter(text)
        self.assertEqual(HOOK.globs_of(fields), ["src/**", "lib/**"])
        self.assertEqual(body.strip(), "body")

    def test_plural_key_accepted(self):
        fields, _ = HOOK.parse_frontmatter("---\nglobs: a/**\n---\nx")
        self.assertEqual(HOOK.globs_of(fields), ["a/**"])

    def test_hash_in_glob_is_literal(self):
        """No comment syntax in frontmatter, so a '#' in a glob survives."""
        fields, _ = HOOK.parse_frontmatter("---\nglob: src/c#/**\n---\nx")
        self.assertEqual(HOOK.globs_of(fields), ["src/c#/**"])

    def test_quotes_are_stripped(self):
        fields, _ = HOOK.parse_frontmatter('---\nglob: "src/a b/**"\n---\nx')
        self.assertEqual(HOOK.globs_of(fields), ["src/a b/**"])

    def test_no_frontmatter_means_no_glob(self):
        fields, body = HOOK.parse_frontmatter("just a body\n")
        self.assertEqual(HOOK.globs_of(fields), [])
        self.assertEqual(body.strip(), "just a body")

    def test_unterminated_frontmatter_is_not_parsed(self):
        fields, _ = HOOK.parse_frontmatter("---\nglob: src/**\nno end marker\n")
        self.assertEqual(HOOK.globs_of(fields), [])

    def test_reinforce_values(self):
        self.assertEqual(HOOK.reinforce_of({"reinforce": "10"}, 25), 10)
        self.assertEqual(HOOK.reinforce_of({"reinforce": "never"}, 25), 0)
        self.assertEqual(HOOK.reinforce_of({}, 25), 25)
        self.assertEqual(HOOK.reinforce_of({"reinforce": "nonsense"}, 25), 25)


class HookEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.proj, "src", "api"), exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def target(self, rel):
        return os.path.join(self.proj, rel)

    def inject(self, rel="src/api/users.py", session="s1", tool="Read", env=None):
        proc = util.run_hook(util.read_payload(tool, self.target(rel), session=session),
                             self.home, env=env)
        return proc, util.injected_text(proc)

    def test_injects_matching_rule_once_per_session(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE CONTENT")
        proc, text = self.inject()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("API RULE CONTENT", text)
        self.assertIn("src/api/**", text)
        self.assertTrue(util.hook_output(proc).get("suppressOutput"))
        self.assertIsNone(self.inject()[1], "second touch must not re-inject")
        self.assertIsNotNone(self.inject(session="s2")[1], "a new session injects again")

    def test_reset_session_reinjects(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE CONTENT")
        self.assertIsNotNone(self.inject()[1])
        self.assertIsNone(self.inject()[1])
        util.run_hook({"session_id": "s1", "hook_event_name": "SessionStart"},
                      self.home, args=("--reset-session",))
        self.assertIsNotNone(self.inject()[1], "after reset the rule injects again")

    def test_non_matching_file_no_output(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE")
        self.assertIsNone(self.inject(rel="src/other/users.py")[1])

    def test_rules_dir_files_never_trigger(self):
        util.write_rule(self.proj, "root.md", "**", "EVERYTHING")
        inside = os.path.join(util.scope_dir(self.proj), "root.md")
        self.assertIsNone(util.injected_text(
            util.run_hook(util.read_payload("Read", inside), self.home)))

    def test_no_rules_anywhere_no_output(self):
        proc, text = self.inject()
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(text)

    def test_rule_without_a_glob_never_injects(self):
        util.write_rule(self.proj, "orphan.md", [], "NEVER")
        proc, text = self.inject()
        self.assertIsNone(text)

    def test_multiple_globs_on_one_rule(self):
        util.write_rule(self.proj, "many.md", ["lib/**", "src/api/**"], "MULTI RULE")
        self.assertIn("MULTI RULE", self.inject()[1])

    def test_multiple_rules_for_the_same_glob_all_inject(self):
        util.write_rule(self.proj, "security.md", "src/api/**", "SECURITY RULE")
        util.write_rule(self.proj, "naming.md", "src/api/**", "NAMING RULE")
        text = self.inject()[1]
        self.assertIn("SECURITY RULE", text)
        self.assertIn("NAMING RULE", text)
        self.assertEqual(text.count('"name":'), 2)

    def test_global_scope(self):
        util.write_rule(self.home, "proj.md",
                        f"{self.proj}/**".replace(os.sep, "/"), "GLOBAL RULE")
        text = self.inject(rel="anything.txt")[1]
        self.assertIn("GLOBAL RULE", text)
        self.assertIn("global", text)

    def test_nested_claude_md_write_denied(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        out = util.hook_output(util.run_hook(
            util.read_payload("Write", self.target("src/CLAUDE.md")), self.home))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_root_claude_md_write_allowed(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        self.assertIsNone(util.hook_output(util.run_hook(
            util.read_payload("Write", self.target("CLAUDE.md")), self.home)))

    def test_nested_claude_md_read_not_denied(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        out = util.hook_output(util.run_hook(
            util.read_payload("Read", self.target("src/CLAUDE.md")), self.home))
        self.assertTrue(out is None or "permissionDecision"
                        not in out.get("hookSpecificOutput", {}))

    def test_nested_repo_claude_md_allowed(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        os.makedirs(os.path.join(self.proj, "vendor", "lib", ".git"), exist_ok=True)
        self.assertIsNone(util.hook_output(util.run_hook(
            util.read_payload("Write", self.target("vendor/lib/CLAUDE.md")), self.home)))

    def test_oversized_rule_truncated(self):
        util.write_rule(self.proj, "big.md", "src/**", "X" * (HOOK.MAX_RULE_CHARS + 5_000))
        text = self.inject()[1]
        self.assertIn("truncated", text)
        self.assertLess(len(text), HOOK.MAX_RULE_CHARS + 2_000)

    def test_budget_defers_extra_rules(self):
        per_rule = HOOK.MAX_RULE_CHARS - 100
        count = (HOOK.MAX_TOTAL_CHARS // per_rule) + 2
        for index in range(count):
            util.write_rule(self.proj, f"r{index:02d}.md", "src/**",
                            f"RULE{index} " + "y" * per_rule)
        proc, text = self.inject(session="cap")
        self.assertIn("RULE0 ", text)
        self.assertIn("budget", proc.stderr)
        later = self.inject(session="cap")[1]
        self.assertIsNotNone(later, "deferred rules arrive on the next call")

    def test_malformed_stdin_never_fails(self):
        proc = util.run_hook("this is not json", self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("unexpected error", proc.stderr)

    def test_payload_without_file_path_no_output(self):
        proc = util.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"},
                              "session_id": "s", "cwd": self.proj}, self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(util.hook_output(proc))

    def test_relative_path_resolved_against_cwd(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE")
        proc = util.run_hook(util.read_payload(
            "Read", os.path.join("src", "api", "x.py"), cwd=self.proj), self.home)
        self.assertIsNotNone(util.injected_text(proc))

    def test_legacy_map_is_reported_not_silently_ignored(self):
        directory = util.scope_dir(self.proj)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "rules-map.yml"), "w", encoding="utf-8") as h:
            h.write('rules:\n  - glob: "src/**"\n')
        proc, text = self.inject()
        self.assertIsNotNone(text, "a legacy scope must not fail silently")
        self.assertIn("migrate", text)


class ReinforcementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.proj, "src"), exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, session="r", env=None):
        proc = util.run_hook(
            util.read_payload("Read", os.path.join(self.proj, "src", "a.py"),
                              session=session),
            self.home, env=env)
        return util.injected_text(proc)

    def test_reminder_after_the_configured_number_of_calls(self):
        util.write_rule(self.proj, "src.md", "src/**",
                        "Always validate DTOs.\n\nMore detail that need not repeat.")
        env = {"RULES_BY_PATH_REINFORCE_EVERY": "3"}
        first = self.touch(env=env)
        self.assertIn("Always validate DTOs.", first)
        self.assertIn('"reminder": false', first)
        self.assertIsNone(self.touch(env=env), "call 2: nothing")
        self.assertIsNone(self.touch(env=env), "call 3: nothing")
        fourth = self.touch(env=env)
        self.assertIsNotNone(fourth, "call 4 is 3 calls after the injection")
        self.assertIn('"reminder": true', fourth)
        self.assertIn("Always validate DTOs.", fourth)
        self.assertNotIn("More detail", fourth, "a reminder is the headline only")

    def test_reinforcement_can_be_disabled(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.")
        env = {"RULES_BY_PATH_REINFORCE_EVERY": "0"}
        self.assertIsNotNone(self.touch(env=env))
        for _ in range(6):
            self.assertIsNone(self.touch(env=env))

    def test_per_rule_override_wins(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["reinforce: never"])
        env = {"RULES_BY_PATH_REINFORCE_EVERY": "2"}
        self.assertIsNotNone(self.touch(env=env))
        for _ in range(5):
            self.assertIsNone(self.touch(env=env))

    def test_edited_rule_reinjects_in_full_not_as_a_reminder(self):
        util.write_rule(self.proj, "src.md", "src/**", "VERSION ONE")
        env = {"RULES_BY_PATH_REINFORCE_EVERY": "50"}
        self.assertIn("VERSION ONE", self.touch(env=env))
        self.assertIsNone(self.touch(env=env))
        util.write_rule(self.proj, "src.md", "src/**", "VERSION TWO body line")
        text = self.touch(env=env)
        self.assertIn("VERSION TWO", text)
        self.assertIn('"reminder": false', text, "a changed rule is new, not a reminder")


if __name__ == "__main__":
    unittest.main()
