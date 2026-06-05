"""Tests for the /api/browse and /api/branches endpoints and TESTING_MODE widget injection.

Gated on the optional dashboard extra (fastapi + httpx); self-skips in stdlib-only CI.
All tests are hermetic: no live OS dialog, no real network, no hanging subprocess.
"""

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
class BrowseEndpointTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-browse-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    @unittest.skipUnless(HAVE_TKINTER, "tkinter not available")
    def test_browse_returns_absolute_path(self):
        known = str(Path(tempfile.mkdtemp(prefix="polis-pick-")).resolve())
        try:
            mock_root = mock.MagicMock()
            with mock.patch("tkinter.Tk", return_value=mock_root), \
                 mock.patch("tkinter.filedialog.askdirectory", return_value=known):
                r = self.client.post("/api/browse")
        finally:
            shutil.rmtree(known, ignore_errors=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["path"], known)
        self.assertIn(r.json()["is_git"], (True, False))  # field is always present

    @unittest.skipUnless(HAVE_TKINTER, "tkinter not available")
    def test_browse_raises_dialog_to_front_before_askdirectory(self):
        chosen = str(Path(tempfile.mkdtemp(prefix="polis-pick-")).resolve())
        try:
            mock_root = mock.MagicMock()
            with mock.patch("tkinter.Tk", return_value=mock_root), \
                 mock.patch("tkinter.filedialog.askdirectory", return_value=chosen) as ad:
                r = self.client.post("/api/browse")
            mock_root.attributes.assert_any_call("-topmost", True)
            mock_root.lift.assert_called()
            mock_root.focus_force.assert_called()
            self.assertTrue(mock_root.focus_force.called and ad.called)
            self.assertEqual(r.status_code, 200)
        finally:
            shutil.rmtree(chosen, ignore_errors=True)

    @unittest.skipUnless(HAVE_TKINTER, "tkinter not available")
    def test_browse_no_display_returns_204_no_body(self):
        # Simulate headless / no display: Tk() raises
        with mock.patch("tkinter.Tk", side_effect=Exception("no display available")):
            r = self.client.post("/api/browse")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(r.content)

    @unittest.skipUnless(HAVE_TKINTER, "tkinter not available")
    def test_browse_cancelled_dialog_returns_204(self):
        # askdirectory returns "" when user clicks Cancel
        mock_root = mock.MagicMock()
        with mock.patch("tkinter.Tk", return_value=mock_root), \
             mock.patch("tkinter.filedialog.askdirectory", return_value=""):
            r = self.client.post("/api/browse")
        self.assertEqual(r.status_code, 204)


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class BranchesEndpointTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-branches-"))
        self.client = TestClient(create_app(self.base))
        self.repo = Path(tempfile.mkdtemp(prefix="polis-repo-"))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_branches_no_path_returns_empty(self):
        r = self.client.get("/api/branches")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["branches"], [])

    def test_branches_non_git_dir_returns_empty(self):
        # A plain temp dir is not a git repo
        r = self.client.get("/api/branches", params={"path": str(self.repo)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["branches"], [])

    def test_branches_git_error_returns_empty(self):
        # Simulate git not installed or subprocess failure
        with mock.patch("polis.dashboard.server.subprocess.run",
                        side_effect=FileNotFoundError("git not found")):
            r = self.client.get("/api/branches", params={"path": str(self.repo)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["branches"], [])

    @unittest.skipUnless(HAVE_GIT, "git not available")
    def test_branches_real_git_repo_lists_branches(self):
        # Create a real git repo with one commit so 'git branch' shows branches
        subprocess.run(["git", "init", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "t@t.com"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "T"],
                       check=True, capture_output=True)
        (self.repo / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(self.repo), "add", "f.txt"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-m", "init"],
                       check=True, capture_output=True)

        r = self.client.get("/api/branches", params={"path": str(self.repo)})
        self.assertEqual(r.status_code, 200)
        self.assertIn("main", r.json()["branches"])

    @unittest.skipUnless(HAVE_GIT, "git not available")
    def test_branches_lists_all_local_branches(self):
        # Create repo with two branches
        subprocess.run(["git", "init", "-b", "main", str(self.repo)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "t@t.com"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "T"],
                       check=True, capture_output=True)
        (self.repo / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(self.repo), "add", "f.txt"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-m", "init"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "checkout", "-b", "feat/x"],
                       check=True, capture_output=True)

        r = self.client.get("/api/branches", params={"path": str(self.repo)})
        self.assertEqual(r.status_code, 200)
        names = r.json()["branches"]
        self.assertIn("main", names)
        self.assertIn("feat/x", names)
        # Branch names must not contain the '*' current-branch marker
        self.assertTrue(all("*" not in b for b in names))


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class WidgetTestingModeTest(unittest.TestCase):
    """Verify feedback widget injection gated on TESTING_MODE (PRD requirement)."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-widget-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_widget_script_present_when_testing_mode_on(self):
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": True})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('<script src="/static/feedback-widget.js"></script>', r.text)

    def test_widget_script_absent_when_testing_mode_off(self):
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": False})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("feedback-widget.js", r.text)


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class RepoStatusInitTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-rsinit-"))
        self.tmp = Path(tempfile.mkdtemp(prefix="polis-rs-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repo_status_no_path_empty(self):
        r = self.client.get("/api/repo-status")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["exists"])
        self.assertFalse(data["is_git"])

    def test_repo_status_non_git_dir(self):
        r = self.client.get("/api/repo-status", params={"path": str(self.tmp)})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["exists"])
        self.assertFalse(data["is_git"])

    @unittest.skipUnless(HAVE_GIT, "git not available")
    def test_repo_init_creates_git_repo_with_main_branch(self):
        r = self.client.post("/api/repo-init", json={"path": str(self.tmp)})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["is_git"])
        self.assertTrue((self.tmp / ".git").is_dir())
        result = subprocess.run(
            ["git", "-C", str(self.tmp), "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "main")
        r2 = self.client.get("/api/repo-status", params={"path": str(self.tmp)})
        self.assertTrue(r2.json()["is_git"])

    def test_repo_init_missing_dir_errors(self):
        nonexistent = str(self.tmp / "does_not_exist")
        r = self.client.post("/api/repo-init", json={"path": nonexistent})
        self.assertEqual(r.status_code, 400)

    def test_repo_init_failure_is_honest(self):
        fake_result = mock.MagicMock()
        fake_result.returncode = 1
        fake_result.stderr = "boom"
        fake_result.stdout = ""
        with mock.patch("polis.dashboard.server.subprocess.run", return_value=fake_result):
            r = self.client.post("/api/repo-init", json={"path": str(self.tmp)})
        self.assertEqual(r.status_code, 500)
        self.assertIn("boom", r.json().get("detail", ""))

    def test_repo_init_idempotent_if_already_git(self):
        (self.tmp / ".git").mkdir()
        r = self.client.post("/api/repo-init", json={"path": str(self.tmp)})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["is_git"])


if __name__ == "__main__":
    unittest.main()
