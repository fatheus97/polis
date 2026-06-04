"""RunManager tests. Write actions are stdlib-only; the end-to-end stub run is
git-gated (it builds a real Government + git workspace, like the integration tests)."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

import types

from polis.dashboard import data, reader
from polis.dashboard.runner import RunManager, _is_self_dev

HAVE_GIT = shutil.which("git") is not None


class RestartHookTest(unittest.TestCase):
    """The dashboard auto-restart fires only when a run MERGES a change to our OWN checkout
    (self-dev) AND a supervisor hook is armed — never otherwise."""

    def setUp(self):
        import polis
        self.checkout = Path(polis.__file__).resolve().parent.parent  # .../<checkout>
        self.base = Path(tempfile.mkdtemp(prefix="polis-restart-"))
        self.rm = RunManager(self.base)

    def tearDown(self):
        self.rm.shutdown()

    def test_is_self_dev_true_only_for_our_checkout(self):
        from polis.projectcfg import write_config
        write_config(self.base, {"workspace": str(self.checkout)})
        self.assertTrue(_is_self_dev(self.base))
        write_config(self.base, {"workspace": tempfile.mkdtemp(prefix="polis-other-")})
        self.assertFalse(_is_self_dev(self.base))

    def test_should_restart_requires_hook_merged_and_self_dev(self):
        from polis.projectcfg import write_config
        write_config(self.base, {"workspace": str(self.checkout)})  # self-dev
        merged, not_merged = types.SimpleNamespace(merged=True), types.SimpleNamespace(merged=False)
        self.assertFalse(self.rm._should_restart(merged))           # no hook armed yet
        self.rm.set_restart_hook(lambda: None)
        self.assertTrue(self.rm._should_restart(merged))            # hook + merged + self-dev
        self.assertFalse(self.rm._should_restart(not_merged))       # didn't merge
        write_config(self.base, {"workspace": tempfile.mkdtemp(prefix="polis-other-")})
        self.assertFalse(self.rm._should_restart(merged))           # merged but not our checkout


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


class ScreenshotDirectiveTest(unittest.TestCase):
    """The tester's screenshot path rides on the feedback directives, so a grounded Architect can
    Read the image without the core ever touching ReportStore."""

    def setUp(self):
        from polis.projectcfg import write_config
        self.base = Path(tempfile.mkdtemp(prefix="polis-shotdir-"))
        write_config(self.base, {"ticketizer": False})  # immediate feedback, no Clerk LLM call
        self.rm = RunManager(self.base)

    def tearDown(self):
        self.rm.shutdown()

    def _directives_for(self, feedback_id):
        pend = reader.read_pending_feedback(self.base)
        return next(p for p in pend if p["id"] == feedback_id)["directives"]

    def test_intake_puts_absolute_screenshot_path_in_directives(self):
        out = self.rm.intake_report(text="headings too close to edge",
                                    screenshot_bytes=b"\x89PNG\r\n\x1a\nimg", screenshot_ext="png")
        shot = self._directives_for(out["feedback_id"]).get("screenshot_path")
        self.assertTrue(shot and Path(shot).is_absolute() and Path(shot).exists())

    def test_intake_without_screenshot_has_none_path(self):
        out = self.rm.intake_report(text="no shot here")
        self.assertIsNone(self._directives_for(out["feedback_id"]).get("screenshot_path"))

    def test_clerk_fallback_path_carries_screenshot_path(self):
        # Route through the async Clerk and force its backend to fail → the fallback path must
        # still file feedback AND carry the screenshot_path (the 3rd intake site).
        from polis.projectcfg import write_config
        write_config(self.base, {"ticketizer": True})

        def _boom():
            raise RuntimeError("clerk backend unavailable")
        self.rm._clerk_backend_factory = _boom
        out = self.rm.intake_report(text="x", screenshot_bytes=b"\x89PNG\r\nimg", screenshot_ext="png")
        rid = out["report_id"]
        fb, deadline = None, time.time() + 10
        while time.time() < deadline:
            fb = next((p for p in reader.read_pending_feedback(self.base)
                       if p["directives"].get("report_id") == rid), None)
            if fb:
                break
            time.sleep(0.1)
        self.assertIsNotNone(fb, "clerk fallback should still file feedback")
        self.assertEqual(fb["directives"]["source"], "feedback-widget:clerk-fallback")
        shot = fb["directives"].get("screenshot_path")
        self.assertTrue(shot and Path(shot).exists())


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

    def _capture_auto_run(self, **cfg):
        from polis import projectcfg
        projectcfg.write_config(self.base, {"auto_run": True, "ticketizer": True, **cfg})
        calls = []
        self.rm.trigger_run = lambda **kw: (calls.append(kw), {"job_id": "j"})[1]
        self.rm.intake_report(text="auto please", state={"console": []})
        deadline = time.time() + 20
        while time.time() < deadline and not calls:
            time.sleep(0.05)
        self.assertTrue(calls, "auto_run did not trigger a run")
        return calls[0]

    def test_auto_run_is_real_by_default(self):
        # auto_run uses real agents by default (real_runs defaults on) and targets the ticket.
        call = self._capture_auto_run()
        self.assertIs(call.get("real"), True)
        self.assertIsNotNone(call.get("feedback_id"))

    def test_auto_run_honors_real_runs_off(self):
        # real_runs=False makes even auto_run a free stub run.
        self.assertIs(self._capture_auto_run(real_runs=False).get("real"), False)


class RetryRunTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-retry-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.rm = RunManager(self.base)

    def tearDown(self):
        self.rm.shutdown()

    def _escalated(self, run_id="run-x", text="add a thing"):
        from polis.models import Stage
        from polis.record import Record
        rec = Record(self.base / "record.jsonl")
        rec.append(run_id=run_id, stage=Stage.INTAKE, actor="procedure", kind="intake",
                   feedback_id="fb-orig", text=text)
        rec.append(run_id=run_id, stage=Stage.ESCALATE, actor="procedure", kind="escalate",
                   reason="revisions_exhausted after 2 revisions")

    def test_retry_carries_guidance_and_reruns(self):
        self._escalated()
        calls = []
        self.rm.trigger_run = lambda **kw: (calls.append(kw), {"job_id": "j", "status": "queued"})[1]
        res = self.rm.retry_run("run-x", "make it server-side")
        self.assertTrue(res.get("feedback_id"))
        txt = reader.read_pending_feedback(self.base)[-1]["text"]
        self.assertIn("add a thing", txt)            # original ask carried forward
        self.assertIn("make it server-side", txt)    # the sovereign's guidance
        self.assertTrue(calls and calls[0]["feedback_id"] == res["feedback_id"])  # re-run fired

    def test_retry_rejects_non_escalated_and_empty(self):
        from polis.models import Stage
        from polis.record import Record
        Record(self.base / "record.jsonl").append(
            run_id="run-ok", stage=Stage.INTAKE, actor="procedure", kind="intake",
            feedback_id="fb", text="x")  # no escalate event
        self.assertEqual(self.rm.retry_run("nope", "go").get("error"), "run not found")
        self.assertIn("escalated", self.rm.retry_run("run-ok", "go").get("error", ""))
        self._escalated("run-e")
        self.assertIn("guidance", self.rm.retry_run("run-e", "   ").get("error", ""))


if __name__ == "__main__":
    unittest.main()
