"""The Clerk — distills a raw tester report + captured web state into a structured
ticket for the architect. Administrative intake (not one of the three branches); runs
async at intake time. Distills but never discards — the raw report stays in the
ReportStore; only a clean ticket is handed to the legislature.

Stdlib + the LLM backend, so it's unit-testable with FakeLLM.
"""

from __future__ import annotations

from .llm import LLMError, extract_json
from .reports import console_errors

CLERK_SYSTEM = (
    "You are the Clerk of an autonomous software team. Turn a raw tester bug/feature "
    "report plus captured browser state into a single STRUCTURED ticket the architect "
    "can act on. Output ONLY a JSON object with keys: title (string), description "
    "(string), repro_steps (array of strings), expected (string), actual (string), "
    "severity (one of: trivial, minor, major, critical), affected_area (string), "
    "relevant_state (object — include ONLY the slice of captured state relevant to this "
    "report, e.g. the matching console errors or the relevant storage keys; OMIT cookies "
    "and full dumps). No prose."
)

SEVERITIES = {"trivial", "minor", "major", "critical"}


def _render_state(state: dict | None) -> str:
    state = state or {}
    console = state.get("console") or []
    errs = console_errors(console)
    chosen = errs[-20:] or console[-10:]
    lines = [f"[{c.get('level')}] " + " ".join(map(str, c.get("args", [])))[:300] for c in chosen]
    keys = list((state.get("localStorage") or {}).keys())[:15]
    return ("console (recent):\n" + ("\n".join(lines) if lines else "(none)") +
            f"\nlocalStorage keys: {keys}\nurl: {state.get('url')}")


def _fallback(text: str) -> dict:
    return {"title": (text[:60] or "Untitled report"), "description": text,
            "repro_steps": [], "expected": "", "actual": "", "severity": "minor",
            "affected_area": "", "relevant_state": {}}


def distill_report(backend, report: dict) -> dict:
    """report -> structured ticket. Degrades to a minimal ticket on any LLM/parse
    failure so a Clerk failure never blocks the pipeline (raw report is preserved)."""
    text = report.get("text") or ""
    prompt = (f"Tester report:\n{text}\n\n"
              f"URL: {report.get('url')}\nViewport: {report.get('viewport')}\n\n"
              f"Captured state:\n{_render_state(report.get('state'))}\n\n"
              "Return the ticket as JSON now.")
    try:
        resp = backend.complete(prompt, system=CLERK_SYSTEM, model="sonnet")
        d = extract_json(resp.text)
    except LLMError:
        return _fallback(text)
    sev = str(d.get("severity") or "minor").lower()
    return {
        "title": d.get("title") or text[:60] or "Untitled report",
        "description": d.get("description") or text,
        "repro_steps": list(d.get("repro_steps") or []),
        "expected": d.get("expected") or "",
        "actual": d.get("actual") or "",
        "severity": sev if sev in SEVERITIES else "minor",
        "affected_area": d.get("affected_area") or "",
        "relevant_state": d.get("relevant_state") or {},
    }


def ticket_to_text(ticket: dict) -> str:
    """Render the ticket as the feedback item's markdown (the architect's input)."""
    t = ticket
    parts = [f"# {t.get('title', '')}  [{t.get('severity', '')}]", "",
             t.get("description", ""), ""]
    if t.get("repro_steps"):
        parts.append("## Repro")
        parts += [f"{i + 1}. {s}" for i, s in enumerate(t["repro_steps"])]
        parts.append("")
    if t.get("expected"):
        parts.append(f"**Expected:** {t['expected']}")
    if t.get("actual"):
        parts.append(f"**Actual:** {t['actual']}")
    if t.get("affected_area"):
        parts.append(f"**Area:** {t['affected_area']}")
    return "\n".join(parts).strip()
