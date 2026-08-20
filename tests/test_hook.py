"""Unit and end-to-end tests for hooks/rules-by-path.py."""

import contextlib
import io
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

    def inject(self, rel="src/api/users.py", session="s1", tool="Read", env=None):
        proc = util.run_hook(util.read_payload(tool, self.target(rel), session=session),
                             self.home, env=env)
        return proc, util.injected_text(proc)

    def test_injects_matching_rule_once_per_session(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE CONTENT")
        proc, text = self.inject()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("API RULE CONTENT", text)
        self.assertNotIn("src/api/**", text, "no provenance is emitted")
        self.assertTrue(util.hook_output(proc).get("suppressOutput"))
        self.assertIsNone(self.inject()[1], "second touch must not re-inject")
        self.assertIsNotNone(self.inject(session="s2")[1], "a new session injects again")

    def test_reset_session_reinjects(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE CONTENT")
        self.assertIsNotNone(self.inject()[1])
        self.assertIsNone(self.inject()[1])
        util.run_hook({"session_id": "s1", "hook_event_name": "SessionStart"},
                      self.home, args=("--reset-session",))
        self.assertIsNotNone(self.inject()[1], "after reset the rule injects again")

    def test_one_rule_with_a_broken_setting_does_not_silence_the_others(self):
        """A `remember_again_after` the parser cannot read is a defect in ONE rule.
        It used to raise out of the per-rule loop, which happens before anything
        is written to stdout — so the whole injection was lost, including rules
        from other scopes, on every tool call that reached the bad file."""
        util.write_rule(self.proj, "a-broken.md", "src/api/**", "BROKEN RULE",
                        extra_frontmatter=["remember_again_after: inf"])
        util.write_rule(self.proj, "b-good.md", "src/api/**", "GOOD RULE",
                        extra_frontmatter=["remember_again_after: 1 calls"])
        self.assertIsNotNone(self.inject()[1], "first touch injects both")
        proc, text = self.inject()  # second touch: the good rule is due again
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("GOOD RULE", text or "")

    def test_non_matching_file_no_output(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE")
        self.assertIsNone(self.inject(rel="src/other/users.py")[1])

    def test_rules_dir_files_never_trigger(self):
        util.write_rule(self.proj, "root.md", "**", "EVERYTHING")
        inside = os.path.join(util.scope_dir(self.proj), "root.md")
        self.assertIsNone(util.injected_text(
            util.run_hook(util.read_payload("Read", inside), self.home)))

    def test_no_rules_anywhere_no_output(self):
        proc, text = self.inject()
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(text)

    def test_rule_without_a_glob_never_injects(self):
        util.write_rule(self.proj, "orphan.md", [], "NEVER")
        proc, text = self.inject()
        self.assertIsNone(text)

    def test_multiple_globs_on_one_rule(self):
        util.write_rule(self.proj, "many.md", ["lib/**", "src/api/**"], "MULTI RULE")
        self.assertIn("MULTI RULE", self.inject()[1])

    def test_multiple_rules_for_the_same_glob_all_inject(self):
        util.write_rule(self.proj, "security.md", "src/api/**", "SECURITY RULE")
        util.write_rule(self.proj, "naming.md", "src/api/**", "NAMING RULE")
        text = self.inject()[1]
        self.assertIn("SECURITY RULE", text)
        self.assertIn("NAMING RULE", text)
        self.assertEqual(text.count(f"\n{HOOK.RULE_SEPARATOR}\n"), 1,
                         "two rules, one separator between them")

    def test_global_scope(self):
        util.write_rule(self.home, "proj.md",
                        f"{self.proj}/**".replace(os.sep, "/"), "GLOBAL RULE")
        text = self.inject(rel="anything.txt")[1]
        self.assertIn("GLOBAL RULE", text)

    def test_nested_claude_md_write_is_no_longer_blocked(self):
        """The plugin used to deny creating a CLAUDE.md in a subfolder. That
        policy belongs to whoever writes the CLAUDE.md, not to this hook."""
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        out = util.hook_output(util.run_hook(
            util.read_payload("Write", self.target("src/CLAUDE.md")), self.home))
        self.assertTrue(out is None or "permissionDecision"
                        not in out.get("hookSpecificOutput", {}))

    def test_root_claude_md_write_allowed(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        self.assertIsNone(util.hook_output(util.run_hook(
            util.read_payload("Write", self.target("CLAUDE.md")), self.home)))

    def test_nested_claude_md_read_not_denied(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        out = util.hook_output(util.run_hook(
            util.read_payload("Read", self.target("src/CLAUDE.md")), self.home))
        self.assertTrue(out is None or "permissionDecision"
                        not in out.get("hookSpecificOutput", {}))

    def test_nested_repo_claude_md_allowed(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        os.makedirs(os.path.join(self.proj, "vendor", "lib", ".git"), exist_ok=True)
        self.assertIsNone(util.hook_output(util.run_hook(
            util.read_payload("Write", self.target("vendor/lib/CLAUDE.md")), self.home)))

    def test_oversized_rule_truncated(self):
        util.write_rule(self.proj, "big.md", "src/**", "X" * (HOOK.MAX_RULE_CHARS + 5_000))
        text = self.inject()[1]
        self.assertIn("truncated", text)
        self.assertLess(len(text), HOOK.MAX_RULE_CHARS + 2_000)

    def test_budget_defers_extra_rules(self):
        per_rule = HOOK.MAX_RULE_CHARS - 100
        count = (HOOK.MAX_TOTAL_CHARS // per_rule) + 2
        for index in range(count):
            util.write_rule(self.proj, f"r{index:02d}.md", "src/**",
                            f"RULE{index} " + "y" * per_rule)
        proc, text = self.inject(session="cap")
        self.assertIn("RULE0 ", text)
        self.assertIn("budget", proc.stderr)
        later = self.inject(session="cap")[1]
        self.assertIsNotNone(later, "deferred rules arrive on the next call")

    def test_malformed_stdin_never_fails(self):
        proc = util.run_hook("this is not json", self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("unexpected error", proc.stderr)

    def test_payload_without_file_path_no_output(self):
        proc = util.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"},
                              "session_id": "s", "cwd": self.proj}, self.home)
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(util.hook_output(proc))

    def test_relative_path_resolved_against_cwd(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE")
        proc = util.run_hook(util.read_payload(
            "Read", os.path.join("src", "api", "x.py"), cwd=self.proj), self.home)
        self.assertIsNotNone(util.injected_text(proc))

    def test_legacy_map_is_reported_not_silently_ignored(self):
        directory = util.scope_dir(self.proj)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "rules-map.yml"), "w", encoding="utf-8") as h:
            h.write('rules:\n  - glob: "src/**"\n')
        proc, text = self.inject()
        self.assertIsNotNone(text, "a legacy scope must not fail silently")
        self.assertIn("migrate", text)


class ReinforcementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.proj, "src"), exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, session="r", env=None):
        proc = util.run_hook(
            util.read_payload("Read", os.path.join(self.proj, "src", "a.py"),
                              session=session),
            self.home, env=env)
        return util.injected_text(proc)

    def test_the_rule_is_sent_again_whole_after_the_configured_distance(self):
        body = "Always validate DTOs.\n\nMore detail, sent again with the rest."
        util.write_rule(self.proj, "src.md", "src/**", body)
        env = {"RULES_BY_PATH_REMEMBER_AGAIN_AFTER": "3 calls"}
        first = self.touch(env=env)
        self.assertIn("Always validate DTOs.", first)
        self.assertIsNone(self.touch(env=env), "call 2: nothing")
        self.assertIsNone(self.touch(env=env), "call 3: nothing")
        fourth = self.touch(env=env)
        self.assertIsNotNone(fourth, "call 4 is 3 calls after the injection")
        self.assertEqual(first, fourth,
                         "repeating means sending the same text again: with no "
                         "header there is no way to mark a fragment as one")

    def test_repetition_can_be_disabled(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.")
        # Deliberately the pre-0.4.0 spelling: an installation that exported it
        # must keep the behaviour it configured.
        env = {"RULES_BY_PATH_REMEMBER_AFTER": "never"}
        self.assertIsNotNone(self.touch(env=env))
        for _ in range(6):
            self.assertIsNone(self.touch(env=env))

    def test_per_rule_override_wins(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: never"])
        env = {"RULES_BY_PATH_REMEMBER_AGAIN_AFTER": "2 calls"}
        self.assertIsNotNone(self.touch(env=env))
        for _ in range(5):
            self.assertIsNone(self.touch(env=env))

    def test_a_rule_asking_for_tokens_still_repeats_when_only_calls_can_be_counted(self):
        """No transcript means no token count. Falling back to the default call
        distance keeps the rule alive; converting tokens to calls would be a
        made-up rate, and staying silent would lose the rule for the session."""
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: 30k"])
        self.assertIsNotNone(self.touch())
        for _ in range(HOOK.DEFAULT_REMEMBER_AGAIN_CALLS - 1):
            self.assertIsNone(self.touch())
        self.assertIsNotNone(self.touch(), "the default call distance applies")

    def test_context_size_reads_the_last_usage_record(self):
        transcript = os.path.join(self.tmp.name, "session.jsonl")
        util.write_transcript(transcript, 10_000, 90_000)
        self.assertEqual(HOOK.context_size({"transcript_path": transcript}), 90_000,
                         "the LAST record is the current size")

    def test_context_size_degrades_instead_of_failing(self):
        self.assertIsNone(HOOK.context_size({}))
        self.assertIsNone(HOOK.context_size({"transcript_path": 42}))
        missing = os.path.join(self.tmp.name, "gone.jsonl")
        self.assertIsNone(HOOK.context_size({"transcript_path": missing}))
        empty = os.path.join(self.tmp.name, "empty.jsonl")
        open(empty, "w").close()
        self.assertIsNone(HOOK.context_size({"transcript_path": empty}))

    def test_context_size_reads_only_the_tail_of_a_huge_transcript(self):
        transcript = os.path.join(self.tmp.name, "big.jsonl")
        with open(transcript, "w", encoding="utf-8") as handle:
            handle.write(("{\"filler\": \"" + "x" * 500 + "\"}\n")
                         * (HOOK.TRANSCRIPT_TAIL_BYTES // 400))
            handle.write(json.dumps({
                "message": {"usage": {"input_tokens": 7,
                                      "cache_read_input_tokens": 120_000}}}) + "\n")
        self.assertGreater(os.path.getsize(transcript), HOOK.TRANSCRIPT_TAIL_BYTES)
        self.assertEqual(HOOK.context_size({"transcript_path": transcript}),
                         120_007)

    def test_the_rule_repeats_once_the_context_has_grown_by_the_token_distance(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: 30k"])
        transcript = os.path.join(self.tmp.name, "t.jsonl")

        def touch_with(total):
            util.write_transcript(transcript, total)
            return util.injected_text(util.run_hook(util.read_payload(
                "Read", os.path.join(self.proj, "src", "a.py"), session="tok",
                transcript_path=transcript), self.home))

        self.assertIsNotNone(touch_with(100_000), "first touch injects")
        self.assertIsNone(touch_with(120_000), "20k later: not yet")
        self.assertIsNotNone(touch_with(131_000), "31k later: sent again")

    def test_calls_and_tokens_can_be_mixed_in_one_session(self):
        """Each rule chooses its own unit, so the state records both measures."""
        util.write_rule(self.proj, "by-tokens.md", "src/**", "TOKEN RULE",
                        extra_frontmatter=["remember_again_after: 30k"])
        util.write_rule(self.proj, "by-calls.md", "src/**", "CALL RULE",
                        extra_frontmatter=["remember_again_after: 2 calls"])
        transcript = os.path.join(self.tmp.name, "mixed.jsonl")

        def touch_with(total):
            util.write_transcript(transcript, total)
            return util.injected_text(util.run_hook(util.read_payload(
                "Read", os.path.join(self.proj, "src", "a.py"), session="mix",
                transcript_path=transcript), self.home))

        first = touch_with(100_000)
        self.assertIn("TOKEN RULE", first)
        self.assertIn("CALL RULE", first)
        self.assertIsNone(touch_with(101_000), "one call on, no distance covered")
        third = touch_with(102_000)
        self.assertIn("CALL RULE", third, "two calls on")
        self.assertNotIn("TOKEN RULE", third, "only 2k of context on")

    def test_a_rule_is_never_repeated_for_a_file_nobody_touches_again(self):
        """Distance covered is necessary, not sufficient: the hook only ever
        asks the question when the rule's glob matched the file at hand."""
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: 1 calls"])
        self.assertIsNotNone(self.touch())
        for _ in range(5):
            util.run_hook(util.read_payload(
                "Read", os.path.join(self.proj, "elsewhere.txt"), session="r"),
                self.home)
        self.assertIsNotNone(self.touch(), "touching a covered file again repeats it")

    def test_edited_rule_is_treated_as_a_new_rule(self):
        util.write_rule(self.proj, "src.md", "src/**", "VERSION ONE")
        env = {"RULES_BY_PATH_REMEMBER_AGAIN_AFTER": "50 calls"}
        self.assertIn("VERSION ONE", self.touch(env=env))
        self.assertIsNone(self.touch(env=env))
        util.write_rule(self.proj, "src.md", "src/**", "VERSION TWO body line")
        text = self.touch(env=env)
        self.assertIn("VERSION TWO", text,
                      "the content hash is part of the dedup key")


if __name__ == "__main__":
    unittest.main()


class SessionNoticeTest(unittest.TestCase):
    """SessionStart tells the agent, once, that the rules directory is managed
    by the plugin — so it never learns the same thing from a permission denial."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.proj, "src"), exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def notice(self):
        payload = {"hook_event_name": "SessionStart", "source": "startup",
                   "cwd": self.proj, "session_id": "notice"}
        proc = util.run_hook(payload, self.home, args=("--session-notice",))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = util.hook_output(proc)
        if out is None:
            return None
        return out["hookSpecificOutput"]["additionalContext"]

    def test_silent_when_no_scope_exists_anywhere(self):
        self.assertIsNone(self.notice(), "a session with no rules must cost nothing")

    def test_announced_when_a_global_scope_exists(self):
        util.write_rule(self.home, "g.md", "**", "Global rule.")
        text = self.notice()
        self.assertIsNotNone(text)
        self.assertIn("rules-by-path", text)
        self.assertIn("list", text, "the notice must name the way in")

    def test_announced_when_only_a_project_scope_exists(self):
        util.write_rule(self.proj, "src.md", "src/**", "Project rule.")
        self.assertIsNotNone(self.notice())
