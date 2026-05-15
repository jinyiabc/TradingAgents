"""FastAPI application — endpoints listed in docs/azure-web-deployment.md §4.1."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.server import db
from tradingagents.server.jobs import JobRunner
from tradingagents.server.schemas import (
    CreateAnalysisRequest,
    CreateAnalysisResponse,
    JobDetail,
    JobSummary,
    OptionsResponse,
)


def _default_db_path() -> Path:
    override = os.environ.get("TRADINGAGENTS_WEB_STATE_DIR")
    if override:
        return Path(override) / "jobs.sqlite"
    return Path.home() / ".tradingagents" / "web" / "jobs.sqlite"


def _allowed_origins() -> list[str]:
    raw = os.environ.get("TRADINGAGENTS_CORS_ORIGINS", "")
    if not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def _parse_client_principal(header_value: str | None) -> dict | None:
    """Decode the X-MS-CLIENT-PRINCIPAL header set by Container Apps Easy Auth.

    The header is base64-encoded JSON; absence means the request is anonymous
    (auth disabled, or the path is in excludedPaths). Returns None on either
    absence or any decoding failure — we never want auth parsing to 500.
    """
    if not header_value:
        return None
    try:
        decoded = base64.b64decode(header_value)
        return json.loads(decoded)
    except (binascii.Error, ValueError, json.JSONDecodeError):
        logger.warning("Failed to decode X-MS-CLIENT-PRINCIPAL header", exc_info=True)
        return None


def _user_from_principal(principal: dict | None) -> dict:
    """Pull display name and email out of the decoded principal."""
    if not principal:
        return {"authenticated": False}
    claims = {c.get("typ"): c.get("val") for c in principal.get("claims") or []}
    return {
        "authenticated": True,
        "name": claims.get("name") or claims.get("preferred_username"),
        "email": claims.get("preferred_username") or claims.get("email"),
        "provider": principal.get("auth_typ"),
    }


def _load_project_dotenv() -> None:
    """Best-effort load of a project-root .env into os.environ.

    Existing env vars always win (override=False), so a key passed on the
    command line still takes precedence over what's in the file. Silently
    skipped if python-dotenv isn't installed or no .env is found.
    """
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)


def create_app(
    *,
    db_path: Path | None = None,
    max_concurrent_jobs: int | None = None,
) -> FastAPI:
    _load_project_dotenv()

    db_path = db_path or _default_db_path()
    max_concurrent = max_concurrent_jobs or int(
        os.environ.get("TRADINGAGENTS_MAX_CONCURRENT_JOBS", "2")
    )

    app = FastAPI(title="TradingAgents API", version="1")
    origins = _allowed_origins()
    # Browsers reject `allow_credentials=True` together with allow_origins=["*"],
    # so credentials are only enabled when CORS is restricted to specific
    # origins. M6 requires this for the frontend to send Easy Auth cookies.
    allow_credentials = origins != ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        allow_credentials=allow_credentials,
    )

    runner = JobRunner(db_path, max_concurrent_jobs=max_concurrent)
    app.state.runner = runner  # so tests can introspect

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/me")
    def me(request: Request) -> dict:
        principal = _parse_client_principal(
            request.headers.get("X-MS-CLIENT-PRINCIPAL")
        )
        return _user_from_principal(principal)

    @app.get("/config/options", response_model=OptionsResponse)
    def options() -> OptionsResponse:
        from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS

        providers = sorted(MODEL_OPTIONS.keys())
        models: dict[str, dict[str, list[dict[str, str]]]] = {}
        for provider, modes in MODEL_OPTIONS.items():
            models[provider] = {
                mode: [{"label": label, "value": value} for label, value in opts]
                for mode, opts in modes.items()
            }
        return OptionsResponse(
            providers=providers,
            models=models,
            analysts=["market", "social", "news", "fundamentals"],
        )

    @app.post("/analyses", response_model=CreateAnalysisResponse, status_code=201)
    async def create_analysis(req: CreateAnalysisRequest) -> CreateAnalysisResponse:
        # Must be async: runner.submit() calls asyncio.create_task(), which
        # requires the route to execute on the event loop rather than in the
        # threadpool FastAPI uses for sync defs.
        job_id = runner.submit(req)
        return CreateAnalysisResponse(job_id=job_id)

    @app.get("/analyses", response_model=list[JobSummary])
    def list_analyses(limit: int = Query(default=50, ge=1, le=500)) -> list[JobSummary]:
        rows = db.list_jobs(runner.db_path, limit=limit)
        return [
            JobSummary(
                job_id=r["job_id"],
                ticker=r["ticker"],
                analysis_date=r["analysis_date"],
                status=r["status"],
                created_at=r["created_at"],
                finished_at=r.get("finished_at"),
            )
            for r in rows
        ]

    @app.get("/analyses/{job_id}", response_model=JobDetail)
    def get_analysis(job_id: str) -> JobDetail:
        row = db.get_job(runner.db_path, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        report_url = f"/analyses/{job_id}/report" if row["status"] == "done" else None
        return JobDetail(
            job_id=row["job_id"],
            ticker=row["ticker"],
            analysis_date=row["analysis_date"],
            status=row["status"],
            current_step=row.get("current_step"),
            progress_pct=row.get("progress_pct"),
            error=row.get("error"),
            report_url=report_url,
            created_at=row["created_at"],
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            config=row["config"],
            prompt_tokens=row.get("prompt_tokens"),
            completion_tokens=row.get("completion_tokens"),
            estimated_cost_usd=row.get("estimated_cost_usd"),
        )

    @app.get("/analyses/{job_id}/report")
    def get_report(job_id: str) -> FileResponse:
        row = db.get_job(runner.db_path, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        if row["status"] != "done":
            raise HTTPException(
                status_code=409,
                detail=f"job is {row['status']}, no report available yet",
            )
        path = row.get("report_path")
        if not path or not Path(path).exists():
            raise HTTPException(status_code=404, detail="report file missing on disk")
        return FileResponse(path, media_type="text/html")

    return app


__all__ = ["create_app"]
# Run with: uvicorn tradingagents.server.app:create_app --factory --host 0.0.0.0 --port 8000
