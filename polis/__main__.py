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


def _positive_seconds(s: str) -> int:
    v = int(s)
    if v <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return v


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

    pl = sub.add_parser("lessons", help="inspect/curate self-learning lessons (case law)")
    pl.add_argument("--all", dest="all_lessons", action="store_true",
                    help="include retired/deleted lessons in the listing")
    pl.add_argument("--stats", action="store_true", help="show counts + usage/win totals")
    pl.add_argument("--retire", metavar="ID", default=None,
                    help="demote a lesson so it stops being injected")
    pl.add_argument("--promote", metavar="ID", default=None, help="reactivate a retired lesson")
    pl.add_argument("--delete", metavar="ID", default=None, help="hide a lesson permanently")
    pl.add_argument("--decay", action="store_true",
                    help="auto-retire well-used lessons with a low merge-win rate (anti-poison)")

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
    pcfg.add_argument("--restart-on-merge", choices=["on", "off"], default=None,
                      help="auto-restart the dashboard after it merges a change to its own checkout (self-dev)")
    pcfg.add_argument("--grounded-agents", choices=["on", "off"], default=None,
                      help="agents read the real repo (+ screenshot) instead of working blind (costs more)")
    pcfg.add_argument("--self-learning", choices=["on", "off"], default=None,
                      help="reflect on finished runs and inject past lessons into future prompts (~$0.10+/run)")
    pcfg.add_argument("--self-learning-sample-good", choices=["on", "off"], default=None,
                      help="also learn 'good practice' from clean first-attempt merges (more cost/noise)")
    pcfg.add_argument("--intake-url", default=None,
                      help="absolute intake URL baked into the served widget (for external apps)")
    pcfg.add_argument("--intake-origins", default=None,
                      help="comma-separated CORS allow-origins for the intake endpoint")
    pcfg.add_argument("--architect-model", default=None,
                      help="model for the architect that writes the spec (e.g. opus, sonnet, haiku)")
    pcfg.add_argument("--dev-model", default=None,
                      help="model for the dev that writes the code (default sonnet)")
    pcfg.add_argument("--dev-plan-model", default=None,
                      help="optional planning model: the dev plans read-only with this (e.g. opus) then executes")
    pcfg.add_argument("--review-model", default=None,
                      help="model for the reviewer")
    pcfg.add_argument("--dev-timeout", type=_positive_seconds, default=None,
                      help="seconds the agentic dev (Claude Code) may run per attempt (default 900)")

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
        from .projectcfg import (is_managed_default, resolve_auto_run, resolve_dev_timeout,
                                 resolve_grounded_agents, resolve_main_branch,
                                 resolve_merge_via_pr, resolve_model_tier_overrides,
                                 resolve_real_runs, resolve_restart_on_merge,
                                 resolve_self_learning, resolve_self_learning_sample_good,
                                 resolve_testing_mode, resolve_ticketizer, resolve_workspace,
                                 write_config)
        updates = {}
        if args.repo is not None:
            # "" clears it (reset to managed default); avoid Path("").resolve() == CWD.
            updates["workspace"] = str(Path(args.repo).resolve()) if args.repo.strip() else ""
        if args.main_branch is not None:
            updates["main_branch"] = args.main_branch
        for flag, val in (("testing_mode", args.testing_mode), ("auto_run", args.auto_run),
                          ("ticketizer", args.ticketizer), ("real_runs", args.real_runs),
                          ("merge_via_pr", args.merge_via_pr),
                          ("restart_on_merge", args.restart_on_merge),
                          ("grounded_agents", args.grounded_agents),
                          ("self_learning", args.self_learning),
                          ("self_learning_sample_good", args.self_learning_sample_good)):
            if val is not None:
                updates[flag] = (val == "on")
        if args.intake_url is not None:
            updates["intake_url"] = args.intake_url.strip()
        if args.intake_origins is not None:
            updates["intake_origins"] = [o.strip() for o in args.intake_origins.split(",") if o.strip()]
        for mkey, mval in (("architect_model", args.architect_model),
                           ("dev_model", args.dev_model), ("review_model", args.review_model),
                           ("dev_plan_model", args.dev_plan_model)):
            if mval is not None:
                updates[mkey] = mval.strip()
        if args.dev_timeout is not None:
            updates["dev_timeout"] = args.dev_timeout
        if updates:
            write_config(args.base, updates)
        tag = "managed default" if is_managed_default(args.base) else "configured"
        print(f"target repo  : {resolve_workspace(args.base)}  ({tag})")
        print(f"main branch  : {resolve_main_branch(args.base)}")
        print(f"testing_mode : {resolve_testing_mode(args.base)}   "
              f"auto_run : {resolve_auto_run(args.base)}   "
              f"ticketizer : {resolve_ticketizer(args.base)}   "
              f"real_runs : {resolve_real_runs(args.base)}   "
              f"merge_via_pr : {resolve_merge_via_pr(args.base)}   "
              f"restart_on_merge : {resolve_restart_on_merge(args.base)}   "
              f"grounded_agents : {resolve_grounded_agents(args.base)}   "
              f"self_learning : {resolve_self_learning(args.base)}"
              + ("  (sample_good)" if resolve_self_learning_sample_good(args.base) else ""))
        t = ModelTier(**resolve_model_tier_overrides(args.base))
        print(f"models       : architect={t.architect}  dev={t.dev}  reviewer={t.reviewer}"
              f"  dev_plan={t.dev_plan_model}   dev_timeout={resolve_dev_timeout(args.base)}s")
        return 0

    if args.cmd == "lessons":
        # Read/curate the lesson store directly — no full Government (no git-init side effect).
        from .lessons import LessonStore
        from .projectcfg import resolve_self_learning
        path = Path(args.base) / "lessons.sqlite"
        if not path.exists():
            print("No lessons yet." if resolve_self_learning(args.base)
                  else "Self-learning is off (enable with: config --self-learning on).")
            return 0
        store = LessonStore(path, jsonl_path=Path(args.base) / "lessons.jsonl")
        try:
            for lesson_id, status in (("retire", "retired"), ("promote", "active"),
                                      ("delete", "deleted")):
                target = getattr(args, lesson_id)
                if target:
                    store.set_status(target, status)
                    print(f"{lesson_id.capitalize()}d {target}.")
            if args.retire or args.promote or args.delete:
                return 0
            if args.decay:
                retired = store.decay()
                print(f"Retired {len(retired)} low-win lesson(s)." if retired
                      else "Nothing to retire.")
                return 0
            if args.stats:
                s = store.stats()
                print(f"Lessons: {s['active']} active / {s['total']} total  "
                      f"(uses={s['uses']}, wins={s['wins']})")
                for g in s["groups"]:
                    print(f"  {g['scope']:9} {g['polarity']:12} {g['status']:8} "
                          f"count={g['count']} uses={g['uses']} wins={g['wins']}")
                return 0
            lessons = store.all(include_retired=args.all_lessons)
            if not lessons:
                print("No lessons yet.")
            for lesson in lessons:
                print(f"{lesson.id}  {lesson.scope:9} {lesson.polarity:12} "
                      f"{lesson.discipline or '-':9} uses={lesson.uses} wins={lesson.wins} "
                      f"[{lesson.status}]")
                print(f"    trigger : {lesson.trigger}")
                print(f"    guidance: {lesson.guidance}")
            return 0
        finally:
            store.close()

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
            base_overrides = resolve_model_tier_overrides(args.base)
            # precedence: per-role flag > --model > config (architect_model/…) > default.
            # A bare --model overrides the three role models but must NOT drop a configured
            # dev_plan_model (plan-then-execute is orthogonal to which model executes).
            tier = (ModelTier(architect=args.model, reviewer=args.model, dev=args.model,
                              dev_plan_model=base_overrides.get("dev_plan_model"))
                    if args.model else ModelTier(**base_overrides))
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
