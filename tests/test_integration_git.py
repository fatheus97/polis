"""End-to-end with the REAL workspace + sandbox: actual git branches/merges and a
real ``unittest`` subprocess. Proves the procedure works against live tooling, not
just fakes. Skipped automatically if git is unavailable.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from polis.constitution import Constitution
from polis.models import FeedbackItem
from polis.orchestrator import Orchestrator, OrchestratorConfig
from polis.record import Record
from polis.registry import Registry
from polis.sandbox import LocalSandbox
from polis.state import RunStore
from polis.treasury import Treasury
from polis.workspace import GitWorkspace

HAVE_GIT = shutil.which("git") is not None


@unittest.skipUnless(HAVE_GIT, "git is required for the integration test")
class GitIntegrationTest(unittest.TestCase):
    def _orchestrator(self, tmp: Path, max_revisions: int = 2):
        treasury = Treasury(":memory:")
        treasury.appropriate(1000)
        ws = GitWorkspace(tmp / "repo")
        orch = Orchestrator(
            registry=Registry.default(),
            treasury=treasury,
            record=Record(tmp / "record.jsonl"),
            constitution=Constitution.load(),
            workspace=ws,
            sandbox=LocalSandbox(),
            run_store=RunStore(":memory:"),
            config=OrchestratorConfig(max_revisions=max_revisions),
        )
        return orch, ws

    def test_happy_path_produces_real_merge_commit(self):
        tmp = Path(tempfile.mkdtemp(prefix="polis-int-"))
        orch, ws = self._orchestrator(tmp)
        res = orch.process(FeedbackItem(text="add a feature that returns ok"))

        self.assertTrue(res.merged, msg=res.reason)
        self.assertTrue(res.test_result.passed)
        self.assertTrue((ws.path / "feature.py").exists(), "feature landed on main")
        log = subprocess.run(["git", "log", "--oneline"], cwd=ws.path,
                             capture_output=True, text=True).stdout.lower()
        self.assertIn("merge", log)

    def test_real_failing_then_passing_revises_and_merges(self):
        tmp = Path(tempfile.mkdtemp(prefix="polis-int-"))
        orch, ws = self._orchestrator(tmp)
        res = orch.process(FeedbackItem(
            text="a feature that needs a second attempt",
            directives={"dev": "fail_n_times", "n": 1},
        ))
        self.assertTrue(res.merged, msg=res.reason)
        self.assertEqual(res.attempts, 1)

    def test_real_secret_is_blocked_no_merge(self):
        tmp = Path(tempfile.mkdtemp(prefix="polis-int-"))
        orch, ws = self._orchestrator(tmp, max_revisions=1)
        res = orch.process(FeedbackItem(
            text="leak a secret", directives={"dev": "emit_secret"},
        ))
        self.assertFalse(res.merged)
        # main never received the secret-bearing change
        self.assertFalse((ws.path / "config.py").exists())


if __name__ == "__main__":
    unittest.main()
