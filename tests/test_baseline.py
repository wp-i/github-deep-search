from __future__ import annotations

from pathlib import Path

from github_deep_search import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_version_is_unified() -> None:
    assert __version__ == "0.1.0"


def test_removed_product_surfaces_are_absent() -> None:
    removed = [
        "github_deep_search/engine.py",
        "github_deep_search/mcp_server.py",
        "github_deep_search/providers/tavily.py",
        "github_deep_search/adversarial_review.py",
        "github_deep_search/decision_brief.py",
        "requirements-mcp.txt",
    ]
    assert all(not (ROOT / relative).exists() for relative in removed)


def test_only_current_product_documents_remain() -> None:
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "docs").rglob("*")
        if path.is_file()
    }
    assert actual == {
        "docs/ARCHITECTURE.md",
        "docs/CHANGE_RECORD_20260817.md",
        "docs/PRODUCT_CONTRACT.md",
        "docs/TESTING.md",
    }


def test_environment_example_matches_current_provider_contract() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "GITHUB_TOKEN=" in example
    assert "LLM_API_KEY=" in example
    assert "RUN_TIMEOUT_SECONDS=600" in example
    assert "TAVILY" not in example
    assert "TASK_DEADLINE_SECONDS" not in example
    assert "USD_PER" not in example


def test_ci_installs_test_dependencies() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "pip install -r requirements-dev.txt" in workflow
