from __future__ import annotations

import asyncio
import os
import re
from collections import OrderedDict
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from github_deep_search.config import get_settings
from github_deep_search.engine import deep_search
from github_deep_search.models import Mode, SearchBudget, SearchReport
from github_deep_search.serializers import report_to_dict


STATIC_DIR = Path(__file__).resolve().parent / "static"
BASELINE_CACHE_SIZE = 12

app = FastAPI(title="GitHub Deep Search", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_baseline_reports: OrderedDict[str, SearchReport] = OrderedDict()
_baseline_lock = asyncio.Lock()


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    mode: Mode = "detailed"
    budget: SearchBudget = "continue"


def _query_key(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip().casefold()


async def _get_baseline(query: str) -> SearchReport | None:
    key = _query_key(query)
    async with _baseline_lock:
        report = _baseline_reports.get(key)
        if report is not None:
            _baseline_reports.move_to_end(key)
        return report


async def _remember_baseline(report: SearchReport) -> None:
    key = _query_key(report.query)
    async with _baseline_lock:
        _baseline_reports[key] = report
        _baseline_reports.move_to_end(key)
        while len(_baseline_reports) > BASELINE_CACHE_SIZE:
            _baseline_reports.popitem(last=False)


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
    baseline = None
    if request.budget != "standard" or request.mode == "detailed":
        baseline = await _get_baseline(request.query)
    report = await deep_search(request.query, request.mode, request.budget, baseline=baseline)
    if request.mode == "light" and request.budget == "standard":
        await _remember_baseline(report)
    return JSONResponse(report_to_dict(report, include_html=True))


def run() -> None:
    host = os.getenv("GITHUB_DEEP_SEARCH_HOST", "127.0.0.1")
    port = int(os.getenv("GITHUB_DEEP_SEARCH_PORT", "8001"))
    reload = os.getenv("GITHUB_DEEP_SEARCH_RELOAD", "1").lower() not in {"0", "false", "no"}
    uvicorn.run("github_deep_search.web:app", host=host, port=port, reload=reload)
