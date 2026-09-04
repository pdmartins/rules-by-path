"""`digest`: harvest sources listed, the user's own turns from this project's
recent sessions distilled and paired with the rules injected in them."""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


def transcript_record(kind, content, session, timestamp="2026-09-01T10:00:00Z",
                      **extra):
    record = {"type": kind, "message": {"content": content}, "sessionId": session,
              "timestamp": timestamp, "gitBranch": "develop"}
    record.update(extra)
    return json.dumps(record)


class DigestTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)

    def transcripts_dir(self):
        slug = re.sub(r"[^A-Za-z0-9]", "-", self.proj)
        return os.path.join(self.home, ".claude", "projects", slug)

    def write_transcript(self, session, lines, mtime=None):
        path = os.path.join(self.transcripts_dir(), f"{session}.jsonl")
        util.write_file(path, "\n".join(lines) + "\n")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def digest(self, *extra):
        proc = self.admin("digest", "--root", self.proj, *extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_lists_harvest_sources_with_native_paths(self):
        util.write_file(os.path.join(self.proj, "CLAUDE.md"), "root\nnotes\n")
        util.write_file(os.path.join(self.proj, "src", "api", "CLAUDE.md"), "nested\n")
        util.write_file(os.path.join(self.home, ".claude", "rules", "py.md"),
                        '---\npaths:\n  - "**/*.py"\n---\nType hints always.\n')
        out = self.digest()
        self.assertIn("## harvest sources", out)
        self.assertIn(f"- {os.path.join(self.proj, 'CLAUDE.md')}  (2 lines)", out)
        self.assertIn(os.path.join("src", "api", "CLAUDE.md"), out)
        self.assertIn("py.md  (5 lines, paths: **/*.py)", out)
        self.assertIn("## recent sessions: none found in", out)

    def test_user_turns_are_distilled_and_harness_noise_dropped(self):
        self.write_transcript("abc12345", [
            transcript_record("user", "<command-name>/model</command-name>", "abc12345"),
            transcript_record("user", "always validate DTOs in src/api", "abc12345"),
            transcript_record("assistant", [{"type": "text", "text": "ok"}], "abc12345"),
            transcript_record("user", [{"type": "tool_result", "content": "x"}], "abc12345"),
            transcript_record("user", [{"type": "text", "text": "no, use ProblemDetails "
                                        "<system-reminder>hidden</system-reminder>"}],
                              "abc12345"),
            transcript_record("user", "meta", "abc12345", isMeta=True),
        ])
        out = self.digest()
        self.assertIn("### 2026-09-01  session abc12345  branch develop", out)
        self.assertIn("1. always validate DTOs in src/api", out)
        self.assertIn("2. no, use ProblemDetails", out)
        self.assertNotIn("hidden", out)
        self.assertNotIn("/model", out)
        self.assertNotIn("meta", out.split("branch develop")[1])
        self.assertIn("rules injected: none recorded", out)

    def test_sessions_are_paired_with_the_rules_usage_stats_remember(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        util.run_hook(util.read_payload("Read", os.path.join(self.proj, "src/api/a.py"),
                                        session="sess-1", cwd=self.proj), self.home)
        self.write_transcript("sess-1", [
            transcript_record("user", "please fix the handler", "sess-1")])
        out = self.digest()
        self.assertIn("rules injected (as far as usage stats remember): CONV_api.md", out)

    def test_sessions_limit_and_char_budget(self):
        for index in range(3):
            self.write_transcript(f"s{index}", [
                transcript_record("user", f"turn of session {index} " + "x" * 500, f"s{index}")],
                mtime=1_700_000_000 + index)
        out = self.digest("--sessions", "2")
        self.assertIn("(2 of 3, newest first)", out)
        self.assertIn("turn of session 2", out)
        self.assertIn("turn of session 1", out)
        self.assertNotIn("turn of session 0", out)
        self.assertIn("[…]", out, "a long turn is truncated")
        out = self.digest("--max-chars", "1000")
        self.assertIn("older sessions omitted", out)

    def test_digest_needs_a_project(self):
        proc = self.admin("digest", "--global")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("needs --root", proc.stderr)


if __name__ == "__main__":
    unittest.main()
