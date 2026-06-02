"""Phase 3 tests: parallel worktrees + merge resolution, deploy hooks, decorrelation."""

import shutil
import tempfile
import unittest
from pathlib import Path

from polis.app import build_government
from polis.llm import FakeLLM
from polis.models import Diff, FeedbackItem, FileChange
from polis.registry import ModelTier, Registry
from polis.workspace import MergeConflict
from polis.worktree import WorktreeManager
from tests._doubles import Harness, ScriptedSandbox, passing

HAVE_GIT = shutil.which("git") is not None


@unittest.skipUnless(HAVE_GIT, "git required for worktree tests")
class ParallelWorktreeTest(unittest.TestCase):
    def test_parallel_runs_all_merge_with_distinct_commits(self):
        base = Path(tempfile.mkdtemp(prefix="polis-par-"))
        gov = build_government(base)
        gov.treasury.appropriate(100_000)
        for i in range(3):
            gov.inbox.submit(f"feature {i}", directives={"module": f"feat{i}"})
        results = gov.run_parallel(gov.inbox.pending(), max_workers=3)

        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.merged for r in results), [r.reason for r in results])
        self.assertEqual(len({r.merge_commit for r in results}), 3)   # 3 distinct merges
        self.assertEqual(gov.treasury.total_spent(), 150)             # 3 x (20+10+20)

    def test_merge_conflict_escalates(self):
        repo = Path(tempfile.mkdtemp(prefix="polis-conf-")) / "repo"
        mgr = WorktreeManager(repo)
        a = mgr.create_worktree("runA")
        b = mgr.create_worktree("runB")
        # Both branch from the same main, then edit the SAME file differently.
        a.start_change("wipA")
        a.apply(Diff(changes=[FileChange("shared.txt", "AAA\n")]))
        b.start_change("wipB")
        b.apply(Diff(changes=[FileChange("shared.txt", "BBB\n")]))
        self.assertTrue(a.merge("merge A"))          # first one wins
        with self.assertRaises(MergeConflict):       # second can't apply cleanly
            b.merge("merge B")
        mgr.remove_worktree(a)
        mgr.remove_worktree(b)


class DeployHookTest(unittest.TestCase):
    def test_deploy_hook_runs_after_merge(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]))
        h.orch.config.deploy_command = "echo deployed"
        res = h.orch.process(FeedbackItem(text="ship it"))
        self.assertTrue(res.merged)
        deploys = [e for e in h.events() if e["kind"] == "deploy"]
        self.assertEqual(len(deploys), 1)
        self.assertTrue(deploys[0]["payload"]["ok"])

    def test_no_deploy_event_without_command(self):
        h = Harness()
        h.orch.process(FeedbackItem(text="x"))
        self.assertNotIn("deploy", h.kinds())


class DecorrelationTest(unittest.TestCase):
    def test_per_role_models_wire_through(self):
        reg = Registry.real(FakeLLM(["x"]),
                            ModelTier(architect="sonnet", reviewer="opus", dev="haiku"))
        dev = reg.hire_dev(None)
        reviewer = reg.spawn("reviewer")
        architect = reg.spawn("architect")
        self.assertEqual(dev.model, "haiku")
        self.assertEqual(reviewer.model, "opus")
        self.assertEqual(architect.model, "sonnet")
        self.assertNotEqual(reviewer.model, dev.model)  # decorrelated


if __name__ == "__main__":
    unittest.main()
