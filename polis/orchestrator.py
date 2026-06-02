"""The Procedure — the deterministic orchestrator.

This is the "rule of law": ordinary Python (not an LLM) walking a fixed state
machine. Non-deterministic officials act *within* a predictable process.

    INTAKE → SPEC → IMPLEMENT → VERIFY → REVIEW → (MERGE | REVISE→IMPLEMENT) → DONE | ESCALATE

Guarantees enforced here, by construction:
  * Review independence — the orchestrator is the *only* caller of the reviewer.
    The architect is never handed the reviewer (or vice versa); branches exchange
    only artifacts (PRD, diff, verdict), routed through this code.
  * Budget safety — affordability is checked before every paid agent call; if the
    treasury (or the per-task cap) cannot cover it, the run ESCALATES cleanly.
  * Termination — the revise loop is bounded by ``max_revisions``; combined with the
    budget guard, the procedure cannot loop forever.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constitution import Constitution
from .llm import LLMError
from .models import Branch, FeedbackItem, RunResult, Stage, gen_id
from .record import Record
from .registry import Registry
from .sandbox import Sandbox
from .state import RunStore
from .treasury import Treasury
from .workspace import Workspace


@dataclass
class OrchestratorConfig:
    max_revisions: int = 2          # attempts = 1 initial + up to max_revisions
    per_task_cap: float | None = None
    spawn_cost: float = 0.0         # cost to staff one official (a spawn debits too)


class Orchestrator:
    def __init__(
        self,
        *,
        registry: Registry,
        treasury: Treasury,
        record: Record,
        constitution: Constitution,
        workspace: Workspace,
        sandbox: Sandbox,
        run_store: RunStore | None = None,
        config: OrchestratorConfig | None = None,
    ):
        self.registry = registry
        self.treasury = treasury
        self.record = record
        self.constitution = constitution
        self.workspace = workspace
        self.sandbox = sandbox
        self.run_store = run_store
        self.config = config or OrchestratorConfig()

    # --- budget guard ---------------------------------------------------
    def _afford(self, cost: float, run_id: str) -> bool:
        if not self.treasury.can_afford(cost):
            return False
        cap = self.config.per_task_cap
        if cap is not None and self.treasury.spent_on(run_id) + cost > cap:
            return False
        return True

    # --- main entry -----------------------------------------------------
    def process(self, feedback: FeedbackItem) -> RunResult:
        run_id = gen_id("run")
        cfg = self.config

        def rec(stage, actor, kind, *, source=Branch.PROCEDURE, cost=0.0, **payload):
            self.record.append(run_id=run_id, stage=stage, actor=actor, kind=kind,
                               source=source, cost=cost, **payload)

        # Mutable run state
        prd = diff = test_result = verdict = None
        merge_commit = None
        attempt = 0

        def result(outcome: Stage, last_stage: Stage, reason: str = "") -> RunResult:
            res = RunResult(
                run_id=run_id, outcome=outcome, last_stage=last_stage, reason=reason,
                attempts=attempt, spend=self.treasury.spent_on(run_id),
                prd=prd, verdict=verdict, test_result=test_result, merge_commit=merge_commit,
            )
            if outcome == Stage.ESCALATE:
                rec(last_stage, "procedure", "escalate", reason=reason)
            if self.run_store:
                self.run_store.save(res, feedback)
            return res

        # Staff the three branches. The orchestrator alone holds all three handles.
        architect = self.registry.spawn("architect")
        dev = self.registry.spawn("dev")
        reviewer = self.registry.spawn("reviewer")
        rec(Stage.INTAKE, "procedure", "intake",
            feedback_id=feedback.id, text=feedback.text)
        for agent in (architect, dev, reviewer):
            rec(Stage.INTAKE, "procedure", "spawn", role=agent.role,
                instance=agent.instance_id, branch_of=agent.branch.value)

        try:
            # Optional staffing cost (a spawn debits the treasury too).
            if cfg.spawn_cost > 0:
                need = cfg.spawn_cost * 3
                if not self._afford(need, run_id):
                    return result(Stage.ESCALATE, Stage.INTAKE,
                                  "budget_exhausted: cannot staff the government")
                for a in (architect, dev, reviewer):
                    self.treasury.debit(f"{a.branch.value}:{a.role}", cfg.spawn_cost,
                                        "spawn", run_id)

            # --- SPEC (legislative) ---
            if not self._afford(architect.cost, run_id):
                return result(Stage.ESCALATE, Stage.SPEC,
                              "budget_exhausted: cannot fund SPEC")
            try:
                prd = architect.write_prd(feedback)
            except LLMError as e:
                return result(Stage.ESCALATE, Stage.SPEC, f"llm_error: {e}")
            self.treasury.debit("legislative:architect", architect.last_cost, "write_prd", run_id)
            rec(Stage.SPEC, "legislative:architect", "prd", cost=architect.last_cost,
                prd_id=prd.id, title=prd.title, revision=prd.revision)

            # --- IMPLEMENT → VERIFY → REVIEW (bounded revise loop) ---
            while True:
                # IMPLEMENT (executive)
                if not self._afford(dev.cost, run_id):
                    return result(Stage.ESCALATE, Stage.IMPLEMENT,
                                  "budget_exhausted: cannot fund IMPLEMENT")
                branch = f"polis/{run_id}/attempt-{attempt}"
                self.workspace.start_change(branch)
                try:
                    diff = dev.implement(
                        prd, attempt=attempt,
                        review_feedback=(verdict.feedback if verdict else ""),
                        directives=feedback.directives,
                        workspace=self.workspace,
                    )
                except LLMError as e:
                    self.workspace.discard()
                    return result(Stage.ESCALATE, Stage.IMPLEMENT, f"llm_error: {e}")
                self.workspace.apply(diff)
                self.treasury.debit("executive:dev", dev.last_cost, "implement", run_id)
                rec(Stage.IMPLEMENT, "executive:dev", "diff", cost=dev.last_cost, attempt=attempt,
                    branch=branch, files=[c.path for c in diff.changes], summary=diff.summary)

                # VERIFY (procedure runs the sandbox; no LLM cost)
                test_result = self.sandbox.run_tests(self.workspace)
                rec(Stage.VERIFY, "procedure", "test_result", attempt=attempt,
                    passed=test_result.passed, summary=test_result.summary)

                # REVIEW (judicial) — invoked ONLY here, by the procedure
                if not self._afford(reviewer.cost, run_id):
                    self.workspace.discard()
                    return result(Stage.ESCALATE, Stage.REVIEW,
                                  "budget_exhausted: cannot fund REVIEW")
                try:
                    verdict = reviewer.review(prd, diff, test_result, self.constitution)
                except LLMError as e:
                    self.workspace.discard()
                    return result(Stage.ESCALATE, Stage.REVIEW, f"llm_error: {e}")
                self.treasury.debit("judicial:reviewer", reviewer.last_cost, "review", run_id)
                rec(Stage.REVIEW, "judicial:reviewer", "verdict", cost=reviewer.last_cost,
                    source=Branch.PROCEDURE, approved=verdict.approved,
                    reasons=verdict.reasons,
                    violations=[v.rule_id for v in verdict.violations])

                # DECISION
                if verdict.approved and test_result.passed:
                    merge_commit = self.workspace.merge(
                        f"Polis: merge {prd.id} (run {run_id}, attempt {attempt})")
                    rec(Stage.MERGE, "procedure", "merge", prd_id=prd.id, commit=merge_commit)
                    rec(Stage.DONE, "procedure", "done", commit=merge_commit)
                    return result(Stage.DONE, Stage.DONE, "merged")

                # Rejected: discard the attempt and maybe revise.
                self.workspace.discard()
                attempt += 1
                if attempt > cfg.max_revisions:
                    return result(Stage.ESCALATE, Stage.REVISE,
                                  f"revisions_exhausted after {cfg.max_revisions} "
                                  f"revisions; last verdict: {verdict.feedback}")
                rec(Stage.REVISE, "procedure", "revise", attempt=attempt,
                    feedback=verdict.feedback)
                # loop back to IMPLEMENT with the reviewer's feedback in hand
        finally:
            for agent in (reviewer, dev, architect):
                self.registry.release(agent)
                rec(Stage.DONE, "procedure", "release",
                    role=agent.role, instance=agent.instance_id)
