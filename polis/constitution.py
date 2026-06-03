"""The Constitution — invariants every agent is bound by.

In a fully autonomous system this is one of the few brakes, so it is *machine
checkable*: rules carry a regex the judiciary runs over every diff. Only the
sovereign (the human) may amend the constitution — agents only read it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .models import Diff, Violation

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "constitution.json"


@dataclass
class Rule:
    id: str
    description: str
    severity: str  # "block" (must reject) | "warn" (logged, non-blocking)
    check: str  # "regex" | "manual"
    pattern: str | None = None
    target: str = "content"  # "content" (scan diff lines) | "path" (match the file path)

    def compiled(self):
        return re.compile(self.pattern) if (self.check == "regex" and self.pattern) else None


class Constitution:
    def __init__(self, rules: list[Rule], version: int = 1, amendable_by: str = "sovereign"):
        self.rules = rules
        self.version = version
        self.amendable_by = amendable_by

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Constitution":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rules = [Rule(**r) for r in data.get("rules", [])]
        return cls(rules, data.get("version", 1), data.get("amendable_by", "sovereign"))

    def check_diff(self, diff: Diff) -> list[Violation]:
        """Return every regex rule a file change breaches (line-by-line)."""
        violations: list[Violation] = []
        for rule in self.rules:
            rx = rule.compiled()
            if rx is None:
                continue
            for change in diff.changes:
                if rule.target == "path":
                    if rx.search(change.path):
                        violations.append(Violation(rule_id=rule.id, severity=rule.severity,
                                                    path=change.path, snippet=change.path))
                    continue
                for line in change.content.splitlines():
                    if rx.search(line):
                        violations.append(
                            Violation(
                                rule_id=rule.id,
                                severity=rule.severity,
                                path=change.path,
                                snippet=line.strip()[:160],
                            )
                        )
        return violations

    def scan_text(self, text: str) -> list[Rule]:
        """Return every regex rule that matches a plain string (used to vet a PRD's
        text for clauses that would mandate a constitutional violation)."""
        hits = []
        for rule in self.rules:
            rx = rule.compiled()
            if rx is not None and rx.search(text):
                hits.append(rule)
        return hits

    @property
    def blocking_ids(self) -> set[str]:
        return {r.id for r in self.rules if r.severity == "block"}
