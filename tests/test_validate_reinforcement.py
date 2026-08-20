"""`validate`'s reinforcement lint: a rule whose body reads like a prohibition
but repeats 'never', and a rule with no prohibition language repeating so
tightly it looks like over-treatment. Advice only — `validate` still exits 0
for these, unlike the errors that mean something will not work at all."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402


class ReinforcementLintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(self.proj)

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self):
        return util.run_admin(["validate", "--root", self.proj], self.home)

    def test_a_prohibition_with_reinforcement_off_gets_a_note(self):
        util.write_rule(self.proj, "BUSN_no-secrets.md", "src/**",
                        "Never log the request body.",
                        extra_frontmatter=["remember_again_after: never"])
        proc = self.validate()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("reads like a prohibition", proc.stdout)
        self.assertIn("BUSN_no-secrets.md", proc.stdout)

    def test_the_note_also_fires_when_never_is_inherited_from_the_type(self):
        """CONV ships with `remember_again_after: never` by default, so a
        rule that names no interval of its own inherits it — and the lint
        must follow that inheritance, not just the rule's own frontmatter."""
        util.write_rule(self.proj, "CONV_naming.md", "src/**",
                        "You must not use abbreviations in public method names.")
        proc = self.validate()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("reads like a prohibition", proc.stdout)

    def test_a_prohibition_with_reinforcement_on_gets_no_note(self):
        util.write_rule(self.proj, "BUSN_no-secrets.md", "src/**",
                        "Never log the request body.",
                        extra_frontmatter=["remember_again_after: 20k"])
        proc = self.validate()
        self.assertNotIn("reads like a prohibition", proc.stdout)

    def test_a_tight_interval_with_no_prohibition_gets_an_over_treatment_note(self):
        util.write_rule(self.proj, "CONV_style.md", "src/**",
                        "Controllers are named PascalCase and end in Controller.",
                        extra_frontmatter=["remember_again_after: 3 calls"])
        proc = self.validate()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("over-treatment", proc.stdout)

    def test_a_tight_interval_on_a_prohibition_is_not_over_treatment(self):
        """A prohibition repeating tightly is the whole point of reinforcing
        it in the first place — not the pattern this note is about."""
        util.write_rule(self.proj, "BUSN_no-secrets.md", "src/**",
                        "Never log the request body.",
                        extra_frontmatter=["remember_again_after: 3 calls"])
        proc = self.validate()
        self.assertNotIn("over-treatment", proc.stdout)

    def test_a_normal_interval_with_no_prohibition_gets_no_note(self):
        util.write_rule(self.proj, "CONV_style.md", "src/**",
                        "Controllers are named PascalCase and end in Controller.",
                        extra_frontmatter=["remember_again_after: 50k"])
        proc = self.validate()
        self.assertNotIn("over-treatment", proc.stdout)
        self.assertNotIn("reads like a prohibition", proc.stdout)

    def test_a_portuguese_prohibition_phrase_is_recognised(self):
        util.write_rule(self.proj, "BUSN_invariante.md", "src/**",
                        "O pedido nunca pode ser cancelado após faturado.",
                        extra_frontmatter=["remember_again_after: never"])
        proc = self.validate()
        self.assertIn("reads like a prohibition", proc.stdout)


if __name__ == "__main__":
    unittest.main()
