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

    prec = sub.add_parser("record", help="read the audit log (the Record)")
    prec.add_argument("--tail", type=int, default=20)

    sub.add_parser("runs", help="list completed runs")
    sub.add_parser("status", help="treasury + inbox + run summary")

    args = p.parse_args(argv)

    config = None
    build_kwargs = {}
    if args.cmd == "run":
        config = OrchestratorConfig(max_revisions=args.max_revisions)
        if args.real:
            backend = ClaudeCliBackend(default_model=args.model or "sonnet")
            tier = (ModelTier(architect=args.model, reviewer=args.model, dev=args.model)
                    if args.model else None)
            build_kwargs.update(agents="real", backend=backend, tier=tier)
        build_kwargs["sandbox"] = (DockerSandbox() if args.sandbox == "docker"
                                   else LocalSandbox())
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
