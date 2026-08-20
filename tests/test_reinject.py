"""The re-injection budget: how many times ANY rule may be sent again in one
session before it is silenced regardless of distance covered — the global
ceiling under the per-type reinforcement defaults (see rules_by_path.reinject
and the ARCH/CONV/OTHR defaults in config.json)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


def write_config(scope_dir, payload):
    os.makedirs(scope_dir, exist_ok=True)
    path = os.path.join(scope_dir, "config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


class CoerceSeenEntryTest(unittest.TestCase):
    """[call, tokens, reinjections] from whatever a previous version — or a
    hand-edited state file — left on disk."""

    def test_a_full_three_element_entry_passes_through(self):
        self.assertEqual(HOOK.coerce_seen_entry([4, 90_000, 2]), [4, 90_000, 2])

    def test_a_pre_budget_two_element_entry_gets_zero_reinjections(self):
        """An entry written before this feature existed has spent none of the
        budget it never knew about."""
        self.assertEqual(HOOK.coerce_seen_entry([4, 90_000]), [4, 90_000, 0])

    def test_the_bare_integer_format_gets_zero_reinjections_too(self):
        self.assertEqual(HOOK.coerce_seen_entry(3), [3, None, 0])

    def test_a_non_numeric_reinjections_slot_falls_back_to_zero(self):
        self.assertEqual(HOOK.coerce_seen_entry([4, 90_000, "many"]), [4, 90_000, 0])

    def test_an_unusable_entry_is_still_none(self):
        self.assertIsNone(HOOK.coerce_seen_entry(["nope", 1, 1]))
        self.assertIsNone(HOOK.coerce_seen_entry(True))
        self.assertIsNone(HOOK.coerce_seen_entry([]))


class ReinjectBudgetConfigTest(unittest.TestCase):
    """The `reinject_budget` config key: merge, and clamp to
    [0, MAX_CONFIGURABLE_REINJECT_BUDGET] the same way in every layer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(self.proj)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, trusted_count=1):
        return HOOK.load_config([self.home, self.proj], trusted_count)

    def test_the_shipped_default_is_in_force_with_no_config(self):
        self.assertEqual(HOOK.reinject_budget(HOOK.load_config()),
                         HOOK.MAX_REINJECTIONS_PER_RULE)

    def test_a_layer_may_set_it(self):
        write_config(self.home, {"reinject_budget": 7})
        self.assertEqual(HOOK.reinject_budget(self.load()), 7)

    def test_the_nearest_layer_wins(self):
        write_config(self.home, {"reinject_budget": 7})
        write_config(self.proj, {"reinject_budget": 1})
        self.assertEqual(HOOK.reinject_budget(self.load()), 1)

    def test_an_untrusted_layer_cannot_raise_it_past_the_ceiling(self):
        write_config(self.proj, {"reinject_budget": 999})
        self.assertEqual(HOOK.reinject_budget(self.load(trusted_count=0)),
                         HOOK.MAX_CONFIGURABLE_REINJECT_BUDGET)

    def test_the_users_own_layer_is_clamped_to_the_same_ceiling(self):
        """Unlike rule_size, there is no direction in which raising this
        number is safe to leave unclamped, even for a trusted layer."""
        write_config(self.home, {"reinject_budget": 999})
        self.assertEqual(HOOK.reinject_budget(self.load(trusted_count=1)),
                         HOOK.MAX_CONFIGURABLE_REINJECT_BUDGET)

    def test_zero_is_a_legitimate_value_not_a_falsy_no_op(self):
        write_config(self.proj, {"reinject_budget": 0})
        self.assertEqual(HOOK.reinject_budget(self.load(trusted_count=0)), 0)

    def test_a_negative_value_is_clamped_to_zero(self):
        write_config(self.proj, {"reinject_budget": -5})
        self.assertEqual(HOOK.reinject_budget(self.load(trusted_count=0)), 0)

    def test_a_non_numeric_value_is_ignored(self):
        write_config(self.proj, {"reinject_budget": "lots"})
        self.assertEqual(HOOK.reinject_budget(self.load(trusted_count=0)),
                         HOOK.MAX_REINJECTIONS_PER_RULE)


class ReinjectBudgetEndToEndTest(unittest.TestCase):
    """Acceptance case: a rule that would otherwise repeat on nearly every
    tool call stops once the budget is spent — the first injection is free,
    only the repeats that follow it are counted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.proj, "src"), exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, session="budget"):
        return util.injected_text(util.run_hook(util.read_payload(
            "Read", os.path.join(self.proj, "src", "a.py"), session=session),
            self.home))

    def test_repetition_stops_once_the_budget_is_spent(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: 1 calls"])
        self.assertIsNotNone(self.touch(), "call 1: first delivery, free")
        for n in range(HOOK.MAX_REINJECTIONS_PER_RULE):
            self.assertIsNotNone(self.touch(), f"reinjection {n + 1} of the budget")
        for _ in range(5):
            self.assertIsNone(self.touch(), "budget spent: no more repeats this session")

    def test_a_budget_of_zero_still_delivers_the_free_first_injection(self):
        write_config(util.scope_dir(self.home), {"reinject_budget": 0})
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: 1 calls"])
        self.assertIsNotNone(self.touch(session="zero"), "the first delivery is free")
        for _ in range(5):
            self.assertIsNone(self.touch(session="zero"),
                              "a budget of 0 allows no reinjection at all")

    def test_a_configured_budget_is_honoured(self):
        write_config(util.scope_dir(self.home), {"reinject_budget": 1})
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: 1 calls"])
        self.assertIsNotNone(self.touch(session="one"), "call 1: first delivery")
        self.assertIsNotNone(self.touch(session="one"), "the one budgeted reinjection")
        for _ in range(5):
            self.assertIsNone(self.touch(session="one"), "budget of 1 is now spent")


if __name__ == "__main__":
    unittest.main()
