from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from github_deep_search import __version__
from github_deep_search.config import Settings, get_settings
from github_deep_search.pipeline import PipelineRunner, build_pipeline
from github_deep_search.run_manager import (
    RunConflictError,
    RunManager,
    RunNotActiveError,
    RunNotFoundError,
)
from github_deep_search.serializers import serialize_event, serialize_run


STATIC_DIR = Path(__file__).resolve().parent / "static"
PipelineFactory = Callable[[Settings], PipelineRunner]
SettingsProvider = Callable[[], Settings]


class SearchRequest(BaseModel):
    query: str


def create_app(
    *,
    pipeline_factory: PipelineFactory = build_pipeline,
    settings_provider: SettingsProvider = get_settings,
) -> FastAPI:
    manager = RunManager()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        del application
        yield
        await manager.close()

    application = FastAPI(
        title="GitHub Deep Search",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.run_manager = manager
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def current_settings() -> Settings:
        return settings_provider()

    @application.get("/", response_class=FileResponse)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/api/status")
    async def api_status() -> JSONResponse:
        settings = current_settings()
        active = await manager.get_active()
        return JSONResponse(
            {
                "version": __version__,
                "hasGithubToken": settings.has_github,
                "hasLlmKey": settings.has_llm,
                "hasActiveRun": active is not None,
            }
        )

    @application.post("/api/runs")
    async def create_run(request: SearchRequest) -> JSONResponse:
        settings = current_settings()
        try:
            pipeline = pipeline_factory(settings)
        except Exception:
            return _error_response(
                503,
                "pipeline_unavailable",
                "The search pipeline could not be initialized.",
            )

        try:
            snapshot = await manager.start(request.query, settings, pipeline)
        except RunConflictError:
            return _error_response(409, "active_run_exists", "Another run is already active.")
        return JSONResponse(serialize_run(snapshot), status_code=202)

    @application.get("/api/runs/active")
    async def get_active_run() -> JSONResponse:
        snapshot = await manager.get_active()
        if snapshot is None:
            return _error_response(404, "active_run_not_found", "There is no active run.")
        return JSONResponse(serialize_run(snapshot))

    @application.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> JSONResponse:
        try:
            snapshot = await manager.get(run_id)
        except RunNotFoundError:
            return _error_response(404, "run_not_found", "The run was not found.")
        return JSONResponse(serialize_run(snapshot))

    @application.get("/api/runs/{run_id}/events")
    async def get_run_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> Response:
        try:
            await manager.get(run_id)
        except RunNotFoundError:
            return _error_response(404, "run_not_found", "The run was not found.")

        resume_after = max(after, _event_sequence(last_event_id))

        async def event_stream():
            async for event in manager.iter_events(run_id, after=resume_after):
                if event is None:
                    yield ": keep-alive\n\n"
                    continue
                payload = json.dumps(serialize_event(event), ensure_ascii=False, separators=(",", ":"))
                yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @application.delete("/api/runs/{run_id}")
    async def cancel_run(run_id: str) -> JSONResponse:
        try:
            snapshot = await manager.cancel(run_id)
        except RunNotFoundError:
            return _error_response(404, "run_not_found", "The run was not found.")
        except RunNotActiveError:
            return _error_response(409, "run_not_active", "The run is no longer active.")
        return JSONResponse(serialize_run(snapshot))

    return application


def _event_sequence(value: str | None) -> int:
    if value is None:
        return 0
    try:
        parsed = int(value)
    except ValueError:
        return 0
    return max(parsed, 0)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status_code,
    )


app = create_app()


def run() -> None:
    host = os.getenv("GITHUB_DEEP_SEARCH_HOST", "127.0.0.1")
    port = int(os.getenv("GITHUB_DEEP_SEARCH_PORT", "8001"))
    reload = os.getenv("GITHUB_DEEP_SEARCH_RELOAD", "1").lower() not in {"0", "false", "no"}
    uvicorn.run("github_deep_search.web:app", host=host, port=port, reload=reload)
