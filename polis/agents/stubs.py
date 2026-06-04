"""Deterministic, LLM-free agents for Phase 0.

They make *fixed* decisions so the procedure can be exercised end-to-end and tested
without a model. Behavior is injectable via ``FeedbackItem.directives`` so tests can
drive specific scenarios (a leaked secret, a flaky implementation, etc.):

    {"dev": "ok"}                       -> clean implementation, tests pass
    {"dev": "emit_secret"}              -> also writes a hardcoded secret (reviewer blocks)
    {"dev": "fail_n_times", "n": 1}     -> tests fail for the first n attempts, then pass
"""

from __future__ import annotations

from ..models import Branch, Diff, FeedbackItem, FileChange, PRD, TestResult, Verdict
from .base import Architect, ConstitutionalJudge, Dev, Reviewer

def _test_src(module: str) -> str:
    return (
        "import unittest\n"
        f"from {module} import feature\n\n"
        "class FeatureTest(unittest.TestCase):\n"
        "    def test_feature_ok(self):\n"
        '        self.assertEqual(feature(), "ok")\n\n'
        'if __name__ == "__main__":\n'
        "    unittest.main()\n"
    )


class StubArchitect(Architect):
    def __init__(self, cost: float = 20.0):
        super().__init__("architect", Branch.LEGISLATIVE, cost)

    def write_prd(self, feedback, repo_summary="", prior=None, review_feedback="", cwd=None):
        if prior is not None:
            # Revision: keep the same law, bump the rev, fold in the court's feedback.
            prior.revision += 1
            if review_feedback:
                prior.constraints.append(f"[rev {prior.revision}] address: {review_feedback}")
            return prior
        title = (feedback.text.strip().splitlines() or ["Untitled"])[0][:60]
        return PRD(
            title=title,
            goal=f"Implement: {feedback.text.strip()}",
            acceptance_criteria=[
                "Feature implemented in the codebase.",
                "Automated tests cover the feature and pass.",
                "No blocking constitution violations.",
            ],
            constraints=["Keep the change minimal and self-contained."],
            out_of_scope=["Unrelated refactors."],
            feedback_id=feedback.id,
            # Lets tests drive specialist hiring: {"discipline": "backend"}.
            discipline=feedback.directives.get("discipline"),
        )

    def vote(self, proposals):
        # Deterministic: prefer the most detailed proposal; ties -> lowest index.
        best_i, best = 0, -1
        for i, p in enumerate(proposals):
            if len(p.acceptance_criteria) > best:
                best, best_i = len(p.acceptance_criteria), i
        return best_i


class StubDev(Dev):
    def __init__(self, cost: float = 10.0, specialty: str | None = None):
        super().__init__("dev", Branch.EXECUTIVE, cost)
        self.specialty = specialty

    def implement(self, prd, attempt=0, review_feedback="", directives=None, workspace=None):
        directives = directives or {}
        mode = directives.get("dev", "ok")
        fail_n = int(directives.get("n", 0)) if mode == "fail_n_times" else 0
        will_pass = not (mode == "fail_n_times" and attempt < fail_n)
        value = "ok" if will_pass else "todo"  # "todo" makes the bundled test fail
        # A unique module name (via directive) lets parallel runs touch different
        # files, so they merge cleanly instead of colliding on feature.py.
        module = directives.get("module", "feature")

        changes = [
            FileChange(f"{module}.py", f'def feature():\n    return "{value}"\n'),
            FileChange(f"test_{module}.py", _test_src(module)),
        ]
        if mode == "emit_secret":
            # Tests still pass, but this breaches the constitution — the judiciary
            # must catch what the tests do not.
            changes.append(FileChange("config.py", 'API_KEY = "sk-deadbeefcafebabe1234"\n'))

        summary = f"attempt {attempt}: implement {prd.id}"
        if mode == "emit_secret":
            summary += " (leaks a secret)"
        elif not will_pass:
            summary += " (intentionally failing)"
        return Diff(changes=changes, summary=summary)


class StubReviewer(Reviewer):
    def __init__(self, cost: float = 20.0):
        super().__init__("reviewer", Branch.JUDICIAL, cost)

    def review(self, prd, diff, test_result, constitution, cwd=None):
        violations = constitution.check_diff(diff)
        blocking = [v for v in violations if v.severity == "block"]
        reasons: list[str] = []
        approved = True

        if diff.is_empty():
            approved = False
            reasons.append("Empty diff: nothing was implemented.")
        if not test_result.passed:
            approved = False
            reasons.append("Tests are not green.")
        if blocking:
            approved = False
            ids = ", ".join(sorted({v.rule_id for v in blocking}))
            reasons.append(f"Blocking constitution violation(s): {ids}.")

        if approved:
            reasons.append("Tests green, no blocking violations — PRD criteria appear met.")
        return Verdict(
            approved=approved,
            reasons=reasons,
            feedback="" if approved else "; ".join(reasons),
            violations=violations,
        )


class StubConstitutionalJudge(ConstitutionalJudge):
    def __init__(self, cost: float = 15.0):
        super().__init__("constitutional-judge", Branch.JUDICIAL, cost)

    def review_prd(self, prd, constitution):
        # Reject a PRD whose text would mandate a blocking constitution violation
        # (e.g. a spec that literally asks to hardcode a secret).
        hits = [r for r in constitution.scan_text(prd.to_markdown()) if r.severity == "block"]
        if hits:
            ids = ", ".join(sorted({r.id for r in hits}))
            return Verdict(
                approved=False,
                reasons=[f"PRD would mandate a constitutional violation: {ids}."],
                feedback=f"Revise the PRD so it no longer requires: {ids}.",
            )
        return Verdict(approved=True, reasons=["PRD is constitutional."])
