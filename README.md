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

## Quick start (Phase 0, stdlib-only)

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

### Watch it live — the dashboard (optional extra)

A local web control panel to *watch* runs walk the stage timeline (color-coded by branch),
see treasury burn, and take light actions (submit feedback, fund the treasury, trigger a run).
Built on FastAPI as an **optional extra** so the core stays stdlib-only:

```powershell
py -m pip install -e ".[dashboard]"
py -m polis --base .polis-real dashboard      # opens http://127.0.0.1:8765
```

By default Polis develops a managed repo at `<base>/workspace`. To point it at **your own
app's repo** (the agents will branch, commit, and merge into it), set the target — via the CLI
or the dashboard's control panel:

```powershell
py -m polis --base .polis-real config --repo C:\path\to\your-app   # --main-branch master if needed
```

### Tester feedback loop (testing mode)

Turn on `testing_mode` and Polis injects a floating **feedback widget** into the app it develops
(and the dashboard itself). A tester describes an issue, pastes a screenshot, and submits; the
widget auto-captures console/storage/cookies/url, an async **Clerk** distills it into a structured
ticket, and it becomes a feedback item the architect works on. Pointed at its own repo, Polis
develops its own dashboard — and a **constitution rule blocks it from touching its core** (it may
only edit `polis/dashboard/`, tests, docs).

```powershell
py -m polis --base .polis-real config --testing-mode on   # show the 🐞 widget on the configured app
py -m polis --base .polis-real dashboard                  # file reports; review the tickets
```

> Want Polis to work on **its own** dashboard? Don't point it at the checkout you're editing —
> see [Self-development (safely)](#self-development-safely) just below.

### Self-development (safely)

Polis is built to develop **other** apps: you give it a folder and its own code isn't in there, so
it just branches, commits, and (with PR-mode) opens PRs in that folder. Nothing special is needed,
and `protect-core` is inert because the target has no Polis core to guard.

Self-development — Polis improving *its own* dashboard — is the one case that needs care, because
its code and the copy you edit could be the same checkout. The safe setup is a **dedicated clone**
plus **PR-based merge**, so Polis never touches your working tree or `main`:

```powershell
git clone <your-polis-repo-url> ../polis-dev    # a repo you can push to (your fork)
py -m polis --base .dog config --repo ../polis-dev --testing-mode on --merge-via-pr on
py -m polis --base .dog dashboard
```

What each line does:

1. **`git clone <your-polis-repo-url> ../polis-dev`** — a throwaway, *dedicated* checkout of the
   repo Polis will edit, separate from the copy you work in. All of Polis's branch/checkout churn
   happens here, never in your tree. Use a repo you have push access to (your own fork), so its
   `origin` is a GitHub repo where the PRs it opens can run CI and be merged after your review.
2. **`config --repo ../polis-dev --testing-mode on --merge-via-pr on`** — for this `.dog` instance:
   point the workspace at the clone (`--repo`), inject the feedback widget (`--testing-mode on`), and
   turn on **PR-based merge** (`--merge-via-pr on`) so Polis lands changes by pushing a branch and
   opening a CI-gated GitHub PR (push → wait for the required checks → squash-merge) instead of
   merging into local `main`.
3. **`dashboard`** — launch the control panel for this instance; file reports with the 🐞 widget and
   **review/merge the PRs Polis opens**. (Its branches are prefixed `polis/`, and the AI-review
   workflow skips them — the orchestrator's own Reviewer already vetted the diff and required CI
   still gates the merge.)

First fund the treasury (`py -m polis --base .dog budget --appropriate 20`) and leave `auto_run`
off so you stay in the loop. Needs `gh` authenticated + CI on the repo — a clone of this repo has both.

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
