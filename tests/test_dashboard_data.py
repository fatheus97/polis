"""Pure data-layer tests for the dashboard. Stdlib only (no FastAPI), so these run in
core CI. They feed REAL Records (produced by the stub Harness) into the derivation funcs.
"""

import unittest

from polis.dashboard import data
from polis.models import FeedbackItem
from tests._doubles import Harness, ScriptedSandbox, failing, passing


def fb(text="add a thing", **directives):
    return FeedbackItem(text=text, directives=directives or {})


class RunSummaryTest(unittest.TestCase):
    def test_merged_run(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]))
        h.orch.process(fb())
        s = data.run_summary(h.events())
        self.assertFalse(s["in_flight"])
        self.assertEqual(s["outcome"], "DONE")
        self.assertIsNotNone(s["merge_commit"])
        self.assertEqual(s["attempts"], 0)
        self.assertEqual(s["spend"], 50)  # architect 20 + dev 10 + reviewer 20
        self.assertEqual(s["reason"], "merged")

    def test_escalated_run(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]), max_revisions=1)
        h.orch.process(fb(dev="emit_secret"))
        s = data.run_summary(h.events())
        self.assertEqual(s["outcome"], "ESCALATE")
        self.assertFalse(s["in_flight"])
        self.assertIn("revisions_exhausted", s["reason"])
        self.assertIsNone(s["merge_commit"])

    def test_in_flight_when_no_terminal_event(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]))
        h.orch.process(fb())
        partial = [e for e in h.events()
                   if e["kind"] not in ("done", "escalate", "merge", "release")]
        s = data.run_summary(partial)
        self.assertTrue(s["in_flight"])
        self.assertIsNone(s["outcome"])
        self.assertIsNone(s["merge_commit"])

    def test_revise_then_merge_attempts(self):
        h = Harness(sandbox=ScriptedSandbox([failing(), passing()]))
        h.orch.process(fb())
        s = data.run_summary(h.events())
        self.assertEqual(s["outcome"], "DONE")
        self.assertEqual(s["attempts"], 1)

    def test_specialist_discipline_surfaced(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]))
        h.orch.process(fb(discipline="backend"))
        s = data.run_summary(h.events())
        self.assertEqual(s["discipline"], "backend")

    def test_panel_winner_title(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]), num_architects=3)
        h.orch.process(fb(text="build the panel feature"))
        s = data.run_summary(h.events())
        self.assertTrue(s["prd_title"])  # derived from the winning proposal

    def test_tolerates_events_missing_payload(self):
        # The tolerant reader can hand us loosely-shaped events; never KeyError.
        events = [
            {"ts": 1, "run_id": "r1", "stage": "INTAKE", "actor": "procedure", "kind": "intake"},
            {"ts": 2, "run_id": "r1", "stage": "DONE", "actor": "procedure", "kind": "done"},
        ]
        s = data.run_summary(events)
        self.assertEqual(s["outcome"], "DONE")
        self.assertIsNone(s["feedback_text"])


class BranchAttributionTest(unittest.TestCase):
    def test_branch_from_actor_not_source(self):
        self.assertEqual(data.event_branch({"actor": "legislative:architect"}), "legislative")
        self.assertEqual(data.event_branch({"actor": "judicial:reviewer"}), "judicial")
        self.assertEqual(data.event_branch({"actor": "procedure"}), "procedure")

    def test_by_branch_spend_splits_by_branch(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]))
        h.orch.process(fb())
        ov = data.overview(h.events())
        bb = ov["by_branch_spend"]
        self.assertEqual(bb["legislative"], 20)
        self.assertEqual(bb["executive"], 10)
        self.assertEqual(bb["judicial"], 20)
        # procedure events (intake/spawn/merge/...) cost nothing
        self.assertEqual(bb.get("procedure", 0), 0)


class OverviewAndDetailTest(unittest.TestCase):
    def test_overview_counts_across_runs(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]))
        h.orch.process(fb(text="one"))
        h.orch.process(fb(text="two"))
        ov = data.overview(h.events())
        self.assertEqual(ov["total_runs"], 2)
        self.assertEqual(ov["done"], 2)
        self.assertEqual(ov["escalate"], 0)
        self.assertEqual(ov["total_spend"], 100)

    def test_run_detail_has_timeline_and_stage_strip(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]))
        h.orch.process(fb())
        d = data.run_detail(h.events())
        self.assertTrue(d["timeline"])
        self.assertTrue(all("branch" in e for e in d["timeline"]))
        strip = {s["stage"]: s["visited"] for s in d["stage_strip"]}
        self.assertTrue(strip["SPEC"] and strip["IMPLEMENT"] and strip["MERGE"])
        self.assertFalse(strip["CONSTITUTIONAL"])  # court was off


if __name__ == "__main__":
    unittest.main()
