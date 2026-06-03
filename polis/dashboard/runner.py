"""Background run executor + write actions for the dashboard — stdlib only.

Triggering a run from a browser is the risky part: a real run takes minutes and costs
money. So:
  * runs execute on a SINGLE-worker pool (serialized — the browser can't spawn chaos);
  * trigger_run() returns a job_id IMMEDIATELY (never blocks the HTTP request);
  * the default is STUB (free); a real run requires the caller to have confirmed;
  * the Treasury still hard-gates spend (the orchestrator ESCALATEs when out of funds).

FeedbackInbox is main-thread-only (check_same_thread=True). We never share one inbox
connection across threads: write actions from request threads open a short-lived
connection on their own thread under a lock; the run worker opens its own (via
build_government) on its single dedicated thread.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _summarize_state(state: dict) -> dict:
    """A compact, architect-friendly digest of the captured web state (the full state
    stays in the report; this rides on the feedback item's directives)."""
    from ..reports import console_errors
    console = state.get("console") or []
    errs = console_errors(console)
    return {
        "url": state.get("url"),
        "console_total": len(console),
        "console_errors": len(errs),
        "last_errors": [" ".join(map(str, c.get("args", [])))[:200] for c in errs[-3:]],
        "storage_keys": list((state.get("localStorage") or {}).keys())[:10],
    }


class RunManager:
    def __init__(self, base):
        self.base = Path(base)
        self._executor = ThreadPoolExecutor(max_workers=1)  # serialize all runs
        self._write_lock = threading.Lock()                 # serialize inbox/treasury writes
        self._jobs: dict[str, dict] = {}
        self._jobs_lock = threading.Lock()
        self._max_jobs = 200                                # bound the in-memory history
        self._clerk_executor = ThreadPoolExecutor(max_workers=1)  # distillation, off the run path
        self._clerk_backend_factory = None                  # test seam (default ClaudeCliBackend)

    # --- write actions (called on request threads) -------------------------
    def submit_feedback(self, text: str, by: str = "tester", directives=None) -> dict:
        from ..feedback import FeedbackInbox
        with self._write_lock:
            inbox = FeedbackInbox(self.base / "feedback.sqlite")
            try:
                item = inbox.submit(text, submitted_by=by, directives=directives or {})
            finally:
                inbox.close()
        return {"id": item.id, "text": item.text}

    def appropriate(self, amount: float) -> dict:
        from ..treasury import Treasury
        if amount <= 0:
            raise ValueError("amount must be positive")
        with self._write_lock:
            t = Treasury(self.base / "treasury.sqlite")
            try:
                t.appropriate(amount)
                snap = {"balance": t.balance(), "appropriated": t.total_appropriated(),
                        "spent": t.total_spent()}
            finally:
                t.close()
        return snap

    # --- tester feedback intake --------------------------------------------
    def intake_report(self, *, text, submitted_by="tester", url="", user_agent="",
                      viewport=None, state=None, screenshot_bytes=None,
                      screenshot_ext=None) -> dict:
        """Store a tester report (raw text + screenshot + captured state). In 4a a
        feedback item is created immediately; 4b defers this to the async Clerk when
        the ticketizer is on."""
        from ..reports import ReportStore
        store = ReportStore(self.base)
        report = store.create(text=text, submitted_by=submitted_by, url=url,
                              user_agent=user_agent, viewport=viewport or {},
                              state=state or {}, screenshot_bytes=screenshot_bytes,
                              screenshot_ext=screenshot_ext)
        from ..projectcfg import resolve_ticketizer
        if resolve_ticketizer(self.base):
            # Defer the feedback item to the async Clerk (distill -> ticket).
            store.set_ticket(report["id"], None, status="pending")
            self._clerk_executor.submit(self._clerk_job, report["id"], submitted_by)
            return {"report_id": report["id"], "feedback_id": None, "ticket_status": "pending"}
        fb = self.submit_feedback(
            text or "(no description)", by=submitted_by,
            directives={"report_id": report["id"], "source": "feedback-widget",
                        "state_summary": _summarize_state(report.get("state") or {})})
        store.set_feedback_id(report["id"], fb["id"])
        return {"report_id": report["id"], "feedback_id": fb["id"], "ticket_status": "none"}

    def _clerk_job(self, report_id, submitted_by):
        from ..reports import ReportStore
        from ..projectcfg import resolve_auto_run
        store = ReportStore(self.base)
        try:
            from ..clerk import distill_report, ticket_to_text
            if self._clerk_backend_factory is not None:
                backend = self._clerk_backend_factory()
            else:
                from ..llm import ClaudeCliBackend
                backend = ClaudeCliBackend()
            report = store.get(report_id)
            ticket = distill_report(backend, report)
            store.set_ticket(report_id, ticket, status="done")
            fb = self.submit_feedback(
                ticket_to_text(ticket), by=submitted_by,
                directives={"report_id": report_id, "source": "feedback-widget:clerk",
                            "severity": ticket.get("severity"),
                            "state_summary": _summarize_state(report.get("state") or {})})
            store.set_feedback_id(report_id, fb["id"])
            if resolve_auto_run(self.base):
                self.trigger_run(real=False, feedback_id=fb["id"], opts={})
        except Exception as e:  # the Clerk worker must never die
            try:
                store.set_ticket(report_id, None, status="error", error=str(e)[:300])
            except Exception:
                pass

    def reports(self) -> list:
        from ..reports import ReportStore
        return ReportStore(self.base).list()

    def report(self, report_id: str):
        from ..reports import ReportStore
        return ReportStore(self.base).get(report_id)

    # --- triggering a run (non-blocking) -----------------------------------
    def trigger_run(self, *, real: bool = False, feedback_id: str | None = None,
                    opts: dict | None = None) -> dict:
        job_id = uuid.uuid4().hex[:12]
        with self._jobs_lock:
            self._jobs[job_id] = {
                "job_id": job_id, "status": "queued", "real": bool(real),
                "feedback_id": feedback_id, "run_id": None, "outcome": None,
                "reason": None, "error": None,
            }
            while len(self._jobs) > self._max_jobs:   # bound memory (drop oldest)
                self._jobs.pop(next(iter(self._jobs)))
        self._executor.submit(self._run_job, job_id, bool(real), feedback_id, opts or {})
        return {"job_id": job_id, "status": "queued"}

    def _run_job(self, job_id, real, feedback_id, opts):
        self._set(job_id, status="running")
        try:
            from ..app import build_government
            from ..orchestrator import OrchestratorConfig
            cfg = OrchestratorConfig(
                max_revisions=int(opts.get("max_revisions", 2)),
                num_architects=int(opts.get("architects", 1)),
                constitutional_review=bool(opts.get("constitution_court", False)),
            )
            gov = build_government(self.base, agents="real" if real else "stub", config=cfg)
            try:
                if feedback_id:
                    item = next((it for it in gov.inbox.pending()
                                 if it.id == feedback_id), None)
                else:
                    item = gov.inbox.pop_next()
                if item is None:
                    reason = (f"feedback_id {feedback_id} not in the pending inbox"
                              if feedback_id else "no pending feedback")
                    self._set(job_id, status="done", reason=reason)
                    return
                res = gov.orchestrator.process(item)
                gov.inbox.mark_processed(item.id, res.run_id)
                self._set(job_id, status="done", run_id=res.run_id,
                          outcome=res.outcome.value, reason=res.reason)
            finally:
                gov.close()  # release SQLite handles (Windows file locks)
        except Exception as e:  # never let a job crash the worker
            self._set(job_id, status="error", error=str(e)[:300])

    # --- job introspection -------------------------------------------------
    def _set(self, job_id, **kw):
        with self._jobs_lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(kw)

    def job(self, job_id) -> dict | None:
        with self._jobs_lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None

    def jobs(self) -> list[dict]:
        with self._jobs_lock:
            return [dict(j) for j in self._jobs.values()]

    def shutdown(self, wait: bool = True):
        # wait=True by default: let an in-flight run finish rather than abandon it
        # mid-merge / mid-SQLite-write (which would corrupt state).
        self._executor.shutdown(wait=wait)
        self._clerk_executor.shutdown(wait=wait)
