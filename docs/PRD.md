# PRD — "Polis": A Separation-of-Powers Multi-Agent Coding System

## Context

The user wants a multi-agent autonomous software-development system organized around the
**separation of powers** principle. Three branches of "government," each a distinct agent
type with distinct incentives, collaborate to evolve a codebase:

- **Legislative** (Architect) — turns human feedback into PRDs ("laws").
- **Executive** (Dev) — implements PRDs into code.
- **Judicial** (Reviewer) — checks code against the PRD and the constitution.

The core thesis (and the reason this beats a single super-agent): **forcing the spec-writer,
the implementer, and the reviewer to be different agents with different incentives prevents an
agent from marking its own homework.** Adversarial independence catches a class of errors a
solo agent rationalizes away.

The metaphor also contributes three load-bearing mechanisms, not just flavor:
- **Constitution** — immutable invariants every agent is bound by; only the human amends it.
- **Treasury / power of the purse** — a finite budget that every LLM call and every agent
  spawn debits; the throttle that stops runaway recursion and cost.
- **Human as sovereign** — the human sets the constitution + budget and supplies feedback,
  but does **not** gate individual changes (this build is fully autonomous).

### Decisions locked in during brainstorming

| Question | Decision |
|---|---|
| North star | **Ship real software.** Governance exists to improve correctness and control cost. |
| Target scope | **General-purpose** — pointable at any repo. (MVP proves it on one concrete repo.) |
| Substrate | **From scratch** — but only the *governance* is from scratch (see next row). |
| Dev's hands | **Wrap an existing coding agent** (Claude Code, headless) for actual file editing. |
| Human gates | **Fully autonomous** — no human approval before merge. |
| Concurrency | **One PRD at a time** for MVP; orchestrator built branch/worktree-ready for parallel later. |
| Model strategy | **Tiered by cost** — cheap/fast model for dev grunt work, strong model for architect + reviewer. |
| Review independence | **Fully independent** — the orchestrator invokes reviewers; the architect literally cannot reach the judiciary. |
| Build stack *(default — overridable)* | **Python** governance system, shelling out to the (Node) coding agent. |
| Merge bar *(default — overridable)* | **Tests green + independent reviewer approval** (both required). |

### Strategic framing

Because the dev wraps a proven coding agent, **almost all from-scratch code is governance** —
which is the only real differentiator (role-based "AI software companies" already exist:
ChatDev, MetaGPT, AutoGen, Devin, OpenHands). The novelty is the *governance layer*:
structural review independence, a constitution of invariants, budget-as-appropriations, and
human-as-sovereign.

Because the system is **fully autonomous + general-purpose + executes arbitrary code**, the
constitution, the independent review, the treasury, and the **sandbox** are not phase-2 polish —
they are the only brakes and must be critical-path.

---

## Architecture

```
┌─ SOVEREIGN (human) — sets Constitution + Treasury budget; submits feedback; reads the Record ─┐
│                                                                                                │
│   ┌──────────── THE PROCEDURE — deterministic orchestrator (Python, NOT an LLM) ────────────┐ │
│   │  routes work through a fixed state machine · enforces budget · owns escalation &         │ │
│   │  conflict resolution · persists all state · invokes the judiciary independently          │ │
│   └────────────────────────────────────────────────────────────────────────────────────────┘ │
│        │                          │                              │                              │
│   ┌────┴───────┐            ┌─────┴──────┐                 ┌─────┴──────┐                        │
│   │ LEGISLATIVE│            │  EXECUTIVE │                 │  JUDICIAL  │                        │
│   │ Architect  │── PRD ───▶ │  Dev       │── diff ───────▶ │  Reviewer  │                        │
│   │ writes PRDs│◀─ feedback │ wraps      │◀── revise ──────│ vs PRD +   │                        │
│   │ from human │   (up the  │ Claude Code│   (bounded loop)│ Constitution│                       │
│   │ feedback   │   procedure)│ (headless)│                 │ + test logs│                        │
│   └────────────┘            └────────────┘                 └────────────┘                        │
│                                                                                                  │
│   Cross-cutting state: Registry (role templates ↔ live instances) · Treasury (budget ledger) ·   │
│                        Constitution (versioned invariants) · Record (append-only event log)      │
│                        Workspace (git repo, sandboxed execution)                                  │
└──────────────────────────────────────────────────────────────────────────────────────────────-─┘
```

### Components

1. **The Procedure (Orchestrator)** — a deterministic Python state machine. This is the "rule
   of law": the *procedure* is fixed and predictable even though the *officials* (agents) are
   non-deterministic. Stages:
   `INTAKE → SPEC → IMPLEMENT → VERIFY → REVIEW → (MERGE | REVISE→IMPLEMENT) → DONE | ESCALATE`.
   Owns: routing, budget checks before every agent call, bounded retry counts, the
   deadlock/escalation path, and persistence. **Reviewers are invoked here — never by the
   architect** (this is how review independence is enforced structurally).

2. **Legislative — Architect** (strong model). Pulls items from the human **feedback inbox** +
   current repo state → produces or updates a **structured PRD** (goal, acceptance criteria,
   constraints, out-of-scope). Cannot reach the judiciary.

3. **Executive — Dev** (tiered: cheap model orchestrating, delegates real work). A thin
   governance wrapper: PRD → coder task → invokes **Claude Code headless** on an isolated
   branch → returns the diff. *(Phase 2: specialist subtypes — frontend/backend/db/infra/
   devops/prompt-eng — spawned on demand from registry templates.)*

4. **Judicial — Reviewer** (strong model), orchestrator-invoked and independent. Reads
   `diff + PRD + constitution + test results` → structured verdict (`approve` |
   `reject + actionable feedback`). Prompted to **default to reject when uncertain**.

5. **The Constitution** — a versioned file of invariants (e.g. "never commit secrets," security
   rules, architecture constraints, budget caps, style). Reviewers check PRDs/code against it.
   **Only the human amends it.**

6. **The Treasury** — a budget ledger. Every LLM call and every spawn debits it. The human
   appropriates a total; the orchestrator enforces per-task and global caps and **halts cleanly
   at zero** (→ ESCALATE). This is the primary runaway-cost/recursion throttle.

7. **The Registry** — role *templates* (job-description prompts) vs live *instances*. "Hire" =
   instantiate a template; "release" = kill the instance, keep the template. MVP ships a fixed
   set (architect, dev, reviewer); the spawn/release machinery is built so Phase 2 can add
   specialists without rework.

8. **The Record** — an append-only JSONL event log of every action (who / what / when / cost /
   verdict). The "fourth estate": observability, audit, and replay/rollback.

9. **Human interfaces (CLI / file-based for MVP)**:
   - **Feedback inbox** — testers submit items (a queue file or simple CLI).
   - **Sovereign console** — set/amend constitution, set budget, tail the Record, submit feedback.

### MVP loop (sequential, autonomous)

```
feedback item
   → Architect writes/updates PRD
   → Dev wraps Claude Code, implements on a fresh branch
   → Orchestrator runs the project's test suite (discovered/configured) in a sandbox
   → Reviewer (independent) checks diff vs PRD vs constitution, sees test results
   → tests green AND approved → auto-merge to main (a revertible commit)
   → reject → bounded REVISE loop back to Dev (with the reviewer's feedback)
   → deadlock / retries exhausted / budget zero → ESCALATE to human; record everything
```

---

## Build stack & key technical choices

- **Governance system:** Python. Deterministic orchestrator (explicit state machine), registry,
  treasury, constitution loader, event log.
- **Dev's hands:** Claude Code in headless mode, invoked per task; returns a diff on a branch.
- **LLM calls:** Anthropic API, tiered — Haiku-class for cheap/grunt ops (dev wrapper, intake),
  Opus/Sonnet-class for Architect + Reviewer.
- **State:** SQLite for structured state + append-only JSONL for the Record. Git for the
  workspace; branches now, **worktree-ready** for future parallel PRDs.
- **Sandbox:** all dev/test execution runs in a container (e.g. Docker) — a safety boundary,
  mandatory because the system executes arbitrary code autonomously.
- **PRD format:** structured Markdown with a fixed schema (goal · acceptance criteria ·
  constraints · out-of-scope), so the reviewer can check against criteria mechanically.

---

## Phased roadmap

- **Phase 0 — Skeleton.** Orchestrator state machine + registry + treasury + event log +
  constitution loader, with **stub agents** (no real LLM). Goal: prove the procedure routes,
  budgets, escalates, and persists correctly.
- **Phase 1 — Single-branch government (MVP).** Real Architect, Dev (wraps Claude Code),
  independent Reviewer; strictly sequential; autonomous merge on `tests-green + approval`; one
  concrete target repo; CLI feedback inbox + sovereign console; sandboxed execution.
- **Phase 2 — Specialization & deliberation.** Specialist dev subtypes + on-demand hiring from
  registry templates; multi-architect proposal + vote on undecidable design calls;
  constitutional review of PRDs (the "constitutional court").
- **Phase 3 — Parallel legislature.** Multiple PRDs in flight on git worktrees + merge
  resolution; model decorrelation (different families per branch); richer human UI; optional
  project-defined deploy hooks.

---

## Out of scope for the MVP

Specialist dev hiring · multi-architect voting · constitutional court over PRDs · parallel PRDs ·
model decorrelation · autonomous **deploy** (merge to main is the terminal autonomous action;
deploy is an optional project-defined hook for Phase 3) · web UIs.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Runaway agent spawning / token cost | Treasury hard caps (per-task + global) + bounded retry counts; orchestrator halts at zero budget. |
| Autonomous bad merge | `tests-green + independent review + constitution` gate; every change is a revertible commit; the Record enables replay/rollback. |
| Arbitrary code execution | All dev/test execution sandboxed in a container. |
| Reviewer rubber-stamping (shared blind spots, since models aren't decorrelated) | Structural independence now (orchestrator-invoked); "default to reject when uncertain" prompt; model decorrelation reserved as a Phase-3 lever. |
| Spec ↔ code deadlock (dev: "spec is wrong" / reviewer: "you didn't follow it") | Bounded REVISE loop, then orchestrator ESCALATES to the human; never sideways between branches. |
| Repo has no test suite | Architect/dev must add minimal verification; if genuinely impossible, fall back to reviewer-only with a logged caveat in the Record. |

---

## Verification (how to test end-to-end)

1. **Pick one real target repo** (e.g. a small web app) as the MVP proving ground.
2. **Seed a feedback item** ("add feature X" / "fix bug Y") into the feedback inbox.
3. **Run the orchestrator** and tail the Record; confirm the state machine walks
   `SPEC → IMPLEMENT → VERIFY → REVIEW → MERGE`.
4. **Assert on artifacts:** a structured PRD file was produced; a branch with a non-empty diff
   exists; the test suite actually ran; a reviewer verdict is recorded; a merge commit landed
   (or an ESCALATE event if it failed); the treasury was debited and total spend stayed within
   budget.
5. **Negative tests:**
   - **Constitution violation** — dev attempts to commit a secret → reviewer must catch and
     block it (no merge; REVISE or ESCALATE).
   - **Budget exhaustion** — set a tiny budget → orchestrator must halt cleanly and ESCALATE,
     never loop forever.
   - **Review independence** — confirm via the Record that the reviewer was invoked by the
     orchestrator, with no architect→reviewer call path.

---

## First implementation step (post-approval)

Scaffold the repo at `C:\multi_coder`, commit this PRD as `docs/PRD.md`, then build **Phase 0**
(the deterministic skeleton with stub agents) before wiring in any real LLM calls.
