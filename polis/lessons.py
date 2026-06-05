"""The Lesson Store — experiential self-learning ("case law").

After an eligible run finishes, a Reflector distills ONE transferable :class:`Lesson`
(the *why* behind the outcome). This module is the deterministic half of the loop —
the orchestrator (not an LLM) stores, retrieves, and selects lessons:

  * storage   — SQLite (a content table) mirrored into an FTS5 index, plus an
                append-only ``lessons.jsonl`` audit sibling like the Record;
  * retrieval — a lexical FTS5 ``MATCH`` scoped by agent + discipline. **stdlib only:
                no embeddings, no vector DB** — that is a hard project constraint;
  * selection — :func:`classify_run`, a pure function mapping a terminal ``RunResult``
                to "reflect / don't, and with what polarity/scope".

Design notes:
  * Retrieval MUST NEVER raise into a live run — a stray character in a feedback string
    fed to FTS5 ``MATCH`` would otherwise crash every run. Queries are sanitized to bare
    tokens and the ``MATCH`` is wrapped defensively.
  * FTS5 is standard in CPython on Windows and Linux CI, but if a build lacks it we fall
    back to a ``LIKE`` scan (honest degradation) instead of failing.
  * Thread-safe like the Treasury/RunStore: one cross-thread connection + a lock, so
    parallel runs can share one store.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .models import Lesson, RunResult, to_jsonable

# Tokens this short or this common carry no retrieval signal — dropping them keeps the
# OR-query focused on the words that actually distinguish one situation from another.
_STOPWORDS = frozenset(
    "the and for that with this from into not but you are was were has have had will its"
    " der die das can use used add adds added make makes when then they them their".split()
)


def _fts_query(text: str) -> str:
    """Turn arbitrary feedback/PRD text into a safe FTS5 OR-query of bare tokens.

    Returns "" when there is no usable signal (caller then returns no lessons). Only
    ``[A-Za-z0-9_]`` survives, so the result can never contain the quotes/parens/``*``/``-``
    that make ``MATCH`` throw.
    """
    seen: list[str] = []
    for tok in re.findall(r"[A-Za-z0-9_]+", (text or "").lower()):
        if len(tok) < 3 or tok in _STOPWORDS or tok in seen:
            continue
        seen.append(tok)
        if len(seen) >= 12:
            break
    return " OR ".join(seen)


# --- terminal-state classification (pure, deterministic — NO prompt) ----------

@dataclass
class ReflectDecision:
    reflect: bool
    polarity: str = "pitfall"     # pitfall | good_practice
    scope: str = "dev"            # architect | dev | reviewer


def classify_run(res: RunResult, *, sample_good: bool = False) -> ReflectDecision:
    """Decide whether a finished run is worth a lesson, and of what kind.

    Keyed to the exact ``reason`` strings the orchestrator emits. Operational/transient
    escalations (budget, llm error, merge conflict, the parallel catch-all) teach nothing
    transferable and are EXCLUDED. ``sample_good`` (default off) gates whether clean
    first-attempt merges yield a 'good_practice' lesson — the caller decides per config.
    """
    reason = res.reason or ""
    if reason.startswith(("llm_error", "merge_conflict", "budget_exhausted", "error:")):
        return ReflectDecision(False)
    if reason.startswith("unconstitutional_prd"):
        return ReflectDecision(True, "pitfall", "architect")
    if reason.startswith("revisions_exhausted"):
        return ReflectDecision(True, "pitfall", "dev")
    if res.merged:
        if res.attempts > 0:                       # merged only after rework — the rework is the lesson
            return ReflectDecision(True, "pitfall", "dev")
        return ReflectDecision(sample_good, "good_practice", "dev")  # clean first try — gated off
    return ReflectDecision(False)                  # unknown escalation — stay conservative


# --- the store ----------------------------------------------------------------

_COLS = ("lesson_id", "run_id", "scope", "discipline", "polarity", "trigger",
         "guidance", "source_reason", "created_at", "uses", "wins", "status")


def _lesson_from_row(d: dict) -> Lesson:
    return Lesson(
        trigger=d["trigger"], guidance=d["guidance"], scope=d["scope"],
        discipline=d["discipline"], polarity=d["polarity"],
        source_reason=d["source_reason"] or "", run_id=d["run_id"] or "",
        id=d["lesson_id"], created_at=d["created_at"] or 0.0,
        uses=d["uses"] or 0, wins=d["wins"] or 0, status=d["status"],
    )


class LessonStore:
    def __init__(self, db_path: str | Path = ":memory:", jsonl_path: str | Path | None = None):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = Path(jsonl_path) if jsonl_path else None
        if self.jsonl_path:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lessons (
                lesson_id     TEXT PRIMARY KEY,
                run_id        TEXT,
                scope         TEXT,
                discipline    TEXT,
                polarity      TEXT,
                trigger       TEXT,
                guidance      TEXT,
                source_reason TEXT,
                created_at    REAL,
                uses          INTEGER DEFAULT 0,
                wins          INTEGER DEFAULT 0,
                status        TEXT DEFAULT 'active'
            )
            """
        )
        self._fts = self._init_fts()
        self.conn.commit()

    def _init_fts(self) -> bool:
        """Create the FTS5 mirror; return False (and fall back to LIKE) if FTS5 is absent."""
        try:
            self.conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts USING fts5(
                    trigger, guidance, discipline, lesson_id UNINDEXED, tokenize='porter')
                """
            )
            return True
        except sqlite3.OperationalError:
            import warnings
            warnings.warn("SQLite FTS5 unavailable — lesson retrieval falls back to a LIKE scan",
                          stacklevel=2)
            return False

    # --- writes ---------------------------------------------------------
    def add(self, lesson: Lesson) -> None:
        with self._lock:
            self.conn.execute(
                f"INSERT OR REPLACE INTO lessons ({','.join(_COLS)}) "
                f"VALUES ({','.join('?' * len(_COLS))})",
                (lesson.id, lesson.run_id, lesson.scope, lesson.discipline, lesson.polarity,
                 lesson.trigger, lesson.guidance, lesson.source_reason, lesson.created_at,
                 lesson.uses, lesson.wins, lesson.status),
            )
            if self._fts:
                self.conn.execute(
                    "INSERT INTO lessons_fts (trigger, guidance, discipline, lesson_id) "
                    "VALUES (?,?,?,?)",
                    (lesson.trigger, lesson.guidance, lesson.discipline or "", lesson.id),
                )
            self.conn.commit()
            if self.jsonl_path:
                with self.jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(to_jsonable(lesson)) + "\n")

    def mark_used(self, ids: list[str]) -> None:
        """Bump ``uses`` — called when lessons are injected into a run (lift denominator)."""
        self._bump("uses", ids)

    def mark_won(self, ids: list[str]) -> None:
        """Bump ``wins`` — called when a run that used these lessons MERGED (lift numerator)."""
        self._bump("wins", ids)

    def _bump(self, column: str, ids: list[str]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            self.conn.execute(
                f"UPDATE lessons SET {column} = {column} + 1 WHERE lesson_id IN ({placeholders})",
                ids)
            self.conn.commit()

    def set_status(self, lesson_id: str, status: str) -> None:
        """Curation: 'active' (promote), 'retired' (demote), or 'deleted' (hide)."""
        with self._lock:
            self.conn.execute("UPDATE lessons SET status=? WHERE lesson_id=?",
                              (status, lesson_id))
            self.conn.commit()

    def decay(self, *, min_uses: int = 5, min_win_rate: float = 0.34) -> list[str]:
        """Auto-retire lessons that have been injected enough times to judge (``min_uses``)
        yet rarely rode a merge (win rate below ``min_win_rate``) — the deterministic guard
        against a self-poisoning loop. Returns the ids retired. Lessons still proving useful,
        and young lessons without enough evidence yet, are left alone."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT lesson_id FROM lessons WHERE status='active' AND uses >= ? "
                "AND (CAST(wins AS REAL) / uses) < ?",
                (min_uses, min_win_rate)).fetchall()
            ids = [r[0] for r in rows]
            if ids:
                placeholders = ",".join("?" * len(ids))
                self.conn.execute(
                    f"UPDATE lessons SET status='retired' WHERE lesson_id IN ({placeholders})",
                    ids)
                self.conn.commit()
        return ids

    # --- reads ----------------------------------------------------------
    def retrieve(self, query: str, *, scope: str, discipline: str | None = None,
                 k: int = 3) -> list[Lesson]:
        """Top-``k`` active lessons for ``scope`` most relevant to ``query`` (lexical).

        Filters to the given ``scope``; when a ``discipline`` is supplied, matches that
        discipline plus generalist (NULL) lessons and ranks the exact-discipline ones
        first. NEVER raises — a bad query or FTS error yields ``[]``.
        """
        terms = _fts_query(query)
        if not terms:
            return []
        with self._lock:
            try:
                if self._fts:
                    rows, cols = self._retrieve_fts(terms, scope, discipline, k)
                else:
                    rows, cols = self._retrieve_like(terms, scope, discipline, k)
            except sqlite3.OperationalError:
                return []
        return [_lesson_from_row(dict(zip(cols, r))) for r in rows]

    def _retrieve_fts(self, terms, scope, discipline, k):
        sql = ["SELECT l.* FROM lessons_fts",
               "JOIN lessons l ON l.lesson_id = lessons_fts.lesson_id",
               "WHERE lessons_fts MATCH ? AND l.status='active' AND l.scope=?"]
        params: list = [terms, scope]
        if discipline is not None:
            sql.append("AND (l.discipline=? OR l.discipline IS NULL)")
            params.append(discipline)
            sql.append("ORDER BY CASE WHEN l.discipline=? THEN 0 ELSE 1 END, bm25(lessons_fts)")
            params.append(discipline)
        else:
            sql.append("ORDER BY bm25(lessons_fts)")
        sql.append("LIMIT ?")
        params.append(k)
        cur = self.conn.execute(" ".join(sql), params)
        return cur.fetchall(), [c[0] for c in cur.description]

    def _retrieve_like(self, terms, scope, discipline, k):
        # FTS5-absent fallback: pull active in-scope rows, rank in Python by how many
        # query tokens appear in trigger+guidance. Cheap and correct, just less precise.
        toks = terms.split(" OR ")
        sql = "SELECT * FROM lessons WHERE status='active' AND scope=?"
        params: list = [scope]
        if discipline is not None:
            sql += " AND (discipline=? OR discipline IS NULL)"
            params.append(discipline)
        cur = self.conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        scored = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            hay = f"{d['trigger']} {d['guidance']}".lower()
            score = sum(1 for t in toks if t in hay)
            if score:
                exact = 0 if (discipline is not None and d["discipline"] == discipline) else 1
                scored.append((exact, -score, r))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [r for _, _, r in scored[:k]], cols

    def get(self, lesson_id: str) -> Lesson | None:
        cur = self.conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (lesson_id,))
        row = cur.fetchone()
        if not row:
            return None
        return _lesson_from_row(dict(zip([c[0] for c in cur.description], row)))

    def all(self, *, include_retired: bool = False) -> list[Lesson]:
        sql = "SELECT * FROM lessons"
        if not include_retired:
            sql += " WHERE status='active'"
        sql += " ORDER BY created_at DESC"
        cur = self.conn.execute(sql)
        cols = [c[0] for c in cur.description]
        return [_lesson_from_row(dict(zip(cols, r))) for r in cur.fetchall()]

    def stats(self) -> dict:
        """Counts + aggregate usage, for the ``lessons`` CLI / dashboard panel."""
        cur = self.conn.execute(
            "SELECT scope, polarity, status, COUNT(*), COALESCE(SUM(uses),0), "
            "COALESCE(SUM(wins),0) FROM lessons GROUP BY scope, polarity, status")
        by = [{"scope": s, "polarity": p, "status": st, "count": c, "uses": u, "wins": w}
              for (s, p, st, c, u, w) in cur.fetchall()]
        (total, active, uses, wins) = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(status='active'),0), COALESCE(SUM(uses),0), "
            "COALESCE(SUM(wins),0) FROM lessons").fetchone()
        return {"total": total, "active": active, "uses": uses, "wins": wins, "groups": by}

    def close(self) -> None:
        self.conn.close()
