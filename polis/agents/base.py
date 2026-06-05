"""Agent interfaces — one base class plus a typed contract per branch.

Key invariant: **an Architect holds no reference to a Reviewer, and vice versa.**
The branches never talk to each other directly; only the orchestrator wires inputs
and outputs between them. This is review independence enforced in the type design,
not just by convention.
"""

from __future__ import annotations

from ..models import Branch, Diff, FeedbackItem, Lesson, PRD, TestResult, Verdict


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
        cwd: str | None = None,
        lessons: list[str] | None = None,
    ) -> PRD:  # pragma: no cover - interface
        # `cwd` (when set) is the repository; a grounded architect Reads it + the tester
        # screenshot to write a code-grounded PRD. Plain architects ignore it.
        # `lessons` (when set) are advisory precedents from past runs (self-learning).
        raise NotImplementedError

    def vote(self, proposals: list[PRD]) -> int:
        """Pick the index of the best proposal among competing PRDs (panel mode)."""
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
        lessons: list[str] | None = None,
    ) -> Diff:  # pragma: no cover - interface
        # `lessons` (when set) are advisory precedents from past runs (self-learning).
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
        cwd: str | None = None,
        lessons: list[str] | None = None,
    ) -> Verdict:  # pragma: no cover - interface
        # `cwd` (when set) is the post-change repository; a grounded reviewer may Read/Grep it
        # to verify criteria a diff cannot show. Plain reviewers ignore it.
        # `lessons` (when set) are advisory precedents from past runs (self-learning).
        raise NotImplementedError


class ConstitutionalJudge(Agent):
    """Judicial review of the LAW (a PRD) against the constitution, BEFORE any code is
    written. ``approved`` means 'constitutional'. Independent — orchestrator-invoked.
    """

    def review_prd(self, prd: PRD, constitution) -> Verdict:  # pragma: no cover
        raise NotImplementedError


class Reflector(Agent):
    """Procedural archivist: distills ONE transferable :class:`Lesson` from a finished run.

    Reads ONLY the run's artifacts (PRD text, the final verdict feedback, the test summary,
    the terminal outcome/reason/attempts) — never the live architect/dev/reviewer and never
    the repo — so branch independence holds. Classification (polarity/scope/discipline) is
    decided deterministically by the procedure and passed IN; the model only writes the
    ``trigger`` + ``guidance``, keeping policy out of the prompt.
    """

    def reflect(
        self,
        *,
        prd_markdown: str,
        verdict_feedback: str,
        test_summary: str,
        outcome: str,
        reason: str,
        attempts: int,
        polarity: str,
        scope: str,
        discipline: str | None,
    ) -> Lesson:  # pragma: no cover - interface
        raise NotImplementedError
