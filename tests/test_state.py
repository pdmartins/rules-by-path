"""Unit and end-to-end tests for rules_by_path.state.detect_context_regression:
the fallback for when SessionStart(compact|clear)'s async --reset-session
loses the race against the very next PreToolUse call, leaving `seen` pointing
at a token high-water mark the context no longer holds."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


class DetectContextRegressionTest(unittest.TestCase):
    """Direct coverage of the function: no subprocess, no transcript file —
    just the state dict shape main() already hands it."""

    def test_a_hard_drop_clears_seen_and_reports_the_regression(self):
        state = {"calls": 5, "seen": {"k1": [3, 100_000], "k2": [4, 40_000]}}
        self.assertTrue(HOOK.detect_context_regression(state, 1_000))
        self.assertEqual(state["seen"], {}, "seen is wiped so rules reinject")

    def test_calls_survive_the_clear(self):
        state = {"calls": 5, "seen": {"k1": [3, 100_000]}}
        HOOK.detect_context_regression(state, 1_000)
        self.assertEqual(state["calls"], 5, "only seen is cleared, never calls")

    def test_tokens_at_or_above_the_recorded_maximum_change_nothing(self):
        state = {"calls": 2, "seen": {"k1": [1, 50_000]}}
        self.assertFalse(HOOK.detect_context_regression(state, 60_000))
        self.assertEqual(state["seen"], {"k1": [1, 50_000]})

    def test_a_drop_within_the_slack_is_not_a_regression(self):
        """A drop of exactly TOKEN_REGRESSION_SLACK sits on the boundary:
        still not a regression, since the check is a strict `<`."""
        state = {"calls": 2, "seen": {"k1": [1, 50_000]}}
        at_the_slack = 50_000 - HOOK.TOKEN_REGRESSION_SLACK
        self.assertFalse(HOOK.detect_context_regression(state, at_the_slack))
        self.assertEqual(state["seen"], {"k1": [1, 50_000]})

    def test_unreadable_transcript_never_clears(self):
        """current_tokens is None means the transcript could not be read —
        there is nothing to compare, so nothing is ever cleared on that
        basis alone."""
        state = {"calls": 2, "seen": {"k1": [1, 500_000]}}
        self.assertFalse(HOOK.detect_context_regression(state, None))
        self.assertEqual(state["seen"], {"k1": [1, 500_000]})

    def test_no_entry_with_a_recorded_token_count_means_nothing_to_compare(self):
        """A session that has only ever repeated by call count leaves every
        seen entry's token slot empty; there is no high-water mark to fall
        below, so nothing is cleared."""
        state = {"calls": 2, "seen": {"k1": [1, None]}}
        self.assertFalse(HOOK.detect_context_regression(state, 10))
        self.assertEqual(state["seen"], {"k1": [1, None]})

    def test_empty_seen_means_nothing_to_compare(self):
        state = {"calls": 0, "seen": {}}
        self.assertFalse(HOOK.detect_context_regression(state, 10))

    def test_old_bare_integer_entry_format_is_coerced_before_comparing(self):
        """The pre-existing coercion helper reads the bare call-number format
        earlier versions wrote, where tokens were never recorded at all — so
        this entry alone still has nothing to compare against."""
        state = {"calls": 1, "seen": {"k1": 1}}
        self.assertFalse(HOOK.detect_context_regression(state, 10))


class CompactionRaceFallbackEndToEndTest(util.SandboxTestCase):
    """Acceptance case: the rule comes back on the very tool call where the
    token count collapses, even though --reset-session never ran. That is
    exactly the race the async SessionStart reset can lose."""

    PROJECT_SUBDIRS = ("src",)

    def test_compaction_drop_reinjects_without_waiting_for_the_async_reset(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: 500k"])
        touch_with = self.inject_with_transcript
        self.assertIsNotNone(touch_with(200_000), "first touch injects")
        self.assertIsNone(touch_with(210_000), "still well within the 500k distance")
        text = touch_with(5_000)  # simulated compaction: context collapsed
        self.assertIsNotNone(text, "the regression fallback reinjects immediately")
        self.assertIn("Rule text.", text)


if __name__ == "__main__":
    unittest.main()
