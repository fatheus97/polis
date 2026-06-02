"""Assembly — wire the whole government from persistent state on disk.

One place that constructs Treasury + Record + Constitution + Registry + Workspace +
Sandbox + RunStore + Inbox + Orchestrator, so the CLI and the integration test share
identical wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from .constitution import Constitution, DEFAULT_PATH
from .feedback import FeedbackInbox
from .llm import ClaudeCliBackend, LLMBackend
from .models import RunResult, Stage, gen_id
from .orchestrator import Orchestrator, OrchestratorConfig
from .record import Record
from .registry import ModelTier, Registry
from .sandbox import LocalSandbox, Sandbox
from .state import RunStore
from .treasury import Treasury
from .workspace import GitWorkspace, Workspace
from .worktree import WorktreeManager


@dataclass
class Government:
    base: Path
    treasury: Treasury
    record: Record
    constitution: Constitution
    registry: Registry
    workspace: Workspace
    sandbox: Sandbox
    run_store: RunStore
    inbox: FeedbackInbox
    orchestrator: Orchestrator

    def run_next(self):
        """Take the next pending feedback item through the procedure. Returns the
        RunResult, or None if the inbox is empty."""
        item = self.inbox.pop_next()
        if item is None:
            return None
        result = self.orchestrator.process(item)
        self.inbox.mark_processed(item.id, result.run_id)
        return result

    def run_parallel(self, items, max_workers: int = 4) -> list[RunResult]:
        """Run feedback items concurrently, each on its own git worktree. Shared state
        (treasury/record/registry/run-store) is thread-safe; merges are serialized.
        The inbox is touched only from this (main) thread, so it needs no locking."""
        if not items:
            return []
        manager = WorktreeManager(self.base / "workspace",
                                  work_root=self.base / "worktrees")

        def run_one(item) -> RunResult:
            ws = manager.create_worktree(item.id)
            try:
                return self.orchestrator.process(item, workspace=ws)
            except Exception as e:  # never let one run kill the batch
                return RunResult(run_id=gen_id("run"), outcome=Stage.ESCALATE,
                                 last_stage=Stage.INTAKE, reason=f"error: {e}")
            finally:
                manager.remove_worktree(ws)

        out: dict[str, RunResult] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(run_one, it): it for it in items}
            for fut, it in futures.items():
                out[it.id] = fut.result()
        for it in items:  # mark processed on the main thread (inbox isn't shared)
            self.inbox.mark_processed(it.id, out[it.id].run_id)
        return [out[it.id] for it in items]


def build_government(
    base_dir: str | Path = ".polis",
    *,
    workspace_dir: str | Path | None = None,
    constitution_path: str | Path = DEFAULT_PATH,
    config: OrchestratorConfig | None = None,
    workspace: Workspace | None = None,
    sandbox: Sandbox | None = None,
    agents: str = "stub",            # "stub" (Phase 0) | "real" (Phase 1, LLM-backed)
    backend: LLMBackend | None = None,
    tier: ModelTier | None = None,
) -> Government:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    treasury = Treasury(base / "treasury.sqlite")
    record = Record(base / "record.jsonl")
    constitution = Constitution.load(constitution_path)
    if agents == "real":
        backend = backend or ClaudeCliBackend()
        registry = Registry.real(backend, tier)
    else:
        registry = Registry.default()
    run_store = RunStore(base / "runs.sqlite")
    inbox = FeedbackInbox(base / "feedback.sqlite")
    workspace = workspace or GitWorkspace(workspace_dir or (base / "workspace"))
    sandbox = sandbox or LocalSandbox()
    orchestrator = Orchestrator(
        registry=registry, treasury=treasury, record=record, constitution=constitution,
        workspace=workspace, sandbox=sandbox, run_store=run_store,
        config=config or OrchestratorConfig(),
    )
    return Government(
        base=base, treasury=treasury, record=record, constitution=constitution,
        registry=registry, workspace=workspace, sandbox=sandbox, run_store=run_store,
        inbox=inbox, orchestrator=orchestrator,
    )
