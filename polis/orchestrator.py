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

import subprocess
from dataclasses import dataclass

from .constitution import Constitution
from .lessons import LessonStore
from .llm import LLMError
from .models import Branch, FeedbackItem, RunResult, Stage, gen_id
from .record import Record
from .registry import Registry
from .sandbox import Sandbox
from .state import RunStore
from .treasury import Treasury
from .workspace import MergeConflict, Workspace


@dataclass
class OrchestratorConfig:
    max_revisions: int = 2          # attempts = 1 initial + up to max_revisions
    per_task_cap: float | None = None
    spawn_cost: float = 0.0         # cost to staff one official (a spawn debits too)
    constitutional_review: bool = False  # vet the PRD against the constitution first
    max_prd_revisions: int = 1      # bounded amend loop when a PRD is unconstitutional
    num_architects: int = 1         # >1 convenes a panel that proposes + votes on the PRD
    deploy_command: str | None = None  # optional shell hook run after a successful merge
    merger: "Merger | None" = None  # how a branch reaches main (default: LocalMerger)


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
        lesson_store: LessonStore | None = None,
        config: OrchestratorConfig | None = None,
    ):
        self.registry = registry
        self.treasury = treasury
        self.record = record
        self.constitution = constitution
        self.workspace = workspace
        self.sandbox = sandbox
        self.run_store = run_store
        # Optional self-learning store. None => retrieval/injection are no-ops (the default),
        # so behavior is byte-for-byte unchanged when self-learning is off.
        self.lesson_store = lesson_store
        self.config = config or OrchestratorConfig()
        # Merge strategy: local git merge by default; a PullRequestMerger turns merges into
        # CI-gated GitHub PRs (never touches local main directly).
        from .merger import LocalMerger
        self.merger = self.config.merger or LocalMerger()

    # --- budget guard ---------------------------------------------------
    def _afford(self, cost: float, run_id: str) -> bool:
        if not self.treasury.can_afford(cost):
            return False
        cap = self.config.per_task_cap
        if cap is not None and self.treasury.spent_on(run_id) + cost > cap:
            return False
        return True

    # --- self-learning (retrieval + injection) --------------------------
    def _retrieve_lessons(self, *, scope, discipline, query, k: int = 3):
        """PURE read: top-k advisory lessons as ``(guidance_list, ids)``. No side effects —
        usage is only counted once an agent actually consumes them (see ``_note_injected``),
        so a run that escalates at a budget gate before the agent runs doesn't inflate the
        ``uses`` denominator that the decay/lift logic depends on."""
        if self.lesson_store is None:
            return None, []
        hits = self.lesson_store.retrieve(query, scope=scope, discipline=discipline, k=k)
        if not hits:
            return None, []
        return [h.guidance for h in hits], [h.id for h in hits]

    def _note_injected(self, ids, rec, *, stage, scope, discipline, injected) -> None:
        """Record that these lessons ACTUALLY reached an agent: bump ``uses`` (the lift
        denominator), remember them for a merge credit, and emit the audit event. Idempotent
        per run via the ``injected`` set, so the dev/reviewer revise loop counts one use, not
        one-per-attempt."""
        fresh = [i for i in ids if i not in injected]
        if not fresh:
            return
        self.lesson_store.mark_used(fresh)
        injected.update(fresh)
        rec(stage, "procedure", "lessons_injected", scope=scope,
            discipline=discipline, lesson_ids=fresh)

    def _deploy(self, command: str, ws, rec) -> None:
        cwd = ws.deploy_dir() if hasattr(ws, "deploy_dir") else getattr(ws, "path", ".")
        try:
            proc = subprocess.run(command, shell=True, cwd=str(cwd),
                                  capture_output=True, text=True, timeout=600)
            ok = proc.returncode == 0
            output = ((proc.stdout or "") + (proc.stderr or ""))[-500:]
        except Exception as e:  # never let a deploy failure crash the run
            ok, output = False, str(e)[:500]
        rec(Stage.DONE, "procedure", "deploy", ok=ok, command=command, output=output)

    # --- main entry -----------------------------------------------------
    def process(self, feedback: FeedbackItem, workspace=None) -> RunResult:
        run_id = gen_id("run")
        cfg = self.config
        ws = workspace or self.workspace  # per-run workspace (a worktree) when parallel

        def rec(stage, actor, kind, *, source=Branch.PROCEDURE, cost=0.0, **payload):
            self.record.append(run_id=run_id, stage=stage, actor=actor, kind=kind,
                               source=source, cost=cost, **payload)

        # Mutable run state
        prd = diff = test_result = verdict = None
        merge_commit = None
        attempt = 0
        injected_ids: set[str] = set()   # self-learning: lessons fed into this run (credited on merge)

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

        # Staff the branches. The orchestrator alone holds the handles. The dev is
        # HIRED later, once the PRD names the discipline it needs (Phase 2).
        architect = self.registry.spawn("architect")
        reviewer = self.registry.spawn("reviewer")
        dev = None
        rec(Stage.INTAKE, "procedure", "intake",
            feedback_id=feedback.id, text=feedback.text)
        for agent in (architect, reviewer):
            rec(Stage.INTAKE, "procedure", "spawn", role=agent.role,
                instance=agent.instance_id, branch_of=agent.branch.value)

        try:
            # Branch every run from the MERGED tip: bring local main up to the source-of-truth
            # before SPEC, so the grounded architect reads current code and start_change can't build
            # on a stale base (which would conflict with an already-merged sibling run). No-op for a
            # local merge; best-effort fetch+reset for a PR merge.
            self.merger.sync_main(ws)

            # Optional staffing cost (a spawn debits the treasury too).
            if cfg.spawn_cost > 0:
                need = cfg.spawn_cost * 3
                if not self._afford(need, run_id):
                    return result(Stage.ESCALATE, Stage.INTAKE,
                                  "budget_exhausted: cannot staff the government")
                for a in (architect, dev, reviewer):
                    self.treasury.debit(f"{a.branch.value}:{a.role}", cfg.spawn_cost,
                                        "spawn", run_id)

            # Self-learning: architect precedents depend only on the feedback (no PRD yet).
            # Retrieval is a pure read here; usage is counted only once the architect runs.
            arch_lessons, arch_ids = self._retrieve_lessons(
                scope="architect", discipline=None, query=feedback.text)

            # --- SPEC (legislative): one architect, or a panel that proposes + votes ---
            if cfg.num_architects <= 1:
                if not self._afford(architect.cost, run_id):
                    return result(Stage.ESCALATE, Stage.SPEC,
                                  "budget_exhausted: cannot fund SPEC")
                try:
                    prd = architect.write_prd(feedback, cwd=str(ws.path), lessons=arch_lessons)
                except LLMError as e:
                    return result(Stage.ESCALATE, Stage.SPEC, f"llm_error: {e}")
                self.treasury.debit("legislative:architect", architect.last_cost,
                                    "write_prd", run_id)
                self._note_injected(arch_ids, rec, stage=Stage.SPEC, scope="architect",
                                    discipline=None, injected=injected_ids)
                rec(Stage.SPEC, "legislative:architect", "prd", cost=architect.last_cost,
                    prd_id=prd.id, title=prd.title, revision=prd.revision)
            else:
                # The standing architect is member 0; convene the rest of the panel.
                panel, extras = [architect], []
                for _ in range(cfg.num_architects - 1):
                    a = self.registry.spawn("architect")
                    panel.append(a)
                    extras.append(a)
                    rec(Stage.SPEC, "procedure", "spawn", role=a.role,
                        instance=a.instance_id, branch_of=a.branch.value)
                try:
                    proposals = []
                    for i, m in enumerate(panel):
                        if not self._afford(m.cost, run_id):
                            return result(Stage.ESCALATE, Stage.SPEC,
                                          "budget_exhausted: cannot fund proposals")
                        try:
                            p = m.write_prd(feedback, cwd=str(ws.path), lessons=arch_lessons)
                        except LLMError as e:
                            return result(Stage.ESCALATE, Stage.SPEC, f"llm_error: {e}")
                        self.treasury.debit("legislative:architect", m.last_cost,
                                            "propose", run_id)
                        # Count the architect lessons used once a panel member actually ran.
                        self._note_injected(arch_ids, rec, stage=Stage.SPEC, scope="architect",
                                            discipline=None, injected=injected_ids)
                        proposals.append(p)
                        rec(Stage.SPEC, "legislative:architect", "proposal",
                            cost=m.last_cost, index=i, prd_id=p.id, title=p.title)
                    tally = [0] * len(proposals)
                    for i, m in enumerate(panel):
                        if not self._afford(m.cost, run_id):
                            return result(Stage.ESCALATE, Stage.SPEC,
                                          "budget_exhausted: cannot fund vote")
                        try:
                            choice = m.vote(proposals)
                        except LLMError as e:
                            return result(Stage.ESCALATE, Stage.SPEC, f"llm_error: {e}")
                        self.treasury.debit("legislative:architect", m.last_cost,
                                            "vote", run_id)
                        choice = choice if 0 <= choice < len(proposals) else 0
                        tally[choice] += 1
                        rec(Stage.SPEC, "legislative:architect", "vote",
                            cost=m.last_cost, voter=i, choice=choice)
                    winner = max(range(len(tally)), key=lambda k: (tally[k], -k))
                    prd = proposals[winner]
                    rec(Stage.SPEC, "procedure", "elected", prd_id=prd.id,
                        winner=winner, tally=tally)
                finally:
                    for a in extras:
                        self.registry.release(a)
                        rec(Stage.DONE, "procedure", "release", role=a.role,
                            instance=a.instance_id)

            # --- CONSTITUTIONAL review of the PRD (opt-in) ---
            # A judge reviews the LAW itself; an unconstitutional PRD is sent back to
            # the architect to amend (bounded), and ESCALATES if it can't be fixed.
            if cfg.constitutional_review:
                judge = self.registry.spawn("constitutional-judge")
                try:
                    prd_rev = 0
                    while True:
                        if not self._afford(judge.cost, run_id):
                            return result(Stage.ESCALATE, Stage.CONSTITUTIONAL,
                                          "budget_exhausted: cannot fund constitutional review")
                        try:
                            ruling = judge.review_prd(prd, self.constitution)
                        except LLMError as e:
                            return result(Stage.ESCALATE, Stage.CONSTITUTIONAL, f"llm_error: {e}")
                        self.treasury.debit("judicial:constitutional-judge",
                                            judge.last_cost, "review_prd", run_id)
                        rec(Stage.CONSTITUTIONAL, "judicial:constitutional-judge", "ruling",
                            cost=judge.last_cost, source=Branch.PROCEDURE,
                            constitutional=ruling.approved, reasons=ruling.reasons)
                        if ruling.approved:
                            break
                        prd_rev += 1
                        if prd_rev > cfg.max_prd_revisions:
                            return result(Stage.ESCALATE, Stage.CONSTITUTIONAL,
                                          f"unconstitutional_prd: {ruling.feedback}")
                        if not self._afford(architect.cost, run_id):
                            return result(Stage.ESCALATE, Stage.CONSTITUTIONAL,
                                          "budget_exhausted: cannot fund PRD amendment")
                        try:
                            prd = architect.write_prd(feedback, prior=prd,
                                                      review_feedback=ruling.feedback,
                                                      cwd=str(ws.path), lessons=arch_lessons)
                        except LLMError as e:
                            return result(Stage.ESCALATE, Stage.CONSTITUTIONAL, f"llm_error: {e}")
                        self.treasury.debit("legislative:architect", architect.last_cost,
                                            "amend_prd", run_id)
                        rec(Stage.CONSTITUTIONAL, "legislative:architect", "amend",
                            cost=architect.last_cost, prd_id=prd.id, revision=prd.revision)
                finally:
                    self.registry.release(judge)
                    rec(Stage.DONE, "procedure", "release",
                        role=judge.role, instance=judge.instance_id)

            # Hire the dev best suited to the PRD's discipline (generalist if none).
            dev = self.registry.hire_dev(prd.discipline)
            rec(Stage.IMPLEMENT, "procedure", "hire", role=dev.role,
                discipline=prd.discipline or "generalist", instance=dev.instance_id)

            # Self-learning: dev + reviewer precedents now that the PRD names the discipline.
            # Fetched ONCE (pure read; not per revision) to bound cost/noise; the same advisory
            # list rides every attempt/review. Usage is counted only when each agent first runs.
            lesson_query = f"{feedback.text} {prd.title}"
            dev_lessons, dev_ids = self._retrieve_lessons(
                scope="dev", discipline=prd.discipline, query=lesson_query)
            rev_lessons, rev_ids = self._retrieve_lessons(
                scope="reviewer", discipline=prd.discipline, query=lesson_query)

            # --- IMPLEMENT → VERIFY → REVIEW (bounded revise loop) ---
            while True:
                # IMPLEMENT (executive)
                if not self._afford(dev.cost, run_id):
                    return result(Stage.ESCALATE, Stage.IMPLEMENT,
                                  "budget_exhausted: cannot fund IMPLEMENT")
                branch = f"polis/{run_id}/attempt-{attempt}"
                ws.start_change(branch)
                try:
                    diff = dev.implement(
                        prd, attempt=attempt,
                        review_feedback=(verdict.feedback if verdict else ""),
                        directives=feedback.directives,
                        workspace=ws,
                        lessons=dev_lessons,
                    )
                except LLMError as e:
                    # Charge any spend that already happened (e.g. a completed plan pass) before
                    # bailing — a failing execute must not hide the plan's real cost.
                    if dev.last_cost:
                        self.treasury.debit("executive:dev", dev.last_cost, "implement", run_id)
                    ws.discard()
                    return result(Stage.ESCALATE, Stage.IMPLEMENT, f"llm_error: {e}")
                ws.apply(diff)
                self.treasury.debit("executive:dev", dev.last_cost, "implement", run_id)
                self._note_injected(dev_ids, rec, stage=Stage.IMPLEMENT, scope="dev",
                                    discipline=prd.discipline, injected=injected_ids)
                rec(Stage.IMPLEMENT, "executive:dev", "diff", cost=dev.last_cost, attempt=attempt,
                    branch=branch, files=[c.path for c in diff.changes], summary=diff.summary)

                # VERIFY (procedure runs the sandbox; no LLM cost)
                test_result = self.sandbox.run_tests(ws)
                rec(Stage.VERIFY, "procedure", "test_result", attempt=attempt,
                    passed=test_result.passed, summary=test_result.summary,
                    details=(test_result.details or "")[-2000:])

                # REVIEW (judicial) — invoked ONLY here, by the procedure
                if not self._afford(reviewer.cost, run_id):
                    ws.discard()
                    return result(Stage.ESCALATE, Stage.REVIEW,
                                  "budget_exhausted: cannot fund REVIEW")
                try:
                    # Hand over the post-change working tree (the feature branch with the diff
                    # applied); a grounded reviewer reads it to verify, a plain one ignores it.
                    verdict = reviewer.review(prd, diff, test_result, self.constitution,
                                              cwd=str(ws.path), lessons=rev_lessons)
                except LLMError as e:
                    ws.discard()  # the per-run worktree, not self.workspace (parallel-safe)
                    return result(Stage.ESCALATE, Stage.REVIEW, f"llm_error: {e}")
                self.treasury.debit("judicial:reviewer", reviewer.last_cost, "review", run_id)
                self._note_injected(rev_ids, rec, stage=Stage.REVIEW, scope="reviewer",
                                    discipline=prd.discipline, injected=injected_ids)
                rec(Stage.REVIEW, "judicial:reviewer", "verdict", cost=reviewer.last_cost,
                    source=Branch.PROCEDURE, approved=verdict.approved,
                    reasons=verdict.reasons,
                    violations=[v.rule_id for v in verdict.violations])

                # DECISION
                if verdict.approved and test_result.passed:
                    # A descriptive title + body so the merge commit / PR reads as version
                    # history (not "Polis: merge prd-xxxx"). The merger uses line 1 as the title.
                    crit = "\n".join(f"- {c}" for c in (prd.acceptance_criteria or [])[:6])
                    message = (
                        f"{prd.title or prd.id}\n\n{(prd.goal or '').strip()}\n\n"
                        + (f"Acceptance criteria:\n{crit}\n\n" if crit else "")
                        + f"— PRD {prd.id} · run {run_id} · attempt {attempt} (opened by Polis)")
                    try:
                        merge_commit = self.merger.merge(ws, branch, message)
                    except MergeConflict as e:
                        ws.discard()
                        return result(Stage.ESCALATE, Stage.MERGE, f"merge_conflict: {e}")
                    rec(Stage.MERGE, "procedure", "merge", prd_id=prd.id, commit=merge_commit)
                    # Optional deploy hook — runs against merged main; a failure is
                    # recorded but never un-does the merge that already landed.
                    if cfg.deploy_command:
                        self._deploy(cfg.deploy_command, ws, rec)
                    # Self-learning: a merge credits every lesson injected into this run
                    # (the 'wins' numerator for measuring whether precedents actually help).
                    # KNOWN SIMPLIFICATION: architect/dev/reviewer wins are pooled, so an
                    # architect lesson shares credit with a dev success in the same run. We
                    # accept this — per-scope attribution isn't worth the bookkeeping yet.
                    if self.lesson_store is not None and injected_ids:
                        self.lesson_store.mark_won(list(injected_ids))
                    rec(Stage.DONE, "procedure", "done", commit=merge_commit)
                    return result(Stage.DONE, Stage.DONE, "merged")

                # Rejected: discard the attempt and maybe revise.
                ws.discard()
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
                if agent is None:
                    continue
                self.registry.release(agent)
                rec(Stage.DONE, "procedure", "release",
                    role=agent.role, instance=agent.instance_id)
