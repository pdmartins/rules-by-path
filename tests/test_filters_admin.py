"""The admin CLI's half of the rule filters: writing `exclude:`/`tool:`,
carrying them through a rewrite, explaining them in `which`, and reporting the
two ways they can silently disable a rule. The hook's half is in
test_filters.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

BODY = "RULE BODY"
TEST_FILES_GLOB = "src/**/*.test.py"


class AdminFilterTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)

    def add(self, *args, stdin=BODY):
        return self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--rule", "CONV_a.md", *args, stdin=stdin)

    def injected(self, rel, tool):
        """What the hook injects for one tool call, in a session of its own."""
        return util.injected_text(self.hook_for(rel, tool=tool,
                                                session=f"{rel}-{tool}"))

    def test_add_writes_both_filters(self):
        proc = self.add("--exclude", TEST_FILES_GLOB, "--tool", "write")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("CONV_a.md")
        self.assertIn(f"exclude: {TEST_FILES_GLOB}", content)
        self.assertIn("tool: write", content)
        self.assertIn(f"exclude: {TEST_FILES_GLOB}", proc.stdout)

    def test_add_writes_several_excludes_as_a_list(self):
        proc = self.add("--exclude", "src/a/**", "--exclude", "src/b/**")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("exclude:\n  - src/a/**\n  - src/b/**",
                      self.read_rule("CONV_a.md"))

    def test_a_rule_written_with_filters_is_what_the_hook_applies(self):
        self.add("--exclude", TEST_FILES_GLOB, "--tool", "write")
        self.assertIsNone(self.injected("src/api/users.py", "Read"))
        self.assertIsNone(self.injected("src/api/u.test.py", "Write"))
        self.assertIn(BODY, self.injected("src/api/users.py", "Write"))

    def test_show_update_round_trip_keeps_the_filters(self):
        self.add("--exclude", TEST_FILES_GLOB, "--tool", "write")
        shown = self.admin("show", "--root", self.proj, "--rule", "CONV_a.md").stdout
        proc = self.admin("update", "--root", self.proj, "--rule", "CONV_a.md",
                          stdin=shown.replace(BODY, "NEW BODY"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("CONV_a.md")
        self.assertIn(f"exclude: {TEST_FILES_GLOB}", content)
        self.assertIn("tool: write", content)
        self.assertIn("NEW BODY", content)

    def test_update_with_the_flag_replaces_a_filter(self):
        self.add("--exclude", "src/old/**")
        proc = self.admin("update", "--root", self.proj, "--rule", "CONV_a.md",
                          "--exclude", "src/new/**", stdin=BODY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("CONV_a.md")
        self.assertIn("exclude: src/new/**", content)
        self.assertNotIn("src/old/**", content)

    def test_tool_any_clears_the_restriction(self):
        self.add("--tool", "write")
        proc = self.admin("update", "--root", self.proj, "--rule", "CONV_a.md",
                          "--tool", "any", stdin=BODY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("tool:", self.read_rule("CONV_a.md"))

    def test_deleting_a_filter_from_the_submitted_frontmatter_removes_it(self):
        """The round trip is the documented way to edit a rule, so a filter
        deleted there has to be a filter removed — otherwise nothing short of
        remove + add could ever take one off."""
        self.add("--exclude", "src/old/**")
        shown = self.admin("show", "--root", self.proj, "--rule", "CONV_a.md").stdout
        stripped = "\n".join(line for line in shown.split("\n")
                             if not line.startswith("exclude:"))
        proc = self.admin("update", "--root", self.proj, "--rule", "CONV_a.md",
                          stdin=stripped)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("exclude:", self.read_rule("CONV_a.md"))

    def test_an_unknown_tool_value_survives_a_rewrite(self):
        """It is the user's typo to see and fix: `validate` reports it, and an
        update must not quietly delete the evidence."""
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["tool: wirte"])
        proc = self.admin("update", "--root", self.proj, "--rule", "CONV_a.md",
                          stdin="NEW BODY")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("tool: wirte", self.read_rule("CONV_a.md"))

    def test_an_exclude_that_cancels_the_rule_is_refused_before_writing(self):
        """`validate` runs after the write, so reporting it there would leave
        the user holding a dead rule and a zero exit code."""
        proc = self.add("--exclude", "**")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("can never inject", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.scope, "CONV_a.md")))

    def test_list_shows_the_filters(self):
        self.add("--exclude", TEST_FILES_GLOB, "--tool", "write")
        listing = self.admin("list", "--root", self.proj).stdout
        self.assertIn(f"exclude: {TEST_FILES_GLOB}", listing)
        self.assertIn("tool: write", listing)

    def test_filters_are_refused_where_they_would_do_nothing(self):
        proc = self.admin("list", "--root", self.proj, "--tool", "write")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--exclude/--tool", proc.stderr)

    def test_too_many_excludes_are_refused_rather_than_silently_dropped(self):
        args = []
        for index in range(HOOK.MAX_GLOBS_PER_RULE + 1):
            args += ["--exclude", f"src/p{index}/**"]
        proc = self.add(*args)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("at most", proc.stderr)


class WhichTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)

    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=[f"exclude: {TEST_FILES_GLOB}",
                                           "tool: write"])

    def which(self, path, *args):
        proc = self.admin("which", "--root", self.proj, "--path", path, *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_a_match_says_the_rule_is_restricted(self):
        self.assertIn("match: rule CONV_a.md (write only)",
                      self.which("src/api/users.py"))

    def test_asking_for_the_wrong_kind_of_call_explains_itself(self):
        out = self.which("src/api/users.py", "--tool", "read")
        self.assertIn("filtered: rule CONV_a.md", out)
        self.assertNotIn("match:", out)

    def test_asking_for_the_right_kind_of_call_matches(self):
        self.assertIn("match: rule CONV_a.md",
                      self.which("src/api/users.py", "--tool", "write"))

    def test_an_excluded_path_names_the_exclude_that_took_it_back(self):
        out = self.which("src/api/users.test.py")
        self.assertIn("excluded: rule CONV_a.md", out)
        self.assertIn(TEST_FILES_GLOB, out)
        self.assertIn("no rule injects for", out)

    def test_a_path_no_glob_covers_is_still_reported_as_uncovered(self):
        self.assertIn("no rule covers", self.which("docs/readme.md"))


class ValidateTest(util.SandboxTestCase):
    def validate(self):
        return self.admin("validate", "--root", self.proj)

    def test_an_exclude_that_takes_everything_back_is_an_error(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["exclude: **"])
        proc = self.validate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("can never inject", proc.stderr)

    def test_an_exclude_cancelling_every_glob_is_an_error(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["exclude: src/**"])
        proc = self.validate()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("can never inject", proc.stderr)

    def test_a_normal_exclude_is_not_flagged(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["exclude: src/vendor/**"])
        proc = self.validate()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("can never inject", proc.stdout + proc.stderr)

    def test_an_unreadable_tool_value_is_reported_not_enforced(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["tool: wirte"])
        proc = self.validate()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("not understood", proc.stdout)

    def test_the_filter_keys_are_not_reported_as_unknown(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["exclude: src/vendor/**",
                                           "tool: write"])
        self.assertNotIn("unknown frontmatter key", self.validate().stdout)

    def test_a_read_only_deny_is_reported_as_inert(self):
        util.write_rule(self.home, "BUSN_a.md", "/tmp/**", BODY,
                        extra_frontmatter=["enforce: deny", "tool: read"])
        self.assertIn("never fires", self.admin("validate", "--global").stdout)


class MigrateAndEnforceTest(util.SandboxTestCase):
    def test_migrating_the_interval_key_keeps_the_filters(self):
        """`migrate` rewrites the whole file, so everything `render_rule`
        writes from an argument has to be handed back to it — a filter dropped
        here widens a rule nobody asked to widen."""
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["exclude: src/vendor/**",
                                           "tool: write",
                                           "remember_after: 30k"])
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("CONV_a.md")
        self.assertIn("remember_again_after: 30k", content)
        self.assertIn("exclude: src/vendor/**", content)
        self.assertIn("tool: write", content)

    def test_a_rule_migrate_cannot_rewrite_does_not_abort_the_others(self):
        """A filter this tool would refuse to write today is `validate`'s to
        report — renaming a key in a neighbouring rule must still happen."""
        util.write_rule(self.proj, "CONV_bad.md", "src/**", BODY,
                        extra_frontmatter=["exclude: **", "remember_after: 30k"])
        util.write_rule(self.proj, "CONV_good.md", "lib/**", BODY,
                        extra_frontmatter=["remember_after: 30k"])
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("skipped CONV_bad.md", proc.stderr)
        self.assertIn("remember_again_after: 30k", self.read_rule("CONV_good.md"))
        self.assertIn("remember_after: 30k", self.read_rule("CONV_bad.md"))

    def test_enforce_list_warns_that_a_native_deny_cannot_exclude(self):
        util.write_rule(self.proj, "BUSN_a.md", "infra/**", BODY,
                        extra_frontmatter=["enforce: deny",
                                           "exclude: infra/README.md"])
        proc = self.admin("enforce", "--root", self.proj, "--list")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("cannot express", proc.stdout)


if __name__ == "__main__":
    unittest.main()
