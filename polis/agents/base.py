"""Agent interfaces — one base class plus a typed contract per branch.

Key invariant: **an Architect holds no reference to a Reviewer, and vice versa.**
The branches never talk to each other directly; only the orchestrator wires inputs
and outputs between them. This is review independence enforced in the type design,
not just by convention.
"""

from __future__ import annotations

from ..models import Branch, Diff, FeedbackItem, PRD, TestResult, Verdict


class Agent:
    """Common identity + a per-invocation cost (a token-spend proxy in Phase 0)."""

    branch: Branch

    def __init__(self, role: str, branch: Branch, cost: float = 10.0):
        self.role = role
        self.branch = branch
        # ``cost`` is the *estimate* used by the orchestrator's pre-call affordability
        # gate (real spend is unknown until the model responds). ``last_cost`` holds
        # the *actual* spend of the most recent call, which is what gets debited.
        # For deterministic stubs the two are equal.
        self.cost = cost
        self.last_cost = cost
        self.instance_id: str | None = None  # set by the Registry on spawn

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.role} cost={self.cost}>"


class Architect(Agent):
    """Legislative: turns feedback (and, on revision, review feedback) into a PRD."""

    def write_prd(
        self,
        feedback: FeedbackItem,
        repo_summary: str = "",
        prior: PRD | None = None,
        review_feedback: str = "",
    ) -> PRD:  # pragma: no cover - interface
        raise NotImplementedError


class Dev(Agent):
    """Executive: turns a PRD into a Diff. Real implementations wrap a coding agent."""

    def implement(
        self,
        prd: PRD,
        attempt: int = 0,
        review_feedback: str = "",
        directives: dict | None = None,
        workspace=None,
    ) -> Diff:  # pragma: no cover - interface
        raise NotImplementedError


class Reviewer(Agent):
    """Judicial: judges a Diff against the PRD, the test result, and the constitution.

    Note the absence of any architect/dev parameter — the reviewer is handed only
    artifacts, never the agents that produced them.
    """

    def review(
        self,
        prd: PRD,
        diff: Diff,
        test_result: TestResult,
        constitution,
    ) -> Verdict:  # pragma: no cover - interface
        raise NotImplementedError
