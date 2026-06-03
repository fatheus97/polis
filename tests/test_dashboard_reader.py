"""Tolerant-reader tests for the dashboard. Stdlib only."""

import json
import tempfile
import unittest
from pathlib import Path

from polis.dashboard import reader
from polis.feedback import FeedbackInbox
from polis.models import FeedbackItem, RunResult, Stage
from polis.record import Record
from polis.state import RunStore
from polis.treasury import Treasury


class TolerantRecordReadTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-dash-"))
        self.rec_path = self.base / "record.jsonl"

    def test_skips_partial_last_line_without_raising(self):
        rec = Record(self.rec_path)
        rec.append(run_id="r1", stage=Stage.INTAKE, actor="procedure", kind="intake", text="hi")
        rec.append(run_id="r1", stage=Stage.SPEC, actor="legislative:architect",
                   kind="prd", cost=20, title="t")
        # Simulate a writer caught mid-append: a truncated final line, no newline.
        with open(self.rec_path, "a", encoding="utf-8") as f:
            f.write('{"ts": 123, "run_id": "r1", "stage": "IMPLEM')

        events = reader.read_record_events(self.rec_path)
        self.assertEqual(len(events), 2)  # the partial line is skipped
        self.assertEqual(events[0]["kind"], "intake")

        # Document why we don't reuse Record.events(): it raises on the same file.
        with self.assertRaises(json.JSONDecodeError):
            Record(self.rec_path).events()

    def test_missing_record_is_empty(self):
        self.assertEqual(reader.read_record_events(self.base / "nope.jsonl"), [])


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-dash-"))

    def test_treasury_snapshot_matches_writer(self):
        t = Treasury(self.base / "treasury.sqlite")
        t.appropriate(100)
        t.debit("legislative:architect", 20, "write_prd", run_id="r1")
        t.close()
        snap = reader.read_treasury_snapshot(self.base)
        self.assertEqual(snap["balance"], 80)
        self.assertEqual(snap["appropriated"], 100)
        self.assertEqual(snap["spent"], 20)
        self.assertEqual(snap["per_run"], {"r1": 20})

    def test_runstore_rows(self):
        store = RunStore(self.base / "runs.sqlite")
        res = RunResult(run_id="r1", outcome=Stage.DONE, last_stage=Stage.DONE,
                        reason="merged", attempts=0, spend=50, merge_commit="abc123")
        store.save(res, FeedbackItem(text="hello", id="fb1"))
        store.close()
        rows = reader.read_runstore_rows(self.base)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "r1")
        self.assertEqual(rows[0]["outcome"], "DONE")
        self.assertEqual(rows[0]["merge_commit"], "abc123")

    def test_pending_feedback(self):
        inbox = FeedbackInbox(self.base / "feedback.sqlite")
        inbox.submit("hello world", directives={"dev": "ok"})
        inbox.close()
        pend = reader.read_pending_feedback(self.base)
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["text"], "hello world")
        self.assertEqual(pend[0]["directives"], {"dev": "ok"})

    def test_missing_stores_return_defaults(self):
        empty = Path(tempfile.mkdtemp(prefix="polis-dash-empty-"))
        self.assertEqual(reader.read_treasury_snapshot(empty)["balance"], 0.0)
        self.assertEqual(reader.read_runstore_rows(empty), [])
        self.assertEqual(reader.read_pending_feedback(empty), [])


if __name__ == "__main__":
    unittest.main()
