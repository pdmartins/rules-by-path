"""Unit tests for the pure functions of hooks/rules-by-path.py: glob matching,
rule-name derivation and frontmatter parsing. No subprocess, no sandbox."""

import contextlib
import io
import os
import sys
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
            "src/api/**": "src-api.md",
            "docs": "docs.md",
            "docs/": "docs.md",
            "src/config.json": "src-config-json.md",
            "**/deploy/**": "deploy.md",
            "/repos/x/**": "repos-x.md",
            "**": "root.md",
            # The forms that used to produce a name the allowlist then refused,
            # which made `add --glob` fail on the most idiomatic globs of all.
            "src/**/*.py": "src-py.md",
            "docs/**/*.md": "docs-md.md",
            "*.cs": "cs.md",
            "/repos/_hv/**/*.cs": "repos-hv-cs.md",
            "docs/architecture.md": "docs-architecture.md",
        }
        for glob, expected in cases.items():
            self.assertEqual(HOOK.derive_rule_name(glob), expected, glob)
            self.assertTrue(HOOK.is_valid_rule_name(HOOK.derive_rule_name(glob)), glob)


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

    def test_remember_again_after_values(self):
        self.assertEqual(HOOK.remember_again_after_of({"remember_again_after": "30k"}),
                         (30_000, "tokens"))
        self.assertEqual(HOOK.remember_again_after_of({"remember_again_after": "30000"}),
                         (30_000, "tokens"))
        self.assertEqual(HOOK.remember_again_after_of({"remember_again_after": "25 calls"}),
                         (25, "calls"))
        self.assertEqual(HOOK.remember_again_after_of({"remember_again_after": "never"}), (0, None))
        self.assertIsNone(HOOK.remember_again_after_of({}))
        self.assertIsNone(HOOK.remember_again_after_of({"remember_again_after": "nonsense"}))

    def test_a_bare_number_too_small_to_be_tokens_is_refused(self):
        """`remember_again_after: 25` is far more likely to be a leftover call count
        than a 25-token budget, and honouring it would repeat the rule on every
        single tool call."""
        self.assertIsNone(HOOK.remember_again_after_of({"remember_again_after": "25"}))
        self.assertEqual(HOOK.remember_again_after_of({"remember_again_after": "25 calls"}),
                         (25, "calls"))

    def test_an_explicit_token_unit_below_the_floor_is_not_called_a_typo(self):
        """`500 tokens` is refused like any sub-minimum value, but the author
        stated the unit — the "leftover call count" guess does not apply and the
        message must not send them hunting for a mistake they did not make."""
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertIsNone(HOOK.remember_again_after_of({"remember_again_after": "500 tokens"}))
        self.assertIn("below the", stderr.getvalue())
        self.assertNotIn("old format", stderr.getvalue())

    def test_an_out_of_range_size_is_a_parse_failure_not_a_crash(self):
        """`inf` reaches int() as a float it cannot convert, which raises
        OverflowError rather than ValueError."""
        for value in ("inf", "-inf", "1e400"):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertIsNone(HOOK.remember_again_after_of({"remember_again_after": value}),
                                  value)
            self.assertIn("not understood", stderr.getvalue())

    def test_a_repeated_frontmatter_key_is_reported(self):
        """The last one wins, as in YAML. Doing it silently makes two `glob:`
        lines look like two covered paths when they are one."""
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            fields, _ = HOOK.parse_frontmatter(
                "---\nglob: src/**\nglob: docs/**\n---\nx")
        self.assertEqual(HOOK.globs_of(fields), ["docs/**"])
        self.assertIn("more than once", stderr.getvalue())

    def test_sizes_accept_k_and_m_suffixes(self):
        self.assertEqual(HOOK.parse_size("30k"), 30_000)
        self.assertEqual(HOOK.parse_size("1M"), 1_000_000)
        self.assertEqual(HOOK.parse_size("200000"), 200_000)

    def test_the_default_follows_what_the_session_can_measure(self):
        config = HOOK.load_config()  # the plugin's own config.json, no overrides
        self.assertEqual(HOOK.remember_again_after_default(config, True),
                         (HOOK.DEFAULT_REMEMBER_AGAIN_TOKENS, "tokens"))
        self.assertEqual(HOOK.remember_again_after_default(config, False),
                         (HOOK.DEFAULT_REMEMBER_AGAIN_CALLS, "calls"))

    def test_the_key_renamed_in_0_4_0_is_still_honoured(self):
        """`remember_after:` was the name until 0.4.0. Dropping the setting
        because a hand-written rule uses the old spelling would change behaviour
        for someone who changed nothing."""
        self.assertEqual(HOOK.remember_again_after_of({"remember_after": "40k"}),
                         (40_000, "tokens"))
        self.assertEqual(
            HOOK.remember_again_after_of({"remember_after": "40k",
                                          "remember_again_after": "10k"}),
            (10_000, "tokens"), "the current key wins when both are present")


if __name__ == "__main__":
    unittest.main()
