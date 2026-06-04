"""Real, model-backed officials (Phase 1).

Same interfaces as the Phase-0 stubs, so the orchestrator is unchanged:
  * LLMArchitect  — feedback -> JSON PRD (read-only reasoning).
  * ClaudeCodeDev — wraps the Claude Code CLI to edit files in the workspace.
  * LLMReviewer   — PRD + diff + tests + constitution -> JSON verdict, with the
                    mechanical constitution scan and tests-green check as HARD GATES
                    that override a too-lenient model.

Each call records its actual USD cost in ``self.last_cost`` for the Treasury.
"""

from __future__ import annotations

from ..llm import LLMBackend, LLMError, extract_json
from ..models import Branch, Diff, PRD, Verdict
from .base import Architect, ConstitutionalJudge, Dev, Reviewer

# Architect/reviewer must not mutate anything — they reason over text only.
READONLY_ARGS = ["--disallowedTools", "Edit", "Write", "Bash", "NotebookEdit", "MultiEdit"]

ARCHITECT_SYSTEM = (
    "You are the Architect (legislative branch) of an autonomous software team. "
    "Turn a tester's feedback into a precise, MINIMAL PRD. "
    "The implementation ALWAYS ships with accompanying automated tests, so an "
    "acceptance criterion MUST require passing tests, and you must NEVER list tests, "
    "test files, or 'changes to other files' as out-of-scope or as a forbidden "
    "change. 'out_of_scope' is only for unrelated features/refactors; 'constraints' "
    "are about quality and behavior, never about which files may be touched. "
    "Also choose the single engineering specialty best suited to implement this in "
    "'discipline' — one of: frontend, backend, database, infra, devops, cli, prompt — "
    "or null if a generalist suffices. "
    "Output ONLY a JSON object with keys: title (string), goal (string), "
    "acceptance_criteria (array of strings), constraints (array of strings), "
    "out_of_scope (array of strings), discipline (string or null). No prose."
)

VOTE_SYSTEM = (
    "You are an Architect voting on competing PRD proposals for the SAME feedback. "
    "Pick the single strongest proposal on merit (clarity, completeness, minimality). "
    "Output ONLY a JSON object: {\"choice\": <0-based index>, \"reason\": <string>}. No prose."
)

DEV_SYSTEM = (
    "You are the Executive/Dev branch of an autonomous software team. Implement the "
    "PRD in the CURRENT working directory, which is a git repository. Make MINIMAL, "
    "correct changes — change only what the PRD requires, and do NOT add summary or "
    "scratch files (no IMPLEMENTATION_SUMMARY.md, no verify_*.py). Reuse the project's "
    "existing helpers and patterns instead of reinventing them. "
    "You MUST add or update automated tests named test_*.py that verify the feature and "
    "pass under `python -m unittest discover -p 'test_*.py'`. Tests MUST be hermetic and "
    "fast: NEVER start a live server, bind a real socket, sleep, or spawn a process that "
    "can hang — a hanging test fails the whole run (the suite is killed after a timeout). "
    "Mirror the existing tests (fakes/stubs, in-memory or temp state). If a test needs a "
    "git repo, create it with `git init -b main` (never assume the default branch name). "
    "Do NOT commit. Never hardcode secrets/credentials and never disable security. "
    "When finished, briefly summarize what you changed."
)

WIDGET_CRITERION = (
    " Since TESTING_MODE is enabled, also include an acceptance criterion: the web "
    "deliverable embeds the Polis feedback widget (a <script> to /static/feedback-widget.js "
    "that loads only under TESTING_MODE), verified by a test."
)
WIDGET_INSTRUCTION = (
    "\nTESTING_MODE is enabled: if this deliverable serves a web UI, the served HTML MUST "
    "include the Polis feedback widget via <script src=\"/static/feedback-widget.js\"> gated "
    "to load only under TESTING_MODE, and add a test asserting the widget is referenced."
)

REVIEWER_SYSTEM = (
    "You are the Judiciary/Reviewer branch. Judge whether a code change correctly and "
    "safely implements its PRD. Be skeptical and DEFAULT TO REJECTION WHEN UNCERTAIN. "
    "Output ONLY a JSON object with keys: approved (boolean), reasons (array of "
    "strings), feedback (string of concrete, actionable guidance for the dev if "
    "rejecting). No prose."
)

CONSTITUTIONAL_SYSTEM = (
    "You are the Constitutional Court. Review a PROPOSED PRD (a law) for compatibility "
    "with the constitution's invariants — BEFORE any code is written. Reject only a PRD "
    "that, if implemented as written, would REQUIRE violating an invariant (e.g. "
    "hardcoding secrets, disabling security). Ordinary feature PRDs are constitutional; "
    "default to approval unless there is a clear conflict. Output ONLY a JSON object with "
    "keys: constitutional (boolean), reasons (array of strings), feedback (string of "
    "concrete guidance for the architect if rejecting). No prose."
)


def _render_diff(diff: Diff, per_file: int = 1500, total: int = 8000) -> str:
    out, used = [], 0
    for ch in diff.changes:
        body = ch.content[:per_file]
        if len(ch.content) > per_file:
            body += "\n...[truncated]"
        block = f"--- {ch.path} ---\n{body}"
        if used + len(block) > total:
            out.append("...[more files omitted]")
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out) if out else "(no textual changes captured)"


class LLMArchitect(Architect):
    def __init__(self, backend: LLMBackend, model: str = "sonnet", cost_estimate: float = 0.40,
                 testing_mode: bool = False):
        super().__init__("architect", Branch.LEGISLATIVE, cost_estimate)
        self.backend = backend
        self.model = model
        self.testing_mode = testing_mode

    def write_prd(self, feedback, repo_summary="", prior=None, review_feedback=""):
        parts = [f"Tester feedback:\n{feedback.text}"]
        if repo_summary:
            parts.append(f"\nRepository summary:\n{repo_summary}")
        if prior is not None:
            parts.append(f"\nThis REVISES an existing PRD. Prior PRD:\n{prior.to_markdown()}")
            if review_feedback:
                parts.append(f"\nThe reviewer rejected the last attempt: {review_feedback}\n"
                             "Only change the spec if it was ambiguous or wrong; "
                             "otherwise keep it stable.")
        parts.append("\nReturn the PRD as JSON now.")
        system = ARCHITECT_SYSTEM + (WIDGET_CRITERION if self.testing_mode else "")
        resp = self.backend.complete("\n".join(parts), system=system,
                                     model=self.model, extra_args=READONLY_ARGS)
        self.last_cost = resp.cost_usd
        revision = (prior.revision + 1) if prior else 0
        try:
            d = extract_json(resp.text)
            return PRD(
                title=d.get("title", feedback.text[:60]),
                goal=d.get("goal", feedback.text),
                acceptance_criteria=list(d.get("acceptance_criteria", [])),
                constraints=list(d.get("constraints", [])),
                out_of_scope=list(d.get("out_of_scope", [])),
                feedback_id=feedback.id, revision=revision,
                discipline=(d.get("discipline") or None),
            )
        except LLMError:
            # Graceful degradation: a minimal PRD straight from the feedback.
            return PRD(
                title=feedback.text[:60], goal=feedback.text,
                acceptance_criteria=["Implements the feedback.", "Has passing tests."],
                feedback_id=feedback.id, revision=revision,
            )

    def vote(self, proposals):
        listing = "\n\n".join(f"[{i}] {p.title}\n{p.to_markdown()}"
                              for i, p in enumerate(proposals))
        resp = self.backend.complete(
            f"Competing proposals:\n{listing}\n\nReturn your vote as JSON now.",
            system=VOTE_SYSTEM, model=self.model, extra_args=READONLY_ARGS)
        self.last_cost = resp.cost_usd
        try:
            idx = int(extract_json(resp.text).get("choice", 0))
        except (LLMError, ValueError, TypeError):
            idx = 0
        return idx if 0 <= idx < len(proposals) else 0


class ClaudeCodeDev(Dev):
    def __init__(self, backend: LLMBackend, model: str = "haiku",
                 permission_mode: str = "acceptEdits", cost_estimate: float = 1.50,
                 specialty: str | None = None, testing_mode: bool = False,
                 timeout: int = 900):
        super().__init__("dev", Branch.EXECUTIVE, cost_estimate)
        self.backend = backend
        self.model = model
        self.permission_mode = permission_mode
        self.specialty = specialty
        self.testing_mode = testing_mode
        # The dev is agentic Claude Code (edits many files + runs tests), so it needs far longer
        # than the architect/reviewer's single completions; 300s was timing real features out.
        self.timeout = timeout

    def implement(self, prd, attempt=0, review_feedback="", directives=None, workspace=None):
        if workspace is None:
            raise ValueError("ClaudeCodeDev requires a workspace to edit")
        system = DEV_SYSTEM
        if self.specialty:
            system += f"\nYou are a specialist in {self.specialty}; apply that expertise."
        if self.testing_mode:
            system += WIDGET_INSTRUCTION
        parts = [f"Implement this PRD in the current repository:\n\n{prd.to_markdown()}"]
        if review_feedback:
            parts.append(f"\nThe previous attempt was REJECTED. Address this feedback:\n"
                         f"{review_feedback}")
        parts.append("\nWrite the implementation and matching test_*.py tests. Do not commit.")
        resp = self.backend.complete(
            "\n".join(parts), system=system, model=self.model,
            cwd=str(workspace.path), permission_mode=self.permission_mode,
            timeout=self.timeout,
        )
        self.last_cost = resp.cost_usd
        changes = workspace.changed_files() if hasattr(workspace, "changed_files") else []
        return Diff(changes=changes, summary=(resp.text or f"implement {prd.id}")[:300])


class LLMReviewer(Reviewer):
    def __init__(self, backend: LLMBackend, model: str = "sonnet", cost_estimate: float = 0.40):
        super().__init__("reviewer", Branch.JUDICIAL, cost_estimate)
        self.backend = backend
        self.model = model

    def review(self, prd, diff, test_result, constitution):
        violations = constitution.check_diff(diff)
        blocking = [v for v in violations if v.severity == "block"]

        parts = [
            f"PRD:\n{prd.to_markdown()}",
            f"\nTest result: {'PASS' if test_result.passed else 'FAIL'} — {test_result.summary}",
        ]
        if test_result.details:
            parts.append(f"\nTest output (tail):\n{test_result.details[-1000:]}")
        parts.append(f"\nChanged files:\n{_render_diff(diff)}")
        if violations:
            scan = ", ".join(f"{v.rule_id}({v.severity}) in {v.path}" for v in violations)
            parts.append(f"\nMechanical constitution scan flagged: {scan}")
        parts.append("\nReturn your verdict as JSON now.")

        resp = self.backend.complete("\n".join(parts), system=REVIEWER_SYSTEM,
                                     model=self.model, extra_args=READONLY_ARGS)
        self.last_cost = resp.cost_usd

        try:
            d = extract_json(resp.text)
            approved = bool(d.get("approved", False))
            reasons = list(d.get("reasons", []))
            feedback = d.get("feedback", "") or ""
        except LLMError:
            approved, reasons, feedback = False, ["Reviewer output was unparseable."], ""

        # HARD GATES — these override the model's judgment, never the other way around.
        if not test_result.passed:
            approved = False
            reasons.append("Hard gate: tests are not green.")
        if blocking:
            approved = False
            ids = ", ".join(sorted({v.rule_id for v in blocking}))
            reasons.append(f"Hard gate: blocking constitution violation(s): {ids}.")

        if not approved and not feedback:
            feedback = "; ".join(reasons)
        return Verdict(approved=approved, reasons=reasons, feedback=feedback,
                       violations=violations)


class LLMConstitutionalJudge(ConstitutionalJudge):
    def __init__(self, backend: LLMBackend, model: str = "sonnet", cost_estimate: float = 0.30):
        super().__init__("constitutional-judge", Branch.JUDICIAL, cost_estimate)
        self.backend = backend
        self.model = model

    def review_prd(self, prd, constitution):
        blocking = [r for r in constitution.scan_text(prd.to_markdown())
                    if r.severity == "block"]
        rules = "\n".join(f"- {r.id} ({r.severity}): {r.description}"
                          for r in constitution.rules)
        prompt = (f"Constitution invariants:\n{rules}\n\n"
                  f"Proposed PRD:\n{prd.to_markdown()}\n\nReturn your ruling as JSON now.")
        resp = self.backend.complete(prompt, system=CONSTITUTIONAL_SYSTEM,
                                     model=self.model, extra_args=READONLY_ARGS)
        self.last_cost = resp.cost_usd
        try:
            d = extract_json(resp.text)
            approved = bool(d.get("constitutional", False))
            reasons = list(d.get("reasons", []))
            feedback = d.get("feedback", "") or ""
        except LLMError:
            approved, reasons, feedback = False, ["Court output was unparseable."], ""

        # Hard gate: a PRD whose text literally matches a blocking rule is unconstitutional.
        if blocking:
            approved = False
            ids = ", ".join(sorted({r.id for r in blocking}))
            reasons.append(f"Hard gate: PRD text matches blocking rule(s): {ids}.")
        if not approved and not feedback:
            feedback = "; ".join(reasons)
        return Verdict(approved=approved, reasons=reasons, feedback=feedback)
