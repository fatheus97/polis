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
from .projectcfg import (resolve_dev_timeout, resolve_grounded_agents, resolve_main_branch,
                         resolve_merge_via_pr, resolve_model_tier_overrides,
                         resolve_testing_mode, resolve_workspace)
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
        # PR-based merge can't run across worktrees (git won't check out main in two places,
        # so the merger's sync step would fail and every run would escalate). Fall back to a
        # local merge for the parallel batch instead of failing silently.
        from .merger import LocalMerger, PullRequestMerger
        if isinstance(self.orchestrator.merger, PullRequestMerger):
            import sys
            print("[polis] merge_via_pr is not supported with --parallel; using a local merge "
                  "for this batch.", file=sys.stderr)
            self.orchestrator.merger = LocalMerger()
        # Honor the configured target repo (not the hardcoded default) so parallel
        # runs develop the same repo as sequential ones.
        manager = WorktreeManager(self.workspace.path,
                                  work_root=self.base / "worktrees",
                                  main_branch=getattr(self.workspace, "main_branch", "main"))

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

    def close(self) -> None:
        """Close the SQLite-backed stores. Important on Windows, where unclosed
        handles can block deleting temp workspaces."""
        for store in (self.treasury, self.run_store, self.inbox):
            try:
                store.close()
            except Exception:
                pass


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
        # No explicit tier (e.g. dashboard runs) => take per-role models from config.
        tier = tier or ModelTier(**resolve_model_tier_overrides(base))
        registry = Registry.real(backend, tier, testing_mode=resolve_testing_mode(base),
                                 dev_timeout=resolve_dev_timeout(base),
                                 grounded=resolve_grounded_agents(base))
    else:
        registry = Registry.default()
    run_store = RunStore(base / "runs.sqlite")
    inbox = FeedbackInbox(base / "feedback.sqlite")
    if workspace is None:
        ws_path = resolve_workspace(base, workspace_dir)   # override > config > default
        # Don't silently git-init a user's existing non-git directory (footgun).
        is_default = ws_path == (base / "workspace").resolve()
        if (not is_default and ws_path.exists() and any(ws_path.iterdir())
                and not (ws_path / ".git").exists()):
            raise ValueError(f"target repo exists but is not a git repository "
                             f"(refusing to git-init it): {ws_path}")
        workspace = GitWorkspace(ws_path, main_branch=resolve_main_branch(base))
    # `protect-core` is a path-rule matched against the TARGET repo's relative paths, so it
    # only makes sense when Polis develops its OWN repo. Drop it for any other workspace so a
    # user app that merely has a polis/ directory isn't falsely blocked.
    polis_root = Path(__file__).resolve().parent.parent
    if Path(getattr(workspace, "path", "") or ".").resolve() != polis_root:
        constitution.rules = [r for r in constitution.rules if r.id != "protect-core"]
    sandbox = sandbox or LocalSandbox()
    config = config or OrchestratorConfig()
    # PR-based merge (opt-in): land changes via a CI-gated GitHub PR instead of a local merge
    # into main. Default stays LocalMerger, so existing runs are unchanged.
    if config.merger is None and resolve_merge_via_pr(base):
        from .merger import PullRequestMerger
        config.merger = PullRequestMerger(main_branch=resolve_main_branch(base))
    orchestrator = Orchestrator(
        registry=registry, treasury=treasury, record=record, constitution=constitution,
        workspace=workspace, sandbox=sandbox, run_store=run_store,
        config=config,
    )
    return Government(
        base=base, treasury=treasury, record=record, constitution=constitution,
        registry=registry, workspace=workspace, sandbox=sandbox, run_store=run_store,
        inbox=inbox, orchestrator=orchestrator,
    )
