"""Backend-level tests — notably that we never leak an ambient API key into the
claude subprocess (so usage always lands on the logged-in subscription)."""

import os
import unittest

from polis.llm import ClaudeCliBackend, extract_json, LLMError


class ChildEnvBillingTest(unittest.TestCase):
    def setUp(self):
        self._orig = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-company-key-DO-NOT-USE"

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._orig

    def test_api_key_scrubbed_by_default(self):
        env = ClaudeCliBackend()._child_env()
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)

    def test_other_env_preserved(self):
        # Scrubbing must not nuke the rest of the environment (PATH, etc.).
        self.assertIn("PATH", ClaudeCliBackend()._child_env())

    def test_opt_in_keeps_key(self):
        env = ClaudeCliBackend(use_subscription=False)._child_env()
        self.assertIn("ANTHROPIC_API_KEY", env)


class ExtractJsonTest(unittest.TestCase):
    def test_bare(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_embedded_in_prose(self):
        self.assertEqual(extract_json('Sure! {"a": 1} done.'), {"a": 1})

    def test_raises_when_absent(self):
        with self.assertRaises(LLMError):
            extract_json("no json here")


class TimeoutTest(unittest.TestCase):
    def test_timeout_fails_fast_and_honors_per_call_override(self):
        import subprocess
        from unittest.mock import patch
        b = ClaudeCliBackend(timeout=300, max_retries=3, retry_base=0)
        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired("claude", 5)) as m:
            with self.assertRaises(LLMError) as cm:
                b.complete("hi", timeout=5)          # per-call override
        self.assertEqual(m.call_count, 1)            # a timeout is NOT retried
        self.assertIn("timed out after 5s", str(cm.exception))  # used the per-call value


if __name__ == "__main__":
    unittest.main()
