"""ReportStore tests. Stdlib only (core CI)."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from polis.reports import ReportStore


class ReportStoreTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-rpt-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.store = ReportStore(self.base)

    def test_create_and_get_roundtrip(self):
        r = self.store.create(text="save button does nothing", submitted_by="alice",
                              url="http://x/", state={"console": [{"level": "error"}]},
                              viewport={"w": 1440, "h": 900})
        got = self.store.get(r["id"])
        self.assertEqual(got["text"], "save button does nothing")
        self.assertEqual(got["submitted_by"], "alice")
        self.assertEqual(got["state"]["console"][0]["level"], "error")
        self.assertEqual(got["ticket_status"], "none")
        self.assertIsNone(got["screenshot"])

    def test_screenshot_stored_and_served(self):
        png = b"\x89PNG\r\n\x1a\n" + b"fakeimagebytes"
        r = self.store.create(text="x", screenshot_bytes=png, screenshot_ext="png")
        self.assertEqual(r["screenshot"], "screenshot.png")
        self.assertEqual(r["screenshot_bytes"], len(png))
        p = self.store.screenshot_path(r["id"])
        self.assertIsNotNone(p)
        self.assertEqual(p.read_bytes(), png)

    def test_screenshot_ext_sanitized(self):
        r = self.store.create(text="x", screenshot_bytes=b"abc", screenshot_ext="../evil")
        self.assertTrue(r["screenshot"].startswith("screenshot."))
        self.assertNotIn("/", r["screenshot"])

    def test_set_feedback_id_and_ticket(self):
        r = self.store.create(text="x")
        self.store.set_feedback_id(r["id"], "fb-123")
        self.store.set_ticket(r["id"], {"title": "T"}, status="done")
        got = self.store.get(r["id"])
        self.assertEqual(got["feedback_id"], "fb-123")
        self.assertEqual(got["ticket"]["title"], "T")
        self.assertEqual(got["ticket_status"], "done")

    def test_list_newest_first_and_tolerant(self):
        a = self.store.create(text="a")
        b = self.store.create(text="b")
        # a corrupt report dir must not break list()
        bad = self.base / "reports" / "rpt-bad"
        bad.mkdir(parents=True)
        (bad / "report.json").write_text("{not json", encoding="utf-8")
        ids = [r["id"] for r in self.store.list()]
        self.assertEqual(ids[:2], [b["id"], a["id"]])  # newest first
        self.assertNotIn("rpt-bad", ids)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.store.get("rpt-nope"))


if __name__ == "__main__":
    unittest.main()
