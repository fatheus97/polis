"""Pure derivation layer for the dashboard — stdlib only, no I/O, no FastAPI.

Turns a flat list of Record events into run summaries, per-run timelines, and overview
stats. Kept pure so it is trivially unit-testable with synthetic events and so the core
test suite never needs the dashboard extra.

The Record is the only source that shows IN-FLIGHT runs (RunStore is written only when a
run finishes), so everything here is derived from events: a run is in-flight iff it has
no terminal `done`/`escalate` event.

Note on branch attribution: agent events carry source="procedure" (the independence
guarantee — the procedure invokes everyone), so the acting branch for UI/colour and
by-branch spend comes from the ACTOR prefix ("legislative:architect" -> legislative),
not from `source`.
"""

from __future__ import annotations

# Canonical stage order for the timeline strip.
STAGE_ORDER = [
    "INTAKE", "SPEC", "CONSTITUTIONAL", "IMPLEMENT", "VERIFY", "REVIEW",
    "MERGE", "REVISE", "DONE", "ESCALATE",
]

TERMINAL_KINDS = {"done", "escalate"}


def event_branch(event: dict) -> str:
    """The acting branch, derived from the actor prefix (not `source`)."""
    actor = event.get("actor", "") or ""
    return actor.split(":", 1)[0] if actor else "procedure"


def _cost(event: dict) -> float:
    return float(event.get("cost") or 0.0)


def group_by_run(events: list[dict]) -> dict[str, list[dict]]:
    """Group events by run_id, preserving append order within each run."""
    groups: dict[str, list[dict]] = {}
    for e in events:
        groups.setdefault(e.get("run_id", "?"), []).append(e)
    return groups


def _first(events: list[dict], kind: str) -> dict | None:
    return next((e for e in events if e.get("kind") == kind), None)


def run_summary(run_events: list[dict]) -> dict:
    """One-line summary of a run derived entirely from its events."""
    evs = run_events
    kinds = [e.get("kind") for e in evs]
    done = _first(evs, "done")
    esc = _first(evs, "escalate")
    in_flight = done is None and esc is None
    outcome = "DONE" if done else ("ESCALATE" if esc else None)

    intake = _first(evs, "intake")
    feedback_id = intake["payload"].get("feedback_id") if intake else None
    feedback_text = intake["payload"].get("text") if intake else None

    # PRD title: single-architect `prd`, else the winning panel `proposal`.
    prd_title = prd_id = None
    prd_ev = _first(evs, "prd")
    if prd_ev:
        prd_title = prd_ev["payload"].get("title")
        prd_id = prd_ev["payload"].get("prd_id")
    else:
        elected = _first(evs, "elected")
        if elected:
            prd_id = elected["payload"].get("prd_id")
            winner = elected["payload"].get("winner")
            win = next((e for e in evs if e.get("kind") == "proposal"
                        and e["payload"].get("index") == winner), None)
            if win:
                prd_title = win["payload"].get("title")

    hire = _first(evs, "hire")
    discipline = hire["payload"].get("discipline") if hire else None

    merge = _first(evs, "merge")
    merge_commit = (merge["payload"].get("commit") if merge
                    else (done["payload"].get("commit") if done else None))

    attempt_idxs = [e["payload"].get("attempt", 0) for e in evs
                    if e.get("kind") in ("diff", "revise")]
    attempts = max(attempt_idxs) if attempt_idxs else 0

    reason = (esc["payload"].get("reason") if esc
              else ("merged" if done else ""))

    return {
        "run_id": evs[0].get("run_id"),
        "started_ts": evs[0].get("ts"),
        "last_ts": evs[-1].get("ts"),
        "in_flight": in_flight,
        "outcome": outcome,
        "last_stage": evs[-1].get("stage"),
        "spend": round(sum(_cost(e) for e in evs), 6),
        "attempts": attempts,
        "discipline": discipline,
        "prd_title": prd_title,
        "prd_id": prd_id,
        "merge_commit": merge_commit,
        "feedback_id": feedback_id,
        "feedback_text": feedback_text,
        "reason": reason,
        "event_count": len(evs),
    }


def run_list(events: list[dict]) -> list[dict]:
    """All runs as summaries, newest first; in-flight runs floated to the top."""
    summaries = [run_summary(evs) for evs in group_by_run(events).values()]
    summaries.sort(key=lambda s: (not s["in_flight"], -(s["started_ts"] or 0)))
    return summaries


def run_detail(run_events: list[dict]) -> dict:
    """Summary + a colourable timeline + a per-stage strip for one run."""
    timeline = []
    for e in run_events:
        timeline.append({
            "ts": e.get("ts"),
            "stage": e.get("stage"),
            "actor": e.get("actor"),
            "branch": event_branch(e),
            "kind": e.get("kind"),
            "cost": _cost(e),
            "payload": e.get("payload", {}),
        })
    visited = {e.get("stage") for e in run_events}
    stage_strip = [{"stage": s, "visited": s in visited} for s in STAGE_ORDER]
    return {**run_summary(run_events), "timeline": timeline, "stage_strip": stage_strip}


def overview(events: list[dict]) -> dict:
    """Aggregate stats across all runs."""
    runs = run_list(events)
    by_branch: dict[str, float] = {}
    for e in events:
        b = event_branch(e)
        by_branch[b] = round(by_branch.get(b, 0.0) + _cost(e), 6)
    return {
        "total_runs": len(runs),
        "in_flight": sum(1 for r in runs if r["in_flight"]),
        "done": sum(1 for r in runs if r["outcome"] == "DONE"),
        "escalate": sum(1 for r in runs if r["outcome"] == "ESCALATE"),
        "total_spend": round(sum(_cost(e) for e in events), 6),
        "by_branch_spend": by_branch,
    }
