"""Clerk (ticketizer) tests. Stdlib only — driven by FakeLLM."""

import unittest

from polis.clerk import distill_report, ticket_to_text
from polis.llm import FakeLLM, LLMError, LLMResponse

TICKET_JSON = ('{"title":"Save fails","description":"clicking save errors",'
               '"repro_steps":["open settings","click save"],"expected":"saved",'
               '"actual":"500 error","severity":"major","affected_area":"settings",'
               '"relevant_state":{"console_errors":1}}')


class ClerkTest(unittest.TestCase):
    def test_distills_structured_ticket(self):
        report = {"text": "save broken", "url": "http://x/",
                  "state": {"console": [{"level": "error", "args": ["boom"]}]}}
        t = distill_report(FakeLLM([LLMResponse(text=TICKET_JSON, cost_usd=0.01)]), report)
        self.assertEqual(t["title"], "Save fails")
        self.assertEqual(t["severity"], "major")
        self.assertEqual(t["repro_steps"], ["open settings", "click save"])
        self.assertEqual(t["affected_area"], "settings")

    def test_normalizes_unknown_severity(self):
        t = distill_report(FakeLLM(['{"title":"x","severity":"SUPER-BAD"}']), {"text": "y"})
        self.assertEqual(t["severity"], "minor")

    def test_graceful_fallback_on_llm_error(self):
        t = distill_report(FakeLLM([LLMError("overloaded")]), {"text": "the thing is broken"})
        self.assertIn("the thing", t["description"])
        self.assertEqual(t["severity"], "minor")
        self.assertEqual(t["repro_steps"], [])

    def test_graceful_fallback_on_bad_json(self):
        t = distill_report(FakeLLM(["not json at all"]), {"text": "do the thing"})
        self.assertIn("do the thing", t["description"])

    def test_ticket_to_text(self):
        txt = ticket_to_text({"title": "T", "severity": "minor", "description": "d",
                              "repro_steps": ["a", "b"], "expected": "e", "actual": "f",
                              "affected_area": "ui"})
        self.assertIn("# T", txt)
        self.assertIn("Repro", txt)
        self.assertIn("**Expected:** e", txt)
        self.assertIn("**Area:** ui", txt)


if __name__ == "__main__":
    unittest.main()
