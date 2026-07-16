from __future__ import annotations

import re
from html import escape
from urllib.parse import urlparse


REPO_URL_RE = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 3)


def compact_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.2) :]
    return f"{head}\n\n...[truncated]...\n\n{tail}"


def extract_github_repos(text: str) -> list[tuple[str, str]]:
    repos: list[tuple[str, str]] = []
    for owner, name in REPO_URL_RE.findall(text or ""):
        name = name.rstrip(".").removesuffix(".git")
        if owner.lower() in {"topics", "marketplace", "search"}:
            continue
        pair = (owner, name)
        if pair not in repos:
            repos.append(pair)
    return repos


def normalize_repo_url(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    if parts[0].lower() in {"topics", "marketplace", "search", "features", "collections"}:
        return None
    return parts[0], parts[1].removesuffix(".git")


def keyword_bag(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    return set(words)


def simple_markdown_to_html(markdown: str) -> str:
    def autolink_plain(value: str) -> str:
        parts: list[str] = []
        cursor = 0
        for match in re.finditer(r"https?://[^\s)]+", value):
            parts.append(escape(value[cursor : match.start()]))
            url = escape(match.group(0))
            parts.append(
                f'<a href="{url}" target="_blank" rel="noreferrer">{url}</a>'
            )
            cursor = match.end()
        parts.append(escape(value[cursor:]))
        return "".join(parts)

    def linkify(value: str) -> str:
        address_match = re.fullmatch(r"(?:Address|地址)[:：]\s*(https?://github\.com/[^\s)]+)", value)
        if address_match:
            url = escape(address_match.group(1))
            return f'<a href="{url}" target="_blank" rel="noreferrer">打开 GitHub 仓库</a>'
        parts: list[str] = []
        cursor = 0
        for match in re.finditer(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", value):
            parts.append(autolink_plain(value[cursor : match.start()]))
            label = escape(match.group(1))
            url = escape(match.group(2))
            parts.append(
                f'<a href="{url}" target="_blank" rel="noreferrer">{label}</a>'
            )
            cursor = match.end()
        parts.append(autolink_plain(value[cursor:]))
        return "".join(parts)

    html_lines: list[str] = []
    in_list = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        if stripped.startswith("#### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h4>{linkify(stripped[5:])}</h4>")
        elif stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{linkify(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{linkify(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h1>{linkify(stripped[2:])}</h1>")
        elif stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{linkify(stripped[2:])}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{linkify(stripped)}</p>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)
