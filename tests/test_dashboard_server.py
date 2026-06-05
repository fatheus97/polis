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

    def test_retry_400_non_escalated_and_empty_guidance(self):
        from polis.models import Stage
        from polis.record import Record
        rec = Record(self.base / "record.jsonl")
        rec.append(run_id="r-done", stage=Stage.INTAKE, actor="procedure", kind="intake",
                   feedback_id="fb", text="x")  # no escalate -> not retryable
        self.assertEqual(self.client.post("/api/runs/r-done/retry",
                                          json={"guidance": "go"}).status_code, 400)
        rec.append(run_id="r-esc", stage=Stage.INTAKE, actor="procedure", kind="intake",
                   feedback_id="fb2", text="x")
        rec.append(run_id="r-esc", stage=Stage.ESCALATE, actor="procedure", kind="escalate",
                   reason="revisions_exhausted")
        self.assertEqual(self.client.post("/api/runs/r-esc/retry",
                                          json={"guidance": "   "}).status_code, 400)  # empty guidance

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

    def test_court_checkbox_uses_full_constitutional_court_label(self):
        html = self.client.get("/").text
        self.assertIn("Constitutional Court", html)
        self.assertIn('id="runCourt"', html)
        self.assertNotIn("> court</label>", html)

    def test_testing_mode_embeds_feedback_widget_script(self):
        from polis.projectcfg import write_config
        self.assertNotIn("feedback-widget.js", self.client.get("/").text)
        write_config(self.base, {"testing_mode": True})
        html = self.client.get("/").text
        self.assertIn('<script src="/static/feedback-widget.js"></script>', html)

    def test_supervise_relaunches_on_restart_code_then_stops(self):
        # The supervisor relaunches the child while it exits with _RESTART_EXIT_CODE (a
        # self-dev merge), and stops + propagates any other exit code.
        # _supervise itself uses no FastAPI, but it lives in server.py (can't import without
        # the extra), so this test is gated on HAVE_FASTAPI like the rest of the class.
        import types as _t

        import polis.dashboard.server as srv
        calls = {"n": 0}

        def fake_run(argv, env=None):
            calls["n"] += 1
            self.assertEqual(env.get("POLIS_DASH_SUPERVISED"), "1")   # child is marked supervised
            self.assertIn("--no-browser", argv)                       # child never opens a browser
            code = srv._RESTART_EXIT_CODE if calls["n"] < 3 else 0
            return _t.SimpleNamespace(returncode=code)

        orig = srv.subprocess.run
        srv.subprocess.run = fake_run
        try:
            code = srv._supervise(self.base, "127.0.0.1", 8799, open_browser=False)
        finally:
            srv.subprocess.run = orig
        self.assertEqual(code, 0)        # the final (non-restart) code is propagated
        self.assertEqual(calls["n"], 3)  # relaunched twice (97, 97), then exited on 0

    def test_text_routes_survive_non_utf8_bytes(self):
        # index/widget decode bytes -> str; a stray non-UTF-8 byte must degrade (errors=replace),
        # never 500.
        import polis.dashboard.server as srv
        tmp = Path(tempfile.mkdtemp(prefix="polis-enc-"))
        (tmp / "index.html").write_bytes(b"<html>\xff\xfe caf\xe9 <!-- FEEDBACK_WIDGET_PLACEHOLDER --></html>")
        (tmp / "feedback-widget.js").write_bytes(b"// caf\xe9\nconsole.log(1);")
        orig = srv._static_dir
        srv._static_dir = lambda: tmp
        try:
            client = TestClient(srv.create_app(Path(tempfile.mkdtemp(prefix="polis-encbase-"))))
        finally:
            srv._static_dir = orig
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/static/feedback-widget.js").status_code, 200)

    def test_static_js_served_as_javascript(self):
        # Windows' registry can map .js -> text/plain (browsers won't execute it); we force
        # application/javascript regardless.
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("application/javascript"))

    def test_styles_css_aligns_row_inputs(self):
        r = self.client.get("/static/styles.css")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/css"))
        css = r.text
        self.assertIn(".row > input", css)
        self.assertRegex(css, r"\.row\s*>\s*input[^{]*\{[^}]*flex:\s*1")

    def test_architects_label_is_spelled_out_in_full(self):
        html = self.client.get("/").text
        self.assertIn(">Architects", html)
        self.assertIn('id="runArchitects"', html)
        self.assertNotIn(">arch", html)

    def test_styles_css_equalizes_row_children_height(self):
        css = self.client.get("/static/styles.css").text
        self.assertIn(".row > input, .row > select, .row > button", css)
        self.assertRegex(css, r"\.row\s*>\s*input[^{]*button[^{]*\{[^}]*box-sizing:\s*border-box")
        self.assertRegex(css, r"\.row\s*>\s*input[^{]*button[^{]*\{[^}]*height:")

    def test_static_served_from_startup_snapshot_immune_to_disk_edits(self):
        # THE FIX: a self-dev run rewrites app.js/index.html in the working tree mid-run; the
        # running dashboard must keep serving the version captured at startup, not the
        # half-written file on disk (which froze the UI).
        import polis.dashboard.server as srv
        tmp = Path(tempfile.mkdtemp(prefix="polis-snap-"))
        (tmp / "app.js").write_text("console.log('v1');", encoding="utf-8")
        (tmp / "index.html").write_text(
            "<html>v1 <!-- FEEDBACK_WIDGET_PLACEHOLDER --></html>", encoding="utf-8")
        orig = srv._static_dir
        srv._static_dir = lambda: tmp
        try:
            client = TestClient(srv.create_app(Path(tempfile.mkdtemp(prefix="polis-snapbase-"))))
        finally:
            srv._static_dir = orig
        # Mutate (and corrupt) the files on disk AFTER startup:
        (tmp / "app.js").write_text("console.log('v2 BROKEN", encoding="utf-8")
        (tmp / "index.html").write_text("<html>v2</html>", encoding="utf-8")
        self.assertIn("v1", client.get("/static/app.js").text)   # snapshot, not the disk edit
        self.assertNotIn("v2", client.get("/static/app.js").text)
        self.assertIn("v1", client.get("/").text)                # index too
        self.assertNotIn("v2", client.get("/").text)


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
