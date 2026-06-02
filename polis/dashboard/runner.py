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


class RunManager:
    def __init__(self, base):
        self.base = Path(base)
        self._executor = ThreadPoolExecutor(max_workers=1)  # serialize all runs
        self._write_lock = threading.Lock()                 # serialize inbox/treasury writes
        self._jobs: dict[str, dict] = {}
        self._jobs_lock = threading.Lock()

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
            item = None
            if feedback_id:
                item = next((it for it in gov.inbox.pending() if it.id == feedback_id), None)
            else:
                item = gov.inbox.pop_next()
            if item is None:
                self._set(job_id, status="done", reason="no pending feedback")
                return
            res = gov.orchestrator.process(item)
            gov.inbox.mark_processed(item.id, res.run_id)
            self._set(job_id, status="done", run_id=res.run_id,
                      outcome=res.outcome.value, reason=res.reason)
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

    def shutdown(self):
        self._executor.shutdown(wait=False)
