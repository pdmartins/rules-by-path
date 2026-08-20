"""Unit and end-to-end tests for the supersede notice: what marks a fresh
injection as replacing an earlier, now-stale copy of the same rule that an
edit left behind in the transcript (rules_by_path.state.pop_superseded_entries
and the wiring in rules_by_path.main / rules_by_path.context)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


class PopSupersededEntriesTest(unittest.TestCase):
    """Direct coverage of the function: no subprocess, no rule files — just
    the `seen` dict shape main() already hands it."""

    def test_a_stale_entry_under_the_same_scope_and_name_is_removed_and_reported(self):
        seen = {"/proj::src.md::old1": [3, 10_000, 0]}
        self.assertTrue(HOOK.pop_superseded_entries(seen, "/proj", "src.md", "new1"))
        self.assertEqual(seen, {}, "the stale entry is gone")

    def test_no_stale_entry_means_nothing_removed_and_false_reported(self):
        seen = {}
        self.assertFalse(HOOK.pop_superseded_entries(seen, "/proj", "src.md", "new1"))
        self.assertEqual(seen, {})

    def test_a_current_digest_already_on_file_is_never_treated_as_stale(self):
        """The digest passed in is the one about to be injected — an entry
        already recorded under that exact key is a repeat, not an edit."""
        seen = {"/proj::src.md::new1": [3, 10_000, 0]}
        self.assertFalse(HOOK.pop_superseded_entries(seen, "/proj", "src.md", "new1"))
        self.assertEqual(seen, {"/proj::src.md::new1": [3, 10_000, 0]})

    def test_a_name_that_merely_shares_a_prefix_is_left_alone(self):
        """`src.md` and `src2.md` must not collide: the `::` after the name is
        what makes the prefix match exact, not just textual."""
        seen = {"/proj::src2.md::old1": [3, 10_000, 0]}
        self.assertFalse(HOOK.pop_superseded_entries(seen, "/proj", "src.md", "new1"))
        self.assertEqual(seen, {"/proj::src2.md::old1": [3, 10_000, 0]})

    def test_a_different_scope_directory_is_left_alone(self):
        seen = {"/other::src.md::old1": [3, 10_000, 0]}
        self.assertFalse(HOOK.pop_superseded_entries(seen, "/proj", "src.md", "new1"))
        self.assertEqual(seen, {"/other::src.md::old1": [3, 10_000, 0]})

    def test_several_earlier_editions_are_all_swept_in_one_call(self):
        """A rule edited more than once in a session must not leave one dead
        entry behind per edit."""
        seen = {
            "/proj::src.md::old1": [1, 10_000, 0],
            "/proj::src.md::old2": [2, 20_000, 1],
            "/proj::other.md::keep": [2, 20_000, 0],
        }
        self.assertTrue(HOOK.pop_superseded_entries(seen, "/proj", "src.md", "new1"))
        self.assertEqual(seen, {"/proj::other.md::keep": [2, 20_000, 0]},
                         "only the two stale editions of THIS rule are gone")


class SupersedeNoticeEndToEndTest(unittest.TestCase):
    """Acceptance case: editing a rule mid-session marks the very next
    delivery as superseding the earlier text, and the stale entry does not
    linger in `seen` afterwards."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.proj, "src"), exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, session="sup"):
        return util.injected_text(util.run_hook(util.read_payload(
            "Read", os.path.join(self.proj, "src", "a.py"), session=session),
            self.home))

    def state_seen(self, session):
        path = os.path.join(self.home, ".claude", "cache", "rules-by-path",
                            f"{session}.json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)["seen"]

    def test_a_brand_new_rule_carries_no_supersede_notice(self):
        util.write_rule(self.proj, "src.md", "src/**", "VERSION ONE")
        text = self.touch()
        self.assertIn("VERSION ONE", text)
        self.assertNotIn(HOOK.SUPERSEDE_NOTICE, text)

    def test_editing_the_rule_marks_the_next_injection_as_superseding(self):
        util.write_rule(self.proj, "src.md", "src/**", "VERSION ONE")
        first = self.touch()
        self.assertIn("VERSION ONE", first)
        self.assertIsNone(self.touch(), "unchanged content: not due yet")

        util.write_rule(self.proj, "src.md", "src/**", "VERSION TWO body line")
        second = self.touch()
        self.assertIn(HOOK.SUPERSEDE_NOTICE, second)
        self.assertIn("VERSION TWO", second)
        self.assertNotIn("VERSION ONE", second, "the old wording is gone entirely")
        self.assertLess(second.index(HOOK.SUPERSEDE_NOTICE), second.index("VERSION TWO"),
                        "the notice comes before the body it is about")

    def test_seen_does_not_accumulate_one_entry_per_edit(self):
        prefix = os.path.realpath(util.scope_dir(self.proj)) + "::src.md::"
        util.write_rule(self.proj, "src.md", "src/**", "VERSION ONE")
        self.touch(session="grow")
        for version in range(2, 6):
            util.write_rule(self.proj, "src.md", "src/**", f"VERSION {version}")
            self.touch(session="grow")
        matching = [key for key in self.state_seen("grow") if key.startswith(prefix)]
        self.assertEqual(len(matching), 1,
                         "only the current edition's entry should remain")

    def test_repeating_the_same_new_version_does_not_repeat_the_notice(self):
        """The notice marks the delivery that supersedes something, not every
        delivery of the version that did the superseding."""
        util.write_rule(self.proj, "src.md", "src/**", "VERSION ONE",
                        extra_frontmatter=["remember_again_after: 1 calls"])
        self.touch(session="once")
        util.write_rule(self.proj, "src.md", "src/**", "VERSION TWO",
                        extra_frontmatter=["remember_again_after: 1 calls"])
        supersedes = self.touch(session="once")
        self.assertIn(HOOK.SUPERSEDE_NOTICE, supersedes)
        repeat = self.touch(session="once")
        self.assertIsNotNone(repeat, "the rule is still due again by call count")
        self.assertNotIn(HOOK.SUPERSEDE_NOTICE, repeat,
                         "this delivery repeats the current version; nothing new "
                         "is being superseded")

    def test_truncation_and_supersede_coexist_in_a_stable_order(self):
        big_v1 = "V1 " + "x" * (HOOK.MAX_RULE_CHARS + 1_000)
        big_v2 = "V2 " + "y" * (HOOK.MAX_RULE_CHARS + 1_000)
        util.write_rule(self.proj, "big.md", "src/**", big_v1)
        first = self.touch(session="trunc")
        self.assertIn("truncated", first)
        self.assertNotIn(HOOK.SUPERSEDE_NOTICE, first)

        util.write_rule(self.proj, "big.md", "src/**", big_v2)
        second = self.touch(session="trunc")
        self.assertIn(HOOK.SUPERSEDE_NOTICE, second)
        self.assertIn("truncated", second)
        self.assertLess(second.index(HOOK.SUPERSEDE_NOTICE), second.index("V2 "),
                        "supersede notice leads the body")
        self.assertLess(second.rindex("V2"), second.index("truncated"),
                        "truncation notice trails the body")


if __name__ == "__main__":
    unittest.main()
