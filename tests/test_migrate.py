"""End-to-end tests for the admin `migrate` subcommand: the pre-plugin
`rules-map.yml` layout, and the 0.4.0 rename of the type prefixes and of the
`remember_after:` frontmatter key."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402


class MigrationTest(util.SandboxTestCase):
    def write_legacy(self, entries, bodies):
        os.makedirs(os.path.join(self.scope, "rules"), exist_ok=True)
        lines = ["rules:"]
        for glob, name in entries:
            lines.append(f'  - glob: "{glob}"')
            if name:
                lines.append(f'    rule: "{name}"')
        with open(os.path.join(self.scope, "rules-map.yml"), "w", encoding="utf-8") as h:
            h.write("\n".join(lines) + "\n")
        for name, body in bodies.items():
            with open(os.path.join(self.scope, "rules", name), "w", encoding="utf-8") as h:
                h.write(body)

    def test_migrate_converts_and_removes_the_legacy_files(self):
        self.write_legacy([("src/api/**", "src--api.md"), ("docs/**", None)],
                          {"src--api.md": "API RULE", "docs.md": "DOCS RULE"})
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        with open(os.path.join(self.scope, "src--api.md"), encoding="utf-8") as h:
            content = h.read()
        self.assertIn("glob: src/api/**", content)
        self.assertIn("API RULE", content)
        self.assertFalse(os.path.isfile(os.path.join(self.scope, "rules-map.yml")))
        self.assertFalse(os.path.isdir(os.path.join(self.scope, "rules")))

    def test_migrated_rules_actually_inject(self):
        self.write_legacy([("src/**", None)], {"src.md": "MIGRATED RULE"})
        self.admin("migrate", "--root", self.proj)
        proc = util.run_hook(util.read_payload(
            "Read", os.path.join(self.proj, "src", "a.py")), self.home)
        self.assertIn("MIGRATED RULE", util.injected_text(proc))

    def test_migrate_merges_globs_that_shared_a_rule_file(self):
        self.write_legacy([("src/**", "shared.md"), ("lib/**", "shared.md")],
                          {"shared.md": "SHARED"})
        self.admin("migrate", "--root", self.proj)
        with open(os.path.join(self.scope, "shared.md"), encoding="utf-8") as h:
            content = h.read()
        self.assertIn("  - src/**", content)
        self.assertIn("  - lib/**", content)

    def test_migrate_keeps_legacy_files_when_something_is_skipped(self):
        self.write_legacy([("src/**", None), ("gone/**", None)], {"src.md": "OK"})
        proc = self.admin("migrate", "--root", self.proj)
        self.assertIn("skipped", proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.scope, "rules-map.yml")),
                        "nothing is deleted while an entry is unresolved")

    def test_migrate_without_legacy_files_is_a_noop(self):
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("nothing to migrate", proc.stdout)


class MigrateToTypedNamesTest(util.SandboxTestCase):
    """0.4.0 renamed the type prefixes and the frontmatter key; `migrate` is
    what carries an existing scope across."""

    def test_legacy_prefixes_are_renamed(self):
        util.write_rule(self.proj, "Business_order-not-cancellable.md",
                        "src/Domain/**", "An invoiced order cannot be cancelled.")
        util.write_rule(self.proj, "Convention_api-problemdetails.md",
                        "src/Api/**", "Errors return ProblemDetails.")
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(
            os.path.join(self.scope, "BUSN_order-not-cancellable.md")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.scope, "CONV_api-problemdetails.md")))
        self.assertFalse(os.path.isfile(
            os.path.join(self.scope, "Business_order-not-cancellable.md")))

    def test_the_renamed_frontmatter_key_is_rewritten(self):
        util.write_rule(self.proj, "OTHR_src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_after: 40k"])
        self.admin("migrate", "--root", self.proj)
        content = self.read_rule("OTHR_src.md")
        self.assertIn("remember_again_after: 40k", content)
        self.assertNotIn("\nremember_after:", content)

    def test_an_untyped_rule_is_reported_never_guessed(self):
        util.write_rule(self.proj, "hv-dotnet-stack.md", "**/*.cs", "Stack rules.")
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("hv-dotnet-stack.md", proc.stdout)
        self.assertIn("no type prefix", proc.stdout)
        self.assertTrue(os.path.isfile(
            os.path.join(self.scope, "hv-dotnet-stack.md")), "left alone")

    def test_migrating_twice_changes_nothing_the_second_time(self):
        util.write_rule(self.proj, "Business_x.md", "src/**", "Rule.",
                        extra_frontmatter=["remember_after: 40k"])
        self.admin("migrate", "--root", self.proj)
        before = self.read_rule("BUSN_x.md")
        proc = self.admin("migrate", "--root", self.proj)
        self.assertIn("nothing to migrate", proc.stdout)
        self.assertEqual(before, self.read_rule("BUSN_x.md"))

    def test_a_rename_does_not_clobber_an_existing_rule(self):
        util.write_rule(self.proj, "Business_x.md", "src/**", "OLD")
        util.write_rule(self.proj, "BUSN_x.md", "src/**", "CURRENT")
        proc = self.admin("migrate", "--root", self.proj)
        self.assertIn("CURRENT", self.read_rule("BUSN_x.md"))
        self.assertIn("already exists", proc.stderr)

    def test_a_migrated_scope_still_injects(self):
        util.write_rule(self.proj, "Business_x.md", "src/**", "MIGRATED RULE",
                        extra_frontmatter=["remember_after: 40k"])
        self.admin("migrate", "--root", self.proj)
        proc = util.run_hook(util.read_payload(
            "Read", os.path.join(self.proj, "src", "a.py")), self.home)
        self.assertIn("MIGRATED RULE", util.injected_text(proc) or "")


if __name__ == "__main__":
    unittest.main()
