from __future__ import annotations

import asyncio

import httpx
import pytest

from github_deep_search.models import Usage
from github_deep_search.providers.github import (
    GitHubAuthenticationError,
    GitHubClient,
)


def test_github_client_requires_authentication() -> None:
    with pytest.raises(GitHubAuthenticationError):
        GitHubClient("", Usage())


def test_repository_mapping_keeps_public_status_and_relationships() -> None:
    client = GitHubClient("test-token", Usage())
    try:
        repository = client._repo_from_json(
            {
                "owner": {"login": "sample"},
                "name": "project",
                "html_url": "https://github.com/sample/project",
                "private": False,
                "archived": True,
                "fork": True,
                "parent": {"full_name": "upstream/project"},
                "pushed_at": "2026-08-01T00:00:00Z",
                "license": {"spdx_id": "MIT"},
            },
            found_by="github:test",
        )
    finally:
        asyncio.run(client.close())

    assert repository.full_name == "sample/project"
    assert repository.is_private is False
    assert repository.is_archived is True
    assert repository.is_fork is True
    assert repository.parent_full_name == "upstream/project"
    assert repository.last_pushed_at == "2026-08-01T00:00:00Z"
    assert repository.license == "MIT"


def test_authentication_failure_never_retries_anonymously() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401, json={"message": "bad credentials"})

    async def run() -> None:
        usage = Usage()
        client = GitHubClient("test-token", usage)
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={"Authorization": "Bearer test-token"},
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(GitHubAuthenticationError):
                await client.validate_authentication()
        finally:
            await client.close()

        assert len(requests) == 1
        assert requests[0].headers["Authorization"] == "Bearer test-token"
        assert usage.provider_events[-1].kind == "authentication"

    asyncio.run(run())
