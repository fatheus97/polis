"""Report store — tester feedback reports (raw text + screenshot + captured state).

Stdlib only. Each report is a directory under ``<base>/reports/<id>/``:

    report.json        canonical record (atomic write via os.replace)
    screenshot.<ext>   the uploaded image, if any

The raw report is ALWAYS preserved here; the Clerk's distilled ticket is stored back
onto the same record (``ticket`` / ``ticket_status``) but never replaces the raw text.
Touched from request threads AND the Clerk worker -> a lock guards read-modify-write;
reads are lock-free (os.replace makes report.json atomic, so a reader never sees a
partial file).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .models import gen_id


def _safe_ext(ext: str | None) -> str:
    return ("".join(c for c in (ext or "") if c.isalnum())[:5]) or "png"


ERROR_LEVELS = ("error", "uncaught", "unhandledrejection")


def console_errors(console) -> list:
    """The error-level entries of a captured console buffer (shared by runner + clerk)."""
    return [c for c in (console or []) if c.get("level") in ERROR_LEVELS]


class ReportStore:
    def __init__(self, base):
        self.root = Path(base) / "reports"
        self._lock = threading.Lock()

    def _dir(self, rid: str) -> Path:
        return self.root / rid

    def _json(self, rid: str) -> Path:
        return self._dir(rid) / "report.json"

    def _write(self, rid: str, data: dict) -> None:
        d = self._dir(rid)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "report.json.tmp"
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._json(rid))  # atomic

    def create(self, *, text: str, submitted_by: str = "tester", url: str = "",
               user_agent: str = "", viewport: dict | None = None,
               state: dict | None = None, screenshot_bytes: bytes | None = None,
               screenshot_ext: str | None = None) -> dict:
        rid = gen_id("rpt")
        with self._lock:
            d = self._dir(rid)
            d.mkdir(parents=True, exist_ok=True)
            shot = None
            if screenshot_bytes:
                shot = f"screenshot.{_safe_ext(screenshot_ext)}"
                (d / shot).write_bytes(screenshot_bytes)
            # Never persist raw session cookies to disk — keep a hint, drop the values.
            safe_state = dict(state or {})
            if safe_state.get("cookies"):
                safe_state["cookies"] = "[redacted]"
            data = {
                "id": rid,
                "created_at": time.time(),
                "submitted_by": submitted_by,
                "text": text,
                "url": url,
                "user_agent": user_agent,
                "viewport": viewport or {},
                "state": safe_state,
                "screenshot": shot,
                "screenshot_size": len(screenshot_bytes) if screenshot_bytes else 0,
                "feedback_id": None,
                "ticket": None,
                "ticket_status": "none",   # none | pending | done | error
                "ticket_error": None,
            }
            self._write(rid, data)
            return data

    def get(self, rid: str) -> dict | None:
        p = self._json(rid)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def list(self, limit: int = 100) -> list[dict]:
        if not self.root.exists():
            return []
        out = []
        for d in self.root.iterdir():
            if not d.is_dir():
                continue
            data = self.get(d.name)
            if data:
                out.append(data)
        out.sort(key=lambda r: r.get("created_at", 0), reverse=True)
        return out[:limit]

    def _update(self, rid: str, **fields) -> dict | None:
        with self._lock:
            data = self.get(rid)
            if data is None:
                return None
            data.update(fields)
            self._write(rid, data)
            return data

    def set_feedback_id(self, rid: str, feedback_id: str) -> dict | None:
        return self._update(rid, feedback_id=feedback_id)

    def set_ticket(self, rid: str, ticket, status: str, error: str | None = None) -> dict | None:
        return self._update(rid, ticket=ticket, ticket_status=status, ticket_error=error)

    def screenshot_path(self, rid: str) -> Path | None:
        data = self.get(rid)
        if not data or not data.get("screenshot"):
            return None
        p = self._dir(rid) / data["screenshot"]
        return p if p.exists() else None
