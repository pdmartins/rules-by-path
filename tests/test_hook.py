"""End-to-end and unit tests for hooks/rules-by-path.py."""

import json
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

    def test_invalid_glob_is_skipped_not_fatal(self):
        self.assertFalse(HOOK.glob_matches("[", "src/a.py", "/proj/src/a.py"))


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


class FallbackParserTest(unittest.TestCase):
    def test_admin_format(self):
        text = (
            "# comment line\n"
            "rules:\n"
            '  - glob: "src/api/**"\n'
            '    rule: "src--api.md"\n'
            '  - glob: "docs/**"\n'
            "  - \"*.tf\"\n"
        )
        entries = HOOK.parse_map_without_yaml(text, "test-map")
        self.assertEqual(entries, [
            {"glob": "src/api/**", "rule": "src--api.md"},
            {"glob": "docs/**", "rule": None},
            {"glob": "*.tf", "rule": None},
        ])

    def test_empty_and_garbage(self):
        self.assertEqual(HOOK.parse_map_without_yaml("rules:\n", "m"), [])
        self.assertEqual(HOOK.parse_map_without_yaml("", "m"), [])
        entries = HOOK.parse_map_without_yaml("rules:\n  nonsense here\n", "m")
        self.assertEqual(entries, [])


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

    def test_injects_matching_project_rule_once_per_session(self):
        util.write_rule_setup(self.proj, [("src/api/**", None, "API RULE CONTENT")])
        payload = util.read_payload("Read", self.target("src/api/users.py"), session="s1")
        proc = util.run_hook(payload, self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = util.hook_output(proc)
        self.assertIsNotNone(out, "expected an injection")
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("API RULE CONTENT", context)
        self.assertIn("src/api/**", context)
        self.assertTrue(out.get("suppressOutput"))
        # same session: dedup
        proc2 = util.run_hook(payload, self.home)
        self.assertIsNone(util.hook_output(proc2), "second touch must not re-inject")
        # other session: injects again
        payload3 = util.read_payload("Read", self.target("src/api/users.py"), session="s2")
        self.assertIsNotNone(util.hook_output(util.run_hook(payload3, self.home)))

    def test_reset_session_reinjects(self):
        util.write_rule_setup(self.proj, [("src/api/**", None, "API RULE CONTENT")])
        payload = util.read_payload("Edit", self.target("src/api/users.py"), session="s1")
        self.assertIsNotNone(util.hook_output(util.run_hook(payload, self.home)))
        self.assertIsNone(util.hook_output(util.run_hook(payload, self.home)))
        reset_payload = {"session_id": "s1", "hook_event_name": "SessionStart"}
        util.run_hook(reset_payload, self.home, args=("--reset-session",))
        self.assertIsNotNone(util.hook_output(util.run_hook(payload, self.home)),
                             "after reset the rule must inject again")

    def test_non_matching_file_no_output(self):
        util.write_rule_setup(self.proj, [("src/api/**", None, "API RULE CONTENT")])
        payload = util.read_payload("Read", self.target("src/other/users.py"))
        self.assertIsNone(util.hook_output(util.run_hook(payload, self.home)))

    def test_rules_dir_files_never_trigger(self):
        util.write_rule_setup(self.proj, [("**", None, "EVERYTHING")])
        inside = os.path.join(self.proj, ".claude", "rules-by-path", "rules", "x.md")
        payload = util.read_payload("Read", inside)
        self.assertIsNone(util.hook_output(util.run_hook(payload, self.home)))

    def test_no_rules_anywhere_no_output(self):
        payload = util.read_payload("Read", self.target("src/api/users.py"))
        proc = util.run_hook(payload, self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(util.hook_output(proc))

    def test_global_scope_absolute_glob(self):
        util.write_rule_setup(self.home, [(f"{self.proj}/**".replace(os.sep, "/"),
                                           "proj.md", "GLOBAL RULE")])
        payload = util.read_payload("Read", self.target("anything.txt"))
        out = util.hook_output(util.run_hook(payload, self.home))
        self.assertIsNotNone(out)
        self.assertIn("GLOBAL RULE", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("global", out["hookSpecificOutput"]["additionalContext"])

    def test_nested_claude_md_write_denied(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        payload = util.read_payload("Write", self.target("src/CLAUDE.md"))
        out = util.hook_output(util.run_hook(payload, self.home))
        self.assertIsNotNone(out)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_root_claude_md_write_allowed(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        payload = util.read_payload("Write", self.target("CLAUDE.md"))
        self.assertIsNone(util.hook_output(util.run_hook(payload, self.home)))

    def test_nested_claude_md_read_not_denied(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        payload = util.read_payload("Read", self.target("src/CLAUDE.md"))
        out = util.hook_output(util.run_hook(payload, self.home))
        self.assertTrue(out is None or "permissionDecision"
                        not in out.get("hookSpecificOutput", {}))

    def test_nested_repo_claude_md_allowed(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        os.makedirs(os.path.join(self.proj, "vendor", "lib", ".git"), exist_ok=True)
        payload = util.read_payload("Write", self.target("vendor/lib/CLAUDE.md"))
        self.assertIsNone(util.hook_output(util.run_hook(payload, self.home)))

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_rule_outside_rules_dir_refused(self):
        secret = os.path.join(self.tmp.name, "secret.txt")
        with open(secret, "w", encoding="utf-8") as handle:
            handle.write("TOP SECRET KEY MATERIAL")
        base = util.write_rule_setup(self.proj, [("src/**", "evil.md", "placeholder")])
        evil = os.path.join(base, "rules", "evil.md")
        os.unlink(evil)
        os.symlink(secret, evil)
        payload = util.read_payload("Read", self.target("src/a.py"))
        proc = util.run_hook(payload, self.home)
        self.assertIsNone(util.hook_output(proc), "symlinked rule must not inject")
        self.assertIn("cannot open rule", proc.stderr)

    def test_oversized_rule_truncated(self):
        util.write_rule_setup(self.proj, [("src/**", None, "X" * 20_000)])
        payload = util.read_payload("Read", self.target("src/a.py"))
        out = util.hook_output(util.run_hook(payload, self.home))
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("truncated", context)
        self.assertLess(len(context), 18_000)

    def test_total_cap_defers_extra_rules(self):
        # four ~15k rules all matching the same file: 3 fit under the 48k cap,
        # the 4th is deferred to the next tool call
        util.write_rule_setup(
            self.proj, [("src/**", f"r{i}.md", f"RULE{i} " + "y" * 15_000) for i in range(4)])
        payload = util.read_payload("Read", self.target("src/a.py"), session="cap")
        proc = util.run_hook(payload, self.home)
        context = util.hook_output(proc)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("RULE0", context)
        self.assertIn("RULE2", context)
        self.assertNotIn("RULE3", context, "fourth rule must be deferred")
        self.assertIn("left for the next tool call", proc.stderr)
        # next call picks up the deferred rule
        proc2 = util.run_hook(payload, self.home)
        context2 = util.hook_output(proc2)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("RULE3", context2)

    def test_huge_map_ignored(self):
        base = util.write_rule_setup(self.proj, [("src/**", None, "RULE")])
        with open(os.path.join(base, "rules-map.yml"), "a", encoding="utf-8") as handle:
            handle.write("# " + "x" * 300_000 + "\n")
        payload = util.read_payload("Read", self.target("src/a.py"))
        proc = util.run_hook(payload, self.home)
        self.assertIsNone(util.hook_output(proc))
        self.assertIn("exceeds", proc.stderr)

    def test_hostile_long_glob_skipped(self):
        base = util.write_rule_setup(self.proj, [("src/**", None, "GOOD")])
        hostile = "*a" * 300
        with open(os.path.join(base, "rules-map.yml"), "w", encoding="utf-8") as handle:
            handle.write(f'rules:\n  - glob: "{hostile}"\n  - glob: "src/**"\n')
        payload = util.read_payload("Read", self.target("src/a.py"))
        proc = util.run_hook(payload, self.home)
        out = util.hook_output(proc)
        self.assertIsNotNone(out, "good rule must still inject")
        self.assertIn("GOOD", out["hookSpecificOutput"]["additionalContext"])

    def test_malformed_stdin_never_fails(self):
        proc = util.run_hook("this is not json", self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("unexpected error", proc.stderr)

    def test_payload_without_file_path_no_output(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls"},
                   "session_id": "s", "cwd": self.proj}
        proc = util.run_hook(payload, self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(util.hook_output(proc))

    def test_relative_path_resolved_against_cwd(self):
        util.write_rule_setup(self.proj, [("src/api/**", None, "API RULE CONTENT")])
        payload = util.read_payload("Read", os.path.join("src", "api", "x.py"),
                                    cwd=self.proj)
        out = util.hook_output(util.run_hook(payload, self.home))
        self.assertIsNotNone(out)

    def test_missing_rule_file_warns_and_skips(self):
        base = util.write_rule_setup(self.proj, [("src/**", None, "RULE")])
        os.unlink(os.path.join(base, "rules", "src.md"))
        payload = util.read_payload("Read", self.target("src/a.py"))
        proc = util.run_hook(payload, self.home)
        self.assertIsNone(util.hook_output(proc))
        self.assertIn("cannot open rule", proc.stderr)

    def test_rule_name_with_separator_refused(self):
        base = util.write_rule_setup(self.proj, [("src/**", None, "RULE")])
        with open(os.path.join(base, "rules-map.yml"), "w", encoding="utf-8") as handle:
            handle.write('rules:\n  - glob: "src/**"\n    rule: "../../secrets.md"\n')
        payload = util.read_payload("Read", self.target("src/a.py"))
        proc = util.run_hook(payload, self.home)
        self.assertIsNone(util.hook_output(proc))
        self.assertIn("invalid rule name", proc.stderr)


if __name__ == "__main__":
    unittest.main()
