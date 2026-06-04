"""FastAPI endpoint tests — gated on the optional dashboard extra (fastapi + httpx),
so the stdlib-only core CI self-skips them. Run after: pip install -e '.[dashboard]'
(plus httpx, which fastapi's TestClient needs).
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

try:
    import httpx  # noqa: F401  (TestClient needs it)
    import multipart  # noqa: F401  (Form/UploadFile need python-multipart)
    from fastapi.testclient import TestClient

    from polis.dashboard.server import create_app
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False

HAVE_GIT = shutil.which("git") is not None


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class DashboardServerTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-srv-"))
        self.client = TestClient(create_app(self.base))

    def test_index_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Polis", r.text)

    def test_overview_empty_base(self):
        j = self.client.get("/api/overview").json()
        self.assertEqual(j["total_runs"], 0)
        self.assertIn("treasury", j)

    def test_runs_empty(self):
        self.assertEqual(self.client.get("/api/runs").json()["runs"], [])

    def test_budget_action_increases_balance(self):
        r = self.client.post("/api/budget", json={"amount": 100})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["balance"], 100)

    def test_budget_rejects_nonpositive(self):
        self.assertEqual(self.client.post("/api/budget", json={"amount": 0}).status_code, 400)

    def test_feedback_action_then_listed(self):
        self.assertEqual(self.client.post("/api/feedback", json={"text": "do x"}).status_code, 200)
        pend = self.client.get("/api/feedback").json()["pending"]
        self.assertEqual([p["text"] for p in pend], ["do x"])

    def test_real_runs_config_default_on_and_toggles(self):
        # Real-vs-stub is a config switch (default on), not a UI/request field.
        self.assertTrue(self.client.get("/api/config").json()["real_runs"])
        self.client.post("/api/config", json={"real_runs": False})
        self.assertFalse(self.client.get("/api/config").json()["real_runs"])

    def test_unknown_run_detail_404(self):
        self.assertEqual(self.client.get("/api/runs/nope").status_code, 404)

    def test_retry_unknown_run_404(self):
        r = self.client.post("/api/runs/nope/retry", json={"guidance": "do x"})
        self.assertEqual(r.status_code, 404)

    def test_config_get_default(self):
        c = self.client.get("/api/config").json()
        self.assertTrue(c["managed_default"])
        self.assertEqual(c["main_branch"], "main")

    def test_config_set_workspace(self):
        target = tempfile.mkdtemp(prefix="polis-target-")
        c = self.client.post("/api/config", json={"workspace": target}).json()
        self.assertFalse(c["managed_default"])
        self.assertIn(Path(target).name, c["workspace"])

    def test_report_intake_creates_report_and_feedback(self):
        self.client.post("/api/config", json={"ticketizer": False})  # immediate feedback (no Clerk)
        r = self.client.post("/api/report-intake",
                             data={"text": "save button broken", "state": '{"url":"http://x/"}'})
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertTrue(j["report_id"] and j["feedback_id"])
        reports = self.client.get("/api/reports").json()["reports"]
        self.assertEqual([rp["text"] for rp in reports], ["save button broken"])
        pend = self.client.get("/api/feedback").json()["pending"]
        self.assertTrue(any(p["directives"].get("report_id") == j["report_id"] for p in pend))

    def test_report_intake_with_screenshot_roundtrips(self):
        self.client.post("/api/config", json={"ticketizer": False})  # no Clerk LLM call
        png = b"\x89PNG\r\n\x1a\n" + b"imgbytes"
        r = self.client.post("/api/report-intake", data={"text": "x"},
                             files={"screenshot": ("s.png", png, "image/png")})
        rid = r.json()["report_id"]
        shot = self.client.get(f"/api/reports/{rid}/screenshot")
        self.assertEqual(shot.status_code, 200)
        self.assertEqual(shot.content, png)

    def test_report_intake_rejects_oversized_screenshot(self):
        big = b"x" * (4 * 1024 * 1024 + 1)
        r = self.client.post("/api/report-intake", data={"text": "x"},
                             files={"screenshot": ("s.png", big, "image/png")})
        self.assertEqual(r.status_code, 413)

    def test_config_testing_mode_toggle(self):
        self.assertFalse(self.client.get("/api/config").json()["testing_mode"])
        self.client.post("/api/config", json={"testing_mode": True})
        self.assertTrue(self.client.get("/api/config").json()["testing_mode"])

    def test_unknown_report_404(self):
        self.assertEqual(self.client.get("/api/reports/nope").status_code, 404)

    def test_widget_js_templated_with_intake_url(self):
        from polis.projectcfg import write_config
        write_config(self.base, {"intake_url": "http://host:8765/api/report-intake"})
        js = self.client.get("/static/feedback-widget.js")
        self.assertEqual(js.status_code, 200)
        self.assertIn("__POLIS_INTAKE_URL", js.text)
        self.assertIn("http://host:8765/api/report-intake", js.text)

    def test_cors_preflight_on_intake(self):
        r = self.client.options("/api/report-intake",
                                headers={"Origin": "http://app.example",
                                         "Access-Control-Request-Method": "POST"})
        self.assertIn(r.status_code, (200, 204))
        self.assertIn("access-control-allow-origin", {k.lower() for k in r.headers})

    def test_cors_not_applied_to_action_endpoints(self):
        # CORS is scoped to the intake endpoint only — action endpoints get no allow-origin.
        r = self.client.options("/api/budget",
                                headers={"Origin": "http://evil.example",
                                         "Access-Control-Request-Method": "POST"})
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in r.headers})

    def test_events_capped_even_with_since_ts_zero(self):
        # Regression: since_ts=0 must NOT bypass the tail cap and dump the whole record.
        from polis.models import Stage
        from polis.record import Record
        rec = Record(self.base / "record.jsonl")
        for i in range(50):
            rec.append(run_id="r1", stage=Stage.INTAKE, actor="procedure", kind="intake", i=i)
        evs = self.client.get("/api/events?since_ts=0&tail=10").json()["events"]
        self.assertLessEqual(len(evs), 10)

    def test_feedback_widget_included_when_testing_mode_on(self):
        # Feedback widget script should be in HTML when TESTING_MODE is on
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": True})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('<script src="/static/feedback-widget.js"></script>', r.text)

    def test_feedback_widget_not_included_when_testing_mode_off(self):
        # Feedback widget should not be in HTML when TESTING_MODE is off
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": False})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('<script src="/static/feedback-widget.js"></script>', r.text)

    def test_feedback_widget_placeholder_removed_from_html(self):
        # The placeholder comment should be completely removed from HTML
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('FEEDBACK_WIDGET_PLACEHOLDER', r.text)


@unittest.skipUnless(HAVE_FASTAPI and HAVE_GIT, "needs the dashboard extra + git")
class DashboardRunFlowTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-srvflow-"))
        self.client = TestClient(create_app(self.base))
        self.client.post("/api/config", json={"real_runs": False})  # free stub runs in tests

    def test_trigger_stub_run_is_nonblocking_and_appears(self):
        self.client.post("/api/budget", json={"amount": 1000})
        self.client.post("/api/feedback",
                         json={"text": "add a feature", "directives": {"module": "srvx"}})
        r = self.client.post("/api/run", json={"real": False})
        self.assertEqual(r.status_code, 200)
        job_id = r.json()["job_id"]  # returned immediately

        j = {}
        deadline = time.time() + 60
        while time.time() < deadline:
            j = self.client.get(f"/api/jobs/{job_id}").json()
            if j["status"] in ("done", "error"):
                break
            time.sleep(0.3)
        self.assertEqual(j["status"], "done", j)

        runs = self.client.get("/api/runs").json()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[0]["in_flight"])
        detail = self.client.get(f"/api/runs/{runs[0]['run_id']}").json()
        self.assertTrue(detail["timeline"])

    def test_run_targets_specific_feedback_id(self):
        # The "▶ Run this ticket" button posts a feedback_id; the run must process THAT item.
        self.client.post("/api/budget", json={"amount": 1000})
        self.client.post("/api/feedback", json={"text": "alpha task", "directives": {"module": "alphamod"}})
        self.client.post("/api/feedback", json={"text": "beta task", "directives": {"module": "betamod"}})
        pend = self.client.get("/api/feedback").json()["pending"]
        beta = next(p for p in pend if "beta" in p["text"])
        job_id = self.client.post("/api/run",
                                  json={"real": False, "feedback_id": beta["id"]}).json()["job_id"]
        j = {}
        deadline = time.time() + 60
        while time.time() < deadline:
            j = self.client.get(f"/api/jobs/{job_id}").json()
            if j["status"] in ("done", "error"):
                break
            time.sleep(0.3)
        self.assertEqual(j["status"], "done", j)
        runs = self.client.get("/api/runs").json()["runs"]
        self.assertEqual(len(runs), 1, runs)  # ONLY the targeted item ran (alpha untouched)
        self.assertIn("beta task", runs[0].get("feedback_text") or "")


if __name__ == "__main__":
    unittest.main()
