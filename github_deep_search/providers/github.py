from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import httpx

from github_deep_search.models import BudgetUsage, CandidateRepository, ProviderEvent


class GitHubProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class GitHubAuthenticationError(GitHubProviderError):
    pass


class GitHubRateLimitError(GitHubProviderError):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str,
        usage: BudgetUsage,
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
            "User-Agent": "github-deep-search-prototype",
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
            return None
        for attempt in range(2):
            if self.request_limit is not None and self.usage.github_requests >= self.request_limit:
                self.paused = True
                self.usage.warnings.append("GitHub request limit reached; further GitHub calls were stopped.")
                self.usage.provider_events.append(
                    ProviderEvent("github", path, "limited", "request_limit")
                )
                return None
            self.usage.github_requests += 1
            try:
                response = await self.client.get(path, params=params)
                if 500 <= response.status_code < 600 and attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                if response.status_code == 401:
                    self.usage.provider_events.append(
                        ProviderEvent("github", path, "failed", "authentication")
                    )
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
                        self.usage.provider_events.append(
                            ProviderEvent("github", path, "failed", "authorization")
                        )
                        raise GitHubProviderError(
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
                    self.usage.provider_events.append(
                        ProviderEvent("github", path, "limited", "rate_limit")
                    )
                    raise GitHubRateLimitError(message, retryable=True)
                if response.status_code == 404:
                    # Search results can outlive a repository, branch, README, or file.
                    # That candidate has no usable material at this endpoint, but the
                    # provider and the surrounding search stage are still healthy.
                    return None
                response.raise_for_status()
                remaining = response.headers.get("x-ratelimit-remaining")
                if remaining is not None and remaining.isdigit() and int(remaining) < 10:
                    self.usage.warnings.append(f"GitHub remaining quota is low: {remaining}")
                return response.json()
            except GitHubProviderError:
                raise
            except httpx.HTTPError as exc:
                if attempt == 0 and not isinstance(exc, httpx.HTTPStatusError):
                    await asyncio.sleep(0.2)
                    continue
                self.usage.warnings.append(f"GitHub request failed: {exc}")
                self.usage.provider_events.append(
                    ProviderEvent("github", path, "failed", type(exc).__name__)
                )
                return None
        return None

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

    async def search_repositories(self, query: str, per_page: int = 10) -> list[CandidateRepository]:
        self.usage.github_search_requests += 1
        data = await self._get(
            "/search/repositories",
            q=query,
            per_page=min(per_page, 30),
        )
        if not data:
            return []
        return [self._repo_from_json(item, found_by=f"github:{query}") for item in data.get("items", [])]

    async def search_code_repositories(self, query: str, per_page: int = 10) -> list[tuple[str, str, str]]:
        self.usage.github_search_requests += 1
        self.usage.github_code_search_requests += 1
        data = await self._get(
            "/search/code",
            q=query,
            per_page=min(per_page, 30),
        )
        if not data:
            return []
        repos: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in data.get("items", []):
            repo_data = item.get("repository") or {}
            owner_data = repo_data.get("owner") or {}
            owner = str(owner_data.get("login") or "")
            name = str(repo_data.get("name") or "")
            path = str(item.get("path") or "")
            if not owner or not name:
                continue
            key = (owner.lower(), name.lower())
            if key in seen:
                continue
            seen.add(key)
            repos.append((owner, name, path))
        return repos

    async def search_topic_repositories(self, topic: str, per_page: int = 10) -> list[CandidateRepository]:
        self.usage.github_search_requests += 1
        self.usage.github_topic_search_requests += 1
        data = await self._get(
            "/search/repositories",
            q=f"topic:{topic}",
            per_page=min(per_page, 30),
        )
        if not data:
            return []
        return [self._repo_from_json(item, found_by=f"github_topic:{topic}") for item in data.get("items", [])]

    async def search_issue_repositories(self, query: str, per_page: int = 10) -> list[tuple[str, str]]:
        self.usage.github_search_requests += 1
        self.usage.github_issue_search_requests += 1
        data = await self._get(
            "/search/issues",
            q=f"{query} type:issue",
            per_page=min(per_page, 30),
        )
        if not data:
            return []
        repos: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in data.get("items", []):
            repo_url = str(item.get("repository_url") or "")
            marker = "/repos/"
            if marker not in repo_url:
                continue
            owner_name = repo_url.rsplit(marker, 1)[-1].strip("/")
            parts = owner_name.split("/")
            if len(parts) < 2:
                continue
            owner, name = parts[0], parts[1]
            if not owner or not name:
                continue
            key = (owner.lower(), name.lower())
            if key in seen:
                continue
            seen.add(key)
            repos.append((owner, name))
        return repos

    async def get_repository(self, owner: str, name: str, found_by: str = "github:url") -> CandidateRepository | None:
        data = await self._get(f"/repos/{owner}/{name}")
        if not data:
            return None
        return self._repo_from_json(data, found_by=found_by)

    async def fetch_readme(self, repo: CandidateRepository) -> str:
        data = await self._get(f"/repos/{repo.owner}/{repo.name}/readme")
        if not data:
            return ""
        encoded = data.get("content") or ""
        try:
            return base64.b64decode(encoded, validate=False).decode("utf-8", errors="replace")
        except Exception as exc:
            self.usage.warnings.append(f"README decode failed for {repo.full_name}: {exc}")
            self.usage.provider_events.append(
                ProviderEvent("github", f"readme:{repo.full_name}", "failed", "decode_error")
            )
            return ""

    async def fetch_tree_paths(self, repo: CandidateRepository, limit: int = 1200) -> list[str]:
        branch = repo.default_branch or "main"
        data = await self._get(f"/repos/{repo.owner}/{repo.name}/git/trees/{branch}", recursive=1)
        if not data:
            return []
        if data.get("truncated"):
            self.usage.warnings.append(f"GitHub tree truncated for {repo.full_name}; source evidence is partial.")
            self.usage.provider_events.append(
                ProviderEvent("github", f"tree:{repo.full_name}", "limited", "truncated_response")
            )
        paths: list[str] = []
        for item in data.get("tree", []):
            if item.get("type") != "blob":
                continue
            path = str(item.get("path") or "")
            if path:
                paths.append(path)
            if len(paths) >= limit:
                break
        return paths

    async def fetch_file_text(self, repo: CandidateRepository, path: str, max_chars: int = 10000) -> str:
        data = await self._get(f"/repos/{repo.owner}/{repo.name}/contents/{path}")
        if not data or data.get("type") != "file":
            return ""
        size = int(data.get("size") or 0)
        if size > 180_000:
            return ""
        encoded = data.get("content") or ""
        if not encoded:
            return ""
        try:
            decoded = base64.b64decode(encoded, validate=False).decode("utf-8", errors="replace")
        except Exception as exc:
            self.usage.warnings.append(f"File decode failed for {repo.full_name}/{path}: {exc}")
            self.usage.provider_events.append(
                ProviderEvent("github", f"file:{repo.full_name}/{path}", "failed", "decode_error")
            )
            return ""
        return decoded[:max_chars]

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
            found_by=[found_by],
        )
        return repo
