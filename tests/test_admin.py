"""End-to-end tests for scripts/rules-by-path-admin.py (subprocess-level)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402


class AdminTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(self.proj)
        self.base = os.path.join(self.proj, ".claude", "rules-by-path")

    def tearDown(self):
        self.tmp.cleanup()

    def admin(self, *args, stdin=""):
        return util.run_admin(list(args), self.home, stdin_text=stdin)

    def read(self, *parts):
        with open(os.path.join(self.base, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_init_creates_skeleton(self):
        proc = self.admin("init", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.base, "rules-map.yml")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "rules")))
        self.assertIn("scope: project", self.read("rules-map.yml"))

    def test_init_global_uses_home(self):
        proc = self.admin("init", "--global")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        global_map = os.path.join(self.home, ".claude", "rules-by-path", "rules-map.yml")
        self.assertTrue(os.path.isfile(global_map))

    def test_add_creates_rule_and_entry(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/api/**",
                          stdin="API RULE")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("src--api.md", proc.stdout)
        self.assertIn("validation ok", proc.stdout)
        self.assertEqual(self.read("rules", "src--api.md").strip(), "API RULE")
        self.assertIn('- glob: "src/api/**"', self.read("rules-map.yml"))

    def test_add_duplicate_refused_force_overwrites(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**", stdin="V1")
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**", stdin="V2")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--force", proc.stderr)
        self.assertEqual(self.read("rules", "src.md").strip(), "V1")
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**", "--force",
                          stdin="V2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read("rules", "src.md").strip(), "V2")

    def test_derived_name_collision_refused(self):
        self.admin("add", "--root", self.proj, "--glob", "src/api/**", stdin="A")
        proc = self.admin("add", "--root", self.proj, "--glob", "src/api/*", stdin="B")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already belongs", proc.stderr)

    def test_metacharacter_name_needs_explicit_rule(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "*.cs", stdin="C#")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--rule", proc.stderr)
        proc = self.admin("add", "--root", self.proj, "--glob", "*.cs",
                          "--rule", "csharp.md", stdin="C#")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read("rules", "csharp.md").strip(), "C#")

    def test_unsafe_rule_name_refused(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--rule", "../evil.md", stdin="X")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("invalid rule name", proc.stderr)

    def test_empty_content_refused(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**", stdin="  \n ")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("empty rule content", proc.stderr)

    def test_which_finds_matches_and_suggests(self):
        self.admin("add", "--root", self.proj, "--glob", "src/api/**", stdin="A")
        proc = self.admin("which", "--root", self.proj, "--path", "src/api/users.py")
        self.assertIn("match: rule src--api.md", proc.stdout)
        # folder query uses the synthetic-child probe
        os.makedirs(os.path.join(self.proj, "src", "api"), exist_ok=True)
        proc = self.admin("which", "--root", self.proj, "--path", "src/api")
        self.assertIn("match: rule src--api.md", proc.stdout)
        proc = self.admin("which", "--root", self.proj, "--path", "docs/guide.md")
        self.assertIn("no entry covers", proc.stdout)
        self.assertIn("docs/**", proc.stdout)

    def test_which_outside_root_fails(self):
        proc = self.admin("which", "--root", self.proj, "--path", "/etc/passwd")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("outside the root", proc.stderr)

    def test_remove_deletes_entry_and_file(self):
        self.admin("add", "--root", self.proj, "--glob", "src/api/**", stdin="A")
        proc = self.admin("remove", "--root", self.proj, "--glob", "src/api/**")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn('\n  - glob: "src/api/**"', self.read("rules-map.yml"))
        self.assertFalse(os.path.isfile(os.path.join(self.base, "rules", "src--api.md")))

    def test_remove_unknown_glob_fails(self):
        self.admin("init", "--root", self.proj)
        proc = self.admin("remove", "--root", self.proj, "--glob", "nope/**")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not registered", proc.stderr)

    def test_validate_detects_missing_rule_file(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**", stdin="A")
        os.unlink(os.path.join(self.base, "rules", "src.md"))
        proc = self.admin("validate", "--root", self.proj)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing rule", proc.stderr)

    def test_validate_empty_scope_ok(self):
        proc = self.admin("validate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("nothing to validate", proc.stdout)

    def test_list_shows_map_and_files(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**", stdin="A")
        proc = self.admin("list", "--root", self.proj)
        self.assertIn("src/**", proc.stdout)
        self.assertIn("src.md", proc.stdout)

    def test_add_then_hook_injects(self):
        """The admin-written map must be exactly what the hook consumes."""
        self.admin("add", "--root", self.proj, "--glob", "src/api/**",
                   stdin="ROUNDTRIP RULE")
        payload = util.read_payload(
            "Read", os.path.join(self.proj, "src", "api", "x.py"))
        out = util.hook_output(util.run_hook(payload, self.home))
        self.assertIsNotNone(out)
        self.assertIn("ROUNDTRIP RULE", out["hookSpecificOutput"]["additionalContext"])

    def test_nonexistent_root_fails(self):
        proc = self.admin("list", "--root", os.path.join(self.tmp.name, "missing"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not exist", proc.stderr)


if __name__ == "__main__":
    unittest.main()
