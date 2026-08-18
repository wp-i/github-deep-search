from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from fastapi.testclient import TestClient

from github_deep_search import __version__
from github_deep_search import web
from github_deep_search.config import Settings
from github_deep_search.models import STAGE_NAMES, StageName
from github_deep_search.pipeline import Pipeline, PipelineContext
from github_deep_search.stages.input import InputStage
from tests.fakes import fake_final_report


def settings(*, github: bool, llm: bool, timeout: int = 600) -> Settings:
    return Settings(
        github_token="github-token" if github else None,
        llm_api_key="llm-key" if llm else None,
        llm_base_url="https://provider.example/v1",
        llm_model="model",
        llm_thinking=None,
        llm_reasoning_effort=None,
        max_github_requests=200,
        max_candidates=80,
        max_evidence_repositories=12,
        run_timeout_seconds=timeout,
    )


@dataclass
class PassStage:
    name: StageName

    async def execute(self, context: PipelineContext) -> None:
        context.raise_if_cancelled()
        if self.name == "report":
            language = context.validated_input.report_language if context.validated_input else "en"
            context.final_report = fake_final_report(language)


@dataclass
class BlockingStage:
    name: StageName

    async def execute(self, context: PipelineContext) -> None:
        context.raise_if_cancelled()
        await asyncio.Event().wait()


def fake_pipeline(*, blocked_stage: StageName | None = None) -> Pipeline:
    stages = []
    for name in STAGE_NAMES:
        if name == "input":
            stages.append(InputStage())
        elif name == blocked_stage:
            stages.append(BlockingStage(name))
        else:
            stages.append(PassStage(name))
    return Pipeline(stages)


def test_status_exposes_real_pipeline_availability() -> None:
    application = web.create_app(
        settings_provider=lambda: settings(github=True, llm=False)
    )
    response = TestClient(application).get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "version": __version__,
        "hasGithubToken": True,
        "hasLlmKey": False,
        "hasActiveRun": False,
    }


def test_input_stage_owns_the_2000_character_limit() -> None:
    application = web.create_app(
        pipeline_factory=lambda configured: fake_pipeline(),
        settings_provider=lambda: settings(github=True, llm=True),
    )
    with TestClient(application) as client:
        response = client.post("/api/runs", json={"query": "x" * 2001})
        assert response.status_code == 202
        failed = wait_for_status(client, response.json()["id"], "failed")

    assert failed["error"]["code"] == "invalid_input"
    assert failed["error"]["stage"] == "input"


def test_injected_pipeline_runs_all_stages_and_sse_can_resume() -> None:
    application = web.create_app(
        pipeline_factory=lambda configured: fake_pipeline(),
        settings_provider=lambda: settings(github=True, llm=True),
    )
    with TestClient(application) as client:
        created = client.post("/api/runs", json={"query": "  Build 一个 client  "})
        assert created.status_code == 202
        run_id = created.json()["id"]
        completed = wait_for_status(client, run_id, "completed")

        assert completed["query"] == "  Build 一个 client  "
        assert completed["reportLanguage"] == "zh"
        assert completed["supplementalDiscoveryIteration"] == 0
        assert [stage["name"] for stage in completed["stages"]] == list(STAGE_NAMES)
        assert all(stage["status"] == "completed" for stage in completed["stages"])
        assert [project["repository"] for project in completed["report"]["projects"]] == [
            "example/project-1",
            "example/project-2",
            "example/project-3",
        ]
        assert completed["report"]["projects"][0]["confirmed"][0]["evidence"][0][
            "label"
        ] == "README.md"
        assert completed["report"]["projects"][0]["confirmed"][0]["evidence"][0][
            "quote"
        ] == "Controlled evidence excerpt."
        assert completed["report"]["usage"] == {
            "llmInputTokens": 120,
            "llmOutputTokens": 30,
            "llmTotalTokens": 150,
        }
        assert "githubRequests" not in str(completed["report"])

        events = client.get(
            f"/api/runs/{run_id}/events",
            headers={"Last-Event-ID": "10"},
        )
        assert events.status_code == 200
        assert events.headers["content-type"].startswith("text/event-stream")
        assert "id: 10\n" not in events.text
        assert "id: 11\n" in events.text
        assert "event: run_completed" in events.text


def test_web_rejects_concurrency_supports_reconnect_and_cancellation() -> None:
    application = web.create_app(
        pipeline_factory=lambda configured: fake_pipeline(blocked_stage="parse"),
        settings_provider=lambda: settings(github=True, llm=True),
    )
    with TestClient(application) as client:
        created = client.post("/api/runs", json={"query": "sample request"})
        assert created.status_code == 202
        run_id = created.json()["id"]
        active = wait_for_stage(client, run_id, "parse", "in_progress")

        assert client.get("/api/status").json()["hasActiveRun"] is True
        assert client.get("/api/runs/active").json() == active
        conflict = client.post("/api/runs", json={"query": "second request"})
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "active_run_exists"

        cancelled = client.delete(f"/api/runs/{run_id}")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["stages"][1]["status"] == "cancelled"
        assert client.get("/api/runs/active").status_code == 404
        assert client.get("/api/status").json()["hasActiveRun"] is False


def test_input_stage_failure_is_returned_without_partial_report_or_credentials() -> None:
    application = web.create_app(
        pipeline_factory=lambda configured: fake_pipeline(),
        settings_provider=lambda: settings(github=False, llm=True),
    )
    with TestClient(application) as client:
        created = client.post("/api/runs", json={"query": "sample request"})
        failed = wait_for_status(client, created.json()["id"], "failed")

    assert failed["error"]["code"] == "github_token_missing"
    assert failed["error"]["stage"] == "input"
    assert "github-token" not in str(failed)
    assert "llm-key" not in str(failed)
    assert "report" not in failed


def test_terminal_run_is_kept_until_the_next_run_starts() -> None:
    application = web.create_app(
        pipeline_factory=lambda configured: fake_pipeline(),
        settings_provider=lambda: settings(github=True, llm=True),
    )
    with TestClient(application) as client:
        first = client.post("/api/runs", json={"query": "first request"}).json()
        wait_for_status(client, first["id"], "completed")
        assert client.get(f"/api/runs/{first['id']}").status_code == 200

        second = client.post("/api/runs", json={"query": "second request"}).json()
        assert client.get(f"/api/runs/{first['id']}").status_code == 404
        wait_for_status(client, second["id"], "completed")


def test_home_page_waits_for_status_and_has_no_fake_progress() -> None:
    response = TestClient(web.app).get("/")

    assert response.status_code == 200
    assert "正在检查本地凭据和搜索服务" in response.text
    assert "disabled" in response.text
    assert 'id="stage-list"' in response.text
    assert 'id="cancel-run"' in response.text
    assert "setInterval" not in response.text

    script = TestClient(web.app).get("/static/app.js").text
    assert "new EventSource" in script
    assert 'fetch("/api/runs/active"' in script
    assert 'method: "DELETE"' in script
    assert "setInterval" not in script


def wait_for_status(client: TestClient, run_id: str, status: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        snapshot = response.json()
        if snapshot["status"] == status:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"run did not reach {status}")


def wait_for_stage(client: TestClient, run_id: str, stage_name: str, status: str) -> dict:
    for _ in range(100):
        snapshot = client.get(f"/api/runs/{run_id}").json()
        stage = next(item for item in snapshot["stages"] if item["name"] == stage_name)
        if stage["status"] == status:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"stage {stage_name} did not reach {status}")
