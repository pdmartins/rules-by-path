"""Regression tests for the admin CLI as an attack primitive: its writes,
deletes and reads must stay inside the root they were given, whatever a cloned
repository symlinks into place (review rounds 2 to 4)."""

import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


class ScopeContainmentTest(util.SandboxTestCase):
    """Round 3: containment validated everything INSIDE the scope but never the
    scope itself, so a symlinked `.claude` redirected reads, writes and deletes
    — a project-scoped add could land in the user's global rules."""

    PROJECT_SUBDIRS = (".claude",)

    def setUp(self):
        super().setUp()
        self.victim = os.path.join(self.tmp.name, "victim")
        os.makedirs(self.victim)
        with open(os.path.join(self.victim, "important.md"), "w", encoding="utf-8") as h:
            h.write("---\nglob: important/**\n---\nUSER'S OWN RULE")

    def link_scope(self):
        os.symlink(self.victim, self.scope)

    def link_dot_claude(self):
        shutil.rmtree(os.path.join(self.proj, ".claude"))
        holder = os.path.join(self.tmp.name, "holder")
        os.makedirs(holder)
        os.rename(self.victim, os.path.join(holder, "rules-by-path"))
        self.victim = os.path.join(holder, "rules-by-path")
        os.symlink(holder, os.path.join(self.proj, ".claude"))

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_add_cannot_write_outside_the_declared_root(self):
        self.link_scope()
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--rule", "evil.md", stdin="ATTACKER RULE")
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.victim, "evil.md")))

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_remove_cannot_delete_outside_the_declared_root(self):
        self.link_scope()
        proc = self.admin("remove", "--root", self.proj, "--rule", "important.md")
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.victim, "important.md")))

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_show_cannot_read_outside_the_declared_root(self):
        self.link_scope()
        proc = self.admin("show", "--root", self.proj, "--rule", "important.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("USER'S OWN RULE", proc.stdout)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_dot_claude_is_refused_too(self):
        self.link_dot_claude()
        proc = self.admin("remove", "--root", self.proj, "--rule", "important.md")
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.victim, "important.md")))

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_hook_does_not_inject_from_a_symlinked_scope(self):
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        os.makedirs(os.path.join(self.proj, "important"), exist_ok=True)
        self.link_scope()
        proc = util.run_hook(util.read_payload(
            "Read", os.path.join(self.proj, "important", "x.py")), self.home)
        self.assertNotIn("USER'S OWN RULE", proc.stdout)


class AdminSafetyTest(util.SandboxTestCase):
    """Round 2: the CLI's own writes and deletes becoming attack primitives."""

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_planted_tmp_symlink_cannot_redirect_a_write(self):
        """A predictable `<rule>.md.tmp` was a symlink target an attacker could
        plant in advance, turning an add into an arbitrary file overwrite."""
        victim = os.path.join(self.tmp.name, "bashrc")
        with open(victim, "w", encoding="utf-8") as handle:
            handle.write("ORIGINAL CONTENT")
        os.makedirs(self.scope, exist_ok=True)
        os.symlink(victim, os.path.join(self.scope, "src.md.tmp"))
        self.admin("add", "--root", self.proj, "--glob", "src/**", stdin="PWNED")
        with open(victim, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "ORIGINAL CONTENT")

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_destination_is_refused(self):
        victim = os.path.join(self.tmp.name, "victim.md")
        with open(victim, "w", encoding="utf-8") as handle:
            handle.write("KEEP")
        os.makedirs(self.scope, exist_ok=True)
        os.symlink(victim, os.path.join(self.scope, "src.md"))
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--force", stdin="PWNED")
        self.assertNotEqual(proc.returncode, 0)
        with open(victim, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "KEEP")

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_remove_refuses_to_delete_through_a_symlink(self):
        victim = os.path.join(self.tmp.name, "victim.md")
        with open(victim, "w", encoding="utf-8") as handle:
            handle.write("KEEP")
        os.makedirs(self.scope, exist_ok=True)
        os.symlink(victim, os.path.join(self.scope, "link.md"))
        proc = self.admin("remove", "--root", self.proj, "--rule", "link.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(os.path.isfile(victim))

    def test_show_does_not_truncate_a_long_rule(self):
        """show feeds the show -> edit -> update round trip; truncating here
        destroyed the tail of a long rule on the next update."""
        long_rule = "L" * (HOOK.MAX_RULE_CHARS + 5_000)
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--type", "OTHR", stdin=long_rule)
        proc = self.admin("show", "--root", self.proj, "--rule", "OTHR_src.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertGreaterEqual(len(proc.stdout), HOOK.MAX_RULE_CHARS + 5_000)
        self.assertNotIn("truncated", proc.stdout)

    def test_non_ascii_glob_survives_a_write(self):
        glob = "src/ação/**"
        proc = self.admin("add", "--root", self.proj, "--glob", glob,
                          "--rule", "OTHR_acao.md", stdin="RULE")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = util.read_payload("Read", os.path.join(self.proj, "src", "ação", "x.py"))
        self.assertIsNotNone(util.injected_text(util.run_hook(payload, self.home)))

    def test_glob_with_hash_and_quotes_survives_a_write(self):
        glob = 'src/c#/**'
        self.admin("add", "--root", self.proj, "--glob", glob,
                   "--rule", "OTHR_csharp.md", stdin="RULE")
        payload = util.read_payload("Read", os.path.join(self.proj, "src", "c#", "x.cs"))
        self.assertIsNotNone(util.injected_text(util.run_hook(payload, self.home)))

    def test_overlong_rule_name_fails_cleanly(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--type", "OTHR", "--rule", "x" * 300 + ".md", stdin="X")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)

    def test_remove_rejects_rule_and_glob_together(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**", stdin="X")
        proc = self.admin("remove", "--root", self.proj,
                          "--glob", "src/**", "--rule", "src.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not both", proc.stderr)

    def test_which_reports_a_miss_without_prescribing_a_fix(self):
        self.admin("add", "--root", self.proj, "--glob", "other/**", stdin="X")
        proc = self.admin("which", "--root", self.proj, "--path", "scripts/deploy")
        self.assertIn("no rule covers 'scripts/deploy'", proc.stdout)
        self.assertNotIn("add ", proc.stdout)


class FourthRoundTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src",)

    def write_legacy(self, rules_target=None):
        os.makedirs(self.scope, exist_ok=True)
        with open(os.path.join(self.scope, "rules-map.yml"), "w", encoding="utf-8") as h:
            h.write('rules:\n  - glob: "**"\n    rule: "CLAUDE.md"\n')
        if rules_target:
            os.symlink(rules_target, os.path.join(self.scope, "rules"))

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_migrate_refuses_a_symlinked_legacy_rules_directory(self):
        """migrate re-created the `rules/` level the rewrite removed from the
        hook, without the containment check that came with it — so a cloned
        repo could read AND delete the user's files."""
        victim_dir = os.path.join(self.tmp.name, "victim")
        os.makedirs(victim_dir)
        victim = os.path.join(victim_dir, "CLAUDE.md")
        with open(victim, "w", encoding="utf-8") as handle:
            handle.write("PRIVATE GLOBAL INSTRUCTIONS")
        self.write_legacy(rules_target=victim_dir)
        proc = self.admin("migrate", "--root", self.proj)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(os.path.isfile(victim), "the victim file was deleted")
        self.assertFalse(os.path.isfile(os.path.join(self.scope, "CLAUDE.md")),
                         "the victim file was stolen into the repo")

    def test_legacy_glob_containing_hash_survives_migration(self):
        os.makedirs(os.path.join(self.scope, "rules"), exist_ok=True)
        with open(os.path.join(self.scope, "rules-map.yml"), "w", encoding="utf-8") as h:
            h.write('rules:\n  - glob: "src/c#/**"\n    rule: "csharp.md"\n')
        with open(os.path.join(self.scope, "rules", "csharp.md"), "w", encoding="utf-8") as h:
            h.write("C# rule")
        self.admin("migrate", "--root", self.proj)
        with open(os.path.join(self.scope, "csharp.md"), encoding="utf-8") as handle:
            self.assertIn("glob: src/c#/**", handle.read())

    def test_show_update_round_trip_is_idempotent(self):
        """The skill documents show -> edit -> update; it used to nest the
        frontmatter inside the body and the rule stopped matching."""
        self.admin("add", "--root", self.proj, "--glob", "src/**", "--type", "OTHR",
                   "--remember-again-after", "10k", stdin="Validate the DTOs.")
        shown = self.admin("show", "--root", self.proj, "--rule", "OTHR_src.md").stdout
        self.admin("update", "--root", self.proj, "--rule", "OTHR_src.md", stdin=shown)
        again = self.admin("show", "--root", self.proj, "--rule", "OTHR_src.md").stdout
        self.assertEqual(shown, again)
        self.assertEqual(again.count("---\n"), 2, "exactly one frontmatter block")
        payload = util.read_payload("Read", os.path.join(self.proj, "src", "a.py"))
        self.assertIsNotNone(util.injected_text(util.run_hook(payload, self.home)))

    def test_a_body_that_starts_with_a_rule_of_its_own_is_not_eaten(self):
        body = "---\ntitle: not our frontmatter\n---\nThe actual rule."
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--rule", "OTHR_keep.md", stdin=body)
        shown = self.admin("show", "--root", self.proj, "--rule", "OTHR_keep.md").stdout
        self.assertIn("title: not our frontmatter", shown)

    def test_glob_with_a_newline_is_refused(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**\nreinforce: 1",
                          "--rule", "OTHR_x.md", stdin="RULE")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("invalid glob", proc.stderr)

    def test_corrupt_state_file_is_repaired(self):
        util.write_rule(self.proj, "src.md", "src/**", "RULE BODY")
        util.write_state(self.home, "broken", "{not json at all")
        self.assertIsNotNone(self.inject(session="broken"))
        self.assertIsNone(self.inject(session="broken"),
                          "the repaired state must dedup on the next call")

    def test_legacy_notice_is_told_once_per_session(self):
        self.write_legacy()
        payload = util.read_payload("Read", os.path.join(self.proj, "src", "a.py"),
                                    session="legacy")
        first = util.injected_text(util.run_hook(payload, self.home))
        self.assertIn("migrate", first)
        second = util.injected_text(util.run_hook(payload, self.home))
        self.assertIsNone(second, "the notice must not repeat on every tool call")


if __name__ == "__main__":
    unittest.main()
