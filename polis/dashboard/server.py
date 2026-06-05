"""FastAPI app — the ONLY module that imports a third-party web framework.

Thin layer: reads go through reader.* + data.* (no side effects, no build_government);
writes/runs go through a single RunManager (serialized). Binds 127.0.0.1 by default.
"""

from __future__ import annotations

import mimetypes
import os
import subprocess
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import json

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from ..projectcfg import (is_managed_default, read_config, resolve_auto_run,
                          resolve_intake_origins, resolve_intake_url, resolve_main_branch,
                          resolve_real_runs, resolve_restart_on_merge, resolve_testing_mode,
                          resolve_ticketizer, resolve_workspace, write_config)
from ..reports import ReportStore
from . import data, reader
from .runner import RunManager

MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024
_IMG_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}

# Explicit MIME map so the served JS is always `application/javascript` — Windows' registry
# can map `.js` to `text/plain`, which browsers refuse to execute under nosniff.
_MEDIA = {".js": "application/javascript", ".mjs": "application/javascript",
          ".css": "text/css", ".html": "text/html; charset=utf-8",
          ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
          ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
          ".gif": "image/gif", ".ico": "image/x-icon", ".woff2": "font/woff2",
          ".map": "application/json", ".txt": "text/plain; charset=utf-8"}

# A child dashboard exits with this code to tell its supervisor "a self-dev change merged —
# relaunch me with the new code" (see serve()/_supervise()). Any other code is final.
_RESTART_EXIT_CODE = 97


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def _media_type(name: str) -> str:
    return _MEDIA.get(Path(name).suffix.lower()) or mimetypes.guess_type(name)[0] \
        or "application/octet-stream"


def _snapshot_static(static: Path) -> dict[str, bytes]:
    """Read every static asset into memory ONCE, at startup. When the dashboard is served
    from the same checkout Polis develops (the self-dev loop), a run rewrites these files in
    the working tree MID-RUN — serving from this frozen snapshot keeps the live UI stable and
    valid until a deliberate restart (see restart_on_merge) applies the new version.

    Assumes small UI assets (the dashboard frontend is ~100 KB) — everything under static/ is
    held in process memory; not intended for large bundles (WASM, big fonts)."""
    snap: dict[str, bytes] = {}
    if static.exists():
        for p in sorted(static.rglob("*")):
            if p.is_file():
                snap[p.relative_to(static).as_posix()] = p.read_bytes()
    return snap


class FeedbackIn(BaseModel):
    text: str
    by: str = "tester"
    directives: dict | None = None


class BudgetIn(BaseModel):
    amount: float


class RunIn(BaseModel):
    # real-vs-stub is a config switch (real_runs), not a request field
    feedback_id: str | None = None
    max_revisions: int = 2
    architects: int = 1
    constitution_court: bool = False


class RetryIn(BaseModel):
    guidance: str = ""


class ConfigIn(BaseModel):
    workspace: str | None = None   # "" resets to the managed default
    main_branch: str | None = None
    testing_mode: bool | None = None
    auto_run: bool | None = None
    ticketizer: bool | None = None
    real_runs: bool | None = None


class RepoInitIn(BaseModel):
    path: str


def create_app(base) -> FastAPI:
    base = Path(base)
    rm = RunManager(base)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        rm.shutdown()  # wait=True: let an in-flight run finish, don't corrupt state

    app = FastAPI(title="Polis Dashboard", docs_url="/api/docs", lifespan=lifespan)
    app.state.run_manager = rm
    # Freeze the UI assets at startup so a self-dev run rewriting them can't corrupt the
    # live page mid-run (the freeze that made the dashboard look "unresponsive").
    assets = _snapshot_static(_static_dir())

    # CORS is scoped to ONLY the intake endpoint (a widget embedded in an external app posts
    # cross-origin). The action endpoints (/api/run, /api/budget, /api/feedback) deliberately
    # get NO CORS headers, so a random page in a local user's browser can't drive them.
    @app.middleware("http")
    async def _intake_cors(request, call_next):
        if request.url.path != "/api/report-intake":
            return await call_next(request)
        origins = resolve_intake_origins(base)
        origin = request.headers.get("origin", "")
        allow = "*" if "*" in origins else (origin if origin in origins else "")
        resp = (Response(status_code=204) if request.method == "OPTIONS"
                else await call_next(request))
        if allow:
            resp.headers["Access-Control-Allow-Origin"] = allow
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp

    def record_path() -> Path:
        return base / "record.jsonl"

    # --- reads (no side effects) -----------------------------------------
    @app.get("/")
    def index():
        raw = assets.get("index.html")
        if raw is None:
            return JSONResponse({"error": "frontend not built"}, status_code=404)
        html = raw.decode("utf-8", errors="replace")   # never 500 on a stray non-UTF-8 byte
        widget_script = ('<script src="/static/feedback-widget.js"></script>'
                         if resolve_testing_mode(base) else '')
        html = html.replace('<!-- FEEDBACK_WIDGET_PLACEHOLDER -->', widget_script)
        return Response(html, media_type="text/html")

    @app.get("/api/overview")
    def overview():
        ov = data.overview(reader.read_record_events(record_path()))
        ov["treasury"] = reader.read_treasury_snapshot(base)
        ov["pending_feedback"] = len(reader.read_pending_feedback(base))
        return ov

    @app.get("/api/runs")
    def runs():
        return {"runs": data.run_list(reader.read_record_events(record_path()))}

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        groups = data.group_by_run(reader.read_record_events(record_path()))
        if run_id not in groups:
            raise HTTPException(404, "run not found")
        detail = data.run_detail(groups[run_id])
        detail["ledger_spend"] = reader.read_treasury_snapshot(base)["per_run"].get(run_id)
        return detail

    @app.get("/api/events")
    def events(tail: int = Query(100, ge=0, le=2000), since_ts: float | None = None):
        evs = reader.read_record_events(record_path())
        if since_ts is not None:
            evs = [e for e in evs if (e.get("ts") or 0) > since_ts]
        evs = evs[-tail:]  # always cap — a stale/zero since_ts must not dump the record
        for e in evs:
            e["branch"] = data.event_branch(e)
        return {"events": evs}

    @app.get("/api/treasury")
    def treasury():
        return reader.read_treasury_snapshot(base)

    @app.get("/api/feedback")
    def feedback():
        return {"pending": reader.read_pending_feedback(base)}

    # --- tester feedback reports ---
    @app.get("/api/reports")
    def reports():
        return {"reports": rm.reports()}

    @app.get("/api/reports/{report_id}")
    def one_report(report_id: str):
        r = rm.report(report_id)
        if not r:
            raise HTTPException(404, "report not found")
        return r

    @app.get("/api/reports/{report_id}/screenshot")
    def report_screenshot(report_id: str):
        p = ReportStore(base).screenshot_path(report_id)
        if not p:
            raise HTTPException(404, "no screenshot")
        return FileResponse(p)

    @app.post("/api/report-intake")
    async def report_intake(
        text: str = Form(""),
        state: str = Form("{}"),
        url: str = Form(""),
        user_agent: str = Form(""),
        viewport: str = Form("{}"),
        submitted_by: str = Form("tester"),
        screenshot: UploadFile | None = File(None),
    ):
        try:
            state_obj = json.loads(state or "{}")
            viewport_obj = json.loads(viewport or "{}")
        except json.JSONDecodeError:
            raise HTTPException(400, "state/viewport must be JSON")
        img = ext = None
        if screenshot is not None:
            img = await screenshot.read()
            if img:
                if len(img) > MAX_SCREENSHOT_BYTES:
                    raise HTTPException(413, "screenshot too large")
                ext = _IMG_EXT.get(screenshot.content_type or "")
                if ext is None:
                    raise HTTPException(415, "unsupported screenshot type")
        return rm.intake_report(text=text, submitted_by=submitted_by, url=url,
                                user_agent=user_agent, viewport=viewport_obj,
                                state=state_obj, screenshot_bytes=img, screenshot_ext=ext)

    def _config_view():
        return {"workspace": str(resolve_workspace(base)),
                "main_branch": resolve_main_branch(base),
                "managed_default": is_managed_default(base),
                "testing_mode": resolve_testing_mode(base),
                "auto_run": resolve_auto_run(base),
                "ticketizer": resolve_ticketizer(base),
                "real_runs": resolve_real_runs(base)}

    @app.get("/api/config")
    def get_config():
        return _config_view()

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": rm.jobs()}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        j = rm.job(job_id)
        if not j:
            raise HTTPException(404, "job not found")
        return j

    # --- actions ----------------------------------------------------------
    @app.post("/api/feedback")
    def post_feedback(body: FeedbackIn):
        return rm.submit_feedback(body.text, by=body.by, directives=body.directives)

    @app.post("/api/budget")
    def post_budget(body: BudgetIn):
        try:
            return rm.appropriate(body.amount)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/run")
    def post_run(body: RunIn):
        # Real-vs-stub is a config decision (real_runs, default on), NOT a UI/request toggle.
        opts = {"max_revisions": body.max_revisions, "architects": body.architects,
                "constitution_court": body.constitution_court}
        return rm.trigger_run(real=resolve_real_runs(base), feedback_id=body.feedback_id, opts=opts)

    @app.post("/api/runs/{run_id}/retry")
    def post_retry(run_id: str, body: RetryIn):
        # Sovereign intervention: re-run an ESCALATED run carrying the human's guidance.
        res = rm.retry_run(run_id, body.guidance)
        if "error" in res:
            raise HTTPException(404 if "not found" in res["error"] else 400, res["error"])
        return res

    @app.post("/api/config")
    def set_config(body: ConfigIn):
        updates = {}
        if body.workspace is not None:
            updates["workspace"] = (str(Path(body.workspace).resolve())
                                    if body.workspace.strip() else "")
        if body.main_branch is not None:
            updates["main_branch"] = body.main_branch
        for flag in ("testing_mode", "auto_run", "ticketizer", "real_runs"):
            v = getattr(body, flag)
            if v is not None:
                updates[flag] = bool(v)
        write_config(base, updates)
        return _config_view()

    @app.post("/api/browse")
    def browse():
        """Open an OS folder picker server-side; return the chosen path or 204 if unavailable."""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            root.lift()
            root.focus_force()
            path = filedialog.askdirectory(parent=root)
            root.destroy()
        except Exception:
            return Response(status_code=204)
        if not path:
            return Response(status_code=204)
        p = Path(path).resolve()
        return {"path": str(p), "is_git": (p / ".git").is_dir()}

    @app.get("/api/repo-status")
    def repo_status(path: str = Query("")):
        """Check whether a folder exists and contains a .git directory."""
        if not path:
            return {"exists": False, "is_git": False, "path": ""}
        p = Path(path).resolve()
        return {"exists": p.is_dir(), "is_git": (p / ".git").is_dir(), "path": str(p)}

    @app.post("/api/repo-init")
    def repo_init(body: RepoInitIn):
        """Initialise a folder as a git repo (git init -b main); idempotent if already a repo."""
        p = Path(body.path).resolve()
        if not p.is_dir():
            raise HTTPException(400, "folder does not exist")
        if (p / ".git").is_dir():
            return {"ok": True, "is_git": True}
        try:
            r = subprocess.run(
                ["git", "init", "-b", resolve_main_branch(base), str(p)],
                capture_output=True, text=True, timeout=10)
        except Exception as e:
            raise HTTPException(500, f"git init failed: {e}")
        if r.returncode != 0:
            raise HTTPException(500, f"git init failed: {(r.stderr or r.stdout).strip()}")
        return {"ok": True, "is_git": True}

    @app.get("/api/branches")
    def list_branches(path: str = Query("")):
        """List local git branches for the given repo path; returns [] on any error."""
        if not path:
            return {"branches": []}
        try:
            result = subprocess.run(
                ["git", "branch"],
                cwd=str(Path(path)),
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return {"branches": []}
        if result.returncode != 0:
            return {"branches": []}
        branches = []
        for line in result.stdout.splitlines():
            name = line.strip()
            if name.startswith("*"):
                name = name[1:].strip()
            if name:
                branches.append(name)
        return {"branches": branches}

    # Templated widget: prepend the configured intake URL so external apps can embed
    # <script src="http://<host>:8765/static/feedback-widget.js">. Declared BEFORE the
    # catch-all static route so this explicit route wins.
    @app.get("/static/feedback-widget.js")
    def widget_js():
        raw = assets.get("feedback-widget.js")
        if raw is None:
            raise HTTPException(404, "widget not found")
        intake = resolve_intake_url(base)
        header = f"window.__POLIS_INTAKE_URL={json.dumps(intake)};\n" if intake else ""
        js = raw.decode("utf-8", errors="replace")     # never 500 on a stray non-UTF-8 byte
        return Response(header + js, media_type="application/javascript")

    # Serve every other asset from the in-memory startup snapshot (not the live working
    # tree), so a self-dev run editing app.js/index.html can't break the running UI. Pure
    # dict lookup — no filesystem touch at request time, so `..` can't traverse out.
    @app.get("/static/{path:path}")
    def static_asset(path: str):
        raw = assets.get(path)
        if raw is None:
            raise HTTPException(404, "not found")
        return Response(raw, media_type=_media_type(path))

    return app


def serve(base, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True):
    supervised = os.environ.get("POLIS_DASH_SUPERVISED") == "1"
    # restart_on_merge: a thin supervisor relaunches the dashboard after it merges a change
    # to its OWN checkout, so the new code/UI is applied without a manual restart. The
    # supervisor runs the real dashboard as a child; the child signals via exit code.
    if resolve_restart_on_merge(base) and not supervised:
        return _supervise(base, host, port, open_browser)

    import uvicorn
    app = create_app(base)
    url = f"http://{host}:{port}/"
    if open_browser and not supervised:   # the supervisor opens the browser once itself
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    restart = {"flag": False}
    if supervised:
        # Arm the restart: after a merge to our own checkout, ask uvicorn to exit gracefully
        # (its lifespan drains the in-flight run), then signal the supervisor to relaunch. The
        # 2s delay lets the browser's last poll render the merge before the brief blip.
        def _request_restart():
            restart["flag"] = True
            server.should_exit = True
        app.state.run_manager.set_restart_hook(
            lambda: threading.Timer(2.0, _request_restart).start())

    print(f"Polis dashboard → {url}  (base: {Path(base).resolve()})")
    # One-time heads-up: real-vs-stub defaults to REAL. Only warn when it isn't explicitly
    # set in config, so a user who has made a deliberate choice isn't nagged.
    if "real_runs" not in read_config(base) and resolve_real_runs(base):
        print("  ⚠ dashboard runs use REAL LLM agents by default ($). Switch to free "
              "stubs:  py -m polis --base <base> config --real-runs off")
    try:
        server.run()
    except OSError as e:
        print(f"Could not bind {host}:{port} ({e}). Try another --port.")
        return 1
    if restart["flag"]:
        print("  ↻ a self-dev change merged — exiting for the supervisor to relaunch…")
        return _RESTART_EXIT_CODE
    return 0


def _supervise(base, host: str, port: int, open_browser: bool) -> int:
    """Parent process for restart_on_merge: run the dashboard as a child and relaunch it
    whenever it exits with _RESTART_EXIT_CODE (a self-dev change merged). Any other exit
    code is final and propagated. A child process + exit code is robust across platforms,
    unlike os.execv on Windows."""
    import sys
    import time
    argv = [sys.executable, "-m", "polis", "--base", str(base), "dashboard",
            "--host", host, "--port", str(port), "--no-browser"]
    env = dict(os.environ, POLIS_DASH_SUPERVISED="1")
    print(f"Polis dashboard (auto-restart on merge) → http://{host}:{port}/")
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    while True:
        code = subprocess.run(argv, env=env).returncode
        if code != _RESTART_EXIT_CODE:
            return code
        time.sleep(0.4)  # let the listen socket fully release before the child rebinds
        print("  ↻ restarting dashboard to apply the merged change…")
