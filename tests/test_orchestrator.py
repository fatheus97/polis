import inspect
import unittest

from polis.agents.base import Reviewer
from polis.agents.stubs import StubArchitect
from polis.models import FeedbackItem, Stage
from tests._doubles import Harness, ScriptedSandbox, failing, passing

# Stub costs: architect=20, dev=10, reviewer=20  (happy-path spend = 50)


def fb(text="add a health endpoint", **directives):
    return FeedbackItem(text=text, directives=directives or {})


class HappyPathTest(unittest.TestCase):
    def test_walks_full_procedure_and_merges(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]))
        res = h.orch.process(fb())

        self.assertTrue(res.merged)
        self.assertEqual(res.outcome, Stage.DONE)
        self.assertIsNotNone(res.merge_commit)
        self.assertEqual(len(h.workspace.merges), 1)
        self.assertEqual(res.attempts, 0)

        # The merge message reads as version history: PRD title (line 1) + goal + traceability.
        msg = h.workspace.merges[0][0]
        self.assertTrue(msg.startswith("add a health endpoint"))  # PRD title, not "Polis: merge …"
        self.assertIn("Implement: add a health endpoint", msg)    # the PRD goal
        self.assertIn("— PRD prd", msg)                           # traceability footer

        # The procedure visited every stage in order.
        kinds = h.kinds()
        for k in ("intake", "prd", "diff", "test_result", "verdict", "merge", "done"):
            self.assertIn(k, kinds)

        # Spend = sum of the three paid agent calls; treasury debited exactly.
        self.assertEqual(res.spend, 50)
        self.assertEqual(h.treasury.balance(), 950)

    def test_run_persisted_to_run_store(self):
        h = Harness()
        res = h.orch.process(fb())
        row = h.run_store.get(res.run_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["outcome"], "DONE")
        self.assertEqual(row["merge_commit"], res.merge_commit)

    def test_officials_released_after_run(self):
        h = Harness()
        h.orch.process(fb())
        self.assertEqual(h.registry.live_instances(), [])


class BudgetTest(unittest.TestCase):
    def test_escalates_at_spec_when_cannot_fund_architect(self):
        h = Harness(budget=5)  # < architect cost (20)
        res = h.orch.process(fb())
        self.assertEqual(res.outcome, Stage.ESCALATE)
        self.assertEqual(res.last_stage, Stage.SPEC)
        self.assertIn("budget", res.reason)
        self.assertEqual(len(h.workspace.merges), 0)
        self.assertIn("escalate", h.kinds())

    def test_escalates_at_implement(self):
        h = Harness(budget=20)  # funds SPEC(20), nothing left for IMPLEMENT(10)
        res = h.orch.process(fb())
        self.assertEqual(res.outcome, Stage.ESCALATE)
        self.assertEqual(res.last_stage, Stage.IMPLEMENT)

    def test_escalates_at_review_and_discards_work(self):
        h = Harness(budget=30)  # funds SPEC(20)+IMPLEMENT(10); REVIEW(20) unaffordable
        res = h.orch.process(fb())
        self.assertEqual(res.outcome, Stage.ESCALATE)
        self.assertEqual(res.last_stage, Stage.REVIEW)
        self.assertGreaterEqual(h.workspace.discards, 1)
        self.assertEqual(len(h.workspace.merges), 0)

    def test_per_task_cap_escalates(self):
        h = Harness(budget=1000, per_task_cap=30)  # cap below full happy-path spend (50)
        res = h.orch.process(fb())
        self.assertEqual(res.outcome, Stage.ESCALATE)
        self.assertEqual(res.last_stage, Stage.REVIEW)
        self.assertIn("budget", res.reason)


class ReviseLoopTest(unittest.TestCase):
    def test_failing_tests_then_passing_merges(self):
        # attempt 0: tests fail -> reject -> revise; attempt 1: tests pass -> merge
        h = Harness(sandbox=ScriptedSandbox([failing(), passing()]))
        res = h.orch.process(fb())
        self.assertTrue(res.merged)
        self.assertEqual(res.attempts, 1)
        self.assertGreaterEqual(h.workspace.discards, 1)
        self.assertIn("revise", h.kinds())

    def test_constitution_violation_blocks_and_escalates(self):
        # Secret leaks every attempt; tests pass but the judiciary blocks. Bounded
        # revisions then a clean ESCALATE (never a merge).
        h = Harness(sandbox=ScriptedSandbox([passing()]), max_revisions=1)
        res = h.orch.process(fb(dev="emit_secret"))
        self.assertEqual(res.outcome, Stage.ESCALATE)
        self.assertEqual(res.last_stage, Stage.REVISE)
        self.assertIn("revisions_exhausted", res.reason)
        self.assertEqual(len(h.workspace.merges), 0)
        self.assertFalse(res.verdict.approved)
        self.assertIn("no-hardcoded-secrets", {v.rule_id for v in res.verdict.violations})
        self.assertEqual(res.attempts, 2)  # initial + 1 revision

    def test_termination_is_bounded(self):
        # Always rejected; the loop MUST terminate (this test finishing proves it).
        h = Harness(sandbox=ScriptedSandbox([passing()]), max_revisions=3)
        res = h.orch.process(fb(dev="emit_secret"))
        self.assertEqual(res.outcome, Stage.ESCALATE)
        self.assertEqual(res.attempts, 4)  # initial + 3 revisions, then escalate


class ReviewIndependenceTest(unittest.TestCase):
    def test_judiciary_only_invoked_by_the_procedure(self):
        h = Harness()
        h.orch.process(fb())
        judicial = [e for e in h.events() if e["actor"].startswith("judicial")]
        self.assertTrue(judicial, "expected at least one judicial event")
        for e in judicial:
            self.assertEqual(
                e["source"], "procedure",
                "the reviewer must be invoked by the procedure, not another branch",
            )

    def test_architect_holds_no_reference_to_a_reviewer(self):
        arch = StubArchitect()
        self.assertNotIn("reviewer", vars(arch))
        self.assertFalse(hasattr(arch, "reviewer"))

    def test_reviewer_signature_takes_only_artifacts(self):
        # The reviewer is handed artifacts (prd, diff, test_result, constitution) —
        # never the agents that produced them.
        params = list(inspect.signature(Reviewer.review).parameters)
        self.assertEqual(params, ["self", "prd", "diff", "test_result", "constitution"])


if __name__ == "__main__":
    unittest.main()
