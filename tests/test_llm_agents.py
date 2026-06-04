"""Hermetic tests for the real (LLM-backed) agents — driven by FakeLLM, no model
calls, no cost. Covers JSON parsing, graceful degradation, the dev capturing
workspace edits, and the reviewer's HARD GATES overriding a lenient model.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from polis.agents.llm_agents import READONLY_ARGS, ClaudeCodeDev, LLMArchitect, LLMReviewer
from polis.constitution import Constitution
from polis.llm import FakeLLM, LLMError, LLMResponse
from polis.models import Diff, FeedbackItem, FileChange, PRD, Stage, TestResult
from polis.orchestrator import Orchestrator, OrchestratorConfig
from polis.record import Record
from polis.registry import Registry
from polis.state import RunStore
from polis.treasury import Treasury
from tests._doubles import FakeWorkspace, ScriptedSandbox, passing

PRD_JSON = ('{"title":"Health","goal":"add /health","acceptance_criteria":["returns ok"],'
            '"constraints":[],"out_of_scope":[]}')


class ArchitectTest(unittest.TestCase):
    def test_parses_json_into_prd(self):
        fake = FakeLLM([LLMResponse(text=PRD_JSON, cost_usd=0.12)])
        arch = LLMArchitect(fake, model="sonnet")
        prd = arch.write_prd(FeedbackItem(text="add health"))
        self.assertEqual(prd.title, "Health")
        self.assertEqual(prd.acceptance_criteria, ["returns ok"])
        self.assertEqual(arch.last_cost, 0.12)
        self.assertEqual(fake.calls[0]["model"], "sonnet")

    def test_handles_fenced_json(self):
        fake = FakeLLM(["here you go:\n```json\n" + PRD_JSON + "\n```"])
        prd = LLMArchitect(fake).write_prd(FeedbackItem(text="x"))
        self.assertEqual(prd.title, "Health")

    def test_degrades_gracefully_on_non_json(self):
        fake = FakeLLM(["sorry, no JSON here"])
        prd = LLMArchitect(fake).write_prd(FeedbackItem(text="do the thing"))
        self.assertIn("do the thing", prd.goal)  # fell back to feedback text


class ArchitectGroundingTest(unittest.TestCase):
    """A grounded architect reads the real repo (cwd) + the tester screenshot, so the PRD names
    actual files/elements; the default architect stays blind (backward-compat)."""

    def _fb(self, **directives):
        return FeedbackItem(text="the headings are too close to the left edge", directives=directives)

    def test_grounded_architect_reads_repo_and_screenshot(self):
        d = Path(tempfile.mkdtemp(prefix="polis-shot-"))
        self.addCleanup(shutil.rmtree, str(d), ignore_errors=True)
        shot = d / "s.png"
        shot.write_bytes(b"\x89PNG\r\n")
        fake = FakeLLM([LLMResponse(text=PRD_JSON, cost_usd=0.2)])
        LLMArchitect(fake, grounded=True).write_prd(self._fb(screenshot_path=str(shot)), cwd="/repo")
        call = fake.calls[0]
        self.assertEqual(call["cwd"], "/repo")                  # reads the real code
        self.assertIn("--add-dir", call["extra_args"])          # screenshot dir whitelisted
        self.assertIn(str(shot.parent), call["extra_args"])
        self.assertIn(str(shot), call["prompt"])                # told to Read the image
        self.assertIn("Read/Grep/Glob", call["prompt"])
        self.assertEqual(call["timeout"], 600)                  # agentic -> longer window

    def test_grounded_architect_without_screenshot_still_specs(self):
        fake = FakeLLM([PRD_JSON])
        prd = LLMArchitect(fake, grounded=True).write_prd(self._fb(), cwd="/repo")
        self.assertEqual(prd.title, "Health")                   # still produced a PRD
        self.assertEqual(fake.calls[0]["cwd"], "/repo")
        self.assertNotIn("--add-dir", fake.calls[0]["extra_args"])  # no screenshot -> no add-dir

    def test_default_architect_is_blind(self):
        fake = FakeLLM([PRD_JSON])
        LLMArchitect(fake).write_prd(self._fb(screenshot_path="/whatever"), cwd="/repo")
        self.assertIsNone(fake.calls[0]["cwd"])                 # default = today's behavior
        self.assertEqual(fake.calls[0]["extra_args"], READONLY_ARGS)
        self.assertNotIn("Read/Grep/Glob", fake.calls[0]["prompt"])

    def test_architect_cost_estimate_is_self_contained(self):
        self.assertEqual(LLMArchitect(FakeLLM(["x"])).cost, 0.40)
        self.assertEqual(LLMArchitect(FakeLLM(["x"]), grounded=True).cost, 1.00)

    def test_blind_architect_keeps_short_timeout(self):
        # Backward-compat: a blind architect is a one-shot call — it keeps the 300s default, not 600.
        fake = FakeLLM([PRD_JSON])
        LLMArchitect(fake).write_prd(self._fb())
        self.assertEqual(fake.calls[0]["timeout"], 300)

    def test_grounded_architect_without_cwd_warns(self):
        fake = FakeLLM([PRD_JSON])
        with self.assertWarns(UserWarning):
            LLMArchitect(fake, grounded=True).write_prd(self._fb())  # no cwd
        self.assertIsNone(fake.calls[0]["cwd"])

    def test_grounded_architect_missing_screenshot_warns(self):
        fake = FakeLLM([PRD_JSON])
        with self.assertWarns(UserWarning):
            LLMArchitect(fake, grounded=True).write_prd(
                self._fb(screenshot_path="/nope/missing.png"), cwd="/repo")
        self.assertNotIn("--add-dir", fake.calls[0]["extra_args"])  # missing file is skipped


class DevTest(unittest.TestCase):
    def test_captures_workspace_edits_as_diff(self):
        fake = FakeLLM([LLMResponse(text="implemented the feature", cost_usd=0.5)])
        ws = FakeWorkspace()
        ws.fake_changes = [FileChange("feature.py", "def f():\n    return 'ok'\n")]
        dev = ClaudeCodeDev(fake, model="haiku")
        diff = dev.implement(PRD(title="t", goal="g"), workspace=ws)
        self.assertEqual([c.path for c in diff.changes], ["feature.py"])
        self.assertEqual(dev.last_cost, 0.5)
        # dev runs in the workspace, with edits auto-accepted
        self.assertEqual(fake.calls[0]["permission_mode"], "acceptEdits")
        self.assertEqual(fake.calls[0]["cwd"], ws.path)

    def test_requires_a_workspace(self):
        with self.assertRaises(ValueError):
            ClaudeCodeDev(FakeLLM(["x"])).implement(PRD(title="t", goal="g"))


class ReviewerHardGateTest(unittest.TestCase):
    def setUp(self):
        self.c = Constitution.load()
        self.prd = PRD(title="t", goal="g")

    def _review(self, approved_json, diff, test_result):
        rev = LLMReviewer(FakeLLM([approved_json]))
        return rev.review(self.prd, diff, test_result, self.c)

    def test_lenient_llm_cannot_pass_failing_tests(self):
        v = self._review('{"approved": true, "reasons": ["lgtm"]}',
                         Diff(changes=[FileChange("a.py", "x = 1\n")]),
                         TestResult(ran=True, passed=False, summary="fail"))
        self.assertFalse(v.approved)
        self.assertTrue(any("not green" in r.lower() for r in v.reasons))

    def test_lenient_llm_cannot_pass_a_secret(self):
        v = self._review('{"approved": true, "reasons": ["lgtm"]}',
                         Diff(changes=[FileChange("config.py", 'API_KEY = "sk-deadbeefcafebabe1234"\n')]),
                         TestResult(ran=True, passed=True))
        self.assertFalse(v.approved)
        self.assertIn("no-hardcoded-secrets", {x.rule_id for x in v.violations})

    def test_clean_change_can_be_approved(self):
        v = self._review('{"approved": true, "reasons": ["good"]}',
                         Diff(changes=[FileChange("feature.py", "def f():\n    return 'ok'\n")]),
                         TestResult(ran=True, passed=True))
        self.assertTrue(v.approved)

    def test_unparseable_review_defaults_to_reject(self):
        v = self._review("not json", Diff(changes=[FileChange("a.py", "x=1\n")]),
                         TestResult(ran=True, passed=True))
        self.assertFalse(v.approved)


class ReviewerGroundingTest(unittest.TestCase):
    """A grounded reviewer is handed the post-change repo (cwd) so it can verify criteria a
    diff can't show; the default reviewer stays blind (backward-compat); hard gates are intact."""

    def setUp(self):
        self.c = Constitution.load()
        self.prd = PRD(title="t", goal="g")
        self.diff = Diff(changes=[FileChange("a.py", "x = 1\n")])

    def test_grounded_reviewer_reads_the_post_change_repo(self):
        fake = FakeLLM(['{"approved": true, "reasons": ["ok"]}'])
        LLMReviewer(fake, grounded=True).review(
            self.prd, self.diff, TestResult(ran=True, passed=True), self.c, cwd="/feature/branch")
        call = fake.calls[0]
        self.assertEqual(call["cwd"], "/feature/branch")        # pointed at the real repo
        self.assertIn("Read/Grep/Glob", call["prompt"])         # told to verify against files
        self.assertIn("--disallowedTools", call["extra_args"])  # but still can't Edit/Write

    def test_default_reviewer_is_blind(self):
        # Backward-compat: without grounding the cwd is ignored — identical to today's behavior.
        fake = FakeLLM(['{"approved": true, "reasons": ["ok"]}'])
        LLMReviewer(fake).review(
            self.prd, self.diff, TestResult(ran=True, passed=True), self.c, cwd="/feature/branch")
        self.assertIsNone(fake.calls[0]["cwd"])
        self.assertNotIn("Read/Grep/Glob", fake.calls[0]["prompt"])

    def test_grounded_reviewer_hard_gates_still_apply(self):
        # A lenient model approves, but tests failed -> the hard gate flips it to reject.
        fake = FakeLLM(['{"approved": true, "reasons": ["lgtm"]}'])
        v = LLMReviewer(fake, grounded=True).review(
            self.prd, self.diff, TestResult(ran=True, passed=False, summary="fail"),
            self.c, cwd="/feature/branch")
        self.assertFalse(v.approved)
        self.assertTrue(any("not green" in r.lower() for r in v.reasons))

    def test_grounded_reviewer_uses_a_longer_timeout(self):
        # An agentic Read/Grep over the repo can exceed the 300s one-shot default (the same reason
        # the dev's window was raised) — so a timeout is passed explicitly.
        fake = FakeLLM(['{"approved": true, "reasons": ["ok"]}'])
        LLMReviewer(fake, grounded=True).review(
            self.prd, self.diff, TestResult(ran=True, passed=True), self.c, cwd="/x")
        self.assertEqual(fake.calls[0]["timeout"], 600)

    def test_reviewer_cost_estimate_is_self_contained(self):
        # The grounded↔cost coupling lives in the constructor, not in the caller.
        self.assertEqual(LLMReviewer(FakeLLM(["x"])).cost, 0.40)
        self.assertEqual(LLMReviewer(FakeLLM(["x"]), grounded=True).cost, 1.00)

    def test_grounded_reviewer_without_cwd_warns_and_falls_back(self):
        fake = FakeLLM(['{"approved": true, "reasons": ["ok"]}'])
        with self.assertWarns(UserWarning):
            LLMReviewer(fake, grounded=True).review(
                self.prd, self.diff, TestResult(ran=True, passed=True), self.c)  # no cwd
        self.assertIsNone(fake.calls[0]["cwd"])  # silently-blind path is now surfaced


class RealAgentsCostAccountingTest(unittest.TestCase):
    """End-to-end through the orchestrator with FakeLLM agents: proves ACTUAL per-call
    cost (not the estimate) is debited, and that the real agents satisfy the same
    procedure + independence guarantees as the stubs."""

    def test_actual_costs_are_debited_and_run_merges(self):
        backend = FakeLLM([
            LLMResponse(text=PRD_JSON, cost_usd=0.10),                                  # architect
            LLMResponse(text="implemented", cost_usd=0.50),                             # dev
            LLMResponse(text='{"approved": true, "reasons": ["ok"]}', cost_usd=0.20),   # reviewer
        ])
        treasury = Treasury(":memory:")
        treasury.appropriate(100)
        ws = FakeWorkspace()
        ws.fake_changes = [FileChange("feature.py", "def f():\n    return 'ok'\n")]
        tmp = Path(tempfile.mkdtemp(prefix="polis-real-"))
        orch = Orchestrator(
            registry=Registry.real(backend),
            treasury=treasury,
            record=Record(tmp / "record.jsonl"),
            constitution=Constitution.load(),
            workspace=ws,
            sandbox=ScriptedSandbox([passing()]),
            run_store=RunStore(":memory:"),
            config=OrchestratorConfig(),
        )
        res = orch.process(FeedbackItem(text="add a feature"))
        self.assertTrue(res.merged)
        self.assertAlmostEqual(treasury.spent_on(res.run_id), 0.80, places=9)
        # independence still holds with real agents
        judicial = [e for e in Record(tmp / "record.jsonl").events()
                    if e["actor"].startswith("judicial")]
        self.assertTrue(judicial and all(e["source"] == "procedure" for e in judicial))

    def test_infra_error_escalates_rather_than_rejecting(self):
        # A transient API failure must ESCALATE (human attention), not be mistaken for
        # a substantive rejection that burns revisions.
        backend = FakeLLM([
            LLMResponse(text=PRD_JSON, cost_usd=0.10),         # architect ok
            LLMResponse(text="implemented", cost_usd=0.50),    # dev ok
            LLMError("claude API error: Overloaded"),          # reviewer infra failure
        ])
        treasury = Treasury(":memory:")
        treasury.appropriate(100)
        ws = FakeWorkspace()
        ws.fake_changes = [FileChange("feature.py", "x = 1\n")]
        tmp = Path(tempfile.mkdtemp(prefix="polis-infra-"))
        orch = Orchestrator(
            registry=Registry.real(backend), treasury=treasury,
            record=Record(tmp / "record.jsonl"), constitution=Constitution.load(),
            workspace=ws, sandbox=ScriptedSandbox([passing()]),
            run_store=RunStore(":memory:"), config=OrchestratorConfig(),
        )
        res = orch.process(FeedbackItem(text="x"))
        self.assertEqual(res.outcome, Stage.ESCALATE)
        self.assertEqual(res.last_stage, Stage.REVIEW)
        self.assertIn("llm_error", res.reason)
        self.assertEqual(len(ws.merges), 0)


class RenderDiffTest(unittest.TestCase):
    def test_reviewer_sees_full_moderate_files(self):
        from polis.agents.llm_agents import _render_diff
        body = "def f():\n    return 1\n" * 300            # ~6.6 KB — a realistic source file
        out = _render_diff(Diff(changes=[FileChange("a.py", body)]))
        self.assertIn(body, out)                            # the WHOLE file reaches the reviewer
        self.assertNotIn("[truncated]", out)
        huge = "z\n" * 15000                                # ~30 KB > per-file cap
        self.assertIn("[truncated]", _render_diff(Diff(changes=[FileChange("b.py", huge)])))


if __name__ == "__main__":
    unittest.main()
