"""Test doubles + a small harness for driving the orchestrator hermetically.

Unit tests use a FakeWorkspace (records calls, no git) and a ScriptedSandbox
(returns preconfigured TestResults), so each procedure path can be exercised fast
and deterministically. The integration test uses the *real* GitWorkspace +
LocalSandbox instead.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from polis.constitution import Constitution
from polis.models import TestResult
from polis.orchestrator import Orchestrator, OrchestratorConfig
from polis.record import Record
from polis.registry import Registry
from polis.sandbox import Sandbox
from polis.state import RunStore
from polis.treasury import Treasury


class FakeWorkspace:
    def __init__(self):
        self.path = "<fake-workspace>"
        self.started: list[str] = []
        self.applied: list = []
        self.merges: list = []
        self.discards = 0
        self.current = None
        self.fake_changes: list = []  # what changed_files() reports (a real dev's edits)

    def changed_files(self):
        return list(self.fake_changes)

    def deploy_dir(self):
        return "."  # a valid cwd so a deploy hook can run in tests

    def start_change(self, branch):
        self.current = branch
        self.started.append(branch)

    def apply(self, diff):
        self.applied.append((self.current, diff))

    def merge(self, message):
        sha = f"{len(self.merges):04d}deadbeefcafef00d"
        self.merges.append((message, sha))
        self.current = None
        return sha

    def discard(self):
        self.discards += 1
        self.current = None


class FakeMerger:
    """Records merge() calls and returns a deterministic sha. No git/gh — for exercising
    the orchestrator's merger seam hermetically."""

    def __init__(self):
        self.calls: list = []  # (branch, message)

    def merge(self, workspace, branch, message):
        sha = f"{len(self.calls):04d}deadbeefcafef00d"
        self.calls.append((branch, message))
        return sha


class ScriptedSandbox(Sandbox):
    """Returns the given TestResults in order; the last one repeats."""

    def __init__(self, results):
        self.results = list(results)
        self.i = 0

    def run_tests(self, workspace):
        r = self.results[min(self.i, len(self.results) - 1)]
        self.i += 1
        return r


def passing():
    return TestResult(ran=True, passed=True, summary="ok")


def failing():
    return TestResult(ran=True, passed=False, summary="nope")


class Harness:
    def __init__(self, *, budget=1000.0, sandbox=None, workspace=None,
                 max_revisions=2, per_task_cap=None, spawn_cost=0.0,
                 constitutional_review=False, max_prd_revisions=1, num_architects=1,
                 merger=None):
        self.tmp = Path(tempfile.mkdtemp(prefix="polis-test-"))
        self.treasury = Treasury(":memory:")
        if budget:
            self.treasury.appropriate(budget)
        self.record = Record(self.tmp / "record.jsonl")
        self.constitution = Constitution.load()
        self.registry = Registry.default()
        self.run_store = RunStore(":memory:")
        self.workspace = workspace or FakeWorkspace()
        self.sandbox = sandbox or ScriptedSandbox([passing()])
        self.orch = Orchestrator(
            registry=self.registry, treasury=self.treasury, record=self.record,
            constitution=self.constitution, workspace=self.workspace, sandbox=self.sandbox,
            run_store=self.run_store,
            config=OrchestratorConfig(max_revisions=max_revisions,
                                      per_task_cap=per_task_cap, spawn_cost=spawn_cost,
                                      constitutional_review=constitutional_review,
                                      max_prd_revisions=max_prd_revisions,
                                      num_architects=num_architects, merger=merger),
        )

    def events(self):
        return self.record.events()

    def kinds(self):
        return [e["kind"] for e in self.events()]
