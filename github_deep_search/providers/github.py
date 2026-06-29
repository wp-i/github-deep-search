from __future__ import annotations

import base64
from typing import Any

import httpx

from github_deep_search.models import BudgetUsage, CandidateRepository


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        usage: BudgetUsage,
        timeout: float = 20.0,
        request_limit: int | None = None,
    ) -> None:
        self.usage = usage
        self.request_limit = request_limit
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-deep-search-prototype",
        }
        if token:
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

    async def _get(self, path: str, **params: Any) -> dict[str, Any] | None:
        if self.paused:
            return None
        if self.request_limit is not None and self.usage.github_requests >= self.request_limit:
            self.paused = True
            self.usage.warnings.append("GitHub request limit reached; further GitHub calls were stopped.")
            return None
        self.usage.github_requests += 1
        try:
            response = await self.client.get(path, params=params)
            if response.status_code in {403, 429}:
                retry_after = response.headers.get("retry-after")
                message = f"GitHub rate limit or secondary limit hit: {response.status_code}"
                if retry_after:
                    message += f", retry-after={retry_after}s"
                self.usage.warnings.append(message)
                return None
            response.raise_for_status()
            remaining = response.headers.get("x-ratelimit-remaining")
            if remaining is not None and remaining.isdigit() and int(remaining) < 10:
                self.usage.warnings.append(f"GitHub remaining quota is low: {remaining}")
                if int(remaining) <= 2:
                    self.paused = True
                    self.usage.warnings.append("GitHub requests paused to avoid exhausting the API quota.")
            return response.json()
        except httpx.HTTPError as exc:
            self.usage.warnings.append(f"GitHub request failed: {exc}")
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
            return ""

    async def fetch_tree_paths(self, repo: CandidateRepository, limit: int = 1200) -> list[str]:
        branch = repo.default_branch or "main"
        data = await self._get(f"/repos/{repo.owner}/{repo.name}/git/trees/{branch}", recursive=1)
        if not data:
            return []
        if data.get("truncated"):
            self.usage.warnings.append(f"GitHub tree truncated for {repo.full_name}; source evidence is partial.")
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
