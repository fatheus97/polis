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


def is_managed_default(base) -> bool:
    """True when the workspace is the auto-created `<base>/workspace`, not a repo the
    user pointed at."""
    return not read_config(base).get("workspace")
