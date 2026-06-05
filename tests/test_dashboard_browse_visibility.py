"""Tests for PRD prd-9de1fb77: Browse button observable, repo-path input hidden by default.

All tests assert *served-asset content* (HTML/JS text) and the /api/browse server contract.
No headless browser is available in the hermetic suite, so live DOM behaviour is not exercised —
this is stated explicitly rather than implied.

Gated on the optional dashboard extra (fastapi + httpx); self-skips in stdlib-only CI.
"""

import re
import shutil
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


@unittest.skipUnless(HAVE_FASTAPI, "install the dashboard extra (pip install -e '.[dashboard]' httpx)")
class BrowseVisibilityTest(unittest.TestCase):
    """Browse button UX: repoPath hidden by default, observable fallback on 204."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-bvis-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # --- HTML structure ---

    def test_repopath_hidden_by_default(self):
        """Served index.html must have the hidden attribute on #repoPath.

        Asserts served HTML content, not live DOM state.
        """
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        # Match the input tag that carries id="repoPath" and also has the hidden attribute.
        pattern = re.compile(r'<input[^>]*id="repoPath"[^>]*hidden', re.IGNORECASE)
        alt_pattern = re.compile(r'<input[^>]*hidden[^>]*id="repoPath"', re.IGNORECASE)
        self.assertTrue(
            pattern.search(r.text) or alt_pattern.search(r.text),
            "#repoPath input must carry the hidden attribute in the served HTML",
        )

    def test_browse_and_setrepo_controls_still_visible(self):
        """Browse button, branch selector, and form must remain visible (no hidden attr).

        Asserts served HTML content, not live DOM state.
        """
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="browsePath"', r.text)
        self.assertIn('id="repoBranchSelect"', r.text)
        self.assertIn('id="repoForm"', r.text)
        # These controls must NOT carry the hidden attribute themselves.
        self.assertNotIn('id="browsePath" hidden', r.text)
        self.assertNotIn('hidden id="browsePath"', r.text)

    # --- app.js handler wiring ---

    def test_browse_handler_reveals_input_on_204_fallback(self):
        """app.js browse handler must unhide #repoPath and call toast() on 204 (no picker).

        Asserts served JS content — not a live browser click simulation.
        """
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        js = r.text
        # The non-200 branch must reveal the input and inform the user.
        self.assertIn('$("repoPath").hidden = false', js)
        self.assertIn('No folder picker available', js)
        # toast() must be called in that branch.
        self.assertIn('toast(', js)

    def test_browse_handler_applies_immediately_on_success(self):
        """app.js browse handler must apply the picked path immediately (no confirm gate).

        When /api/browse returns 200 with is_git=true, applyRepo(path) is called
        directly — no intermediate text input or confirm() dialog required.
        Asserts served JS content — not a live browser click simulation.
        """
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        js = r.text
        # 200 branch reads is_git and calls applyRepo immediately.
        self.assertIn('is_git', js)
        self.assertIn('applyRepo(path)', js)
        # loadBranches is still called (inside applyRepo).
        self.assertIn('loadBranches(path)', js)
        # Old "Selected:" confirm/toast removed — apply is immediate.
        self.assertNotIn('toast(`Selected:', js)
        self.assertNotIn('confirm(', js)

    def test_setrepo_still_posts_repopath_value(self):
        """app.js #repoForm submit must still read $("repoPath").value and POST /api/config.

        Backward-compat guard: a hidden input retains its .value, so this keeps working.
        Asserts served JS content — not a live browser interaction.
        """
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)
        js = r.text
        self.assertIn('$("repoPath").value', js)
        self.assertIn('/api/config', js)
        self.assertIn('"repoForm"', js)

    # --- /api/browse contract ---

    def test_browse_endpoint_204_when_picker_cancelled(self):
        """POST /api/browse returns 204 when the user cancels (askdirectory returns "").

        Asserts the server-side 204 contract; the dialog is mocked, not real.
        """
        mock_root = mock.MagicMock()
        with mock.patch("tkinter.Tk", return_value=mock_root), \
             mock.patch("tkinter.filedialog.askdirectory", return_value=""):
            r = self.client.post("/api/browse")
        self.assertEqual(r.status_code, 204)

    def test_browse_endpoint_204_when_tkinter_unavailable(self):
        """POST /api/browse returns 204 when tkinter raises on import.

        Asserts the server-side 204 contract; simulates a headless environment.
        """
        with mock.patch("tkinter.Tk", side_effect=Exception("no display")):
            r = self.client.post("/api/browse")
        self.assertEqual(r.status_code, 204)

    @unittest.skipUnless(HAVE_TKINTER, "tkinter not available in this environment")
    def test_browse_endpoint_200_with_mocked_picker(self):
        """POST /api/browse returns 200 {path} when the picker succeeds (mocked).

        Asserts the server-side 200 contract; the dialog is mocked, not real.
        """
        chosen = str(Path(tempfile.mkdtemp(prefix="polis-pick-")).resolve())
        try:
            mock_root = mock.MagicMock()
            with mock.patch("tkinter.Tk", return_value=mock_root), \
                 mock.patch("tkinter.filedialog.askdirectory", return_value=chosen):
                r = self.client.post("/api/browse")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["path"], chosen)
        finally:
            shutil.rmtree(chosen, ignore_errors=True)

    # --- TESTING_MODE feedback widget ---

    def test_feedback_widget_present_when_testing_mode_enabled(self):
        """Feedback widget <script> must appear in served HTML when testing_mode is on.

        Asserts served HTML content; verifies AC #5 (TESTING_MODE widget injection).
        """
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": True})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('<script src="/static/feedback-widget.js"></script>', r.text)

    def test_feedback_widget_absent_when_testing_mode_disabled(self):
        """Feedback widget must not appear in served HTML when testing_mode is off."""
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": False})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("feedback-widget.js", r.text)


if __name__ == "__main__":
    unittest.main()
