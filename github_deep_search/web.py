from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from github_deep_search.config import get_settings
from github_deep_search.engine import deep_search
from github_deep_search.run_trace import SearchRunFailed
from github_deep_search.serializers import failure_artifact_to_dict, report_to_dict


STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="GitHub Deep Search", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def api_status() -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "hasGithubToken": bool(settings.github_token),
            "hasLlmKey": settings.has_llm,
            "hasTavilyKey": settings.has_tavily,
            "recommendedKeys": ["GITHUB_TOKEN", "LLM_API_KEY"],
        }
    )


@app.post("/api/search")
async def api_search(request: SearchRequest) -> JSONResponse:
    try:
        report = await deep_search(request.query)
    except SearchRunFailed as exc:
        artifact = failure_artifact_to_dict(exc.artifact)
        status_code = 400 if exc.artifact.failure.kind == "invalid_request" else 502 if exc.artifact.failure.kind == "provider" else 500
        return JSONResponse(
            {
                "error": exc.artifact.failure.message,
                "failureArtifact": artifact,
            },
            status_code=status_code,
        )
    return JSONResponse(report_to_dict(report, include_html=True))


def run() -> None:
    host = os.getenv("GITHUB_DEEP_SEARCH_HOST", "127.0.0.1")
    port = int(os.getenv("GITHUB_DEEP_SEARCH_PORT", "8001"))
    reload = os.getenv("GITHUB_DEEP_SEARCH_RELOAD", "1").lower() not in {"0", "false", "no"}
    uvicorn.run("github_deep_search.web:app", host=host, port=port, reload=reload)
