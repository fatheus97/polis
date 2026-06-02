"""Assembly — wire the whole government from persistent state on disk.

One place that constructs Treasury + Record + Constitution + Registry + Workspace +
Sandbox + RunStore + Inbox + Orchestrator, so the CLI and the integration test share
identical wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .constitution import Constitution, DEFAULT_PATH
from .feedback import FeedbackInbox
from .llm import ClaudeCliBackend, LLMBackend
from .orchestrator import Orchestrator, OrchestratorConfig
from .record import Record
from .registry import ModelTier, Registry
from .sandbox import LocalSandbox, Sandbox
from .state import RunStore
from .treasury import Treasury
from .workspace import GitWorkspace, Workspace


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
