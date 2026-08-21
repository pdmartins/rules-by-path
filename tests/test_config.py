"""The configuration file: the three layers, what a project layer may not do,
and the fact that the hook actually runs on the result."""

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()
# The module object behind the facade, for the one test that has to make a
# sanitizer fail on purpose: `HOOK` re-exports functions by value, so patching
# it would not reach the reference `load_layer` actually calls. Taken out of
# `sys.modules` rather than imported, because the package is only importable
# once `load_hook_module` has put the hooks directory on the path.
CONFIG = sys.modules["rules_by_path.config"]


class ShippedDefaultTest(unittest.TestCase):
    def test_the_plugin_ships_a_usable_taxonomy(self):
        config = HOOK.load_config()
        self.assertEqual(HOOK.type_prefixes(config),
                         ("BUSN", "ARCH", "CONV", "OTHR"))
        for entry in HOOK.rule_types(config):
            self.assertTrue(entry["name"] and entry["purpose"], entry)

    def test_every_shipped_type_declares_its_own_repeat_distance(self):
        config = HOOK.load_config()
        for prefix in HOOK.type_prefixes(config):
            value = HOOK.remember_again_after_for_type(config, prefix)
            self.assertIsNotNone(value, prefix)
            self.assertIsNotNone(HOOK.parse_remember_again_after(value, prefix))

    def test_the_plugin_ships_english_as_the_language(self):
        """The shipped layer declares it explicitly rather than relying on the
        fallback, so `config` can name a file as its source."""
        config = HOOK.load_config()
        self.assertEqual(HOOK.language(config), HOOK.DEFAULT_LANGUAGE)
        self.assertEqual(config["sources"][HOOK.LANGUAGE_KEY],
                         HOOK.PLUGIN_CONFIG_PATH)
        self.assertTrue(HOOK.has_translation(HOOK.language(config)))

    def test_a_type_is_found_case_insensitively(self):
        config = HOOK.load_config()
        self.assertEqual(HOOK.find_rule_type(config, "busn")["prefix"], "BUSN")
        self.assertIsNone(HOOK.find_rule_type(config, "nope"))

    def test_only_the_prohibition_flavoured_type_reinforces_by_default(self):
        """Only prohibition-shaped constraints are known to decay under long
        context (arXiv:2604.20911); the other shipped types (convention,
        architecture, memory) default to never repeating on their own."""
        config = HOOK.load_config()
        self.assertEqual(HOOK.remember_again_after_for_type(config, "BUSN"), "20k")
        for prefix in ("ARCH", "CONV", "OTHR"):
            self.assertEqual(HOOK.remember_again_after_for_type(config, prefix),
                             "never", prefix)


class LayeringTest(util.SandboxTestCase):
    """`self.home` is the user's layer and `self.proj` the project's, nearest
    last — the order load_config merges them in."""

    def load(self, trusted_count=1):
        return HOOK.load_config([self.home, self.proj], trusted_count)

    def test_a_layer_overrides_only_what_it_declares(self):
        util.write_config(self.home, {"remember_again_after": {"tokens": "45k"}})
        config = self.load()
        self.assertEqual(HOOK.remember_again_after_default(config, True),
                         (45_000, "tokens"))
        self.assertEqual(HOOK.remember_again_after_default(config, False),
                         (HOOK.DEFAULT_REMEMBER_AGAIN_CALLS, "calls"),
                         "the untouched half still comes from the plugin")

    def test_the_nearest_layer_wins(self):
        util.write_config(self.home, {"remember_again_after": {"tokens": "45k"}})
        util.write_config(self.proj, {"remember_again_after": {"tokens": "60k"}})
        self.assertEqual(HOOK.remember_again_after_default(self.load(), True),
                         (60_000, "tokens"))

    def test_rule_types_are_replaced_whole_never_merged(self):
        """Merging two taxonomies by prefix would produce a hybrid nobody
        wrote — half the user's vocabulary, half the plugin's."""
        util.write_config(self.home, {"rule_types": [
            {"prefix": "AAA", "name": "Only one", "purpose": "The whole taxonomy"}]})
        self.assertEqual(HOOK.type_prefixes(self.load()), ("AAA",))

    def test_where_each_value_came_from_is_recorded(self):
        user_path = util.write_config(self.home,
                                      {"remember_again_after": {"tokens": "45k"}})
        sources = self.load()["sources"]
        self.assertEqual(sources["remember_again_after.tokens"], user_path)
        self.assertEqual(sources["rule_types"], HOOK.PLUGIN_CONFIG_PATH)


class UntrustedLayerTest(util.SandboxTestCase):
    """A project's config arrives with whatever repository is checked out."""

    def load(self, trusted_count=0):
        return HOOK.load_config([self.proj], trusted_count)

    def test_it_cannot_ask_for_a_repeat_on_nearly_every_call(self):
        util.write_config(self.proj, {"remember_again_after": {"calls": "1 calls"}})
        value, unit = HOOK.remember_again_after_default(self.load(), False)
        self.assertEqual(unit, "calls")
        self.assertEqual(value, HOOK.MIN_REMEMBER_AGAIN_CALLS)

    def test_the_call_floor_does_not_apply_to_the_users_own_config(self):
        util.write_config(self.proj, {"remember_again_after": {"calls": "1 calls"}})
        self.assertEqual(HOOK.remember_again_after_default(self.load(1), False),
                         (1, "calls"))

    def test_a_token_budget_below_the_floor_is_dropped_for_everyone(self):
        util.write_config(self.proj, {"remember_again_after": {"tokens": "500"}})
        self.assertEqual(HOOK.remember_again_after_default(self.load(), True),
                         (HOOK.DEFAULT_REMEMBER_AGAIN_TOKENS, "tokens"))

    def test_a_type_default_is_clamped_too(self):
        util.write_config(self.proj, {"rule_types": [
            {"prefix": "SPAM", "name": "Spam", "purpose": "Repeat me constantly",
             "remember_again_after": "1 calls"}]})
        self.assertEqual(HOOK.remember_again_after_for_type(self.load(), "SPAM"),
                         f"{HOOK.MIN_REMEMBER_AGAIN_CALLS} calls")

    def test_a_value_filed_under_the_wrong_unit_is_not_reinterpreted(self):
        """There is no faithful conversion between tokens and calls, so a calls
        value under `tokens` is dropped rather than honoured as tokens."""
        util.write_config(self.proj, {"remember_again_after": {"tokens": "25 calls"}})
        self.assertEqual(HOOK.remember_again_after_default(self.load(), True),
                         (HOOK.DEFAULT_REMEMBER_AGAIN_TOKENS, "tokens"))

    def test_a_type_text_that_is_not_one_printable_line_is_dropped(self):
        util.write_config(self.proj, {"rule_types": [
            {"prefix": "EVIL", "name": "x\ny", "purpose": "smuggled"},
            {"prefix": "GOOD", "name": "Fine", "purpose": "fine"}]})
        self.assertEqual(HOOK.type_prefixes(self.load()), ("GOOD",))

    def test_a_long_type_text_is_truncated_not_echoed_whole(self):
        util.write_config(self.proj, {"rule_types": [
            {"prefix": "LONG", "name": "N" * 5_000, "purpose": "ok"}]})
        entry = HOOK.find_rule_type(self.load(), "LONG")
        self.assertEqual(len(entry["name"]), HOOK.MAX_TYPE_TEXT_CHARS)

    def test_a_prefix_must_be_ascii_letters_and_digits(self):
        util.write_config(self.proj, {"rule_types": [
            {"prefix": "../x", "name": "Traversal", "purpose": "no"},
            {"prefix": "OK1", "name": "Fine", "purpose": "yes"}]})
        self.assertEqual(HOOK.type_prefixes(self.load()), ("OK1",))

    def test_more_types_than_the_cap_are_ignored(self):
        util.write_config(self.proj, {"rule_types": [
            {"prefix": f"T{i:02d}", "name": "N", "purpose": "P"}
            for i in range(HOOK.MAX_RULE_TYPES + 10)]})
        self.assertEqual(len(HOOK.rule_types(self.load())), HOOK.MAX_RULE_TYPES)

    def test_an_unknown_key_is_ignored(self):
        util.write_config(self.proj, {"nonsense": {"a": 1},
                                    "remember_again_after": {"tokens": "40k"}})
        config = self.load()
        self.assertNotIn("nonsense", config)
        self.assertEqual(HOOK.remember_again_after_default(config, True),
                         (40_000, "tokens"))


class RuleSizeTest(util.SandboxTestCase):
    """How long a rule may be is configuration too — a rule is resent whole at
    every repeat, so this is the knob that decides what a reminder costs."""

    PROJECT_SUBDIRS = ("src",)

    def load(self, trusted_count=1):
        return HOOK.load_config([self.global_scope, self.scope], trusted_count)

    def test_the_shipped_defaults_are_in_force_with_no_config(self):
        config = self.load()
        self.assertEqual(HOOK.max_rule_chars(config), HOOK.MAX_RULE_CHARS)
        self.assertEqual(HOOK.warn_rule_chars(config), HOOK.RULE_WARN_CHARS)

    def test_the_user_may_raise_the_limit_up_to_one_injection(self):
        util.write_config(self.global_scope,
                          {"rule_size": {"max_chars": 9_000, "warn_chars": 6_000}})
        config = self.load()
        self.assertEqual(HOOK.max_rule_chars(config), 9_000)
        self.assertEqual(HOOK.warn_rule_chars(config), 6_000)

    def test_a_project_may_shorten_a_rule_but_never_lengthen_one(self):
        """Raising the cut is the one direction that costs the reader: a cloned
        repository could ship a 20,000-character rule and have all of it
        repeated into the context of everyone who opens it."""
        util.write_config(self.scope, {"rule_size": {"max_chars": 20_000}})
        self.assertEqual(HOOK.max_rule_chars(self.load()), HOOK.MAX_RULE_CHARS)
        util.write_config(self.scope, {"rule_size": {"max_chars": 800}})
        self.assertEqual(HOOK.max_rule_chars(self.load()), 800)

    def test_absurd_values_are_clamped_into_range(self):
        util.write_config(self.global_scope, {"rule_size": {"max_chars": 5}})
        self.assertEqual(HOOK.max_rule_chars(self.load()),
                         HOOK.MIN_CONFIGURABLE_RULE_CHARS)
        util.write_config(self.global_scope, {"rule_size": {"max_chars": 10 ** 9}})
        self.assertEqual(HOOK.max_rule_chars(self.load()), HOOK.MAX_TOTAL_CHARS)

    def test_a_soft_limit_above_the_hard_cut_is_pulled_down(self):
        """A warning that only fires after the text is already gone is no
        warning at all."""
        util.write_config(self.global_scope,
                          {"rule_size": {"max_chars": 1_000, "warn_chars": 3_000}})
        self.assertEqual(HOOK.warn_rule_chars(self.load()), 1_000)

    def test_a_non_numeric_value_is_ignored(self):
        util.write_config(self.global_scope, {"rule_size": {"max_chars": "lots"}})
        self.assertEqual(HOOK.max_rule_chars(self.load()), HOOK.MAX_RULE_CHARS)

    def test_the_hook_cuts_the_body_at_the_configured_limit(self):
        util.write_config(self.global_scope, {"rule_size": {"max_chars": 300}})
        util.write_rule(self.proj, "OTHR_src.md", "src/**", "B" * 900)
        text = self.inject(session="size")
        self.assertIsNotNone(text)
        self.assertEqual(text.count("B"), 300)
        self.assertIn("truncated", text)


class UnreadableLayerTest(util.SandboxTestCase):
    """Nothing about a config may break injection: a layer that cannot be read
    is warned about and skipped, and the layer below it still applies."""

    def assert_falls_back(self):
        config = HOOK.load_config([self.proj], 0)
        self.assertEqual(HOOK.type_prefixes(config),
                         ("BUSN", "ARCH", "CONV", "OTHR"))

    def test_invalid_json(self):
        util.write_config(self.proj, "{not json at all")
        self.assert_falls_back()

    def test_a_json_document_that_is_not_an_object(self):
        util.write_config(self.proj, "[1, 2, 3]")
        self.assert_falls_back()

    def test_an_oversized_file(self):
        util.write_config(self.proj, json.dumps(
            {"note": "x" * (HOOK.MAX_CONFIG_BYTES + 100)}))
        self.assert_falls_back()

    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
    def test_a_symlink_is_not_followed(self):
        secret = os.path.join(self.tmp.name, "secret.json")
        with open(secret, "w", encoding="utf-8") as handle:
            json.dump({"rule_types": [{"prefix": "LEAK", "name": "L",
                                       "purpose": "P"}]}, handle)
        os.makedirs(self.proj, exist_ok=True)
        os.symlink(secret, os.path.join(self.proj, "config.json"))
        self.assert_falls_back()

    def test_a_directory_in_its_place(self):
        os.makedirs(os.path.join(self.proj, "config.json"))
        self.assert_falls_back()

    def test_no_config_anywhere(self):
        self.assert_falls_back()

    def test_a_number_json_accepts_and_int_cannot_hold(self):
        """`1e400` is strict, standard JSON; `json` reads it as float('inf');
        and `int()` of that raises OverflowError, which is not a ValueError.
        It must cost the key it is written under and nothing else."""
        util.write_config(self.proj, '{"rule_size": {"max_chars": 1e400}}')
        config = HOOK.load_config([self.proj], 0)
        self.assertEqual(HOOK.max_rule_chars(config), HOOK.MAX_RULE_CHARS)
        util.write_config(self.proj, '{"reinject_budget": 1e400}')
        self.assertEqual(HOOK.reinject_budget(HOOK.load_config([self.proj], 0)),
                         HOOK.MAX_REINJECTIONS_PER_RULE)

    def test_a_document_nested_deeper_than_the_json_decoder_recurses(self):
        """MAX_CONFIG_BYTES is room for sixteen thousand nested arrays, and the
        decoder answers those with RecursionError rather than ValueError."""
        depth = 16_000
        util.write_config(self.proj,
                          '{"a": ' + "[" * depth + "]" * depth + "}")
        self.assert_falls_back()

    def test_a_layer_no_sanitizer_can_survive_costs_only_that_layer(self):
        """The guard standing behind the per-key ones. Whatever future shape of
        input makes a sanitizer raise, `load_layer` answers with a warning and
        an empty layer — because the caller is also the code path that decides
        an `enforce: deny`, and an escaping exception cancels the denial."""
        def explode(*_args, **_kwargs):
            raise RuntimeError("boom")

        util.write_config(self.proj, {"rule_size": {"max_chars": 500}})
        original = getattr(CONFIG, "sanitize_config")
        setattr(CONFIG, "sanitize_config", explode)
        self.addCleanup(setattr, CONFIG, "sanitize_config", original)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            layer = HOOK.load_layer(HOOK.config_path_for(self.proj), False)
        self.assertEqual(layer, {})
        self.assertIn("boom", stderr.getvalue())


class HookUsesTheConfigTest(util.SandboxTestCase):
    """End to end: the file on disk changes what the hook does."""

    PROJECT_SUBDIRS = ("src",)

    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "OTHR_src.md", "src/**", "Rule text.")

    def test_the_global_config_sets_the_repeat_distance(self):
        util.write_config(self.global_scope, {"remember_again_after": {"calls": "2 calls"}})
        self.assertIsNotNone(self.inject(), "first touch injects")
        self.assertIsNone(self.inject(), "one call on: nothing")
        self.assertIsNotNone(self.inject(), "two calls on: sent again")

    def test_a_project_config_overrides_the_global_one(self):
        util.write_config(self.global_scope, {"remember_again_after": {"calls": "2 calls"}})
        util.write_config(self.scope, {"remember_again_after": {"calls": "50 calls"}})
        self.assertIsNotNone(self.inject(session="over"))
        for _ in range(6):
            self.assertIsNone(self.inject(session="over"))

    def test_the_environment_variable_beats_every_layer(self):
        util.write_config(self.global_scope, {"remember_again_after": {"calls": "50 calls"}})
        env = {"RULES_BY_PATH_REMEMBER_AGAIN_AFTER": "2 calls"}
        self.assertIsNotNone(self.inject(session="env", env=env))
        self.assertIsNone(self.inject(session="env", env=env))
        self.assertIsNotNone(self.inject(session="env", env=env))

    def test_a_config_file_never_triggers_an_injection_of_its_own(self):
        util.write_config(self.scope, {"remember_again_after": {}})
        proc = self.hook_for(os.path.join(util.RULES_DIR_RELPATH, "config.json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(util.injected_text(proc))


if __name__ == "__main__":
    unittest.main()
