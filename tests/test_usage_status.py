"""`status` reads the hook's usage stats: a per-rule label, a "never injected"
note once stats exist, and a narrowing note when every injection sits under
one subfolder of the glob."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


class UsageStatusTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api/handlers", "src/web")

    def touch(self, relative, session):
        util.run_hook(util.read_payload("Read", os.path.join(self.proj, relative),
                                        session=session, cwd=self.proj), self.home)

    def status(self, *extra):
        proc = self.admin("status", "--root", self.proj, *extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_no_stats_yet_means_no_usage_notes(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        out = self.status()
        self.assertIn("usage stats: nothing recorded yet", out)
        self.assertNotIn("never injected", out)

    def test_usage_label_and_never_injected_note(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        util.write_rule(self.proj, "CONV_dead.md", "docs/**", "DOCS")
        self.touch("src/web/a.py", "s1")
        self.touch("src/web/a.py", "s2")
        out = self.status()
        self.assertIn("usage stats since", out)
        self.assertIn("CONV_api.md  <-  src/**  (3 chars; injected 2x in 2 session(s), last", out)
        self.assertIn("note: never injected since usage stats began", out)
        self.assertIn("CONV_dead.md", out.split("never injected")[1])
        report = json.loads(self.status("--json"))
        rules = {rule["name"]: rule for rule in report["scopes"][1]["rules"]}
        self.assertEqual(rules["CONV_api.md"]["usage"]["injections"], 2)
        self.assertEqual(rules["CONV_api.md"]["usage"]["dirs"], {"src/web": 2})
        self.assertNotIn("recent_sessions", rules["CONV_api.md"]["usage"])
        self.assertIsNone(rules["CONV_dead.md"]["usage"])

    def test_narrowing_note_when_every_injection_sits_under_one_subfolder(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        for index in range(5):
            self.touch("src/api/handlers/a.py", f"s{index}")
        out = self.status()
        self.assertIn("note: CONV_api.md: injected 5x, always under 'src/api/handlers/', "
                      "while its glob 'src/**' reaches wider", out)
        self.assertIn("--glob 'src/api/handlers/**'", out)

    def test_no_narrowing_note_when_injections_spread_across_the_glob(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        for index in range(5):
            self.touch("src/api/handlers/a.py", f"s{index}")
        self.touch("src/web/a.py", "other")
        self.assertNotIn("reaches wider", self.status())

    def test_no_narrowing_note_below_the_injection_threshold(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        for index in range(4):
            self.touch("src/api/handlers/a.py", f"s{index}")
        self.assertNotIn("reaches wider", self.status())


class NarrowingHelpersTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(util.PLUGIN_ROOT, "scripts"))
        from rules_by_path_admin import usage
        self.usage = usage

    def test_glob_base_stops_at_the_first_metacharacter(self):
        self.assertEqual(self.usage.glob_base_segments("src/api/**"), ["src", "api"])
        self.assertEqual(self.usage.glob_base_segments("**/docs/**"), [])
        self.assertEqual(self.usage.glob_base_segments("/opt/x/*.md"), ["opt", "x"])

    def test_common_prefix_of_recorded_directories(self):
        self.assertEqual(self.usage.common_prefix(["src/api/a", "src/api/b"]), ["src", "api"])
        self.assertEqual(self.usage.common_prefix(["src/api", "."]), [])
        self.assertEqual(self.usage.common_prefix([]), [])

    def test_absolute_globs_keep_their_leading_slash_in_the_suggestion(self):
        entry = {"injections": 9, "dirs": {"/opt/x/deep/er": 9}}
        note = self.usage.narrowing_note("OTHR_x.md", ["/opt/x/**"], entry)
        self.assertIn("--glob '/opt/x/deep/er/**'", note)

    def test_multi_glob_rules_are_never_asked_to_narrow(self):
        entry = {"injections": 9, "dirs": {"src/api/deep": 9}}
        self.assertIsNone(self.usage.narrowing_note("x.md", ["src/**", "lib/**"], entry))


if __name__ == "__main__":
    unittest.main()
