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

    def test_real_run_requires_confirm(self):
        r = self.client.post("/api/run", json={"real": True, "confirm": False})
        self.assertEqual(r.status_code, 400)

    def test_unknown_run_detail_404(self):
        self.assertEqual(self.client.get("/api/runs/nope").status_code, 404)


@unittest.skipUnless(HAVE_FASTAPI and HAVE_GIT, "needs the dashboard extra + git")
class DashboardRunFlowTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-srvflow-"))
        self.client = TestClient(create_app(self.base))

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


if __name__ == "__main__":
    unittest.main()
