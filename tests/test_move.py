"""`move`: a rule carried between scopes, its globs rewritten for the frame
the destination matches in — and the one ambiguous shape asked about."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

ONLY_MINE_TAXONOMY = {"rule_types": [{"prefix": "MINE", "name": "Mine",
                                      "purpose": "Just one"}]}


class MoveTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)

    def global_rule(self, name):
        with open(os.path.join(self.global_scope, name), encoding="utf-8") as handle:
            return handle.read()

    def test_a_bare_glob_moves_to_global_unchanged(self):
        util.write_rule(self.proj, "CONV_cs.md", "*.cs", "C# RULE",
                        extra_frontmatter=("remember_again_after: 20k", "tool: write"))
        proc = self.admin("move", "--root", self.proj, "--rule", "CONV_cs.md", "--to-global")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok: moved CONV_cs.md", proc.stdout)
        self.assertNotIn("->  '", proc.stdout.split("ok:")[1].split("\n")[0])
        self.assertFalse(os.path.exists(os.path.join(self.scope, "CONV_cs.md")))
        content = self.global_rule("CONV_cs.md")
        self.assertIn("glob: *.cs", content)
        self.assertIn("remember_again_after: 20k", content)
        self.assertIn("tool: write", content)
        self.assertIn("C# RULE", content)

    def test_a_root_anchored_glob_needs_an_anchor_to_go_global(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        proc = self.admin("move", "--root", self.proj, "--rule", "CONV_api.md", "--to-global")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--anchor any-project", proc.stderr)
        self.assertIn("'**/src/api/**'", proc.stderr)
        self.assertIn(f"'{self.proj}/src/api/**'", proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.scope, "CONV_api.md")),
                        "nothing moved")

    def test_any_project_floats_the_glob_and_this_project_pins_it(self):
        util.write_rule(self.proj, "CONV_api.md", "./src/api/**", "API RULE",
                        extra_frontmatter=("exclude: src/api/tests/**",))
        proc = self.admin("move", "--root", self.proj, "--rule", "CONV_api.md",
                          "--to-global", "--anchor", "any-project")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("glob: './src/api/**' -> '**/src/api/**'", proc.stdout)
        self.assertIn("exclude: 'src/api/tests/**' -> '**/src/api/tests/**'", proc.stdout)
        content = self.global_rule("CONV_api.md")
        self.assertIn("glob: **/src/api/**", content)
        self.assertIn("exclude: **/src/api/tests/**", content)

        util.write_rule(self.proj, "CONV_pinned.md", "src/api/**", "PINNED")
        proc = self.admin("move", "--root", self.proj, "--rule", "CONV_pinned.md",
                          "--to-global", "--anchor", "this-project")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"glob: {self.proj}/src/api/**", self.global_rule("CONV_pinned.md"))

    def test_global_to_project_makes_an_absolute_glob_relative(self):
        util.write_rule(self.home, "CONV_api.md", f"{self.proj}/src/api/**", "API RULE")
        util.write_rule(self.home, "CONV_docs.md", "**/docs/**", "DOCS")
        proc = self.admin("move", "--global", "--rule", "CONV_api.md", "--to-root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"glob: '{self.proj}/src/api/**' -> 'src/api/**'", proc.stdout)
        self.assertIn("glob: src/api/**", self.read_rule("CONV_api.md"))
        proc = self.admin("move", "--global", "--rule", "CONV_docs.md", "--to-root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("glob: **/docs/**", self.read_rule("CONV_docs.md"))

    def test_an_absolute_glob_outside_the_project_cannot_move_there(self):
        util.write_rule(self.home, "CONV_x.md", "/somewhere/else/**", "X")
        proc = self.admin("move", "--global", "--rule", "CONV_x.md", "--to-root", self.proj)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("points outside", proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.global_scope, "CONV_x.md")))

    def test_the_type_must_exist_in_the_destination_taxonomy(self):
        util.write_config(self.scope, ONLY_MINE_TAXONOMY)
        util.write_rule(self.home, "CONV_x.md", "*.cs", "X")
        proc = self.admin("move", "--global", "--rule", "CONV_x.md", "--to-root", self.proj)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not in the destination's taxonomy", proc.stderr)
        self.assertIn("MINE", proc.stderr)

    def test_refuses_to_clobber_without_force(self):
        util.write_rule(self.proj, "CONV_cs.md", "*.cs", "PROJECT VERSION")
        util.write_rule(self.home, "CONV_cs.md", "*.cs", "GLOBAL VERSION")
        proc = self.admin("move", "--root", self.proj, "--rule", "CONV_cs.md", "--to-global")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already exists", proc.stderr)
        proc = self.admin("move", "--root", self.proj, "--rule", "CONV_cs.md",
                          "--to-global", "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PROJECT VERSION", self.global_rule("CONV_cs.md"))

    def test_language_and_enforce_are_warned_about(self):
        util.write_config(self.scope, {"language": "pt-BR"})
        util.write_rule(self.proj, "CONV_cs.md", "*.cs", "REGRA",
                        extra_frontmatter=("enforce: deny",))
        proc = self.admin("move", "--root", self.proj, "--rule", "CONV_cs.md", "--to-global")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("not translated", proc.stderr)
        self.assertIn("will be BLOCKED", proc.stderr)
        self.assertIn("enforce: deny", self.global_rule("CONV_cs.md"))

    def test_flags_are_validated(self):
        proc = self.admin("move", "--root", self.proj, "--rule", "x.md")
        self.assertIn("--to-global OR --to-root", proc.stderr)
        proc = self.admin("move", "--global", "--rule", "x.md", "--to-global")
        self.assertIn("already in the global scope", proc.stderr)
        proc = self.admin("list", "--root", self.proj, "--to-global")
        self.assertIn("belong to `move`", proc.stderr)


if __name__ == "__main__":
    unittest.main()
