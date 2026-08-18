from __future__ import annotations

import asyncio

import httpx
import pytest

from github_deep_search.models import Usage
from github_deep_search.providers.github import (
    GitHubAuthenticationError,
    GitHubAuthorizationError,
    GitHubClient,
    GitHubProviderError,
    GitHubQueryError,
    GitHubRateLimitError,
    GitHubRequestLimitError,
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
                "mirror_url": "https://mirror.example/project.git",
                "size": 321,
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
    assert repository.mirror_url == "https://mirror.example/project.git"
    assert repository.size_kb == 321
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

    asyncio.run(run())


def test_repository_search_forces_public_results() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "owner": {"login": "sample"},
                        "name": "project",
                        "html_url": "https://github.com/sample/project",
                        "private": False,
                    }
                ]
            },
        )

    async def run() -> None:
        client = GitHubClient("test-token", Usage())
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={"Authorization": "Bearer test-token"},
            transport=httpx.MockTransport(handler),
        )
        try:
            repositories = await client.search_repositories(
                "streaming validation",
                page=2,
            )
        finally:
            await client.close()

        assert [repository.full_name for repository in repositories] == ["sample/project"]
        assert repositories[0].found_by == ["github:streaming validation"]

    asyncio.run(run())
    assert requests[0].url.params["q"] == (
        "streaming validation in:name,description,readme is:public"
    )
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[0].url.params["page"] == "2"


def test_latest_release_uses_published_timestamp_and_allows_no_release() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/with-release/releases/latest"):
            return httpx.Response(
                200,
                json={"published_at": "2026-08-01T00:00:00Z"},
            )
        return httpx.Response(404, json={"message": "Not Found"})

    async def run() -> None:
        client = GitHubClient("test-token", Usage())
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={"Authorization": "Bearer test-token"},
            transport=httpx.MockTransport(handler),
        )
        try:
            released = await client.fetch_latest_release_at(
                client._repo_from_json(
                    {
                        "owner": {"login": "sample"},
                        "name": "with-release",
                        "html_url": "https://github.com/sample/with-release",
                    },
                    found_by="test",
                )
            )
            missing = await client.fetch_latest_release_at(
                client._repo_from_json(
                    {
                        "owner": {"login": "sample"},
                        "name": "without-release",
                        "html_url": "https://github.com/sample/without-release",
                    },
                    found_by="test",
                )
            )
        finally:
            await client.close()

        assert released == "2026-08-01T00:00:00Z"
        assert missing is None

    asyncio.run(run())
    assert len(requests) == 2


def test_empty_repository_tree_conflict_is_candidate_level_empty_material() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"message": "Git Repository is empty."})

    async def run() -> None:
        client = GitHubClient("test-token", Usage())
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={"Authorization": "Bearer test-token"},
            transport=httpx.MockTransport(handler),
        )
        repository = client._repo_from_json(
            {
                "owner": {"login": "sample"},
                "name": "empty",
                "html_url": "https://github.com/sample/empty",
            },
            found_by="test",
        )
        try:
            paths = await client.fetch_tree_paths(repository)
        finally:
            await client.close()
        assert paths == []

    asyncio.run(run())


def test_invalid_search_query_is_distinct_from_an_empty_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Validation Failed"})

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
            with pytest.raises(GitHubQueryError):
                await client.search_repositories("invalid query")
        finally:
            await client.close()


    asyncio.run(run())


def test_empty_repository_search_is_a_successful_empty_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

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
            repositories = await client.search_repositories("no matches")
        finally:
            await client.close()

        assert repositories == []

    asyncio.run(run())


def test_non_rate_limit_403_is_an_authorization_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

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
            with pytest.raises(GitHubAuthorizationError):
                await client.search_repositories("query")
        finally:
            await client.close()


    asyncio.run(run())


def test_rate_limit_failure_is_explicit_after_one_bounded_retry() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, headers={"retry-after": "0"}, json={"message": "limited"})

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
            with pytest.raises(GitHubRateLimitError):
                await client.search_repositories("query")
        finally:
            await client.close()


    asyncio.run(run())
    assert len(requests) == 2


def test_server_failure_is_explicit_after_one_bounded_retry() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, json={"message": "unavailable"})

    async def run() -> None:
        client = GitHubClient("test-token", Usage())
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={"Authorization": "Bearer test-token"},
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(GitHubProviderError) as caught:
                await client.search_repositories("query")
        finally:
            await client.close()

        assert caught.value.retryable is True

    asyncio.run(run())
    assert len(requests) == 2


def test_network_failure_is_explicit_after_one_transport_retry() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ConnectError("offline", request=request)

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
            with pytest.raises(GitHubProviderError) as caught:
                await client.search_repositories("query")
        finally:
            await client.close()

        assert caught.value.retryable is True

    asyncio.run(run())
    assert len(requests) == 2


def test_invalid_github_response_structure_fails_explicitly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

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
            with pytest.raises(GitHubProviderError):
                await client.search_repositories("query")
        finally:
            await client.close()


    asyncio.run(run())


def test_local_request_limit_is_not_silently_treated_as_no_results() -> None:
    async def run() -> None:
        usage = Usage()
        client = GitHubClient("test-token", usage, request_limit=0)
        try:
            with pytest.raises(GitHubRequestLimitError):
                await client.search_repositories("query")
        finally:
            await client.close()


    asyncio.run(run())
