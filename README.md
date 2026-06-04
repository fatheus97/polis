# Polis — a separation-of-powers multi-agent coding system

[![CI](https://github.com/fatheus97/polis/actions/workflows/ci.yml/badge.svg)](https://github.com/fatheus97/polis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

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

## Quickstart — open the dashboard and give feedback

You drive Polis from the **dashboard**: a local web panel where you give feedback, watch it work, and
approve runs. After a **one-time setup per project**, starting work is a *single* command —
`py -m polis --base <name> dashboard`. `--base <name>` is just a folder where that project's state
lives (budget, config, history, workspace); pick any name — but **start it with `.polis-`** so git
ignores it (`.gitignore` covers `.polis-*/`). Same name = same project; reuse it each session.

Install the dashboard extra once:

```powershell
py -m pip install -e ".[dashboard]"
```

### A) Work on your own app (the usual case)

```powershell
# one-time, for this project
py -m polis --base .polis-myapp config --repo C:\path\to\your-app   # the repo Polis develops
py -m polis --base .polis-myapp budget --appropriate 20            # fund it (real runs cost money)

# every session — just this:
py -m polis --base .polis-myapp dashboard                          # opens http://127.0.0.1:8765
```

In the browser: type what you want built or fixed in the **feedback box** (under *Control panel*),
click **Submit**, then **Run next** — and watch it move through the stage timeline. That's the whole loop.

### B) Polis on its own dashboard (self-development)

To improve Polis's *own* dashboard with a loop that actually closes (give feedback → **see** the
fix), **run the dashboard from a dedicated clone and point Polis at that same clone.** Then the
dashboard you use *is* the repo Polis edits:

```powershell
# one-time
git clone <your-polis-repo-url> ../polis-dev      # a repo you can push to (your fork)
cd ../polis-dev                                    # run everything from the clone
py -m polis --base .polis-selfdev config --repo . --testing-mode on --merge-via-pr on --restart-on-merge on --grounded-agents on --dev-plan-model opus
py -m polis --base .polis-selfdev budget --appropriate 20

# every session (from ../polis-dev):
py -m polis --base .polis-selfdev dashboard        # serves THIS clone
```

> **Requires** `gh auth login` + a repo with CI (a fork of this repo has both) — `--merge-via-pr on`
> opens a real PR and waits for the checks.

In the browser: click the **🐞** button (bottom-right) → describe the issue, optionally paste a
screenshot → **Submit**. It becomes a ticket in the **Tester reports** panel; open it and click
**▶ Run this ticket**. Polis opens a PR — **review and merge it.**

**Why run *from* the clone?** The dashboard always serves files from whichever checkout you run
`py -m polis` in — **not** from `--repo` (which only sets where Polis *develops*). Running from the
clone makes them the same. To keep the UI stable while a run rewrites these very files, the dashboard
serves the assets it **snapshotted at startup**; the merged version is applied on the next restart —
which **`--restart-on-merge on`** does for you automatically (a thin supervisor relaunches the
dashboard the moment a self-dev change merges, so the new code *and* UI just appear).

**What the one-time flags mean** (they persist, so later sessions are just `dashboard`):

| flag | what it does |
| --- | --- |
| `config --repo <path>` | the repo Polis develops (use a dedicated clone for self-dev) |
| `config --testing-mode on` | injects the 🐞 feedback widget (off → no widget) |
| `config --merge-via-pr on` | lands changes via a CI-gated GitHub PR, never local `main` |
| `config --restart-on-merge on` | auto-restarts the dashboard after a self-dev merge so changes show |
| `config --grounded-agents on` | agents read the real repo (+ screenshot) instead of working blind — costs more, but verifies against reality |
| `config --dev-plan-model opus` | the dev plans with Opus (read-only) then executes with `dev_model` — best quality, priciest (drop to economize) |
| `budget --appropriate 20` | funds the treasury; real runs draw from it (leave `auto_run` off) |

Everything below is **background** — how it works, the build phases, the full CLI. For day-to-day use,
the two recipes above are all you need.

## Status: Phase 4 — autonomous tester feedback loop

Real, model-backed officials run a procedure with legislative deliberation, review of the law,
**multiple PRDs in flight at once**, and now a **closed feedback loop from human testers**:

```
INTAKE → SPEC → [CONSTITUTIONAL] → IMPLEMENT → VERIFY → REVIEW → (MERGE | REVISE) → [DEPLOY] → DONE | ESCALATE
         (1 architect, or a panel that proposes + votes)
```

**Phase 4 features (opt-in, `testing_mode`):**
- **Tester feedback widget** — a floating widget injected into apps Polis develops (and the
  dashboard itself); a tester describes an issue, pastes a screenshot, and submits. The widget
  auto-captures console/storage/cookies/url so the report carries its own context.
- **The Clerk** — an async ticketizer that distills each raw report + captured state into a
  structured ticket and files it as architect feedback (config-gated; default on, the raw
  report is always preserved).
- **protect-core** — a constitution path-rule so that when Polis develops *its own* repo it can
  only touch `polis/dashboard/`, tests, and docs — never its orchestration core, and it cannot
  weaken the constitution itself.

**Phase 3 features (opt-in):**
- **Parallel PRDs on git worktrees** — `run --parallel N` runs N pending PRDs concurrently,
  each in its own isolated worktree; merges into `main` are serialized, and a branch that can't
  apply cleanly raises a conflict that ESCALATES. Shared state (treasury/record/registry) is
  thread-safe.
- **Model decorrelation** — per-role models (`--architect-model/--dev-model/--review-model`) and
  a `--decorrelate` convenience that puts the reviewer on a different model than the dev to
  reduce shared blind spots. *(The `claude` CLI is Claude-only, so this is cross-tier, not
  cross-provider; the `LLMBackend` seam is where a second provider would slot in.)*
- **Deploy hooks** — `--deploy "<cmd>"` runs a command against merged `main` after a successful
  merge; a deploy failure is recorded but never un-does the merge.

**Phase 2 features (all opt-in; defaults = Phase 1):**
- **Specialist hiring** — the architect tags each PRD with a `discipline`; the orchestrator
  *hires* the matching specialist dev (frontend/backend/database/infra/devops/cli/prompt), and
  synthesizes an expert on the fly for any discipline it doesn't already know.
- **Multi-architect voting** — `--architects N` convenes a panel that each writes a proposal
  independently, then votes; the winning PRD proceeds (the deliberation is on the Record).
- **Constitutional court** — `--constitution-court` adds a judge that reviews the PRD against
  the constitution *before* code is written; an unconstitutional PRD is sent back to the
  architect to amend (bounded), then ESCALATES.

**Phase 1 foundation:**

- **Architect** & **Reviewer** — the authenticated `claude` CLI in headless JSON mode
  (read-only; they never touch the Polis repo).
- **Dev** — wraps **Claude Code** to edit files in the workspace; its edits are captured as a
  diff for review.
- **Independent review** — the orchestrator is the only caller of the reviewer; the reviewer's
  verdict is backed by **hard gates** (tests-green + constitution scan) that override a lenient
  model.
- **Real cost** — every call debits the Treasury its actual USD cost; transient API errors are
  retried, and a persistent infra failure ESCALATES (it is never mistaken for a rejection).
- **Sandbox** — `LocalSandbox` by default; `DockerSandbox` (no-network container) is available
  when the Docker daemon is running.

Stub agents (Phase 0) remain the default and power the hermetic test suite.

## Driving it from the CLI (no dashboard)

The dashboard (Quickstart) is the easy path; you can also run the same procedure straight from the
CLI — useful for scripting, contributing, or the free stub-agent demo:

```powershell
# run the test suite (no dependencies required)
py -m unittest discover -t . -s tests -v

# drive the procedure end-to-end with STUB agents (free, deterministic)
py -m polis budget --appropriate 1000          # fund the treasury
py -m polis submit "Add a /health endpoint that returns ok"
py -m polis run                                 # process the next feedback item
py -m polis record --tail 30                    # read the audit log

# drive it with REAL agents (calls the authenticated claude CLI; costs money)
py -m polis --base .polis-real budget --appropriate 20
py -m polis --base .polis-real submit "Add a function celsius_to_fahrenheit(c) in temperature.py"
py -m polis --base .polis-real run --real        # add --sandbox docker to isolate test runs

# Phase 2: a voting panel of architects + a constitutional court
py -m polis --base .polis-real run --real --architects 3 --constitution-court

# Phase 3: many PRDs at once on isolated worktrees, with a decorrelated reviewer
py -m polis --base .polis-real run --real --parallel 4 --decorrelate
```

### The dashboard & feedback loop (background)

The dashboard is a FastAPI **optional extra** (the core stays stdlib-only) where you watch runs walk
the stage timeline, see the treasury burn, submit feedback, and trigger/approve runs — see the
[Quickstart](#quickstart--open-the-dashboard-and-give-feedback) above for the two ways to use it.

Beyond the **Submit feedback** box, `testing_mode` injects the floating **🐞 widget** into the app
Polis develops (and the dashboard itself): a tester describes an issue, pastes a screenshot, and
submits; the widget auto-captures console/storage/cookies/url, an async **Clerk** distills it into a
structured ticket, and it becomes architect feedback.

**Self-development safety.** Polis is built to develop *other* apps — you give it a folder and its own
code isn't there, so nothing special is needed (`protect-core` is inert because the target has no
Polis core to guard). Polis improving *its own* repo is the one case that needs care: always point it
at a **dedicated clone** (Quickstart B), never the checkout you're editing. Two guards then keep it
safe — a **constitution rule** fences it to `polis/dashboard/`, tests, and docs (never its
orchestration core), and **`--merge-via-pr on`** lands changes through a CI-gated PR instead of local
`main`. Its PRs land on `polis/`-prefixed branches, which the AI-review workflow skips (the
orchestrator's own Reviewer already vetted the diff and required CI still gates the merge). Needs `gh`
authenticated + CI on the target repo — a clone of this repo has both.

## Roadmap

- **Phase 0** ✅ — deterministic skeleton + stub agents.
- **Phase 1** ✅ — real Architect / Dev (wraps Claude Code) / independent Reviewer; sequential;
  autonomous merge on `tests-green + approval`; Docker sandbox seam.
- **Phase 2** ✅ — specialist dev hiring; multi-architect voting; constitutional review of PRDs.
- **Phase 3** ✅ — parallel PRDs on git worktrees; model decorrelation; deploy hooks.
- **Phase 4** ✅ — autonomous tester feedback loop: in-app widget → intake → Clerk ticketizer →
  architect feedback; a protect-core guard for self-development.
- **Phase 5** ✅ — opt-in **PR-based merge** (`config --merge-via-pr on`): Polis lands changes
  by pushing a branch and opening a CI-gated GitHub PR (push → wait for required checks →
  squash-merge), never touching local `main` directly — so it works like a careful contributor.

## License

[MIT](LICENSE) © fatheus97
