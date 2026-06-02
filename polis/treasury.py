"""The Treasury — power of the purse.

A SQLite-backed ledger. The sovereign *appropriates* a budget; every agent call
and every spawn *debits* it. The orchestrator checks affordability before each
agent call and ESCALATES cleanly when funds run out — this is the primary throttle
that stops a fully autonomous system from spending unboundedly.

SQLite means the ledger survives process restarts: a fresh ``Treasury`` pointed at
the same db sees the prior balance.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class InsufficientFunds(Exception):
    pass


class Treasury:
    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      REAL    NOT NULL,
                kind    TEXT    NOT NULL,   -- 'appropriation' | 'debit'
                actor   TEXT    NOT NULL,
                amount  REAL    NOT NULL,   -- positive=credit, negative=debit
                reason  TEXT,
                run_id  TEXT
            )
            """
        )
        self.conn.commit()

    # --- writes ---------------------------------------------------------
    def appropriate(self, amount: float, by: str = "sovereign") -> None:
        if amount <= 0:
            raise ValueError("appropriation must be positive")
        self._entry("appropriation", by, amount, "budget appropriation", None)

    def debit(self, actor: str, amount: float, reason: str, run_id: str | None = None) -> None:
        if amount < 0:
            raise ValueError("debit amount must be non-negative")
        if self.balance() < amount:
            raise InsufficientFunds(
                f"{actor} needs {amount} but treasury holds {self.balance()}"
            )
        self._entry("debit", actor, -amount, reason, run_id)

    def _entry(self, kind, actor, amount, reason, run_id):
        self.conn.execute(
            "INSERT INTO ledger (ts, kind, actor, amount, reason, run_id) VALUES (?,?,?,?,?,?)",
            (time.time(), kind, actor, amount, reason, run_id),
        )
        self.conn.commit()

    # --- reads ----------------------------------------------------------
    def balance(self) -> float:
        (total,) = self.conn.execute("SELECT COALESCE(SUM(amount), 0) FROM ledger").fetchone()
        return float(total)

    def can_afford(self, amount: float) -> bool:
        return self.balance() >= amount

    def total_appropriated(self) -> float:
        (t,) = self.conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE kind='appropriation'"
        ).fetchone()
        return float(t)

    def total_spent(self) -> float:
        (t,) = self.conn.execute(
            "SELECT COALESCE(-SUM(amount),0) FROM ledger WHERE kind='debit'"
        ).fetchone()
        return float(t)

    def spent_on(self, run_id: str) -> float:
        (t,) = self.conn.execute(
            "SELECT COALESCE(-SUM(amount),0) FROM ledger WHERE kind='debit' AND run_id=?",
            (run_id,),
        ).fetchone()
        return float(t)

    def close(self) -> None:
        self.conn.close()
