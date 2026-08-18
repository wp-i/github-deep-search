from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, NoReturn
from urllib.parse import quote

import httpx

from github_deep_search.models import CandidateRepository, Usage


class GitHubProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class GitHubAuthenticationError(GitHubProviderError):
    pass


class GitHubAuthorizationError(GitHubProviderError):
    pass


class GitHubRateLimitError(GitHubProviderError):
    pass


class GitHubQueryError(GitHubProviderError):
    pass


class GitHubRequestLimitError(GitHubProviderError):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str,
        usage: Usage,
        timeout: float = 20.0,
        request_limit: int | None = None,
    ) -> None:
        if not token.strip():
            raise GitHubAuthenticationError(
                "GitHub authentication is required. Configure GITHUB_TOKEN; anonymous fallback is disabled.",
                retryable=False,
            )
        self.usage = usage
        self.request_limit = request_limit
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-deep-search/0.1.0",
        }
        headers["Authorization"] = f"Bearer {token}"
        self.paused = False
        self.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def validate_authentication(self) -> None:
        data = await self._get("/rate_limit")
        if data is None:
            raise GitHubProviderError(
                "GitHub authentication could not be validated. "
                "Authenticated search will not start, and anonymous fallback is disabled.",
                retryable=True,
            )

    async def _get(self, path: str, **params: Any) -> dict[str, Any] | None:
        if self.paused:
            raise GitHubRequestLimitError(
                "The configured GitHub request budget has been exhausted.",
                retryable=False,
            )
        for attempt in range(2):
            if self.request_limit is not None and self.usage.github_requests >= self.request_limit:
                self.paused = True
                self.usage.warnings.append("GitHub request limit reached; further GitHub calls were stopped.")
                raise GitHubRequestLimitError(
                    "The configured GitHub request budget has been exhausted.",
                    retryable=False,
                )
            self.usage.github_requests += 1
            try:
                response = await self.client.get(path, params=params)
                if 500 <= response.status_code < 600 and attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                if response.status_code == 401:
                    raise GitHubAuthenticationError(
                        "GitHub rejected the configured GITHUB_TOKEN (HTTP 401). "
                        "Replace or re-authorize the token; anonymous fallback is disabled.",
                        retryable=False,
                    )
                if response.status_code in {403, 429}:
                    retry_after = response.headers.get("retry-after")
                    remaining = response.headers.get("x-ratelimit-remaining")
                    is_rate_limit = (
                        response.status_code == 429
                        or remaining == "0"
                        or retry_after is not None
                    )
                    if not is_rate_limit:
                        raise GitHubAuthorizationError(
                            "GitHub rejected the authenticated request (HTTP 403). "
                            "Verify token repository access and read permissions; anonymous fallback is disabled.",
                            retryable=False,
                        )
                    reset_delay = self._rate_limit_delay(response.headers)
                    if attempt == 0 and reset_delay is not None and reset_delay <= 60:
                        self.usage.warnings.append(
                            f"GitHub rate limit reached for {path}; retrying after {reset_delay:.1f}s."
                        )
                        await asyncio.sleep(reset_delay)
                        continue
                    message = (
                        "GitHub API rate limits prevented a complete authenticated search "
                        f"(HTTP {response.status_code}). Retry after the limit resets; "
                        "anonymous fallback is disabled."
                    )
                    if retry_after:
                        message += f" Retry-after: {retry_after}s."
                    raise GitHubRateLimitError(message, retryable=True)
                if response.status_code == 422 and path.startswith("/search/"):
                    raise GitHubQueryError(
                        "GitHub rejected one planned search query (HTTP 422).",
                        retryable=False,
                    )
                if response.status_code == 404:
                    # Search results can outlive a repository, branch, README, or file.
                    # That candidate has no usable material at this endpoint, but the
                    # provider and the surrounding search stage are still healthy.
                    return None
                if response.status_code == 409 and "/git/trees/" in path:
                    # GitHub reports an empty repository as a tree conflict. This is
                    # a candidate-level absence of material, not a provider failure.
                    return None
                response.raise_for_status()
                remaining = response.headers.get("x-ratelimit-remaining")
                if remaining is not None and remaining.isdigit() and int(remaining) < 10:
                    self.usage.warnings.append(f"GitHub remaining quota is low: {remaining}")
                try:
                    data = response.json()
                except ValueError:
                    self._raise_invalid_response(path)
                if not isinstance(data, dict):
                    self._raise_invalid_response(path)
                return data
            except GitHubProviderError:
                raise
            except httpx.HTTPError as exc:
                if attempt == 0 and not isinstance(exc, httpx.HTTPStatusError):
                    await asyncio.sleep(0.2)
                    continue
                self.usage.warnings.append(
                    "GitHub authenticated request failed; no anonymous fallback was attempted."
                )
                retryable = not isinstance(exc, httpx.HTTPStatusError)
                if isinstance(exc, httpx.HTTPStatusError):
                    retryable = exc.response.status_code >= 500
                raise GitHubProviderError(
                    "GitHub could not complete an authenticated API request.",
                    retryable=retryable,
                ) from None
        raise GitHubProviderError(
            "GitHub could not complete an authenticated API request.",
            retryable=True,
        )

    def _raise_invalid_response(self, operation: str) -> NoReturn:
        self.usage.warnings.append("GitHub returned an invalid response structure.")
        raise GitHubProviderError(
            "GitHub returned an invalid response structure.",
            retryable=False,
        )

    @staticmethod
    def _rate_limit_delay(headers: httpx.Headers) -> float | None:
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                return None
        reset_at = headers.get("x-ratelimit-reset")
        if not reset_at:
            return None
        try:
            return max(0.0, float(reset_at) - time.time() + 1.0)
        except ValueError:
            return None

    async def search_repositories(
        self,
        query: str,
        per_page: int = 10,
        page: int = 1,
    ) -> list[CandidateRepository]:
        data = await self._get(
            "/search/repositories",
            q=f"{query} in:name,description,readme is:public",
            per_page=min(per_page, 30),
            page=max(1, page),
        )
        if not data:
            return []
        items = data.get("items")
        if not isinstance(items, list):
            self._raise_invalid_response("/search/repositories")
        repositories: list[CandidateRepository] = []
        for item in items:
            if not isinstance(item, dict):
                self._raise_invalid_response("/search/repositories")
            try:
                repository = self._repo_from_json(item, found_by=f"github:{query}")
            except (AttributeError, TypeError, ValueError):
                self._raise_invalid_response("/search/repositories")
            if not repository.owner or not repository.name:
                self._raise_invalid_response("/search/repositories")
            repositories.append(repository)
        return repositories

    async def get_repository(self, owner: str, name: str, found_by: str = "github:url") -> CandidateRepository | None:
        data = await self._get(f"/repos/{owner}/{name}")
        if not data:
            return None
        try:
            repository = self._repo_from_json(data, found_by=found_by)
        except (AttributeError, TypeError, ValueError):
            self._raise_invalid_response(f"/repos/{owner}/{name}")
        if not repository.owner or not repository.name:
            self._raise_invalid_response(f"/repos/{owner}/{name}")
        return repository

    async def fetch_readme(self, repo: CandidateRepository) -> str:
        data = await self._get(f"/repos/{repo.owner}/{repo.name}/readme")
        if not data:
            return ""
        encoded = data.get("content") or ""
        if not isinstance(encoded, str):
            self._raise_invalid_response(f"/repos/{repo.owner}/{repo.name}/readme")
        try:
            return base64.b64decode(encoded, validate=False).decode("utf-8", errors="replace")
        except Exception as exc:
            self.usage.warnings.append(f"README decode failed for {repo.full_name}: {exc}")
            return ""

    async def fetch_tree_paths(self, repo: CandidateRepository, limit: int = 1200) -> list[str]:
        branch = repo.default_branch or "main"
        encoded_branch = quote(branch, safe="")
        data = await self._get(
            f"/repos/{repo.owner}/{repo.name}/git/trees/{encoded_branch}",
            recursive=1,
        )
        if not data:
            return []
        if data.get("truncated"):
            self.usage.warnings.append(f"GitHub tree truncated for {repo.full_name}; source evidence is partial.")
        tree = data.get("tree")
        if not isinstance(tree, list):
            self._raise_invalid_response(f"/repos/{repo.owner}/{repo.name}/git/trees/{branch}")
        paths: list[str] = []
        for item in tree:
            if not isinstance(item, dict):
                self._raise_invalid_response(
                    f"/repos/{repo.owner}/{repo.name}/git/trees/{branch}"
                )
            if item.get("type") != "blob":
                continue
            path = str(item.get("path") or "")
            if path:
                paths.append(path)
            if len(paths) >= limit:
                break
        return paths

    async def fetch_file_text(self, repo: CandidateRepository, path: str, max_chars: int = 10000) -> str:
        encoded_path = quote(path, safe="/")
        data = await self._get(
            f"/repos/{repo.owner}/{repo.name}/contents/{encoded_path}"
        )
        if not data or data.get("type") != "file":
            return ""
        try:
            size = int(data.get("size") or 0)
        except (TypeError, ValueError):
            self._raise_invalid_response(f"/repos/{repo.owner}/{repo.name}/contents/{path}")
        if size > 180_000:
            return ""
        encoded = data.get("content") or ""
        if not encoded:
            return ""
        if not isinstance(encoded, str):
            self._raise_invalid_response(f"/repos/{repo.owner}/{repo.name}/contents/{path}")
        try:
            decoded = base64.b64decode(encoded, validate=False).decode("utf-8", errors="replace")
        except Exception as exc:
            self.usage.warnings.append(f"File decode failed for {repo.full_name}/{path}: {exc}")
            return ""
        return decoded[:max_chars]

    async def fetch_latest_release_at(self, repo: CandidateRepository) -> str | None:
        data = await self._get(f"/repos/{repo.owner}/{repo.name}/releases/latest")
        if not data:
            return None
        released_at = data.get("published_at") or data.get("created_at")
        if released_at is not None and not isinstance(released_at, str):
            self._raise_invalid_response(f"/repos/{repo.owner}/{repo.name}/releases/latest")
        return released_at

    def _repo_from_json(self, data: dict[str, Any], found_by: str) -> CandidateRepository:
        owner = (data.get("owner") or {}).get("login") or ""
        license_info = data.get("license") or {}
        repo = CandidateRepository(
            owner=owner,
            name=data.get("name") or "",
            url=data.get("html_url") or f"https://github.com/{owner}/{data.get('name', '')}",
            description=data.get("description") or "",
            stars=int(data.get("stargazers_count") or 0),
            forks=int(data.get("forks_count") or 0),
            language=data.get("language"),
            topics=list(data.get("topics") or []),
            last_pushed_at=data.get("pushed_at"),
            license=license_info.get("spdx_id") if isinstance(license_info, dict) else None,
            default_branch=data.get("default_branch") or "main",
            is_private=bool(data.get("private", False)),
            is_archived=bool(data.get("archived", False)),
            is_fork=bool(data.get("fork", False)),
            parent_full_name=(
                str((data.get("parent") or {}).get("full_name") or "") or None
            ),
            mirror_url=str(data.get("mirror_url") or "") or None,
            size_kb=int(data.get("size") or 0),
            found_by=[found_by],
        )
        return repo
