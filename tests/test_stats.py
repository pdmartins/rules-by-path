"""Per-rule usage stats: written by the hook on every injection, kept across
sessions, bounded, and never in the way of a tool call."""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


class StatsTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api/handlers", "src/web")

    def stats_file(self):
        return os.path.join(util.state_dir(self.home), HOOK.STATS_FILE_NAME)

    def stats(self):
        with open(self.stats_file(), encoding="utf-8") as handle:
            return json.load(handle)

    def touch(self, relative, session="s1"):
        path = os.path.join(self.proj, relative)
        return util.run_hook(util.read_payload("Read", path, session=session,
                                               cwd=self.proj), self.home)

    def entry(self, name):
        key = f"{os.path.realpath(self.scope)}::{name}"
        return self.stats()["rules"][key]

    def test_an_injection_is_counted_with_its_directory_and_glob(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        proc = self.touch("src/api/handlers/a.py")
        self.assertIsNotNone(util.injected_text(proc))
        entry = self.entry("CONV_api.md")
        self.assertEqual(entry["injections"], 1)
        self.assertEqual(entry["reinjections"], 0)
        self.assertEqual(entry["sessions"], 1)
        self.assertEqual(entry["dirs"], {"src/api/handlers": 1})
        self.assertEqual(entry["globs"], {"src/**": 1})
        self.assertIsInstance(entry["last"], int)

    def test_a_dedup_hit_is_not_an_injection_but_a_new_session_is(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        self.touch("src/api/handlers/a.py")
        self.touch("src/api/handlers/b.py")  # same session: deduplicated
        self.assertEqual(self.entry("CONV_api.md")["injections"], 1)
        self.touch("src/web/c.py", session="s2")
        entry = self.entry("CONV_api.md")
        self.assertEqual(entry["injections"], 2)
        self.assertEqual(entry["sessions"], 2)
        self.assertEqual(entry["dirs"], {"src/api/handlers": 1, "src/web": 1})

    def test_nothing_is_written_when_nothing_is_injected(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API")
        self.touch("src/web/c.py")
        self.assertFalse(os.path.exists(self.stats_file()))

    def test_the_stale_sweep_keeps_the_stats_file(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        self.touch("src/web/c.py")
        old = time.time() - HOOK.STATE_MAX_AGE_SECONDS - 10
        os.utime(self.stats_file(), (old, old))
        stale = util.write_state(self.home, "gone", "{}")
        os.utime(stale, (old, old))
        self.touch("src/web/c.py", session="fresh")  # call 1 runs the sweep
        self.assertTrue(os.path.exists(self.stats_file()))
        self.assertFalse(os.path.exists(stale))

    def test_a_corrupt_stats_file_is_replaced_and_the_call_still_injects(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        util.write_file(self.stats_file(), "{not json")
        proc = self.touch("src/web/c.py")
        self.assertEqual(proc.returncode, 0)
        self.assertIsNotNone(util.injected_text(proc))
        self.assertEqual(self.entry("CONV_api.md")["injections"], 1)

    def test_a_symlinked_stats_path_is_refused_without_breaking_the_call(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        target = os.path.join(self.tmp.name, "elsewhere.json")
        util.write_file(target, "{}")
        os.makedirs(util.state_dir(self.home), exist_ok=True)
        os.symlink(target, self.stats_file())
        proc = self.touch("src/web/c.py")
        self.assertEqual(proc.returncode, 0)
        self.assertIsNotNone(util.injected_text(proc))
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{}")


class BoundedCountersTest(unittest.TestCase):
    def test_a_newcomer_past_the_cap_evicts_only_a_singleton(self):
        counter = {"a": 5, "b": 1}
        HOOK.stats.bump_bounded(counter, "c", 2)
        self.assertEqual(counter, {"a": 5, "c": 1})
        HOOK.stats.bump_bounded(counter, "c", 2)
        HOOK.stats.bump_bounded(counter, "d", 2)
        self.assertEqual(counter, {"a": 5, "c": 2}, "nothing rarer to evict")

    def test_matched_dir_is_relative_for_a_project_and_absolute_for_global(self):
        self.assertEqual(HOOK.matched_dir("/p/src/api/a.py", "/p"), "src/api")
        self.assertEqual(HOOK.matched_dir("/p/a.py", "/p"), ".")
        self.assertEqual(HOOK.matched_dir("/p/src/api/a.py", None), "/p/src/api")


if __name__ == "__main__":
    unittest.main()
