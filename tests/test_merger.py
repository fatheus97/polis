"""Merger strategy tests — stdlib only (core CI). The PullRequestMerger is exercised by
patching its single ``_run`` subprocess seam, so no real git/gh/network is touched."""

import json
import types
import unittest

from polis.merger import LocalMerger, PullRequestMerger
from polis.workspace import MergeConflict
from tests._doubles import FakeMerger, FakeWorkspace, Harness, ScriptedSandbox, passing


def _R(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class ScriptRunner:
    """Stand-in for PullRequestMerger._run. Returns canned results per command; the
    `gh pr checks` poll walks `checks_seq` (last entry repeats)."""

    def __init__(self, checks_seq=None, fail_cmd=None):
        self.calls = []          # list of arg-tuples
        self.fail_cmd = fail_cmd  # a prefix tuple to force returncode=1
        self.checks_seq = list(checks_seq or [[{"name": "tests (3.12)", "bucket": "pass"}]])
        self._i = 0

    def __call__(self, cwd, *args, timeout=120):
        self.calls.append(args)
        a = list(args)
        if self.fail_cmd and tuple(a[:len(self.fail_cmd)]) == tuple(self.fail_cmd):
            return _R(1, "", "forced failure")
        if a[:4] == ["git", "remote", "get-url", "origin"]:
            return _R(0, "git@github.com:fatheus97/polis.git")
        if a[:3] == ["gh", "pr", "create"]:
            return _R(0, "https://github.com/fatheus97/polis/pull/42")
        if a[:3] == ["gh", "pr", "checks"]:
            checks = self.checks_seq[min(self._i, len(self.checks_seq) - 1)]
            self._i += 1
            return _R(0, json.dumps(checks))
        if a[:3] == ["gh", "pr", "merge"]:
            return _R(0, "merged")
        if a[:2] == ["git", "rev-parse"]:
            return _R(0, "abc123def456")
        return _R(0, "")  # push / fetch / checkout / reset / branch -D


class LocalAndFakeMergerTest(unittest.TestCase):
    def test_local_merger_delegates_to_workspace(self):
        ws = FakeWorkspace()
        ws.start_change("b")
        sha = LocalMerger().merge(ws, "b", "msg")
        self.assertEqual(ws.merges[0][0], "msg")
        self.assertEqual(sha, ws.merges[0][1])

    def test_fake_merger_records(self):
        m = FakeMerger()
        sha = m.merge(object(), "br", "the message")
        self.assertEqual(m.calls, [("br", "the message")])
        self.assertTrue(sha)


class PullRequestMergerTest(unittest.TestCase):
    def _merger(self, runner):
        m = PullRequestMerger(main_branch="main", poll_interval=0, timeout=5)
        m._run = runner
        return m

    def test_happy_path_push_pr_checks_merge_sync(self):
        runner = ScriptRunner()
        m = self._merger(runner)
        sha = m.merge(types.SimpleNamespace(path="/tmp/repo"),
                      "polis/run-1/attempt-0", "Easier repo selection\n\nbody text here")
        self.assertEqual(sha, "abc123def456")  # from the synced `git rev-parse HEAD`
        verbs = [tuple(c) for c in runner.calls]
        # the essential sequence happened, and we NEVER pushed main
        self.assertIn(("git", "push", "-u", "origin", "polis/run-1/attempt-0"), verbs)
        self.assertTrue(any(c[:3] == ("gh", "pr", "create") and "--base" in c and "main" in c
                            for c in verbs))
        # squash-merge pins the subject to the PR title (not the dev's WIP commit subject)
        merge = next(c for c in verbs if c[:3] == ("gh", "pr", "merge"))
        self.assertIn("--squash", merge)
        self.assertIn("--subject", merge)
        self.assertEqual(merge[merge.index("--subject") + 1], "Easier repo selection")
        # the commit body must NOT repeat the subject line (--subject already carries it)
        commit_body = merge[merge.index("--body") + 1]
        self.assertNotIn("Easier repo selection", commit_body)
        self.assertIn("body text here", commit_body)
        self.assertNotIn(("git", "push", "origin", "main"), verbs)

    def test_ci_failure_escalates(self):
        m = self._merger(ScriptRunner(checks_seq=[[{"name": "tests", "bucket": "fail"}]]))
        with self.assertRaises(MergeConflict):
            m.merge(types.SimpleNamespace(path="/x"), "polis/b", "msg")

    def test_ci_timeout_escalates(self):
        m = PullRequestMerger(poll_interval=0, timeout=0)
        m._run = ScriptRunner(checks_seq=[[{"name": "tests", "bucket": "pending"}]])
        with self.assertRaises(MergeConflict):
            m.merge(types.SimpleNamespace(path="/x"), "polis/b", "msg")

    def test_missing_origin_escalates(self):
        m = self._merger(ScriptRunner(fail_cmd=("git", "remote", "get-url")))
        with self.assertRaises(MergeConflict):
            m.merge(types.SimpleNamespace(path="/x"), "polis/b", "msg")

    def test_push_failure_escalates(self):
        m = self._merger(ScriptRunner(fail_cmd=("git", "push")))
        with self.assertRaises(MergeConflict):
            m.merge(types.SimpleNamespace(path="/x"), "polis/b", "msg")


class SyncMainTest(unittest.TestCase):
    def test_pr_merger_syncs_local_main_to_origin(self):
        # Before branching, fast-forward local main to origin/main so no run builds on a stale base.
        runner = ScriptRunner()
        m = PullRequestMerger(main_branch="main")
        m._run = runner
        m.sync_main(types.SimpleNamespace(path="/tmp/repo"))
        verbs = [tuple(c) for c in runner.calls]
        self.assertIn(("git", "fetch", "origin"), verbs)
        self.assertIn(("git", "checkout", "main"), verbs)
        self.assertIn(("git", "reset", "--hard", "origin/main"), verbs)
        self.assertNotIn(("git", "push", "origin", "main"), verbs)  # never pushes main

    def test_pr_merger_sync_is_noop_without_origin(self):
        # No origin → nothing to sync against; must not raise or run fetch/reset.
        runner = ScriptRunner(fail_cmd=("git", "remote", "get-url"))
        m = PullRequestMerger()
        m._run = runner
        m.sync_main(types.SimpleNamespace(path="/x"))  # no exception
        verbs = [tuple(c)[:2] for c in runner.calls]
        self.assertNotIn(("git", "fetch"), verbs)

    def test_pr_merger_sync_warns_and_skips_reset_on_fetch_failure(self):
        # A failed fetch must NOT reset to a possibly-stale origin ref — warn + keep local main.
        runner = ScriptRunner(fail_cmd=("git", "fetch"))
        m = PullRequestMerger()
        m._run = runner
        with self.assertWarns(UserWarning):
            m.sync_main(types.SimpleNamespace(path="/x"))
        verbs = [tuple(c)[:2] for c in runner.calls]
        self.assertNotIn(("git", "reset"), verbs)  # never reset to a stale ref

    def test_local_merger_sync_is_noop(self):
        LocalMerger().sync_main(FakeWorkspace())  # local main IS the source — nothing to do


class OrchestratorMergerSeamTest(unittest.TestCase):
    def test_run_routes_merge_through_injected_merger(self):
        from polis.models import FeedbackItem
        fm = FakeMerger()
        h = Harness(sandbox=ScriptedSandbox([passing()]), merger=fm)
        res = h.orch.process(FeedbackItem(text="add a feature"))
        self.assertTrue(res.merged, res.reason)
        self.assertEqual(len(fm.calls), 1)             # merge went through the merger
        self.assertEqual(len(h.workspace.merges), 0)   # NOT the workspace's local merge
        _branch, msg = fm.calls[0]
        self.assertIn(res.run_id, msg)

    def test_run_syncs_main_before_branching(self):
        # The orchestrator must sync local main to the source-of-truth at the start of every run,
        # so a run never branches from a stale base (the cause of the PR-conflict escalation).
        from polis.models import FeedbackItem
        fm = FakeMerger()
        h = Harness(sandbox=ScriptedSandbox([passing()]), merger=fm)
        h.orch.process(FeedbackItem(text="add a feature"))
        self.assertEqual(fm.synced, [h.workspace])  # synced the run's workspace...
        self.assertEqual(fm.order[0], "sync")       # ...and did so BEFORE the merge
        self.assertIn("merge", fm.order)


if __name__ == "__main__":
    unittest.main()
