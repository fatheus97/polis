"""Per-base project configuration — stdlib only.

A small JSON file at ``<base>/config.json`` holds settings that belong to a Polis
"project" rather than a single run. Today: the **target repo** Polis develops
(``workspace``) and that repo's default branch (``main_branch``).

Resolution order for the workspace: explicit override > config.json > the managed
default ``<base>/workspace`` (a fresh repo Polis creates).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

_write_lock = threading.Lock()  # serialize read-modify-write of config.json


def config_path(base) -> Path:
    return Path(base) / "config.json"


def read_config(base) -> dict:
    p = config_path(base)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def write_config(base, updates: dict) -> dict:
    """Merge updates into config.json under a lock. None leaves a key unchanged;
    "" clears it (back to the default)."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        cfg = read_config(base)
        for k, v in updates.items():
            if v is None:
                continue
            if v == "":
                cfg.pop(k, None)
            else:
                cfg[k] = v
        config_path(base).write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return cfg


def resolve_workspace(base, override=None) -> Path:
    """Absolute path of the repo Polis develops."""
    if override:
        return Path(override).resolve()
    ws = read_config(base).get("workspace")
    return Path(ws).resolve() if ws else (Path(base) / "workspace").resolve()


def resolve_main_branch(base) -> str:
    return read_config(base).get("main_branch", "main")


def resolve_testing_mode(base) -> bool:
    """Whether apps Polis develops (and the dashboard itself) load the feedback widget."""
    return bool(read_config(base).get("testing_mode", False))


def resolve_auto_run(base) -> bool:
    """Whether a submitted report auto-triggers a run (else it queues for a human click)."""
    return bool(read_config(base).get("auto_run", False))


def resolve_ticketizer(base) -> bool:
    """Whether the async Clerk distills reports into structured tickets (default on)."""
    return bool(read_config(base).get("ticketizer", True))


def resolve_real_runs(base) -> bool:
    """Whether dashboard-triggered runs (manual + auto_run) use REAL LLM agents.
    Real is the default; switch to free stub runs only via config (no UI toggle)."""
    return bool(read_config(base).get("real_runs", True))


def resolve_merge_via_pr(base) -> bool:
    """When on, Polis lands changes via a GitHub PR (push -> wait CI -> squash-merge) instead
    of a local merge into main. Needs an 'origin' remote + gh + CI. Default off."""
    return bool(read_config(base).get("merge_via_pr", False))


def resolve_restart_on_merge(base) -> bool:
    """When on, the dashboard auto-restarts after it merges a change to its OWN checkout (the
    self-dev loop), so the new code/UI is applied without a manual restart. Default off."""
    return bool(read_config(base).get("restart_on_merge", False))


def resolve_grounded_agents(base) -> bool:
    """When on, agents get EYES: the Reviewer reads the post-change repo to verify criteria a diff
    can't show (and, later, the Architect reads the code + tester screenshot). These are agentic
    `claude` calls — more tokens/latency than a one-shot completion. Default off."""
    return bool(read_config(base).get("grounded_agents", False))


def resolve_dev_timeout(base) -> int:
    """Seconds the agentic dev (Claude Code) may run per attempt before timing out (default 900).
    The architect keeps the backend's shorter default; a grounded reviewer uses its own 600s
    window (its agentic Read/Grep can outlast the 300s one-shot default)."""
    try:
        v = int(read_config(base).get("dev_timeout", 900))
    except (TypeError, ValueError):
        v = 900
    return v if v > 0 else 900


def resolve_model_tier_overrides(base) -> dict:
    """Per-role model overrides from config (architect_model/dev_model/review_model, plus the
    optional dev_plan_model for plan-then-execute), as a dict for `ModelTier(**overrides)`.
    Unset keys fall back to ModelTier's defaults."""
    c = read_config(base)
    out = {}
    for field, key in (("architect", "architect_model"), ("reviewer", "review_model"),
                       ("dev", "dev_model"), ("dev_plan_model", "dev_plan_model")):
        if c.get(key):
            out[field] = c[key]
    return out


def resolve_intake_url(base) -> str:
    """Absolute intake URL baked into the served widget for cross-origin apps
    (empty => the widget posts same-origin to /api/report-intake)."""
    return read_config(base).get("intake_url", "") or ""


def resolve_intake_origins(base) -> list:
    """CORS allow-origins for the intake endpoint (default ['*'] for local dev)."""
    v = read_config(base).get("intake_origins")
    return v if isinstance(v, list) and v else ["*"]


def is_managed_default(base) -> bool:
    """True when the workspace is the auto-created `<base>/workspace`, not a repo the
    user pointed at."""
    return not read_config(base).get("workspace")
