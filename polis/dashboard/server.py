"""FastAPI app — the ONLY module that imports a third-party web framework.

Thin layer: reads go through reader.* + data.* (no side effects, no build_government);
writes/runs go through a single RunManager (serialized). Binds 127.0.0.1 by default.
"""

from __future__ import annotations

import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import json

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..projectcfg import (is_managed_default, resolve_auto_run, resolve_intake_origins,
                          resolve_intake_url, resolve_main_branch, resolve_real_runs,
                          resolve_testing_mode, resolve_ticketizer, resolve_workspace,
                          write_config)
from ..reports import ReportStore
from . import data, reader
from .runner import RunManager

MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024
_IMG_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


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


class ConfigIn(BaseModel):
    workspace: str | None = None   # "" resets to the managed default
    main_branch: str | None = None
    testing_mode: bool | None = None
    auto_run: bool | None = None
    ticketizer: bool | None = None
    real_runs: bool | None = None


def create_app(base) -> FastAPI:
    base = Path(base)
    rm = RunManager(base)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        rm.shutdown()  # wait=True: let an in-flight run finish, don't corrupt state

    app = FastAPI(title="Polis Dashboard", docs_url="/api/docs", lifespan=lifespan)
    app.state.run_manager = rm
    static = _static_dir()

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
        idx = static / "index.html"
        if idx.exists():
            return FileResponse(idx)
        return JSONResponse({"error": "frontend not built"}, status_code=404)

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

    # Templated widget: prepend the configured intake URL so external apps can embed
    # <script src="http://<host>:8765/static/feedback-widget.js">. Declared BEFORE the
    # StaticFiles mount so this explicit route wins.
    @app.get("/static/feedback-widget.js")
    def widget_js():
        path = static / "feedback-widget.js"
        if not path.exists():
            raise HTTPException(404, "widget not found")
        js = path.read_text(encoding="utf-8")
        intake = resolve_intake_url(base)
        header = f"window.__POLIS_INTAKE_URL={json.dumps(intake)};\n" if intake else ""
        return Response(header + js, media_type="application/javascript")

    if static.exists():
        app.mount("/static", StaticFiles(directory=str(static)), name="static")
    return app


def serve(base, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True):
    import uvicorn
    app = create_app(base)
    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Polis dashboard → {url}  (base: {Path(base).resolve()})")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except OSError as e:
        print(f"Could not bind {host}:{port} ({e}). Try another --port.")
        return 1
    return 0
