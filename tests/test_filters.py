"""The restrictive filters a rule may declare besides its glob — `exclude:` and
`tool:` — as the hook applies them. The admin CLI's half is in
test_filters_admin.py.

Every filter only ever NARROWS a rule, and they are ANDed: a rule applies when
one glob matches, no exclude matches, and the tool call is of a kind the rule
accepts. The property asserted throughout is that a filter the parser cannot
read is IGNORED, never enforced — a typo must not be why a rule silently stops
arriving.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

BODY = "FILTERED RULE CONTENT"
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def exclude_frontmatter(excludes):
    """The `exclude:` lines for a rule file: one value inline, several as a
    list — both shapes the frontmatter parser reads."""
    if len(excludes) == 1:
        return [f"exclude: {excludes[0]}"]
    return ["exclude:"] + [f"  - {exclude}" for exclude in excludes]


class ParsingTest(unittest.TestCase):
    """`excludes_of` and `tools_of` on their own."""

    def test_exclude_reads_one_value_or_a_list_under_either_key(self):
        self.assertEqual(HOOK.excludes_of({"exclude": "a/**"}), ["a/**"])
        self.assertEqual(HOOK.excludes_of({"exclude": ["a/**", "b/**"]}),
                         ["a/**", "b/**"])
        self.assertEqual(HOOK.excludes_of({"excludes": "a/**"}), ["a/**"])
        self.assertEqual(HOOK.excludes_of({}), [])

    def test_exclude_answers_to_the_same_bounds_as_glob(self):
        long_one = "x" * (HOOK.MAX_GLOB_CHARS + 1)
        self.assertEqual(HOOK.excludes_of({"exclude": [long_one, "ok/**"]}),
                         ["ok/**"])
        many = [f"p{index}/**" for index in range(HOOK.MAX_GLOBS_PER_RULE + 5)]
        self.assertEqual(len(HOOK.excludes_of({"exclude": many})),
                         HOOK.MAX_GLOBS_PER_RULE)

    def test_tool_reads_the_kinds_it_knows(self):
        self.assertEqual(HOOK.tools_of({"tool": "write"}), ("write",))
        self.assertEqual(HOOK.tools_of({"tool": "READ"}), ("read",))
        self.assertEqual(HOOK.tools_of({"tools": ["read", "write"]}),
                         ("read", "write"))

    def test_an_unreadable_tool_value_is_no_filter_at_all(self):
        """Fail-open, deliberately: a filter only narrows, so a typo in one
        must never be the reason a rule stops being injected."""
        self.assertEqual(HOOK.tools_of({"tool": "wirte"}), ())
        self.assertEqual(HOOK.tools_of({"tool": ""}), ())
        self.assertEqual(HOOK.tools_of({}), ())

    def test_any_says_no_restriction_out_loud(self):
        self.assertEqual(HOOK.tools_of({"tool": "any"}), ())
        self.assertEqual(HOOK.tools_of({"tool": "all"}), ())
        self.assertEqual(HOOK.tools_of({"tool": ["any", "write"]}), ())

    def test_tool_values_keeps_what_tools_of_discards(self):
        """The raw list is what `validate` reports and what a rewrite carries
        through — dropping an unknown value on `update` would delete the typo
        the user still has to see."""
        self.assertEqual(HOOK.tool_values_of({"tool": ["Write", "wirte"]}),
                         ["write", "wirte"])

    def test_a_write_tool_is_a_write_and_everything_else_is_a_read(self):
        for tool in WRITE_TOOLS:
            self.assertEqual(HOOK.tool_kind(tool), HOOK.TOOL_KIND_WRITE)
        self.assertEqual(HOOK.tool_kind("Read"), HOOK.TOOL_KIND_READ)

    def test_no_tool_in_hand_means_no_filtering(self):
        """`which` asks what a rule covers without a tool call — and a payload
        that somehow arrives without a tool name must not lose rules either."""
        self.assertTrue(HOOK.tool_allows({"tool": "write"}, None))
        self.assertFalse(HOOK.tool_allows({"tool": "write"},
                                          HOOK.TOOL_KIND_READ))


class HookExcludeTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)
    TOUCHED = "src/api/users.py"

    def write_rule(self, *excludes):
        util.write_rule(self.proj, "CONV_src.md", "src/**", BODY,
                        extra_frontmatter=exclude_frontmatter(excludes))

    def test_an_excluded_path_gets_nothing(self):
        self.write_rule("src/**/*.test.py")
        self.assertIsNone(self.touch(rel="src/api/users.test.py")[1])

    def test_a_path_the_exclude_misses_still_gets_the_rule(self):
        self.write_rule("src/**/*.test.py")
        self.assertIn(BODY, self.touch()[1])

    def test_several_excludes_all_apply(self):
        self.write_rule("src/**/*.test.py", "src/vendor/**")
        self.assertIsNone(self.touch(rel="src/vendor/lib.py", session="s1")[1])
        self.assertIsNone(self.touch(rel="src/api/users.test.py", session="s2")[1])
        self.assertIn(BODY, self.touch(rel="src/api/users.py", session="s3")[1])

    def test_an_exclude_only_binds_the_rule_that_declares_it(self):
        self.write_rule("src/**/*.test.py")
        util.write_rule(self.proj, "CONV_other.md", "src/**", "OTHER RULE")
        text = self.touch(rel="src/api/users.test.py")[1]
        self.assertIn("OTHER RULE", text)
        self.assertNotIn(BODY, text)

    def test_exclude_works_in_the_global_scope_on_absolute_paths(self):
        project = self.proj.replace(os.sep, "/")
        util.write_rule(self.home, "CONV_everything.md", f"{project}/src/**", BODY,
                        extra_frontmatter=[f"exclude: {project}/src/vendor/**"])
        self.assertIn(BODY, self.touch()[1])
        self.assertIsNone(self.touch(rel="src/vendor/lib.py", session="s2")[1])


class HookToolFilterTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)
    TOUCHED = "src/api/users.py"

    def write_rule(self, tool):
        util.write_rule(self.proj, "CONV_src.md", "src/**", BODY,
                        extra_frontmatter=[f"tool: {tool}"])

    def test_write_only_rule_skips_a_read(self):
        self.write_rule("write")
        self.assertIsNone(self.touch(tool="Read")[1])

    def test_write_only_rule_fires_for_every_write_tool(self):
        self.write_rule("write")
        for index, tool in enumerate(WRITE_TOOLS):
            with self.subTest(tool=tool):
                self.assertIn(BODY, self.touch(tool=tool, session=f"s{index}")[1])

    def test_read_only_rule_skips_a_write(self):
        self.write_rule("read")
        self.assertIn(BODY, self.touch(tool="Read")[1])
        self.assertIsNone(self.touch(tool="Write", session="s2")[1])

    def test_an_unreadable_value_leaves_the_rule_unfiltered(self):
        self.write_rule("wirte")
        self.assertIn(BODY, self.touch(tool="Read")[1])
        self.assertIn(BODY, self.touch(tool="Write", session="s2")[1])

    def test_a_read_does_not_spend_a_write_only_rule(self):
        """The economy the filter buys: without it the Read delivers the rule
        and the dedup counts it as seen, so the Write — the call that was
        actually about to break the convention — gets nothing."""
        self.write_rule("write")
        self.assertIsNone(self.touch(tool="Read")[1])
        self.assertIn(BODY, self.touch(tool="Write")[1])


class HookCombinedFiltersTest(util.SandboxTestCase):
    """Every filter is restrictive: all of them must be satisfied."""

    PROJECT_SUBDIRS = ("src/api",)
    TOUCHED = "src/api/users.py"

    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "CONV_src.md", "src/**", BODY,
                        extra_frontmatter=["exclude: src/**/*.test.py",
                                           "tool: write"])

    def test_all_three_satisfied_injects(self):
        self.assertIn(BODY, self.touch(tool="Write")[1])

    def test_the_glob_alone_is_not_enough(self):
        self.assertIsNone(self.touch(tool="Read")[1])

    def test_the_tool_alone_is_not_enough(self):
        self.assertIsNone(self.touch(rel="src/api/users.test.py", tool="Write")[1])

    def test_outside_the_glob_nothing_applies(self):
        self.assertIsNone(self.touch(rel="docs/readme.md", tool="Write")[1])


class HookEnforceInteractionTest(util.SandboxTestCase):
    """`enforce: deny` answers to the filters like everything else — it acts on
    the rules that APPLY, and a filter decides which those are."""

    PROJECT_SUBDIRS = ("infra/prod",)
    TOUCHED = "infra/prod/main.tf"

    def write_global_rule(self, *extra):
        project = self.proj.replace(os.sep, "/")
        util.write_rule(self.home, "BUSN_no-prod-writes.md",
                        f"{project}/infra/prod/**", "Never touch prod by hand.",
                        extra_frontmatter=["enforce: deny", *extra])

    def test_a_write_only_deny_still_denies(self):
        self.write_global_rule("tool: write")
        hso = util.hook_specific_output(self.hook_for(tool="Write"))
        self.assertEqual(hso.get("permissionDecision"), "deny")

    def test_a_read_only_deny_never_fires(self):
        self.write_global_rule("tool: read")
        hso = util.hook_specific_output(self.hook_for(tool="Write"))
        self.assertNotIn("permissionDecision", hso)

    def test_an_excluded_path_is_not_denied(self):
        self.write_global_rule(f"exclude: {self.proj}/infra/prod/README.md")
        hso = util.hook_specific_output(self.hook_for(rel="infra/prod/README.md",
                                                      tool="Write"))
        self.assertNotIn("permissionDecision", hso)
        denied = util.hook_specific_output(self.hook_for(tool="Write"))
        self.assertEqual(denied.get("permissionDecision"), "deny")


if __name__ == "__main__":
    unittest.main()
