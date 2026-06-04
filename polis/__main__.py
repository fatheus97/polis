"""Sovereign console — the human's CLI into the government.

    py -m polis budget --appropriate 1000
    py -m polis submit "Add a /health endpoint that returns ok"
    py -m polis run
    py -m polis record --tail 30
    py -m polis runs
    py -m polis status
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .app import build_government
from .llm import ClaudeCliBackend
from .orchestrator import OrchestratorConfig
from .registry import ModelTier
from .sandbox import DockerSandbox, LocalSandbox


def _fmt_result(res) -> str:
    bits = [
        f"[{res.outcome.value}]",
        f"run={res.run_id}",
        f"last={res.last_stage.value}",
        f"attempts={res.attempts}",
        f"spend={res.spend:g}",
    ]
    if res.merge_commit:
        bits.append(f"commit={res.merge_commit[:10]}")
    if res.reason:
        bits.append(f"reason={res.reason}")
    return " ".join(bits)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="polis",
        description="Separation-of-powers multi-agent coding system (Phase 0).",
    )
    p.add_argument("--base", default=".polis", help="state directory (default: .polis)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("budget", help="show or appropriate budget")
    pb.add_argument("--appropriate", type=float, default=None,
                    help="add this much to the treasury")

    ps = sub.add_parser("submit", help="submit a feedback item to the inbox")
    ps.add_argument("text")
    ps.add_argument("--by", default="tester")
    ps.add_argument("--directives", default=None,
                    help='JSON scenario hook, e.g. \'{"dev":"emit_secret"}\'')

    pr = sub.add_parser("run", help="process pending feedback through the procedure")
    pr.add_argument("--all", action="store_true", help="drain the whole inbox")
    pr.add_argument("--max-revisions", type=int, default=2)
    pr.add_argument("--real", action="store_true",
                    help="use real LLM-backed agents (calls the claude CLI; costs money)")
    pr.add_argument("--sandbox", choices=["local", "docker"], default="local")
    pr.add_argument("--model", default=None,
                    help="override the model for all branches (e.g. sonnet, opus, haiku)")
    pr.add_argument("--architects", type=int, default=1,
                    help="convene a panel of N architects that propose + vote (default 1)")
    pr.add_argument("--constitution-court", action="store_true",
                    help="vet each PRD against the constitution before implementation")
    pr.add_argument("--parallel", type=int, default=1,
                    help="run N pending PRDs concurrently on isolated git worktrees")
    pr.add_argument("--architect-model", default=None)
    pr.add_argument("--dev-model", default=None)
    pr.add_argument("--review-model", default=None)
    pr.add_argument("--decorrelate", action="store_true",
                    help="put the reviewer on a different model than the dev "
                         "(reduces shared blind spots; claude CLI is Claude-only, so this "
                         "is cross-tier, not cross-provider)")
    pr.add_argument("--deploy", default=None,
                    help="shell command to run against merged main after a successful merge")
    pr.add_argument("--repo", default=None,
                    help="target repo to develop for this run (overrides the configured one)")

    prec = sub.add_parser("record", help="read the audit log (the Record)")
    prec.add_argument("--tail", type=int, default=20)

    sub.add_parser("runs", help="list completed runs")
    sub.add_parser("status", help="treasury + inbox + run summary")

    pd = sub.add_parser("dashboard",
                        help="serve the web control panel (needs the 'dashboard' extra)")
    pd.add_argument("--host", default="127.0.0.1")
    pd.add_argument("--port", type=int, default=8765)
    pd.add_argument("--no-browser", action="store_true", help="don't auto-open a browser")

    pcfg = sub.add_parser("config", help="show or set project config (e.g. the target repo)")
    pcfg.add_argument("--repo", default=None,
                      help="set the target repo directory Polis develops (the app under work)")
    pcfg.add_argument("--main-branch", default=None,
                      help="set that repo's default branch (default: main)")
    pcfg.add_argument("--testing-mode", choices=["on", "off"], default=None,
                      help="show the feedback widget in apps Polis develops (and the dashboard)")
    pcfg.add_argument("--auto-run", choices=["on", "off"], default=None,
                      help="auto-trigger a run when a tester report arrives (else queue it)")
    pcfg.add_argument("--ticketizer", choices=["on", "off"], default=None,
                      help="distill reports into structured tickets via the Clerk (default on)")
    pcfg.add_argument("--real-runs", choices=["on", "off"], default=None,
                      help="dashboard runs use real LLM agents (default on) vs free stub runs")
    pcfg.add_argument("--merge-via-pr", choices=["on", "off"], default=None,
                      help="land changes via a CI-gated GitHub PR instead of a local merge (needs origin+gh+CI)")
    pcfg.add_argument("--intake-url", default=None,
                      help="absolute intake URL baked into the served widget (for external apps)")
    pcfg.add_argument("--intake-origins", default=None,
                      help="comma-separated CORS allow-origins for the intake endpoint")
    pcfg.add_argument("--architect-model", default=None,
                      help="model for the architect that writes the spec (e.g. opus, sonnet, haiku)")
    pcfg.add_argument("--dev-model", default=None,
                      help="model for the dev that writes the code (default sonnet)")
    pcfg.add_argument("--review-model", default=None,
                      help="model for the reviewer")

    args = p.parse_args(argv)

    # The dashboard serves the web UI; it opens stores read-only and never needs a
    # full Government (no git-init side effect). Lazy import so the core works without
    # the optional FastAPI extra installed.
    if args.cmd == "dashboard":
        try:
            from .dashboard.server import serve
        except ImportError:
            print("The dashboard needs extra deps:  py -m pip install 'polis[dashboard]'")
            return 1
        return serve(args.base, host=args.host, port=args.port,
                     open_browser=not args.no_browser)

    if args.cmd == "config":
        from .projectcfg import (is_managed_default, resolve_auto_run,
                                 resolve_main_branch, resolve_merge_via_pr,
                                 resolve_model_tier_overrides, resolve_real_runs,
                                 resolve_testing_mode, resolve_ticketizer,
                                 resolve_workspace, write_config)
        updates = {}
        if args.repo is not None:
            # "" clears it (reset to managed default); avoid Path("").resolve() == CWD.
            updates["workspace"] = str(Path(args.repo).resolve()) if args.repo.strip() else ""
        if args.main_branch is not None:
            updates["main_branch"] = args.main_branch
        for flag, val in (("testing_mode", args.testing_mode), ("auto_run", args.auto_run),
                          ("ticketizer", args.ticketizer), ("real_runs", args.real_runs),
                          ("merge_via_pr", args.merge_via_pr)):
            if val is not None:
                updates[flag] = (val == "on")
        if args.intake_url is not None:
            updates["intake_url"] = args.intake_url.strip()
        if args.intake_origins is not None:
            updates["intake_origins"] = [o.strip() for o in args.intake_origins.split(",") if o.strip()]
        for mkey, mval in (("architect_model", args.architect_model),
                           ("dev_model", args.dev_model), ("review_model", args.review_model)):
            if mval is not None:
                updates[mkey] = mval.strip()
        if updates:
            write_config(args.base, updates)
        tag = "managed default" if is_managed_default(args.base) else "configured"
        print(f"target repo  : {resolve_workspace(args.base)}  ({tag})")
        print(f"main branch  : {resolve_main_branch(args.base)}")
        print(f"testing_mode : {resolve_testing_mode(args.base)}   "
              f"auto_run : {resolve_auto_run(args.base)}   "
              f"ticketizer : {resolve_ticketizer(args.base)}   "
              f"real_runs : {resolve_real_runs(args.base)}   "
              f"merge_via_pr : {resolve_merge_via_pr(args.base)}")
        t = ModelTier(**resolve_model_tier_overrides(args.base))
        print(f"models       : architect={t.architect}  dev={t.dev}  reviewer={t.reviewer}")
        return 0

    config = None
    build_kwargs = {}
    if args.cmd == "run":
        config = OrchestratorConfig(
            max_revisions=args.max_revisions,
            num_architects=args.architects,
            constitutional_review=args.constitution_court,
            deploy_command=args.deploy,
        )
        if args.real:
            from .projectcfg import resolve_model_tier_overrides
            # precedence: per-role flag > --model > config (architect_model/…) > default
            tier = (ModelTier(architect=args.model, reviewer=args.model, dev=args.model)
                    if args.model else ModelTier(**resolve_model_tier_overrides(args.base)))
            if args.architect_model:
                tier.architect = args.architect_model
            if args.dev_model:
                tier.dev = args.dev_model
            if args.review_model:
                tier.reviewer = args.review_model
            if args.decorrelate and tier.reviewer == tier.dev:
                tier.reviewer = "opus" if tier.dev != "opus" else "sonnet"
            backend = ClaudeCliBackend(default_model=args.model or "sonnet")
            build_kwargs.update(agents="real", backend=backend, tier=tier)
        build_kwargs["sandbox"] = (DockerSandbox() if args.sandbox == "docker"
                                   else LocalSandbox())
        if args.repo:
            build_kwargs["workspace_dir"] = args.repo
    gov = build_government(args.base, config=config, **build_kwargs)

    if args.cmd == "budget":
        if args.appropriate:
            gov.treasury.appropriate(args.appropriate)
            print(f"Appropriated {args.appropriate:g}.")
        print(f"Treasury balance: {gov.treasury.balance():g} "
              f"(appropriated {gov.treasury.total_appropriated():g}, "
              f"spent {gov.treasury.total_spent():g})")
        return 0

    if args.cmd == "submit":
        directives = json.loads(args.directives) if args.directives else None
        item = gov.inbox.submit(args.text, submitted_by=args.by, directives=directives)
        print(f"Submitted {item.id}: {item.text!r}")
        return 0

    if args.cmd == "run":
        if args.parallel > 1:
            items = gov.inbox.pending()
            if not items:
                print("Inbox empty — nothing to do.")
                return 0
            print(f"Running {len(items)} PRD(s) on up to {args.parallel} worktrees...")
            for res in gov.run_parallel(items, max_workers=args.parallel):
                print(_fmt_result(res))
            return 0
        ran = 0
        while True:
            res = gov.run_next()
            if res is None:
                if ran == 0:
                    print("Inbox empty — nothing to do.")
                break
            print(_fmt_result(res))
            ran += 1
            if not args.all:
                break
        return 0

    if args.cmd == "record":
        for e in gov.record.tail(args.tail):
            payload = {k: v for k, v in e["payload"].items()}
            short = json.dumps(payload)
            if len(short) > 90:
                short = short[:87] + "..."
            print(f"{e['stage']:9} {e['actor']:22} {e['kind']:12} "
                  f"cost={e['cost']:g} {short}")
        return 0

    if args.cmd == "runs":
        rows = gov.run_store.all()
        if not rows:
            print("No runs yet.")
        for r in rows:
            print(f"{r['run_id']}  {r['outcome']:8} attempts={r['attempts']} "
                  f"spend={r['spend']:g} commit={(r['merge_commit'] or '-')[:10]}  "
                  f"{r['feedback_text'][:50]!r}")
        return 0

    if args.cmd == "status":
        print(f"Treasury : balance {gov.treasury.balance():g} / "
              f"appropriated {gov.treasury.total_appropriated():g} / "
              f"spent {gov.treasury.total_spent():g}")
        print(f"Inbox    : {len(gov.inbox.pending())} pending")
        print(f"Runs     : {len(gov.run_store.all())} total")
        print(f"Constitution v{gov.constitution.version} "
              f"({len(gov.constitution.rules)} rules)")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
