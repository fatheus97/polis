"""Self-learning: LessonStore + classifier + Reflector + retrieval/injection + the hook.

Hermetic — stub agents + FakeLLM + in-memory SQLite. No `claude`, no network, no cost.
"""

import tempfile
import threading
import unittest
from pathlib import Path

from polis.agents.llm_agents import ClaudeCodeDev, LLMArchitect, LLMReflector, LLMReviewer
from polis.agents.stubs import StubReflector
from polis.app import Government
from polis.constitution import Constitution
from polis.feedback import FeedbackInbox
from polis.lessons import LessonStore, ReflectDecision, classify_run
from polis.llm import FakeLLM
from polis.models import Diff, FeedbackItem, FileChange, Lesson, PRD, RunResult, Stage
from tests._doubles import Harness, ScriptedSandbox, failing, passing


def L(**kw) -> Lesson:
    base = dict(trigger="t", guidance="g", scope="dev", discipline=None, polarity="pitfall")
    base.update(kw)
    return Lesson(**base)


def rr(reason, *, outcome=Stage.ESCALATE, attempts=0) -> RunResult:
    return RunResult(run_id="run-x", outcome=outcome, last_stage=Stage.REVISE,
                     reason=reason, attempts=attempts)


def make_gov(*, lesson_store=None, sandbox=None, budget=1000.0, sample_good=False,
             max_revisions=2):
    """A Government wired from the hermetic doubles, so we can drive run_next + _reflect."""
    h = Harness(sandbox=sandbox, lesson_store=lesson_store, budget=budget,
                max_revisions=max_revisions)
    inbox = FeedbackInbox(":memory:")
    gov = Government(
        base=h.tmp, treasury=h.treasury, record=h.record, constitution=h.constitution,
        registry=h.registry, workspace=h.workspace, sandbox=h.sandbox,
        run_store=h.run_store, inbox=inbox, orchestrator=h.orch,
        lesson_store=lesson_store, sample_good=sample_good)
    return gov, h, inbox


# --- 1. LessonStore + FTS5 retrieval ------------------------------------------

class LessonStoreTest(unittest.TestCase):
    def test_add_and_retrieve(self):
        s = LessonStore(":memory:")
        s.add(L(trigger="flaky socket tests", guidance="use temp dirs", scope="dev"))
        hits = s.retrieve("socket tests flaky", scope="dev")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].guidance, "use temp dirs")

    def test_scope_filter_excludes_other_scope(self):
        s = LessonStore(":memory:")
        s.add(L(trigger="spec should stay minimal", guidance="keep the prd small",
                scope="architect"))
        self.assertEqual(s.retrieve("spec minimal small prd", scope="dev"), [])
        self.assertEqual(len(s.retrieve("spec minimal small prd", scope="architect")), 1)

    def test_exact_discipline_ranks_above_generalist(self):
        s = LessonStore(":memory:")
        s.add(L(trigger="backend api pagination", guidance="paginate large lists",
                scope="dev", discipline="backend", run_id="a"))
        s.add(L(trigger="api pagination generally", guidance="page everything",
                scope="dev", discipline=None, run_id="b"))
        hits = s.retrieve("api pagination", scope="dev", discipline="backend", k=5)
        self.assertEqual(len(hits), 2)                 # generalist (NULL) lessons also match
        self.assertEqual(hits[0].discipline, "backend")  # but exact-discipline ranks first

    def test_empty_store_returns_empty(self):
        self.assertEqual(LessonStore(":memory:").retrieve("anything at all", scope="dev"), [])

    def test_adversarial_query_never_raises(self):
        # The regression test that protects EVERY live run: a stray quote/paren/*/- in a
        # feedback string fed to FTS5 MATCH must not crash retrieval.
        s = LessonStore(":memory:")
        s.add(L(trigger="quotes and parens", guidance="be careful with input"))
        for q in ['"unterminated', "a AND (b", "foo* -bar", "it's a (test)", ")((", '""', "*"]:
            self.assertIsInstance(s.retrieve(q, scope="dev"), list)

    def test_jsonl_audit_written(self):
        tmp = Path(tempfile.mkdtemp(prefix="polis-lessons-"))
        s = LessonStore(tmp / "lessons.sqlite", jsonl_path=tmp / "lessons.jsonl")
        s.add(L())
        s.add(L(trigger="another"))
        self.assertEqual(len((tmp / "lessons.jsonl").read_text(encoding="utf-8").splitlines()), 2)

    def test_mark_used_and_won_counted_separately(self):
        s = LessonStore(":memory:")
        lesson = L()
        s.add(lesson)
        s.mark_used([lesson.id])
        s.mark_used([lesson.id])
        s.mark_won([lesson.id])
        got = s.get(lesson.id)
        self.assertEqual((got.uses, got.wins), (2, 1))

    def test_retire_hides_from_retrieval_but_not_from_curation(self):
        s = LessonStore(":memory:")
        lesson = L(trigger="health endpoint", guidance="add a regression test")
        s.add(lesson)
        self.assertEqual(len(s.retrieve("health endpoint", scope="dev")), 1)
        s.set_status(lesson.id, "retired")
        self.assertEqual(s.retrieve("health endpoint", scope="dev"), [])
        self.assertEqual(s.all(), [])                              # active-only by default
        self.assertEqual(len(s.all(include_retired=True)), 1)      # curation still sees it

    def test_like_fallback_when_fts_absent(self):
        s = LessonStore(":memory:")
        s._fts = False                       # simulate a Python build without FTS5
        s.add(L(trigger="flaky socket tests", guidance="use temp dirs", scope="dev"))
        hits = s.retrieve("socket flaky", scope="dev")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].guidance, "use temp dirs")

    def test_stats(self):
        s = LessonStore(":memory:")
        s.add(L(scope="dev"))
        s.add(L(scope="architect"))
        st = s.stats()
        self.assertEqual(st["total"], 2)
        self.assertEqual(st["active"], 2)

    def test_decay_retires_well_used_low_win_lessons_only(self):
        s = LessonStore(":memory:")
        poison = L(trigger="bad advice", guidance="do the wrong thing", uses=8, wins=1)
        useful = L(trigger="good advice", guidance="do the right thing", uses=8, wins=6)
        young = L(trigger="too new", guidance="not judged yet", uses=2, wins=0)
        for lesson in (poison, useful, young):
            s.add(lesson)
        retired = s.decay()                         # min_uses=5, min_win_rate~0.34
        self.assertEqual(retired, [poison.id])      # only the well-used, low-win one
        self.assertEqual(s.get(poison.id).status, "retired")
        self.assertEqual(s.get(useful.id).status, "active")
        self.assertEqual(s.get(young.id).status, "active")

    def test_thread_safety_smoke(self):
        s = LessonStore(":memory:")

        def worker(n):
            for i in range(20):
                s.add(L(trigger=f"topic {n} item {i}", guidance="g", scope="dev"))
                s.retrieve(f"topic {n}", scope="dev")

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(s.all(include_retired=True)), 80)


# --- 2. Deterministic terminal classification ---------------------------------

class ClassifyRunTest(unittest.TestCase):
    def test_operational_escalations_excluded(self):
        for reason in ("llm_error: boom", "merge_conflict: x", "budget_exhausted: y",
                       "error: parallel catch-all"):
            self.assertFalse(classify_run(rr(reason)).reflect, reason)

    def test_unconstitutional_prd_is_architect_pitfall(self):
        d = classify_run(rr("unconstitutional_prd: hardcodes a secret"))
        self.assertEqual((d.reflect, d.polarity, d.scope), (True, "pitfall", "architect"))

    def test_revisions_exhausted_is_dev_pitfall(self):
        d = classify_run(rr("revisions_exhausted after 2 revisions; last verdict: nope"))
        self.assertEqual((d.reflect, d.polarity, d.scope), (True, "pitfall", "dev"))

    def test_clean_first_merge_gated_off_by_default(self):
        m0 = rr("merged", outcome=Stage.DONE, attempts=0)
        self.assertFalse(classify_run(m0).reflect)
        d = classify_run(m0, sample_good=True)
        self.assertEqual((d.reflect, d.polarity), (True, "good_practice"))

    def test_merged_after_revision_is_dev_pitfall(self):
        d = classify_run(rr("merged", outcome=Stage.DONE, attempts=1))
        self.assertEqual((d.reflect, d.polarity, d.scope), (True, "pitfall", "dev"))


# --- 3. The Reflector agent ---------------------------------------------------

class ReflectorTest(unittest.TestCase):
    def test_llm_reflector_parses_and_keeps_passed_in_classification(self):
        r = LLMReflector(FakeLLM(['{"trigger":"T","guidance":"G"}']))
        lesson = r.reflect(prd_markdown="# PRD", verdict_feedback="fix x", test_summary="ok",
                           outcome="ESCALATE", reason="revisions_exhausted ...", attempts=3,
                           polarity="pitfall", scope="dev", discipline="backend")
        self.assertEqual((lesson.trigger, lesson.guidance), ("T", "G"))
        self.assertEqual((lesson.scope, lesson.polarity, lesson.discipline),
                         ("dev", "pitfall", "backend"))
        self.assertEqual(r.last_cost, 0.01)            # FakeLLM's default recorded cost

    def test_llm_reflector_unparseable_degrades_to_empty_guidance(self):
        r = LLMReflector(FakeLLM(["not json at all"]))
        lesson = r.reflect(prd_markdown="", verdict_feedback="", test_summary="", outcome="DONE",
                           reason="revisions_exhausted xyz", attempts=1, polarity="pitfall",
                           scope="dev", discipline=None)
        self.assertEqual(lesson.guidance, "")          # => orchestrator will NOT store it
        self.assertTrue(lesson.trigger.startswith("revisions_exhausted"))

    def test_stub_reflector_echoes_feedback(self):
        lesson = StubReflector().reflect(
            prd_markdown="x", verdict_feedback="be careful with sockets", test_summary="ok",
            outcome="ESCALATE", reason="revisions_exhausted abc", attempts=2,
            polarity="pitfall", scope="dev", discipline="backend")
        self.assertEqual(lesson.scope, "dev")
        self.assertIn("be careful with sockets", lesson.guidance)


# --- 4. Injection into the real agents' prompts -------------------------------

class InjectionTest(unittest.TestCase):
    _PRD_JSON = ('{"title":"t","goal":"g","acceptance_criteria":[],"constraints":[],'
                 '"out_of_scope":[],"discipline":null}')

    def test_architect_injects_lessons(self):
        backend = FakeLLM([self._PRD_JSON])
        LLMArchitect(backend).write_prd(FeedbackItem(text="add health"),
                                        lessons=["use temp dirs not real sockets"])
        prompt = backend.calls[-1]["prompt"]
        self.assertIn("LESSONS from past runs", prompt)
        self.assertIn("use temp dirs not real sockets", prompt)

    def test_architect_no_lessons_means_no_block(self):
        backend = FakeLLM([self._PRD_JSON])
        LLMArchitect(backend).write_prd(FeedbackItem(text="add health"))
        self.assertNotIn("LESSONS from past runs", backend.calls[-1]["prompt"])

    def test_dev_injects_lessons(self):
        from tests._doubles import FakeWorkspace
        backend = FakeLLM(["done"])
        ClaudeCodeDev(backend).implement(PRD(title="t", goal="g"), workspace=FakeWorkspace(),
                                         lessons=["mirror existing tests"])
        self.assertIn("mirror existing tests", backend.calls[-1]["prompt"])

    def test_reviewer_injects_lessons(self):
        backend = FakeLLM(['{"approved":true,"reasons":[],"feedback":""}'])
        diff = Diff(changes=[FileChange("f.py", "x")], summary="s")
        LLMReviewer(backend).review(PRD(title="t", goal="g"), diff, passing(),
                                    Constitution.load(), lessons=["verify removals via grep"])
        self.assertIn("verify removals via grep", backend.calls[-1]["prompt"])


# --- 5. End-to-end hook (toggle on/off, retrieval, reflection, budget) --------

class HookTest(unittest.TestCase):
    def test_toggle_off_is_inert(self):
        gov, h, inbox = make_gov(lesson_store=None, sandbox=ScriptedSandbox([passing()]))
        inbox.submit("add a health endpoint")
        res = gov.run_next()
        self.assertTrue(res.merged)
        kinds = h.kinds()
        for k in ("reflect", "reflect_skipped", "reflect_failed", "lessons_injected"):
            self.assertNotIn(k, kinds)

    def test_revisions_exhausted_produces_a_lesson(self):
        store = LessonStore(":memory:")
        gov, h, inbox = make_gov(lesson_store=store, sandbox=ScriptedSandbox([failing()]))
        inbox.submit("add a health endpoint")
        res = gov.run_next()
        self.assertEqual(res.outcome, Stage.ESCALATE)
        self.assertTrue(res.reason.startswith("revisions_exhausted"))
        self.assertIn("reflect", h.kinds())
        lessons = store.all()
        self.assertEqual(len(lessons), 1)
        self.assertEqual((lessons[0].scope, lessons[0].polarity), ("dev", "pitfall"))
        # the reflect event carries the reflector's cost (StubReflector = 5.0)
        reflect_ev = [e for e in h.events() if e["kind"] == "reflect"][0]
        self.assertEqual(reflect_ev["cost"], 5.0)

    def test_merged_after_revision_produces_a_lesson(self):
        store = LessonStore(":memory:")
        gov, h, inbox = make_gov(lesson_store=store,
                                 sandbox=ScriptedSandbox([failing(), passing()]))
        inbox.submit("add a health endpoint")
        res = gov.run_next()
        self.assertTrue(res.merged)
        self.assertEqual(res.attempts, 1)
        self.assertIn("reflect", h.kinds())
        self.assertEqual(store.all()[0].polarity, "pitfall")

    def test_injection_records_event_and_credits_on_merge(self):
        store = LessonStore(":memory:")
        store.add(L(trigger="health endpoint reliability",
                    guidance="always add a regression test for the endpoint",
                    scope="dev", discipline=None))
        gov, h, inbox = make_gov(lesson_store=store, sandbox=ScriptedSandbox([passing()]))
        inbox.submit("add a health endpoint")
        res = gov.run_next()
        self.assertTrue(res.merged)
        injected = [e for e in h.events() if e["kind"] == "lessons_injected"]
        self.assertTrue(injected)
        self.assertEqual(injected[0]["payload"]["scope"], "dev")
        seeded = store.all()[0]
        self.assertEqual(seeded.uses, 1)        # bumped at injection
        self.assertEqual(seeded.wins, 1)        # credited because the run merged

    def test_reflection_is_budget_gated(self):
        # Fund the run (revisions_exhausted spends 110) but leave < the reflector's 5.0.
        store = LessonStore(":memory:")
        gov, h, inbox = make_gov(lesson_store=store, sandbox=ScriptedSandbox([failing()]),
                                 budget=114)
        inbox.submit("add a health endpoint")
        res = gov.run_next()
        self.assertEqual(res.outcome, Stage.ESCALATE)
        self.assertTrue(res.reason.startswith("revisions_exhausted"))
        self.assertIn("reflect_skipped", h.kinds())
        self.assertNotIn("reflect", h.kinds())
        self.assertEqual(store.all(), [])


if __name__ == "__main__":
    unittest.main()
