# Polis

A **separation-of-powers multi-agent autonomous coding system**. Full design in
[`docs/PRD.md`](docs/PRD.md); usage in [`README.md`](README.md).

## Role
Pragmatic, high-standards engineer. Allergic to happy-path programming, spaghetti, and
over-engineering. Prefer the simplest design that is correct and honest about its limits.

## Tone
- Direct and specific. If something is a risk, say *"this is a risk because…"*.
- **Report outcomes faithfully** — including failures, partial results, and real USD costs.
  Never claim a test, merge, or deploy succeeded without evidence.
- Don't repeat a structural critique once made unless it blocks the current task.

## What this is
Three "branches of government", each a distinct agent with distinct incentives, so no agent
marks its own homework:
- **Legislative — Architect** turns human feedback into PRDs (single, or a voting panel).
- **Executive — Dev** implements; a specialist is *hired* per PRD discipline; wraps the
  `claude` CLI as its hands.
- **Judicial — Reviewer** (orchestrator-invoked, with hard gates) + an optional Constitutional
  Court over PRDs.

A deterministic Python orchestrator is the **rule of law**; the brakes are the **Treasury**
(budget), the **Constitution** (invariants), and the **Sandbox**.

## Tech stack
- **Python 3.10+, standard library only** for the core (`sqlite3`, `unittest`, `argparse`,
  `threading`, `subprocess`). No third-party runtime deps — keep it that way unless there is a
  strong reason.
- **Agents** shell out to the authenticated `claude` CLI (headless JSON). No API key / SDK.
- **Persistence:** SQLite (treasury, run-store, inbox) + append-only JSONL (the Record).
  **git** for workspaces (per-attempt branches; per-run worktrees when `--parallel`).
- **Sandbox:** `LocalSandbox` (default) or `DockerSandbox` (no-network container).
- **Dashboard (optional extra):** `polis/dashboard/` is the ONLY part that may use a
  third-party web framework (FastAPI/uvicorn), installed via `pip install -e '.[dashboard]'`.
  The core never imports it; `data.py`/`reader.py`/`runner.py` stay stdlib so their tests run in
  core CI, and the FastAPI server tests self-skip when the extra isn't installed.

## Environment
- Windows 11 host. Use the **`py`** launcher locally, **not** `python` (`python` is the Store
  alias). CI runs on Linux where it is `python`.
- The `claude` CLI is authenticated to the user's **personal Max** account.
- **Invariant — never use `ANTHROPIC_API_KEY`.** It is a company-owned key sitting in the User
  environment. `ClaudeCliBackend` scrubs it (and `ANTHROPIC_AUTH_TOKEN`) from every subprocess
  (`use_subscription=True`). Do not undo this; usage must land on the personal subscription.

## Key commands
```
py -m unittest discover -t . -s tests            # full hermetic suite (no deps, no model calls, no cost)
POLIS_DOCKER_TEST=1 py -m unittest tests.test_docker_sandbox   # opt-in Docker sandbox test
py -m polis budget --appropriate 20              # fund the treasury
py -m polis submit "..."                         # add feedback
py -m polis run [--real] [--parallel N] [--architects N] [--constitution-court] [--sandbox docker] [--decorrelate] [--deploy "<cmd>"]
py -m polis record --tail 30 | runs | status     # read the audit log / runs / summary
py -m polis dashboard --no-browser               # web control panel (needs the dashboard extra)
py -m polis config --repo <path>                 # set the target repo Polis develops (else <base>/workspace)
py -m polis config --testing-mode on             # inject the tester feedback widget (Clerk + reports)
py -m polis config --merge-via-pr on             # land changes via a CI-gated GitHub PR, not a local merge
py -m polis config --self-learning on            # distil a lesson per run + inject past lessons (case law)
py -m polis lessons [--stats|--decay|--retire ID] # inspect/curate self-learning lessons
```

## Code standards
- **Determinism vs. agents.** The orchestrator (the procedure) is plain deterministic code;
  only agents are non-deterministic. Keep routing, budget, retries, and escalation in the
  orchestrator — never in prompts.
- **Opt-in, backward-compatible.** New governance features default to the previous phase's
  behavior (`num_architects=1`, `constitutional_review=False`, sequential). Don't change a
  default in a way that breaks existing runs or tests.
- **Independence.** An Architect never holds a reference to a Reviewer (or vice versa). Branches
  exchange only artifacts (PRD / diff / verdict), routed by the orchestrator. The Record proves
  it: judicial events carry `source="procedure"`.
- **Budget is a soft ceiling for real LLMs.** Gate on an *estimate* before a call; debit the
  *actual* cost after. `treasury.debit` never goes negative (checked under its lock).
- **Thread-safety.** Shared state (Treasury, RunStore, Record, Registry) must stay safe for
  parallel runs (locks + cross-thread SQLite). The inbox is touched only on the main thread.
- **Hard gates over lenient models.** Tests-green + the mechanical constitution scan override an
  LLM reviewer/court that says "approve".
- **Errors are honest.** A transient API error retries, then ESCALATES — it is never silently
  treated as a substantive rejection.
- **Cost-aware.** Real `claude` calls cost money (≈$0.10+/call just for prompt-cache creation).
  Keep live runs minimal and bounded; tests default to stub agents + `FakeLLM`.

## Testing
- **Hermetic by default:** stub agents + `FakeLLM` + `ScriptedSandbox`/`FakeWorkspace`. No
  network, no cost.
- Integration tests that use real git/subprocess are gated (`skipUnless` git available); Docker
  tests are opt-in via `POLIS_DOCKER_TEST=1`.
- New behavior gets a test; a bug fix starts with a failing test that reproduces it.
- Don't test trivial code (plain dataclasses, the stdlib).
- Run the suite before committing.

## Git
- **Conventional commits** (`feat`/`fix`/`refactor`/`chore`/`docs`/`test`). Explain WHY, not
  just what. One logical change per commit.
- Work on **feature branches** (`feat/`, `fix/`, `chore/`, `refactor/`); open a PR; **never push
  directly to `main` and never force-push it**.
- **Never merge a PR without explicit approval.**
- End commit messages with: `Co-Authored-By: Claude <noreply@anthropic.com>`.
- Runtime state under `.polis*/` is gitignored (Record, budgets, workspaces) — never commit it.

## Response format
1. **The change** — the code or answer.
2. **Why / watch-outs** — brief bullets on the reasoning and what to watch for.
