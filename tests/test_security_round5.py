"""Regression tests for the fifth review round: the recurring 'a fix introduces
a variant of the bug it fixed' pattern, plus the portability and provenance gaps
the earlier rounds did not reach."""

import os
import shutil
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402


class FifthRoundTest(util.SandboxTestCase):
    """Round 5: findings from the fifth multi-agent review — the recurring
    'a fix introduces a variant of the bug it fixed' pattern, plus portability
    and provenance gaps the earlier rounds did not reach."""

    PROJECT_SUBDIRS = ("src",)

    # R1 — migrate's cleanup loop must never unlink a file outside the scope.
    def test_migrate_cannot_delete_a_file_outside_the_scope(self):
        victim = os.path.join(self.tmp.name, "precious.txt")
        with open(victim, "w", encoding="utf-8") as handle:
            handle.write("KEEP")
        os.makedirs(os.path.join(self.scope, "rules"), exist_ok=True)
        with open(os.path.join(self.scope, "rules-map.yml"), "w", encoding="utf-8") as handle:
            handle.write("rules:\n"
                         '  - glob: "**"\n    rule: "good.md"\n'
                         f'  - glob: "x/**"\n    rule: "{victim}"\n')
        with open(os.path.join(self.scope, "rules", "good.md"), "w", encoding="utf-8") as handle:
            handle.write("Good rule body")
        # --force is required to reach the cleanup loop; the malicious entry is
        # the one that gets skipped, which is exactly what nudges --force.
        self.admin("migrate", "--root", self.proj, "--force")
        self.assertTrue(os.path.isfile(victim),
                        "migrate deleted a file outside the scope via a hostile rule name")

    # R2 — an untrusted label cannot forge a `scope: global` header field.
    def test_a_project_dir_name_cannot_forge_a_trusted_scope(self):
        """Originally a header-forgery regression: a directory named
        `payments | scope: global` forged a scope claim in the emitted header.
        The header is gone, so the attack has nothing to write into — this now
        fixes that no path or scope is emitted at all."""
        os.makedirs(os.path.join(self.proj, ".git"), exist_ok=True)
        hostile = os.path.join(self.proj, "payments | scope: global")
        util.write_rule(hostile, "r.md", "**", "ATTACKER RULE")
        text = self.inject(rel="payments | scope: global/service.py")
        self.assertIsNotNone(text)
        self.assertIn("ATTACKER RULE", text)  # the block is emitted...
        self.assertNotIn("scope: global", text)  # ...but cannot claim the global scope

    # R3 — a scope full of pathological globs cannot stall every tool call.
    def test_matching_is_bounded_across_many_hostile_globs(self):
        util.write_rule(self.proj, "0-good.md", "src/**", "GOOD RULE")  # sorts first
        evil_glob = "/".join(["*a" * 15] * 8)  # ~247 chars, 8 segments
        for i in range(150):
            util.write_rule(self.proj, f"evil-{i:03d}.md", [evil_glob] * 16, "EVIL")
        deep = "/".join([("abcdefghij" * 12) for _ in range(20)])
        start = time.perf_counter()
        text = self.inject(rel=os.path.join("src", deep, "file.py"), session="dos")
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 8.0, "the match budget did not bound total work")
        self.assertIn("GOOD RULE", text, "an early rule must still inject (fail-open)")

    # R4 — a leading UTF-8 BOM must not make a valid rule invisible.
    def test_a_rule_with_a_utf8_bom_is_still_injected(self):
        os.makedirs(self.scope, exist_ok=True)
        with open(os.path.join(self.scope, "bom.md"), "w", encoding="utf-8-sig") as handle:
            handle.write("---\nglob: src/**\n---\nBOM RULE BODY")
        text = self.inject()
        self.assertIsNotNone(text)
        self.assertIn("BOM RULE BODY", text)

    # R5 — the admin's max frontmatter must fit the hook's read window.
    def test_sixteen_long_globs_stay_visible_to_the_hook(self):
        args = ["add", "--root", self.proj, "--rule", "OTHR_many.md"]
        for i in range(16):
            args += ["--glob", f"d{i:02d}/" + "z" * 240 + "/**"]
        proc = self.admin(*args, stdin="MANY RULE")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        target = os.path.join("d15", "z" * 240, "x.py")  # the 16th glob
        self.assertIn("MANY RULE", self.inject(rel=target) or "")

    # R6 — a show -> update round trip must preserve an unknown-but-kept key.
    def test_show_update_round_trip_preserves_an_extra_key(self):
        util.write_rule(self.proj, "owner.md", "src/**", "Validate the DTOs.",
                        extra_frontmatter=["owner: pedro"])
        shown = self.admin("show", "--root", self.proj, "--rule", "owner.md").stdout
        self.admin("update", "--root", self.proj, "--rule", "owner.md", stdin=shown)
        again = self.admin("show", "--root", self.proj, "--rule", "owner.md").stdout
        self.assertEqual(shown, again, "the round trip must be idempotent")
        self.assertIn("owner: pedro", again)
        self.assertEqual(again.count("---\n"), 2, "exactly one frontmatter block")
        text = self.inject()
        self.assertIsNotNone(text)
        self.assertNotIn("owner: pedro", text, "the extra key must not leak into the body")

    # R7 — the POSIX launcher must never block a tool call, even mis-installed.
    @unittest.skipIf(os.name == "nt", "POSIX shell launcher")
    def test_hook_launcher_exits_zero_when_the_script_is_missing(self):
        plugin = os.path.join(self.tmp.name, "plugin")
        os.makedirs(os.path.join(plugin, "bin"))
        os.makedirs(os.path.join(plugin, "hooks"))  # deliberately empty: no .py
        launcher = os.path.join(plugin, "bin", "rules-by-path-hook")
        shutil.copy(os.path.join(util.PLUGIN_ROOT, "bin", "rules-by-path-hook"), launcher)
        proc = subprocess.run(["/bin/sh", launcher], input="{}",
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    # R9 — a wrong-typed `calls` must repair, not re-inject on every call.
    def test_typed_corrupt_state_is_repaired(self):
        util.write_rule(self.proj, "src.md", "src/**", "RULE BODY")
        util.write_state(self.home, "typed", '{"calls": "garbage", "seen": {}}')
        self.assertIsNotNone(self.inject(session="typed"))
        self.assertIsNone(self.inject(session="typed"),
                          "the repaired state must dedup on the next call")

    # R10 — a wrong-typed `seen` value must not disable all injection.
    def test_bad_seen_value_does_not_disable_injection(self):
        util.write_rule(self.proj, "src.md", "src/**", "RULE BODY")
        util.write_state(self.home, "dark",
                         '{"calls": 30, "seen": {"whatever": "2026-08-16T10:00:00"}}')
        self.assertIsNotNone(self.inject(session="dark"),
                             "a bad seen value must not abort the whole injection")

    # R12 — the admin must refuse more globs than the hook will ever match.
    def test_admin_refuses_more_globs_than_the_hook_matches(self):
        args = ["add", "--root", self.proj, "--rule", "OTHR_toomany.md"]
        for i in range(17):
            args += ["--glob", f"d{i}/**"]
        proc = self.admin(*args, stdin="RULE")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("at most", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.scope, "OTHR_toomany.md")))

    # R13 — a --remember-again-after value cannot inject a frontmatter line.
    def test_remember_again_after_value_cannot_inject_a_frontmatter_line(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "docs/**",
                          "--rule", "OTHR_inj.md",
                          "--remember-again-after", "never\nglob: **", stdin="INJ")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("invalid remember_again_after", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.scope, "OTHR_inj.md")))

    # A1 — migrate must keep an unquoted '#' that is part of a glob.
    def test_migrate_keeps_an_unquoted_hash_in_a_glob(self):
        os.makedirs(os.path.join(self.scope, "rules"), exist_ok=True)
        with open(os.path.join(self.scope, "rules-map.yml"), "w", encoding="utf-8") as handle:
            handle.write("rules:\n  - glob: build/#tmp/**    # cache dir\n    rule: build.md\n")
        with open(os.path.join(self.scope, "rules", "build.md"), "w", encoding="utf-8") as handle:
            handle.write("Build rule")
        self.admin("migrate", "--root", self.proj)
        with open(os.path.join(self.scope, "build.md"), encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("glob: build/#tmp/**", content, "the '#' inside the glob was lost")
        self.assertNotIn("cache dir", content, "the real trailing comment was not stripped")

    # A2 — add/--force must not clobber a non-rule markdown file.
    def test_add_refuses_to_overwrite_a_non_rule_markdown_file(self):
        os.makedirs(self.scope, exist_ok=True)
        notes = os.path.join(self.scope, "notes.md")
        with open(notes, "w", encoding="utf-8") as handle:
            handle.write("# My personal notes\n\nnot a rule\n")
        proc = self.admin("add", "--root", self.proj, "--glob", "notes/**",
                          "--rule", "notes.md", "--force", stdin="RULE BODY")
        self.assertNotEqual(proc.returncode, 0)
        with open(notes, encoding="utf-8") as handle:
            self.assertIn("personal notes", handle.read())


if __name__ == "__main__":
    unittest.main()
