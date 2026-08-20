"""End-to-end tests for scripts/rules-by-path-admin.py (subprocess-level)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


class AdminTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(self.proj)
        self.scope = util.scope_dir(self.proj)

    def tearDown(self):
        self.tmp.cleanup()

    def admin(self, *args, stdin=""):
        return util.run_admin(list(args), self.home, stdin_text=stdin)

    def read(self, name):
        with open(os.path.join(self.scope, name), encoding="utf-8") as handle:
            return handle.read()

    def test_init_creates_the_scope(self):
        proc = self.admin("init", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isdir(self.scope))

    def test_init_global_uses_home(self):
        proc = self.admin("init", "--global")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isdir(util.scope_dir(self.home)))

    def test_add_writes_frontmatter_and_body(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/api/**",
                          "--type", "CONV", stdin="API RULE")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("CONV_src-api.md", proc.stdout)
        content = self.read("CONV_src-api.md")
        self.assertIn("glob: src/api/**", content)
        self.assertIn("API RULE", content)

    def test_add_with_several_globs(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--glob", "lib/**", "--rule", "OTHR_code.md", stdin="RULE")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read("OTHR_code.md")
        self.assertIn("  - src/**", content)
        self.assertIn("  - lib/**", content)

    def test_add_refuses_to_clobber_without_force(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--type", "OTHR", stdin="V1")
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--type", "OTHR", stdin="V2")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already exists", proc.stderr)
        self.assertIn("V1", self.read("OTHR_src.md"))
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--type", "OTHR", "--force", stdin="V2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("V2", self.read("OTHR_src.md"))

    def test_two_rules_for_the_same_glob_are_allowed(self):
        first = self.admin("add", "--root", self.proj, "--glob", "src/api/**",
                           "--rule", "BUSN_security.md", stdin="SECURITY")
        second = self.admin("add", "--root", self.proj, "--glob", "src/api/**",
                            "--rule", "CONV_naming.md", stdin="NAMING")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        listing = self.admin("list", "--root", self.proj).stdout
        self.assertIn("BUSN_security.md", listing)
        self.assertIn("CONV_naming.md", listing)

    def test_validate_notes_a_shared_glob(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--rule", "OTHR_a.md", stdin="A")
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--rule", "OTHR_b.md", stdin="B")
        proc = self.admin("validate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("share the glob", proc.stdout)

    def test_validate_notes_a_long_rule(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--type", "OTHR", stdin="L" * (HOOK.RULE_WARN_CHARS + 100))
        proc = self.admin("validate", "--root", self.proj)
        self.assertIn("constraints", proc.stdout)

    def test_add_warns_about_a_long_rule(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--type", "OTHR", stdin="L" * (HOOK.RULE_WARN_CHARS + 100))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("soft limit", proc.stderr)

    def test_validate_flags_a_rule_without_a_glob(self):
        util.write_rule(self.proj, "orphan.md", [], "NO GLOB")
        proc = self.admin("validate", "--root", self.proj)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no glob", proc.stderr)

    def test_every_glob_shape_derives_a_usable_name(self):
        """`add --glob` without `--rule` used to fail on `*.cs`, `src/**/*.py`
        and every other form with a wildcard in the middle — which is what the
        docs present as the normal path."""
        for glob, expected in (("*.cs", "OTHR_cs.md"),
                               ("src/**/*.py", "OTHR_src-py.md"),
                               ("docs/**/*.md", "OTHR_docs-md.md")):
            proc = self.admin("add", "--root", self.proj, "--glob", glob,
                              "--type", "OTHR", stdin="X")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(expected, proc.stdout)

    def test_unsafe_rule_name_refused(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--type", "OTHR", "--rule", "../evil.md", stdin="X")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("invalid rule name", proc.stderr)

    def test_empty_content_refused(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--type", "OTHR", stdin="  \n ")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("empty rule content", proc.stderr)

    def test_which_reports_matches_and_misses(self):
        self.admin("add", "--root", self.proj, "--glob", "src/api/**",
                   "--type", "OTHR", stdin="A")
        proc = self.admin("which", "--root", self.proj, "--path", "src/api/users.py")
        self.assertIn("match: rule OTHR_src-api.md", proc.stdout)
        os.makedirs(os.path.join(self.proj, "src", "api"), exist_ok=True)
        proc = self.admin("which", "--root", self.proj, "--path", "src/api")
        self.assertIn("match: rule OTHR_src-api.md", proc.stdout)
        proc = self.admin("which", "--root", self.proj, "--path", "docs/guide.md")
        self.assertIn("no rule covers", proc.stdout)

    def test_which_outside_root_fails(self):
        proc = self.admin("which", "--root", self.proj, "--path", "/etc/passwd")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("outside the root", proc.stderr)

    def test_show_and_update_round_trip(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--type", "OTHR", stdin="ORIGINAL")
        proc = self.admin("show", "--root", self.proj, "--rule", "OTHR_src.md")
        self.assertIn("ORIGINAL", proc.stdout)
        self.assertIn("glob: src/**", proc.stdout)
        proc = self.admin("update", "--root", self.proj, "--rule", "OTHR_src.md",
                          stdin="REPLACED")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read("OTHR_src.md")
        self.assertIn("REPLACED", content)
        self.assertIn("glob: src/**", content, "update keeps the globs")

    def test_update_unknown_rule_fails(self):
        proc = self.admin("update", "--root", self.proj, "--rule", "ghost.md", stdin="X")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no such rule", proc.stderr)

    def test_remove_by_name_and_by_glob(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--type", "OTHR", stdin="A")
        proc = self.admin("remove", "--root", self.proj, "--rule", "OTHR_src.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.isfile(os.path.join(self.scope, "OTHR_src.md")))
        self.admin("add", "--root", self.proj, "--glob", "lib/**",
                   "--type", "OTHR", stdin="B")
        proc = self.admin("remove", "--root", self.proj, "--glob", "lib/**")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.isfile(os.path.join(self.scope, "OTHR_lib.md")))

    def test_remove_by_ambiguous_glob_asks_for_a_name(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--rule", "OTHR_a.md", stdin="A")
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--rule", "OTHR_b.md", stdin="B")
        proc = self.admin("remove", "--root", self.proj, "--glob", "src/**")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("pick one with --rule", proc.stderr)

    def test_remove_unknown_fails(self):
        proc = self.admin("remove", "--root", self.proj, "--rule", "ghost.md")
        self.assertNotEqual(proc.returncode, 0)

    def test_validate_empty_scope_ok(self):
        proc = self.admin("validate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0)

    def test_add_then_hook_injects(self):
        """What the CLI writes must be exactly what the hook consumes."""
        self.admin("add", "--root", self.proj, "--glob", "src/api/**",
                   "--type", "OTHR", stdin="ROUNDTRIP RULE")
        proc = util.run_hook(util.read_payload(
            "Read", os.path.join(self.proj, "src", "api", "x.py")), self.home)
        self.assertIn("ROUNDTRIP RULE", util.injected_text(proc))

    def test_validate_flags_a_name_outside_the_type_convention(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--rule", "ARCH_handlers-inherit-base.md", stdin="A")
        proc = self.admin("validate", "--root", self.proj)
        self.assertNotIn("convention", proc.stdout)
        # A rule the CLI would refuse to create can still arrive by hand.
        util.write_rule(self.proj, "whatever.md", ["lib/**"], "B")
        proc = self.admin("validate", "--root", self.proj)
        self.assertIn("whatever.md", proc.stdout)
        self.assertIn("convention", proc.stdout)
        self.assertEqual(proc.returncode, 0, "a name is a note, never an error")

    def test_add_without_a_type_says_which_types_exist(self):
        """The type is a judgement about what violating the rule costs, and
        `add` is the only moment a human is there to make it."""
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**", stdin="X")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("a rule needs a type", proc.stderr)
        for prefix in ("BUSN", "ARCH", "CONV", "OTHR"):
            self.assertIn(prefix, proc.stderr)

    def test_type_is_normalised_and_may_not_contradict_the_name(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--type", "arch", "--rule", "handlers.md", stdin="X")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ARCH_handlers.md", proc.stdout)
        proc = self.admin("add", "--root", self.proj, "--glob", "lib/**",
                          "--type", "BUSN", "--rule", "ARCH_other.md", stdin="X")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("contradicts", proc.stderr)
        proc = self.admin("add", "--root", self.proj, "--glob", "lib/**",
                          "--type", "NOPE", "--rule", "x.md", stdin="X")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unknown rule type", proc.stderr)

    def test_remember_again_after_is_written_and_validated(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**", "--type", "OTHR",
                   "--remember-again-after", "never", stdin="RULE")
        self.assertIn("remember_again_after: never", self.read("OTHR_src.md"))
        self.admin("update", "--root", self.proj, "--rule", "OTHR_src.md",
                   "--remember-again-after", "30k", stdin="RULE")
        self.assertIn("remember_again_after: 30k", self.read("OTHR_src.md"))
        proc = self.admin("add", "--root", self.proj, "--glob", "lib/**",
                          "--rule", "OTHR_lib.md", "--remember-again-after", "soon",
                          stdin="X")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("remember_again_after not understood", proc.stderr)

    def test_a_type_default_is_written_into_the_rule(self):
        """The interval a type declares in config.json is materialised into the
        file, so the rule states its own schedule and the hook never has to know
        what a type is."""
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                   "--type", "BUSN", "--rule", "BUSN_invariant.md", stdin="RULE")
        self.assertIn("remember_again_after: 20k", self.read("BUSN_invariant.md"))
        # An explicit value still wins over the type's default.
        self.admin("add", "--root", self.proj, "--glob", "lib/**",
                   "--type", "BUSN", "--rule", "BUSN_other.md",
                   "--remember-again-after", "80k", stdin="RULE")
        self.assertIn("remember_again_after: 80k", self.read("BUSN_other.md"))

    def test_nonexistent_root_fails(self):
        proc = self.admin("list", "--root", os.path.join(self.tmp.name, "missing"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not exist", proc.stderr)


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(self.proj)
        self.scope = util.scope_dir(self.proj)

    def tearDown(self):
        self.tmp.cleanup()

    def admin(self, *args, stdin=""):
        return util.run_admin(list(args), self.home, stdin_text=stdin)

    def write_legacy(self, entries, bodies):
        os.makedirs(os.path.join(self.scope, "rules"), exist_ok=True)
        lines = ["rules:"]
        for glob, name in entries:
            lines.append(f'  - glob: "{glob}"')
            if name:
                lines.append(f'    rule: "{name}"')
        with open(os.path.join(self.scope, "rules-map.yml"), "w", encoding="utf-8") as h:
            h.write("\n".join(lines) + "\n")
        for name, body in bodies.items():
            with open(os.path.join(self.scope, "rules", name), "w", encoding="utf-8") as h:
                h.write(body)

    def test_migrate_converts_and_removes_the_legacy_files(self):
        self.write_legacy([("src/api/**", "src--api.md"), ("docs/**", None)],
                          {"src--api.md": "API RULE", "docs.md": "DOCS RULE"})
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        with open(os.path.join(self.scope, "src--api.md"), encoding="utf-8") as h:
            content = h.read()
        self.assertIn("glob: src/api/**", content)
        self.assertIn("API RULE", content)
        self.assertFalse(os.path.isfile(os.path.join(self.scope, "rules-map.yml")))
        self.assertFalse(os.path.isdir(os.path.join(self.scope, "rules")))

    def test_migrated_rules_actually_inject(self):
        self.write_legacy([("src/**", None)], {"src.md": "MIGRATED RULE"})
        self.admin("migrate", "--root", self.proj)
        proc = util.run_hook(util.read_payload(
            "Read", os.path.join(self.proj, "src", "a.py")), self.home)
        self.assertIn("MIGRATED RULE", util.injected_text(proc))

    def test_migrate_merges_globs_that_shared_a_rule_file(self):
        self.write_legacy([("src/**", "shared.md"), ("lib/**", "shared.md")],
                          {"shared.md": "SHARED"})
        self.admin("migrate", "--root", self.proj)
        with open(os.path.join(self.scope, "shared.md"), encoding="utf-8") as h:
            content = h.read()
        self.assertIn("  - src/**", content)
        self.assertIn("  - lib/**", content)

    def test_migrate_keeps_legacy_files_when_something_is_skipped(self):
        self.write_legacy([("src/**", None), ("gone/**", None)], {"src.md": "OK"})
        proc = self.admin("migrate", "--root", self.proj)
        self.assertIn("skipped", proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.scope, "rules-map.yml")),
                        "nothing is deleted while an entry is unresolved")

    def test_migrate_without_legacy_files_is_a_noop(self):
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("nothing to migrate", proc.stdout)


if __name__ == "__main__":
    unittest.main()


class SplitSuggestionTest(unittest.TestCase):
    """A rule hands its WHOLE text to every file its glob matches, so a rule
    carrying constraints that govern only part of that tree should be split."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.proj, "src", "Api", "Controllers"))
        open(os.path.join(self.proj, "src", "Api", "DependencyInjection.cs"), "w").close()

    def tearDown(self):
        self.tmp.cleanup()

    def admin(self, *args, stdin=""):
        return util.run_admin(list(args), self.home, stdin_text=stdin)

    def test_add_points_at_the_narrower_globs_it_could_use(self):
        body = ("Controllers follow pattern X.\n"
                "DependencyInjection.cs follows pattern Y.\n"
                "No file may exceed 300 lines.\n")
        proc = self.admin("add", "--root", self.proj, "--glob", "src/Api/**",
                          "--type", "ARCH", stdin=body)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = proc.stdout + proc.stderr
        self.assertIn("Controllers", output)
        self.assertIn("DependencyInjection.cs", output)
        self.assertIn("src/Api/Controllers/**", output)
        self.assertIn("src/Api/DependencyInjection.cs", output)

    def test_already_split_rules_say_nothing(self):
        self.admin("add", "--root", self.proj, "--glob", "src/Api/**",
                   "--rule", "CONV_api-size.md", stdin="No file may exceed 300 lines.\n")
        self.admin("add", "--root", self.proj, "--glob", "src/Api/Controllers/**",
                   "--type", "CONV", stdin="One endpoint per method.\n")
        self.admin("add", "--root", self.proj, "--glob", "src/Api/DependencyInjection.cs",
                   "--type", "ARCH", stdin="Register by assembly scanning.\n")
        proc = self.admin("validate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("narrower than it", proc.stdout)
        self.assertIn("validation ok: 3 rule(s)", proc.stdout)

    def test_a_glob_that_names_one_file_is_never_flagged(self):
        proc = self.admin("add", "--root", self.proj, "--type", "ARCH",
                          "--glob", "src/Api/DependencyInjection.cs",
                          stdin="DependencyInjection.cs registers by scanning.\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("narrower than it", proc.stdout + proc.stderr)

    def test_a_name_that_does_not_exist_on_disk_is_not_invented(self):
        proc = self.admin("add", "--root", self.proj, "--glob", "src/Api/**",
                          "--rule", "ARCH_prose.md",
                          stdin="Repositories must not be called from Handlers.\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("narrower than it", proc.stdout + proc.stderr)


class ConfigCommandTest(unittest.TestCase):
    """`config` is how the manage skill learns the rule types, so nothing else
    may carry a second copy of them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(self.proj)

    def tearDown(self):
        self.tmp.cleanup()

    def admin(self, *args, stdin=""):
        return util.run_admin(list(args), self.home, stdin_text=stdin)

    def write_config(self, scope_dir, payload):
        os.makedirs(scope_dir, exist_ok=True)
        with open(os.path.join(scope_dir, "config.json"), "w", encoding="utf-8") as h:
            json.dump(payload, h)

    def test_it_prints_the_shipped_types_and_defaults(self):
        proc = self.admin("config", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for prefix in ("BUSN", "ARCH", "CONV", "OTHR"):
            self.assertIn(prefix, proc.stdout)
        self.assertIn("remember_again_after", proc.stdout)

    def test_it_names_the_layer_each_value_came_from(self):
        self.write_config(util.scope_dir(self.home),
                          {"rule_types": [{"prefix": "MINE", "name": "Mine",
                                           "purpose": "Just one"}]})
        proc = self.admin("config", "--root", self.proj)
        self.assertIn("MINE", proc.stdout)
        self.assertIn(os.path.join(self.home, ".claude"), proc.stdout)
        self.assertIn("(absent)", proc.stdout, "the project has no config of its own")

    def test_a_configured_size_limit_is_what_add_and_validate_report(self):
        self.write_config(util.scope_dir(self.home),
                          {"rule_size": {"max_chars": 900, "warn_chars": 300}})
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--type", "OTHR", stdin="L" * 400)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("soft limit 300", proc.stderr)
        self.assertIn("hard truncation at 900", proc.stderr)
        proc = self.admin("validate", "--root", self.proj)
        self.assertIn("soft limit 300", proc.stdout)

    def test_it_prints_the_size_limits(self):
        proc = self.admin("config", "--root", self.proj)
        self.assertIn("rule size", proc.stdout)
        self.assertIn("max_chars", proc.stdout)

    def test_a_configured_taxonomy_is_what_add_enforces(self):
        self.write_config(util.scope_dir(self.home),
                          {"rule_types": [{"prefix": "MINE", "name": "Mine",
                                           "purpose": "Just one"}]})
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--type", "ARCH", stdin="X")
        self.assertNotEqual(proc.returncode, 0, "ARCH is no longer configured")
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--type", "MINE", stdin="X")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("MINE_src.md", proc.stdout)


class MigrateToTypedNamesTest(unittest.TestCase):
    """0.4.0 renamed the type prefixes and the frontmatter key; `migrate` is
    what carries an existing scope across."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.proj = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.home)
        os.makedirs(self.proj)
        self.scope = util.scope_dir(self.proj)

    def tearDown(self):
        self.tmp.cleanup()

    def admin(self, *args, stdin=""):
        return util.run_admin(list(args), self.home, stdin_text=stdin)

    def read(self, name):
        with open(os.path.join(self.scope, name), encoding="utf-8") as handle:
            return handle.read()

    def test_legacy_prefixes_are_renamed(self):
        util.write_rule(self.proj, "Business_order-not-cancellable.md",
                        "src/Domain/**", "An invoiced order cannot be cancelled.")
        util.write_rule(self.proj, "Convention_api-problemdetails.md",
                        "src/Api/**", "Errors return ProblemDetails.")
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(
            os.path.join(self.scope, "BUSN_order-not-cancellable.md")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.scope, "CONV_api-problemdetails.md")))
        self.assertFalse(os.path.isfile(
            os.path.join(self.scope, "Business_order-not-cancellable.md")))

    def test_the_renamed_frontmatter_key_is_rewritten(self):
        util.write_rule(self.proj, "OTHR_src.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_after: 40k"])
        self.admin("migrate", "--root", self.proj)
        content = self.read("OTHR_src.md")
        self.assertIn("remember_again_after: 40k", content)
        self.assertNotIn("\nremember_after:", content)

    def test_an_untyped_rule_is_reported_never_guessed(self):
        util.write_rule(self.proj, "hv-dotnet-stack.md", "**/*.cs", "Stack rules.")
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("hv-dotnet-stack.md", proc.stdout)
        self.assertIn("no type prefix", proc.stdout)
        self.assertTrue(os.path.isfile(
            os.path.join(self.scope, "hv-dotnet-stack.md")), "left alone")

    def test_migrating_twice_changes_nothing_the_second_time(self):
        util.write_rule(self.proj, "Business_x.md", "src/**", "Rule.",
                        extra_frontmatter=["remember_after: 40k"])
        self.admin("migrate", "--root", self.proj)
        before = self.read("BUSN_x.md")
        proc = self.admin("migrate", "--root", self.proj)
        self.assertIn("nothing to migrate", proc.stdout)
        self.assertEqual(before, self.read("BUSN_x.md"))

    def test_a_rename_does_not_clobber_an_existing_rule(self):
        util.write_rule(self.proj, "Business_x.md", "src/**", "OLD")
        util.write_rule(self.proj, "BUSN_x.md", "src/**", "CURRENT")
        proc = self.admin("migrate", "--root", self.proj)
        self.assertIn("CURRENT", self.read("BUSN_x.md"))
        self.assertIn("already exists", proc.stderr)

    def test_a_migrated_scope_still_injects(self):
        util.write_rule(self.proj, "Business_x.md", "src/**", "MIGRATED RULE",
                        extra_frontmatter=["remember_after: 40k"])
        self.admin("migrate", "--root", self.proj)
        proc = util.run_hook(util.read_payload(
            "Read", os.path.join(self.proj, "src", "a.py")), self.home)
        self.assertIn("MIGRATED RULE", util.injected_text(proc) or "")
