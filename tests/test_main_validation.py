"""Regression tests from the file-by-file validation of the hook's orchestration
module: the paths a glob is matched against, the legacy notice as a block like
any other, and the SessionStart reset as a hook that must stay quiet."""

import ntpath
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

LEGACY_MAP = 'rules:\n  - glob: "src/**"\n'


class PathTargetsTest(unittest.TestCase):
    """`path_targets` runs on every tool call that reaches a scope, before
    anything is written to stdout: whatever it raises takes the entire
    injection with it, the user's own global rules included."""

    def test_a_resolved_path_on_another_volume_counts_as_outside(self):
        """On Windows `os.path.relpath` RAISES ValueError, rather than
        answering '..', when the two paths sit on different drives — which is
        what a junction pointing at another volume produces. Reproduced with
        ntpath so the case runs on any platform."""
        with mock.patch("os.path.relpath", ntpath.relpath), \
             mock.patch("os.path.realpath", lambda path: path):
            targets = HOOK.path_targets("C:/proj/src/a.py", "D:/vol/a.py", "C:/proj")
        self.assertEqual([target for _rel, target in targets], ["C:/proj/src/a.py"],
                         "an off-volume target is dropped like any other path "
                         "that resolves out of the project")


class LegacyNoticeTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src",)
    TOUCHED = "src/a.py"

    def write_legacy_map(self):
        util.write_file(os.path.join(self.scope, "rules-map.yml"), LEGACY_MAP)

    def test_the_notice_records_a_seen_entry_shaped_like_every_other(self):
        """Every other writer of `seen` stores three slots (call number, context
        tokens, reinjections spent). The notice stored two, and only survived
        the next read because `coerce_seen_entry` repairs short entries — a
        tolerance meant for state written by OLDER versions, not for state this
        version writes."""
        self.write_legacy_map()
        self.assertIsNotNone(self.inject(session="legacy"))
        seen = util.read_state(self.home, "legacy")["seen"]
        entries = [value for key, value in seen.items() if key.startswith("legacy::")]
        self.assertTrue(entries, "the notice must record that it was told")
        for entry in entries:
            self.assertEqual(len(entry), 3, f"malformed seen entry: {entry}")

    def test_the_notice_waits_for_the_next_call_when_the_budget_is_full(self):
        """The notice was appended after `build_blocks` had stopped counting, so
        it rode out on top of an injection that had already spent the whole
        character ceiling — the one block the budget could not hold back. Held
        back, it must be retried rather than counted as told."""
        self.write_legacy_map()
        rules = HOOK.MAX_TOTAL_CHARS // HOOK.MAX_RULE_CHARS
        for index in range(rules):
            util.write_rule(self.proj, f"big{index}.md", "src/**",
                            "B" * HOOK.MAX_RULE_CHARS)
        first = self.inject(session="budget")
        self.assertIn("B" * 100, first, "the rules themselves fill the budget")
        self.assertNotIn("migrate", first,
                         "the notice must not ride out over a full budget")
        self.assertIn("migrate", self.inject(session="budget"),
                      "a held-back notice is retried on the next tool call")


class ResetSessionTest(util.SandboxTestCase):
    def test_a_malformed_payload_is_not_reported_as_an_unexpected_error(self):
        """SessionStart hands the reset the same payload the notice reads, and
        the notice already tolerates an unreadable one. The reset did not, so a
        payload it could not parse came out as `unexpected error` on stderr —
        noise from a hook whose whole job is to be invisible."""
        proc = util.run_hook("{not json at all", self.home, args=("--reset-session",))
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("unexpected error", proc.stderr)


if __name__ == "__main__":
    unittest.main()
