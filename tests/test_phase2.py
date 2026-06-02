"""Phase 2 hermetic tests: specialist hiring, constitutional court, architect voting."""

import unittest

from polis.constitution import Constitution
from polis.llm import FakeLLM
from polis.models import FeedbackItem, PRD, Stage
from polis.registry import Registry, SPECIALISTS
from tests._doubles import Harness, ScriptedSandbox, passing

SECRET_FEEDBACK = 'Hardcode the secret API_KEY = "sk-deadbeefcafebabe1234" into settings'


class SpecialistHiringTest(unittest.TestCase):
    def test_hire_event_records_discipline(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]))
        res = h.orch.process(FeedbackItem(text="build an API",
                                          directives={"discipline": "backend"}))
        self.assertTrue(res.merged)
        hires = [e for e in h.events() if e["kind"] == "hire"]
        self.assertEqual(len(hires), 1)
        self.assertEqual(hires[0]["payload"]["discipline"], "backend")

    def test_default_is_generalist(self):
        h = Harness()
        h.orch.process(FeedbackItem(text="do a thing"))
        hires = [e for e in h.events() if e["kind"] == "hire"]
        self.assertEqual(hires[0]["payload"]["discipline"], "generalist")

    def test_real_registry_synthesizes_known_specialist(self):
        reg = Registry.real(FakeLLM(["x"]))
        dev = reg.hire_dev("backend")
        self.assertIn("backend", (dev.specialty or "").lower())
        reg.release(dev)

    def test_real_registry_hires_unknown_discipline_on_the_fly(self):
        # "Hire the expert even if one doesn't exist yet."
        self.assertNotIn("quantum", SPECIALISTS)
        reg = Registry.real(FakeLLM(["x"]))
        dev = reg.hire_dev("quantum")
        self.assertIn("quantum", (dev.specialty or "").lower())
        reg.release(dev)

    def test_no_discipline_is_generalist(self):
        reg = Registry.real(FakeLLM(["x"]))
        dev = reg.hire_dev(None)
        self.assertIsNone(dev.specialty)
        reg.release(dev)


class ConstitutionalCourtTest(unittest.TestCase):
    def test_constitutional_prd_proceeds_to_merge(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]), constitutional_review=True)
        res = h.orch.process(FeedbackItem(text="add a greeting function"))
        self.assertTrue(res.merged)
        rulings = [e for e in h.events() if e["kind"] == "ruling"]
        self.assertEqual(len(rulings), 1)
        self.assertTrue(rulings[0]["payload"]["constitutional"])

    def test_unconstitutional_prd_escalates_before_any_code(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]),
                    constitutional_review=True, max_prd_revisions=1)
        res = h.orch.process(FeedbackItem(text=SECRET_FEEDBACK))
        self.assertEqual(res.outcome, Stage.ESCALATE)
        self.assertEqual(res.last_stage, Stage.CONSTITUTIONAL)
        self.assertIn("unconstitutional_prd", res.reason)
        # The law was struck down before a single line was written or merged.
        self.assertEqual(len(h.workspace.merges), 0)
        self.assertNotIn("diff", h.kinds())

    def test_court_is_independent(self):
        h = Harness(constitutional_review=True)
        h.orch.process(FeedbackItem(text="add a thing"))
        rulings = [e for e in h.events() if e["kind"] == "ruling"]
        self.assertTrue(rulings)
        for e in rulings:
            self.assertEqual(e["source"], "procedure")
            self.assertTrue(e["actor"].startswith("judicial"))

    def test_disabled_by_default(self):
        h = Harness()  # constitutional_review defaults off
        h.orch.process(FeedbackItem(text="add a thing"))
        self.assertNotIn("ruling", h.kinds())

    def test_llm_court_hard_gate_overrides_lenient_model(self):
        from polis.agents.llm_agents import LLMConstitutionalJudge
        judge = LLMConstitutionalJudge(FakeLLM(['{"constitutional": true, "reasons": ["fine"]}']))
        prd = PRD(title="t", goal='store API_KEY = "sk-deadbeefcafebabe1234"')
        v = judge.review_prd(prd, Constitution.load())
        self.assertFalse(v.approved)


class PanelVotingTest(unittest.TestCase):
    def test_panel_proposes_votes_and_elects(self):
        h = Harness(sandbox=ScriptedSandbox([passing()]), num_architects=3)
        res = h.orch.process(FeedbackItem(text="add a feature"))
        self.assertTrue(res.merged)
        kinds = h.kinds()
        self.assertEqual(kinds.count("proposal"), 3)
        self.assertEqual(kinds.count("vote"), 3)
        elected = [e for e in h.events() if e["kind"] == "elected"]
        self.assertEqual(len(elected), 1)
        self.assertEqual(sum(elected[0]["payload"]["tally"]), 3)

    def test_single_architect_has_no_panel_events(self):
        h = Harness()
        h.orch.process(FeedbackItem(text="x"))
        self.assertNotIn("proposal", h.kinds())
        self.assertNotIn("elected", h.kinds())

    def test_majority_wins_with_lowest_index_tiebreak(self):
        from polis.agents.stubs import StubArchitect
        from polis.models import Branch
        from polis.registry import RoleTemplate

        class _ScriptedVoter(StubArchitect):
            queue: list = []

            def vote(self, proposals):
                return _ScriptedVoter.queue.pop(0) if _ScriptedVoter.queue else 0

        h = Harness(sandbox=ScriptedSandbox([passing()]), num_architects=3)
        h.registry.register(RoleTemplate("architect", Branch.LEGISLATIVE, _ScriptedVoter))
        _ScriptedVoter.queue = [1, 1, 0]  # -> tally [1, 2, 0], winner 1
        h.orch.process(FeedbackItem(text="x"))
        elected = [e for e in h.events() if e["kind"] == "elected"][0]["payload"]
        self.assertEqual(elected["tally"], [1, 2, 0])
        self.assertEqual(elected["winner"], 1)

    def test_llm_architect_vote_parses_index(self):
        from polis.agents.llm_agents import LLMArchitect
        a = LLMArchitect(FakeLLM(['{"choice": 2, "reason": "best"}']))
        choice = a.vote([PRD(title="a", goal="g"), PRD(title="b", goal="g"),
                         PRD(title="c", goal="g")])
        self.assertEqual(choice, 2)


if __name__ == "__main__":
    unittest.main()
