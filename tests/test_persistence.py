import tempfile
import unittest
from pathlib import Path

from polis.models import Branch, Stage
from polis.record import Record
from polis.state import RunStore
from polis.treasury import Treasury
from tests._doubles import Harness


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="polis-persist-"))

    def test_treasury_survives_new_instance(self):
        db = self.tmp / "treasury.sqlite"
        t1 = Treasury(db)
        t1.appropriate(100)
        t1.debit("dev", 30, "implement", run_id="r1")
        t1.close()

        t2 = Treasury(db)  # fresh instance, same file
        self.assertEqual(t2.balance(), 70)
        self.assertEqual(t2.spent_on("r1"), 30)

    def test_record_survives_new_instance(self):
        path = self.tmp / "record.jsonl"
        r1 = Record(path)
        r1.append(run_id="r1", stage=Stage.SPEC, actor="legislative:architect",
                  kind="prd", source=Branch.PROCEDURE, cost=20, title="x")
        r2 = Record(path)  # fresh instance, same file
        events = r2.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "prd")
        self.assertEqual(events[0]["source"], "procedure")

    def test_run_store_survives_new_instance(self):
        db = self.tmp / "runs.sqlite"
        # Run a full procedure whose RunStore points at a real file.
        h = Harness()
        h.run_store.close()
        h.run_store = RunStore(db)
        h.orch.run_store = h.run_store
        from polis.models import FeedbackItem
        res = h.orch.process(FeedbackItem(text="persist me"))
        h.run_store.close()

        store2 = RunStore(db)
        row = store2.get(res.run_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["outcome"], "DONE")


if __name__ == "__main__":
    unittest.main()
