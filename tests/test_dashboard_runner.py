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


if __name__ == "__main__":
    unittest.main()
