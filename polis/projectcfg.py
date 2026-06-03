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
