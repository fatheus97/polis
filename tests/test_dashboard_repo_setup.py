"""Tests for PRD prd-c14b37e3: Folder-selection UX overhaul.

Covers:
- /api/browse returns is_git flag
- /api/repo-check endpoint
- /api/repo-init endpoint (initialises + honest failure)
- Non-git folder rejection → git-init offer (served-asset assertions)
- Immediate apply on browse pick (no second click, no confirm)
- HTML structure tidy (repoMsg, repoForm hidden by default)
- TESTING_MODE widget injection

All tests are hermetic: no live OS dialog, no real network, no hanging subprocess.
Gated on the optional dashboard extra (fastapi + httpx); self-skips in stdlib-only CI.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

try:
    import httpx  # noqa: F401
    import multipart  # noqa: F401
    from fastapi.testclient import TestClient

    from polis.dashboard.server import create_app
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False

try:
    import tkinter  # noqa: F401
    HAVE_TKINTER = True
except ImportError:
    HAVE_TKINTER = False

HAVE_GIT = shutil.which("git") is not None


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class BrowseIsGitTest(unittest.TestCase):
    """POST /api/browse must report is_git alongside the chosen path."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-rsetup-browse-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    @unittest.skipUnless(HAVE_TKINTER, "tkinter not available")
    def test_browse_reports_is_git_true_for_git_dir(self):
        """browse returns is_git: True when the chosen folder contains .git."""
        chosen = Path(tempfile.mkdtemp(prefix="polis-gitdir-"))
        try:
            (chosen / ".git").mkdir()
            mock_root = mock.MagicMock()
            with mock.patch("tkinter.Tk", return_value=mock_root), \
                 mock.patch("tkinter.filedialog.askdirectory", return_value=str(chosen)):
                r = self.client.post("/api/browse")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["path"], str(chosen.resolve()))
            self.assertTrue(body["is_git"])
        finally:
            shutil.rmtree(chosen, ignore_errors=True)

    @unittest.skipUnless(HAVE_TKINTER, "tkinter not available")
    def test_browse_reports_is_git_false_for_plain_dir(self):
        """browse returns is_git: False when the chosen folder has no .git."""
        chosen = Path(tempfile.mkdtemp(prefix="polis-plain-"))
        try:
            mock_root = mock.MagicMock()
            with mock.patch("tkinter.Tk", return_value=mock_root), \
                 mock.patch("tkinter.filedialog.askdirectory", return_value=str(chosen)):
                r = self.client.post("/api/browse")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertFalse(body["is_git"])
        finally:
            shutil.rmtree(chosen, ignore_errors=True)


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class RepoCheckEndpointTest(unittest.TestCase):
    """GET /api/repo-check must validate a typed path for .git."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-rsetup-check-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_repo_check_git_dir_returns_true(self):
        d = Path(tempfile.mkdtemp(prefix="polis-rc-git-"))
        try:
            (d / ".git").mkdir()
            r = self.client.get("/api/repo-check", params={"path": str(d)})
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(body["is_git"])
            self.assertEqual(body["path"], str(d.resolve()))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_repo_check_plain_dir_returns_false(self):
        d = Path(tempfile.mkdtemp(prefix="polis-rc-plain-"))
        try:
            r = self.client.get("/api/repo-check", params={"path": str(d)})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["is_git"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_repo_check_empty_path_returns_false(self):
        r = self.client.get("/api/repo-check")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["is_git"])
        self.assertEqual(body["path"], "")


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class RepoInitEndpointTest(unittest.TestCase):
    """POST /api/repo-init must run git init honestly."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-rsetup-init-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    @unittest.skipUnless(HAVE_GIT, "git not available")
    def test_repo_init_creates_git_dir(self):
        """POST /api/repo-init on a fresh directory must initialise it and return ok."""
        d = Path(tempfile.mkdtemp(prefix="polis-ri-fresh-"))
        try:
            r = self.client.post("/api/repo-init", json={"path": str(d)})
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["path"], str(d.resolve()))
            self.assertTrue((d / ".git").exists(), ".git dir must exist after init")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_repo_init_nonexistent_path_returns_400(self):
        """POST /api/repo-init on a non-existent path must return 400 — never silent success."""
        r = self.client.post("/api/repo-init", json={"path": "/nonexistent/path/that/does/not/exist"})
        self.assertEqual(r.status_code, 400)

    def test_repo_init_git_failure_returns_400(self):
        """POST /api/repo-init must surface git errors as 400, not silently succeed."""
        d = Path(tempfile.mkdtemp(prefix="polis-ri-fail-"))
        try:
            # Simulate git returning non-zero
            fake_result = mock.MagicMock()
            fake_result.returncode = 128
            fake_result.stderr = "fatal: not a git repository"
            fake_result.stdout = ""
            with mock.patch("polis.dashboard.server.subprocess.run", return_value=fake_result):
                r = self.client.post("/api/repo-init", json={"path": str(d)})
            self.assertEqual(r.status_code, 400)
            self.assertIn("fatal", r.json()["detail"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class RepoSetupAssetTest(unittest.TestCase):
    """Served static assets must reflect the new single-step repo-setup flow."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-rsetup-asset-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # --- app.js: non-git rejection + git-init offer ---

    def test_app_js_contains_repo_init_endpoint(self):
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/api/repo-init", r.text)

    def test_app_js_contains_repo_check_endpoint(self):
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/api/repo-check", r.text)

    def test_app_js_contains_git_init_offer_text(self):
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Initialise with git init", r.text)

    def test_app_js_contains_show_repo_needs_init(self):
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("showRepoNeedsInit", r.text)

    # --- app.js: immediate apply, no second click, no confirm ---

    def test_app_js_browse_calls_apply_repo_when_is_git(self):
        """Browse handler must call applyRepo(path) immediately when is_git is true."""
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        js = r.text
        self.assertIn("is_git", js)
        self.assertIn("applyRepo(path)", js)

    def test_app_js_no_confirm_in_browse_flow(self):
        """No confirm() dialog must gate the picker flow (one-step UX)."""
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("confirm(", r.text)

    def test_app_js_apply_repo_posts_config(self):
        """applyRepo must POST to /api/config — the one place that writes the workspace."""
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        js = r.text
        self.assertIn("applyRepo", js)
        self.assertIn("/api/config", js)

    def test_app_js_load_branches_called_in_apply_repo(self):
        """loadBranches(path) must be called from applyRepo so branches refresh on pick."""
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("loadBranches(path)", r.text)

    # --- HTML tidy ---

    def test_html_has_repo_msg_element(self):
        """Served HTML must contain #repoMsg for inline error + git-init offer."""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="repoMsg"', r.text)

    def test_html_repo_form_is_hidden_by_default(self):
        """#repoForm must carry the hidden attribute (fallback only)."""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        # Match id="repoForm" ... hidden or hidden ... id="repoForm"
        pattern_a = re.compile(r'id="repoForm"[^>]*hidden', re.IGNORECASE)
        pattern_b = re.compile(r'hidden[^>]*id="repoForm"', re.IGNORECASE)
        self.assertTrue(
            pattern_a.search(r.text) or pattern_b.search(r.text),
            "#repoForm must carry the hidden attribute in the served HTML",
        )

    def test_html_browse_button_not_hidden(self):
        """#browsePath must be visible (no hidden attr) — it's the primary action."""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="browsePath"', r.text)
        self.assertNotIn('id="browsePath" hidden', r.text)
        self.assertNotIn('hidden id="browsePath"', r.text)

    def test_html_branch_select_not_hidden(self):
        """#repoBranchSelect must be visible (no hidden attr)."""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="repoBranchSelect"', r.text)
        self.assertNotIn('id="repoBranchSelect" hidden', r.text)

    # --- TESTING_MODE widget (AC#6) ---

    def test_widget_present_when_testing_mode_on(self):
        """Feedback widget <script> must be injected when testing_mode is True."""
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": True})
        client = TestClient(create_app(self.base))
        r = client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('<script src="/static/feedback-widget.js"></script>', r.text)

    def test_widget_absent_when_testing_mode_off(self):
        """Feedback widget must not appear when testing_mode is False."""
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": False})
        client = TestClient(create_app(self.base))
        r = client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("feedback-widget.js", r.text)


if __name__ == "__main__":
    unittest.main()
