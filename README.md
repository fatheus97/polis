# Polis — a separation-of-powers multi-agent coding system

Polis evolves a codebase using three branches of "government," each a distinct agent type
with distinct incentives, so that **no agent marks its own homework**:

- **Legislative — Architect** turns human feedback into PRDs ("laws").
- **Executive — Dev** implements PRDs into code (wraps a real coding agent for its "hands").
- **Judicial — Reviewer** checks code against the PRD and the constitution.

A deterministic **orchestrator** (the "rule of law") routes work through a fixed procedure and
is the *only* thing that can invoke the judiciary — the architect literally cannot reach the
reviewer. Three cross-cutting mechanisms are the brakes on a fully autonomous system:

- **Constitution** — invariants every agent is bound by; only the human amends them.
- **Treasury** — a finite budget every LLM call / spawn debits; the throttle on runaway cost.
- **Sandbox** — arbitrary code runs contained.

See [`docs/PRD.md`](docs/PRD.md) for the full design.

## Status: Phase 0 — Skeleton

The deterministic procedure with **stub agents** (no real LLM yet). The goal of Phase 0 is to
prove the machinery: the procedure routes, budgets, escalates, and persists correctly.

```
INTAKE → SPEC → IMPLEMENT → VERIFY → REVIEW → (MERGE | REVISE→IMPLEMENT) → DONE | ESCALATE
```

## Quick start (Phase 0, stdlib-only)

```powershell
# run the test suite (no dependencies required)
py -m unittest discover -s tests -v

# drive the procedure end-to-end from the CLI
py -m polis budget --appropriate 1000          # fund the treasury
py -m polis submit "Add a /health endpoint that returns ok"
py -m polis run                                 # process the next feedback item
py -m polis record --tail 30                    # read the audit log
```

## Roadmap

- **Phase 0** — deterministic skeleton + stub agents (this).
- **Phase 1** — real Architect / Dev (wraps Claude Code headless) / independent Reviewer;
  sequential; autonomous merge on `tests-green + approval`; sandboxed execution.
- **Phase 2** — specialist dev hiring; multi-architect voting; constitutional review of PRDs.
- **Phase 3** — parallel PRDs on worktrees; model decorrelation; deploy hooks.
