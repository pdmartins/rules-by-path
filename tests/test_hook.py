"""End-to-end tests for hooks/rules-by-path.py: what the hook injects, when it
repeats a rule, and what SessionStart announces. The pure parsing and matching
functions are covered in test_frontmatter.py."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


class HookEndToEndTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)
    TOUCHED = "src/api/users.py"

    def test_injects_matching_rule_once_per_session(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE CONTENT")
        proc, text = self.touch()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("API RULE CONTENT", text)
        self.assertNotIn("src/api/**", text, "no provenance is emitted")
        self.assertTrue(util.hook_output(proc).get("suppressOutput"))
        self.assertIsNone(self.touch()[1], "second touch must not re-inject")
        self.assertIsNotNone(self.touch(session="s2")[1], "a new session injects again")

    def test_reset_session_reinjects(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE CONTENT")
        self.assertIsNotNone(self.touch()[1])
        self.assertIsNone(self.touch()[1])
        util.run_hook({"session_id": "s1", "hook_event_name": "SessionStart"},
                      self.home, args=("--reset-session",))
        self.assertIsNotNone(self.touch()[1], "after reset the rule injects again")

    def test_one_rule_with_a_broken_setting_does_not_silence_the_others(self):
        """A `remember_again_after` the parser cannot read is a defect in ONE rule.
        It used to raise out of the per-rule loop, which happens before anything
        is written to stdout — so the whole injection was lost, including rules
        from other scopes, on every tool call that reached the bad file."""
        util.write_rule(self.proj, "a-broken.md", "src/api/**", "BROKEN RULE",
                        extra_frontmatter=["remember_again_after: inf"])
        util.write_rule(self.proj, "b-good.md", "src/api/**", "GOOD RULE",
                        extra_frontmatter=["remember_again_after: 1 calls"])
        self.assertIsNotNone(self.touch()[1], "first touch injects both")
        proc, text = self.touch()  # second touch: the good rule is due again
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("GOOD RULE", text or "")

    def test_non_matching_file_no_output(self):
        util.write_rule(self.proj, "src--api.md", "src/api/**", "API RULE")
        self.assertIsNone(self.touch(rel="src/other/users.py")[1])

    def test_rules_dir_files_never_trigger(self):
        util.write_rule(self.proj, "root.md", "**", "EVERYTHING")
        inside = os.path.join(util.RULES_DIR_RELPATH, "root.md")
        self.assertIsNone(self.touch(rel=inside)[1])

    def test_no_rules_anywhere_no_output(self):
        proc, text = self.touch()
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(text)

    def test_rule_without_a_glob_never_injects(self):
        util.write_rule(self.proj, "orphan.md", [], "NEVER")
        proc, text = self.touch()
        self.assertIsNone(text)

    def test_multiple_globs_on_one_rule(self):
        util.write_rule(self.proj, "many.md", ["lib/**", "src/api/**"], "MULTI RULE")
        self.assertIn("MULTI RULE", self.touch()[1])

    def test_multiple_rules_for_the_same_glob_all_inject(self):
        util.write_rule(self.proj, "security.md", "src/api/**", "SECURITY RULE")
        util.write_rule(self.proj, "naming.md", "src/api/**", "NAMING RULE")
        text = self.touch()[1]
        self.assertIn("SECURITY RULE", text)
        self.assertIn("NAMING RULE", text)
        self.assertEqual(text.count(f"\n{HOOK.RULE_SEPARATOR}\n"), 1,
                         "two rules, one separator between them")

    def test_global_scope(self):
        util.write_rule(self.home, "proj.md",
                        f"{self.proj}/**".replace(os.sep, "/"), "GLOBAL RULE")
        text = self.touch(rel="anything.txt")[1]
        self.assertIn("GLOBAL RULE", text)

    def test_nested_claude_md_write_is_no_longer_blocked(self):
        """The plugin used to deny creating a CLAUDE.md in a subfolder. That
        policy belongs to whoever writes the CLAUDE.md, not to this hook."""
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        proc = self.hook_for("src/CLAUDE.md", tool="Write")
        self.assertNotIn("permissionDecision", util.hook_specific_output(proc))

    def test_root_claude_md_write_allowed(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        self.assertIsNone(util.hook_output(self.hook_for("CLAUDE.md", tool="Write")))

    def test_nested_claude_md_read_not_denied(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        proc = self.hook_for("src/CLAUDE.md")
        self.assertNotIn("permissionDecision", util.hook_specific_output(proc))

    def test_nested_repo_claude_md_allowed(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        os.makedirs(os.path.join(self.proj, "vendor", "lib", ".git"), exist_ok=True)
        self.assertIsNone(util.hook_output(
            self.hook_for("vendor/lib/CLAUDE.md", tool="Write")))

    def test_oversized_rule_truncated(self):
        util.write_rule(self.proj, "big.md", "src/**", "X" * (HOOK.MAX_RULE_CHARS + 5_000))
        text = self.touch()[1]
        self.assertIn("truncated", text)
        self.assertLess(len(text), HOOK.MAX_RULE_CHARS + 2_000)

    def test_budget_defers_extra_rules(self):
        per_rule = HOOK.MAX_RULE_CHARS - 100
        count = (HOOK.MAX_TOTAL_CHARS // per_rule) + 2
        for index in range(count):
            util.write_rule(self.proj, f"r{index:02d}.md", "src/**",
                            f"RULE{index} " + "y" * per_rule)
        proc, text = self.touch(session="cap")
        self.assertIn("RULE0 ", text)
        self.assertIn("budget", proc.stderr)
        later = self.touch(session="cap")[1]
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
        util.write_file(os.path.join(self.scope, "rules-map.yml"),
                        'rules:\n  - glob: "src/**"\n')
        proc, text = self.touch()
        self.assertIsNotNone(text, "a legacy scope must not fail silently")
        self.assertIn("migrate", text)


class ReinforcementTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src",)

    def test_the_rule_is_sent_again_whole_after_the_configured_distance(self):
        body = "Always validate DTOs.\n\nMore detail, sent again with the rest."
        util.write_rule(self.proj, "src.md", "src/**", body)
        env = {"RULES_BY_PATH_REMEMBER_AGAIN_AFTER": "3 calls"}
        first = self.inject(env=env)
        self.assertIn("Always validate DTOs.", first)
        self.assertIsNone(self.inject(env=env), "call 2: nothing")
        self.assertIsNone(self.inject(env=env), "call 3: nothing")
        fourth = self.inject(env=env)
        self.assertIsNotNone(fourth, "call 4 is 3 calls after the injection")
        self.assertEqual(first, fourth,
                         "repeating means sending the same text again: with no "
                         "header there is no way to mark a fragment as one")

    def test_repetition_can_be_disabled(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.")
        # Deliberately the pre-0.4.0 spelling: an installation that exported it
        # must keep the behaviour it configured.
        env = {"RULES_BY_PATH_REMEMBER_AFTER": "never"}
        self.assertIsNotNone(self.inject(env=env))
        for _ in range(6):
            self.assertIsNone(self.inject(env=env))

    def test_per_rule_override_wins(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: never"])
        env = {"RULES_BY_PATH_REMEMBER_AGAIN_AFTER": "2 calls"}
        self.assertIsNotNone(self.inject(env=env))
        for _ in range(5):
            self.assertIsNone(self.inject(env=env))

    def test_a_rule_asking_for_tokens_still_repeats_when_only_calls_can_be_counted(self):
        """No transcript means no token count. Falling back to the default call
        distance keeps the rule alive; converting tokens to calls would be a
        made-up rate, and staying silent would lose the rule for the session."""
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: 30k"])
        self.assertIsNotNone(self.inject())
        for _ in range(HOOK.DEFAULT_REMEMBER_AGAIN_CALLS - 1):
            self.assertIsNone(self.inject())
        self.assertIsNotNone(self.inject(), "the default call distance applies")

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
        empty = util.write_file(os.path.join(self.tmp.name, "empty.jsonl"), "")
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
        touch_with = self.inject_with_transcript
        self.assertIsNotNone(touch_with(100_000), "first touch injects")
        self.assertIsNone(touch_with(120_000), "20k later: not yet")
        self.assertIsNotNone(touch_with(131_000), "31k later: sent again")

    def test_calls_and_tokens_can_be_mixed_in_one_session(self):
        """Each rule chooses its own unit, so the state records both measures."""
        util.write_rule(self.proj, "by-tokens.md", "src/**", "TOKEN RULE",
                        extra_frontmatter=["remember_again_after: 30k"])
        util.write_rule(self.proj, "by-calls.md", "src/**", "CALL RULE",
                        extra_frontmatter=["remember_again_after: 2 calls"])
        touch_with = self.inject_with_transcript
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
        self.assertIsNotNone(self.inject())
        for _ in range(5):
            self.hook_for("elsewhere.txt")
        self.assertIsNotNone(self.inject(), "touching a covered file again repeats it")

    def test_edited_rule_is_treated_as_a_new_rule(self):
        util.write_rule(self.proj, "src.md", "src/**", "VERSION ONE")
        env = {"RULES_BY_PATH_REMEMBER_AGAIN_AFTER": "50 calls"}
        self.assertIn("VERSION ONE", self.inject(env=env))
        self.assertIsNone(self.inject(env=env))
        util.write_rule(self.proj, "src.md", "src/**", "VERSION TWO body line")
        text = self.inject(env=env)
        self.assertIn("VERSION TWO", text,
                      "the content hash is part of the dedup key")


class SessionNoticeTest(util.SandboxTestCase):
    """SessionStart tells the agent, once, that the rules directory is managed
    by the plugin — so it never learns the same thing from a permission denial."""

    PROJECT_SUBDIRS = ("src",)

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


if __name__ == "__main__":
    unittest.main()
