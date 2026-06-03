"""RunManager tests. Write actions are stdlib-only; the end-to-end stub run is
git-gated (it builds a real Government + git workspace, like the integration tests)."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from polis.dashboard import data, reader
from polis.dashboard.runner import RunManager

HAVE_GIT = shutil.which("git") is not None


class WriteActionsTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-runner-"))
        self.rm = RunManager(self.base)

    def tearDown(self):
        self.rm.shutdown()

    def test_submit_feedback_appears_pending(self):
        out = self.rm.submit_feedback("do a thing", by="tester")
        self.assertIn("id", out)
        pend = reader.read_pending_feedback(self.base)
        self.assertEqual([p["text"] for p in pend], ["do a thing"])

    def test_appropriate_accumulates(self):
        self.assertEqual(self.rm.appropriate(100)["balance"], 100)
        self.assertEqual(self.rm.appropriate(50)["balance"], 150)

    def test_appropriate_rejects_nonpositive(self):
        with self.assertRaises(ValueError):
            self.rm.appropriate(0)


@unittest.skipUnless(HAVE_GIT, "git required for a real stub run")
class TriggerRunTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-runner-"))
        self.rm = RunManager(self.base)

    def tearDown(self):
        self.rm.shutdown()

    def _wait(self, job_id, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            j = self.rm.job(job_id)
            if j and j["status"] in ("done", "error"):
                return j
            time.sleep(0.2)
        return self.rm.job(job_id)

    def test_stub_run_is_nonblocking_and_completes(self):
        self.rm.appropriate(1000)
        self.rm.submit_feedback("add a feature", directives={"module": "dashx"})
        out = self.rm.trigger_run(real=False)
        self.assertEqual(out["status"], "queued")  # returned immediately, non-blocking

        j = self._wait(out["job_id"])
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(j["outcome"], "DONE")

        runs = data.run_list(reader.read_record_events(self.base / "record.jsonl"))
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[0]["in_flight"])


class ClerkFlowTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-clerk-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.rm = RunManager(self.base)
        from polis.llm import FakeLLM, LLMResponse
        ticket = ('{"title":"Fix save","description":"save errors","severity":"major",'
                  '"repro_steps":["click save"]}')
        self.rm._clerk_backend_factory = lambda: FakeLLM([LLMResponse(text=ticket, cost_usd=0.01)])

    def tearDown(self):
        self.rm.shutdown()

    def test_ticketizer_distills_then_creates_feedback(self):
        from polis.reports import ReportStore
        out = self.rm.intake_report(text="save is broken", state={"console": []})
        self.assertEqual(out["ticket_status"], "pending")  # non-blocking — deferred to the Clerk
        self.assertIsNone(out["feedback_id"])

        store = ReportStore(self.base)
        r = None
        deadline = time.time() + 20
        while time.time() < deadline:
            r = store.get(out["report_id"])
            if r and r["feedback_id"]:  # the LAST write in the job — avoids a status/feedback race
                break
            time.sleep(0.1)
        self.assertEqual(r["ticket_status"], "done", r)
        self.assertEqual(r["ticket"]["title"], "Fix save")
        self.assertIsNotNone(r["feedback_id"])
        pend = reader.read_pending_feedback(self.base)
        self.assertTrue(any("Fix save" in p["text"] for p in pend))

    def test_clerk_failure_still_files_feedback(self):
        # Simulate the claude CLI being unavailable on a fresh install: the backend blows
        # up before distill. The report must NOT be lost — it falls back to a bare feedback
        # item so it still reaches the architect.
        from polis.reports import ReportStore

        def boom():
            raise RuntimeError("claude CLI not found")
        self.rm._clerk_backend_factory = boom

        out = self.rm.intake_report(text="login is broken", state={"console": []})
        self.assertEqual(out["ticket_status"], "pending")

        store = ReportStore(self.base)
        r = None
        deadline = time.time() + 20
        while time.time() < deadline:
            r = store.get(out["report_id"])
            if r and r["feedback_id"]:  # the LAST write in the job — avoids a status/feedback race
                break
            time.sleep(0.1)
        self.assertEqual(r["ticket_status"], "error", r)
        self.assertIsNotNone(r["feedback_id"])  # report still reached the architect
        pend = reader.read_pending_feedback(self.base)
        self.assertTrue(any("login is broken" in p["text"] for p in pend))

    def test_auto_run_triggers_a_real_run(self):
        # with auto_run on, a distilled ticket must kick off a REAL run (not a stub).
        from polis import projectcfg
        projectcfg.write_config(self.base, {"auto_run": True, "ticketizer": True})
        calls = []
        self.rm.trigger_run = lambda **kw: (calls.append(kw), {"job_id": "j"})[1]

        self.rm.intake_report(text="auto please", state={"console": []})
        deadline = time.time() + 20
        while time.time() < deadline and not calls:
            time.sleep(0.05)
        self.assertTrue(calls, "auto_run did not trigger a run")
        self.assertIs(calls[0].get("real"), True)        # real, not a stub
        self.assertIsNotNone(calls[0].get("feedback_id"))  # targets the distilled ticket


if __name__ == "__main__":
    unittest.main()
