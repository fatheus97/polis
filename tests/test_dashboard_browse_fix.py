"""Tests verifying the Browse button is wired and legacy repoBranch field is removed.

Gated on the optional dashboard extra (fastapi + httpx); self-skips in stdlib-only CI.
All tests are hermetic: no live OS dialog, no real network, no hanging subprocess.
"""

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
class BrowseButtonUiTest(unittest.TestCase):
    """Browse button presence, legacy-field removal, picker wiring, and widget injection."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-brfix-"))
        self.client = TestClient(create_app(self.base))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_browse_button_present_in_rendered_html(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="browsePath"', r.text)

    def test_legacy_branch_text_input_absent_from_rendered_html(self):
        """repoBranch input superseded by the branch dropdown must not appear in the page."""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('id="repoBranch"', r.text)

    def test_branch_select_dropdown_present_in_rendered_html(self):
        """The branch dropdown introduced in PR #22 must be present."""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="repoBranchSelect"', r.text)

    @unittest.skipUnless(HAVE_TKINTER, "tkinter not available")
    def test_browse_button_click_handler_fires_and_opens_picker(self):
        """POST /api/browse (the target of the Browse button onclick) must open the
        folder picker and return the chosen path so the new input field receives it."""
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

    def test_widget_script_referenced_when_testing_mode_enabled(self):
        """Feedback widget must be included via a <script> tag when TESTING_MODE is on."""
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": True})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('<script src="/static/feedback-widget.js"></script>', r.text)

    def test_widget_script_absent_when_testing_mode_disabled(self):
        """Feedback widget must not be loaded when TESTING_MODE is off."""
        from polis.projectcfg import write_config
        write_config(self.base, {"testing_mode": False})
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("feedback-widget.js", r.text)


if __name__ == "__main__":
    unittest.main()
