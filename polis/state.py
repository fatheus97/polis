"""Structured run state (SQLite).

The Record (JSONL) is the full event history; this is the queryable summary — one
row per run — so the CLI and tests can ask "what happened to this run?" without
replaying the log. Like the Treasury, it survives restarts.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .models import FeedbackItem, RunResult, Stage


class RunStore:
    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id        TEXT PRIMARY KEY,
                feedback_id   TEXT,
                feedback_text TEXT,
                outcome       TEXT,
                last_stage    TEXT,
                reason        TEXT,
                attempts      INTEGER,
                spend         REAL,
                merge_commit  TEXT,
                prd_id        TEXT,
                updated_at    REAL
            )
            """
        )
        self.conn.commit()

    def save(self, res: RunResult, feedback: FeedbackItem) -> None:
        with self._lock:
            self._save(res, feedback)

    def _save(self, res: RunResult, feedback: FeedbackItem) -> None:
        self.conn.execute(
            """
            INSERT INTO runs (run_id, feedback_id, feedback_text, outcome, last_stage,
                              reason, attempts, spend, merge_commit, prd_id, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET
                outcome=excluded.outcome, last_stage=excluded.last_stage,
                reason=excluded.reason, attempts=excluded.attempts, spend=excluded.spend,
                merge_commit=excluded.merge_commit, prd_id=excluded.prd_id,
                updated_at=excluded.updated_at
            """,
            (
                res.run_id, feedback.id, feedback.text,
                res.outcome.value if isinstance(res.outcome, Stage) else res.outcome,
                res.last_stage.value if isinstance(res.last_stage, Stage) else res.last_stage,
                res.reason, res.attempts, res.spend, res.merge_commit,
                res.prd.id if res.prd else None, time.time(),
            ),
        )
        self.conn.commit()

    def get(self, run_id: str) -> dict | None:
        cur = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, row))

    def all(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM runs ORDER BY updated_at")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
