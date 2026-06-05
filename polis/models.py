"""Core data types shared across the branches of government.

These are plain dataclasses — the *nouns* of the system (feedback, PRDs, diffs,
verdicts) plus the ``Stage`` and ``Branch`` enums that name the procedure's states
and the actors that move through it.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum


def gen_id(prefix: str) -> str:
    """Short, readable, unique id (e.g. ``prd-3f9a1c2b``)."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Stage(str, Enum):
    """States of the deterministic procedure (the orchestrator's state machine)."""

    INTAKE = "INTAKE"
    SPEC = "SPEC"
    CONSTITUTIONAL = "CONSTITUTIONAL"  # judicial review of the PRD (the law) itself
    IMPLEMENT = "IMPLEMENT"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    MERGE = "MERGE"
    REVISE = "REVISE"
    DONE = "DONE"
    ESCALATE = "ESCALATE"


class Branch(str, Enum):
    """Who is acting. The procedure is neutral; the sovereign is the human."""

    LEGISLATIVE = "legislative"  # Architect — writes PRDs
    EXECUTIVE = "executive"      # Dev — implements
    JUDICIAL = "judicial"        # Reviewer — judges
    PROCEDURE = "procedure"      # the deterministic orchestrator
    SOVEREIGN = "sovereign"      # the human


@dataclass
class FeedbackItem:
    """A citizen petition: a tester's request that enters the legislative inbox."""

    text: str
    submitted_by: str = "tester"
    id: str = field(default_factory=lambda: gen_id("fb"))
    created_at: float = field(default_factory=time.time)
    # Free-form side-channel on a feedback item. Phase-0 scenario hook for deterministic stubs
    # (e.g. {"dev": "emit_secret"} or {"dev": "fail_n_times", "n": 1}). Well-known keys set by
    # the dashboard intake: "report_id", "source", "screenshot_path" (absolute path the grounded
    # architect Reads), "state_summary", "severity". Keep new producers/consumers in sync here.
    directives: dict = field(default_factory=dict)


@dataclass
class PRD:
    """A law: a structured spec the executive must implement and the judiciary checks against."""

    title: str
    goal: str
    acceptance_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    feedback_id: str = ""
    id: str = field(default_factory=lambda: gen_id("prd"))
    revision: int = 0
    # The engineering specialty best suited to implement this (Phase 2 hiring).
    # None => a generalist dev handles it.
    discipline: str | None = None

    def to_markdown(self) -> str:
        def bullets(items):
            return "\n".join(f"- {i}" for i in items) if items else "- (none)"

        return (
            f"# PRD {self.id} (rev {self.revision}): {self.title}\n\n"
            f"## Goal\n{self.goal}\n\n"
            f"## Acceptance criteria\n{bullets(self.acceptance_criteria)}\n\n"
            f"## Constraints\n{bullets(self.constraints)}\n\n"
            f"## Out of scope\n{bullets(self.out_of_scope)}\n"
        )


@dataclass
class FileChange:
    """A single file written in full. Phase 0 keeps diffs simple (whole-file writes)."""

    path: str
    content: str


@dataclass
class Diff:
    """The executive's output: a set of file changes plus a human summary."""

    changes: list[FileChange] = field(default_factory=list)
    summary: str = ""

    def is_empty(self) -> bool:
        return not self.changes


@dataclass
class TestResult:
    """Outcome of running the project's tests in the sandbox."""

    ran: bool
    passed: bool
    summary: str = ""
    details: str = ""


@dataclass
class Violation:
    """A constitution rule that a diff breached."""

    rule_id: str
    severity: str  # "block" | "warn"
    path: str
    snippet: str


@dataclass
class Verdict:
    """The judiciary's ruling on a diff."""

    approved: bool
    reasons: list[str] = field(default_factory=list)
    feedback: str = ""  # actionable guidance handed back to the dev on rejection
    violations: list[Violation] = field(default_factory=list)


@dataclass
class RunResult:
    """The terminal outcome of taking one feedback item through the procedure."""

    run_id: str
    outcome: Stage  # DONE or ESCALATE
    last_stage: Stage
    reason: str = ""
    attempts: int = 0
    spend: float = 0.0
    prd: PRD | None = None
    verdict: Verdict | None = None
    test_result: TestResult | None = None
    merge_commit: str | None = None

    @property
    def merged(self) -> bool:
        return self.outcome == Stage.DONE


@dataclass
class Lesson:
    """A distilled, transferable lesson from a finished run — the system's *case-law*.

    The Reflector ("Archivist") writes one after an eligible run; the orchestrator
    retrieves the relevant ones (lexical FTS5 over ``trigger``/``guidance``) and injects
    them as ADVISORY guidance into future agent prompts. The hard gates (tests-green +
    constitution scan) still override, so a bad lesson can never merge bad code.
    """

    trigger: str                       # the situation this lesson applies to (FTS-indexed)
    guidance: str                      # the transferable advice (FTS-indexed)
    scope: str = "dev"                 # architect | dev | reviewer — which official it informs
    discipline: str | None = None      # e.g. "backend"; None = generalist / any discipline
    polarity: str = "pitfall"          # pitfall | good_practice
    source_reason: str = ""            # the RunResult.reason that produced it
    run_id: str = ""
    id: str = field(default_factory=lambda: gen_id("lesson"))
    created_at: float = field(default_factory=time.time)
    uses: int = 0                      # times injected into a later run (lift measurement / decay)
    wins: int = 0                      # times injected into a later MERGED run (confidence)
    status: str = "active"             # active | retired | deleted


def to_jsonable(obj):
    """Best-effort conversion of our dataclasses/enums to JSON-serializable data."""
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj
