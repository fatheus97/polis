"""The Feedback Inbox — where citizens (testers) petition the legislature.

A SQLite-backed FIFO queue of feedback items. The architect pulls the next pending
item; the orchestrator marks it processed once a run completes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import FeedbackItem


class FeedbackInbox:
    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id           TEXT PRIMARY KEY,
                text         TEXT NOT NULL,
                submitted_by TEXT,
                created_at   REAL,
                status       TEXT DEFAULT 'pending',   -- pending | processed
                directives   TEXT,                     -- json
                run_id       TEXT
            )
            """
        )
        self.conn.commit()

    def submit(self, text, submitted_by="tester", directives=None) -> FeedbackItem:
        item = FeedbackItem(text=text, submitted_by=submitted_by, directives=directives or {})
        self.conn.execute(
            "INSERT INTO feedback (id, text, submitted_by, created_at, status, directives) "
            "VALUES (?,?,?,?, 'pending', ?)",
            (item.id, item.text, item.submitted_by, item.created_at,
             json.dumps(item.directives)),
        )
        self.conn.commit()
        return item

    def _to_item(self, row) -> FeedbackItem:
        id_, text, by, created_at, _status, directives, _run = row
        return FeedbackItem(
            id=id_, text=text, submitted_by=by, created_at=created_at,
            directives=json.loads(directives) if directives else {},
        )

    def pop_next(self) -> FeedbackItem | None:
        row = self.conn.execute(
            "SELECT id, text, submitted_by, created_at, status, directives, run_id "
            "FROM feedback WHERE status='pending' ORDER BY created_at LIMIT 1"
        ).fetchone()
        return self._to_item(row) if row else None

    def mark_processed(self, feedback_id: str, run_id: str) -> None:
        self.conn.execute(
            "UPDATE feedback SET status='processed', run_id=? WHERE id=?",
            (run_id, feedback_id),
        )
        self.conn.commit()

    def pending(self) -> list[FeedbackItem]:
        rows = self.conn.execute(
            "SELECT id, text, submitted_by, created_at, status, directives, run_id "
            "FROM feedback WHERE status='pending' ORDER BY created_at"
        ).fetchall()
        return [self._to_item(r) for r in rows]

    def close(self) -> None:
        self.conn.close()
