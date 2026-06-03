"""Polis web dashboard / control panel (optional extra).

Only `polis.dashboard.server` imports FastAPI/uvicorn; `data`, `reader`, and `runner`
are stdlib-only so the dashboard's logic is testable without the extra installed and
the Polis core never depends on a web framework.
"""
