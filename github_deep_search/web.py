from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from github_deep_search import __version__
from github_deep_search.config import get_settings


STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="GitHub Deep Search", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def api_status() -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "version": __version__,
            "hasGithubToken": settings.has_github,
            "hasLlmKey": settings.has_llm,
            "searchAvailable": False,
            "hasActiveRun": False,
        }
    )


@app.post("/api/runs", status_code=503)
async def create_run(request: SearchRequest) -> JSONResponse:
    del request
    return JSONResponse(
        {
            "error": {
                "code": "development_baseline",
                "message": "The six-stage search pipeline is being rebuilt and is not available yet.",
            }
        },
        status_code=503,
    )


def run() -> None:
    host = os.getenv("GITHUB_DEEP_SEARCH_HOST", "127.0.0.1")
    port = int(os.getenv("GITHUB_DEEP_SEARCH_PORT", "8001"))
    reload = os.getenv("GITHUB_DEEP_SEARCH_RELOAD", "1").lower() not in {"0", "false", "no"}
    uvicorn.run("github_deep_search.web:app", host=host, port=port, reload=reload)
