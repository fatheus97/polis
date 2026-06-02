"""The Record — an append-only JSONL log of everything the government does.

This is the "fourth estate": observability, audit, and the data backing two
governance guarantees —
  * review independence: every judicial event carries ``source="procedure"``,
    proving the orchestrator (not the architect) invoked the reviewer;
  * accountability: every spend, verdict, and state transition is on the record.

Append-only by design: events are facts that happened, never edited.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .models import Branch, Stage, to_jsonable


class Record:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        run_id: str,
        stage: Stage,
        actor: str,
        kind: str,
        source: Branch = Branch.PROCEDURE,
        cost: float = 0.0,
        **payload,
    ) -> dict:
        event = {
            "ts": time.time(),
            "run_id": run_id,
            "stage": stage.value if isinstance(stage, Stage) else stage,
            "actor": actor,
            "source": source.value if isinstance(source, Branch) else source,
            "kind": kind,
            "cost": cost,
            "payload": to_jsonable(payload),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
        return event

    def events(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def tail(self, n: int = 20) -> list[dict]:
        return self.events()[-n:]
