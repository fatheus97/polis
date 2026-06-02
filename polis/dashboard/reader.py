"""Tolerant, read-only access to the on-disk Polis state — stdlib only.

The dashboard is a SEPARATE process from any running orchestrator, so:
  * Reading record.jsonl mid-append can yield a partial last line -> skip unparseable
    lines (do NOT reuse Record.events(), which json.loads-raises).
  * SQLite is opened read-only (mode=ro URI) with a busy_timeout so a read can never
    block or lock-escalate the writer; connections are short-lived (open/close per call).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def read_record_events(path, *, tail: int | None = None) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # partial/corrupt line (e.g. the last line mid-write) — skip it
    if tail is not None and tail >= 0:
        return out[-tail:]
    return out


def open_ro_sqlite(db_path, busy_timeout_ms: int = 3000) -> sqlite3.Connection | None:
    p = Path(db_path)
    if not p.exists():
        return None
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


def read_treasury_snapshot(base) -> dict:
    default = {"balance": 0.0, "appropriated": 0.0, "spent": 0.0, "per_run": {}}
    conn = open_ro_sqlite(Path(base) / "treasury.sqlite")
    if conn is None:
        return default
    try:
        (bal,) = conn.execute("SELECT COALESCE(SUM(amount),0) FROM ledger").fetchone()
        (appr,) = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE kind='appropriation'").fetchone()
        (spent,) = conn.execute(
            "SELECT COALESCE(-SUM(amount),0) FROM ledger WHERE kind='debit'").fetchone()
        per_run = {rid: float(amt) for rid, amt in conn.execute(
            "SELECT run_id, COALESCE(-SUM(amount),0) FROM ledger "
            "WHERE kind='debit' AND run_id IS NOT NULL GROUP BY run_id")}
        return {"balance": float(bal), "appropriated": float(appr),
                "spent": float(spent), "per_run": per_run}
    except sqlite3.Error:
        return default
    finally:
        conn.close()


def read_runstore_rows(base) -> list[dict]:
    conn = open_ro_sqlite(Path(base) / "runs.sqlite")
    if conn is None:
        return []
    try:
        cur = conn.execute("SELECT * FROM runs ORDER BY updated_at")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def read_pending_feedback(base) -> list[dict]:
    conn = open_ro_sqlite(Path(base) / "feedback.sqlite")
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT id, text, submitted_by, created_at, directives FROM feedback "
            "WHERE status='pending' ORDER BY created_at")
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["directives"] = json.loads(r["directives"]) if r.get("directives") else {}
        return rows
    except sqlite3.Error:
        return []
    finally:
        conn.close()
