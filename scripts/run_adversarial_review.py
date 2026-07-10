from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from github_deep_search.adversarial_review import REVIEW_ROLES, reviews_to_dict, run_adversarial_reviews
from github_deep_search.config import get_settings
from github_deep_search.models import BudgetUsage
from github_deep_search.providers.llm import LLMClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run independent LLM adversarial reviews for an exported report")
    parser.add_argument("report", type=Path, help="Path to a JSON report exported by GitHub Deep Search")
    parser.add_argument("--output", type=Path, required=True, help="Path for adversarial-review.json")
    parser.add_argument(
        "--roles",
        default="user,semantic,evidence,reliability",
        help=f"Comma-separated roles: {', '.join(REVIEW_ROLES)}",
    )
    parser.add_argument(
        "--source-context",
        type=Path,
        help="Optional reviewed diff or source manifest; required when using the architecture role",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    report = _load_json(args.report)
    roles = _roles(args.roles)
    if "architecture" in roles and args.source_context is None:
        raise SystemExit("--source-context is required for the architecture role")
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit("LLM_API_KEY is required to run adversarial reviews")
    source_context = args.source_context.read_text(encoding="utf-8") if args.source_context else ""
    usage = BudgetUsage()
    reviewer = LLMClient(
        settings.llm_api_key,
        settings.llm_base_url,
        settings.llm_model,
        usage,
        thinking=settings.llm_thinking,
        reasoning_effort=settings.llm_reasoning_effort,
    )
    try:
        reviews = await run_adversarial_reviews(reviewer, report, roles, source_context)
    finally:
        await reviewer.close()
    payload = {
        "schemaVersion": "1",
        "roles": roles,
        "reviews": reviews_to_dict(reviews),
        "usage": {
            "llmInputTokens": usage.llm_input_tokens,
            "llmOutputTokens": usage.llm_output_tokens,
            "llmTokenEstimated": usage.llm_token_estimated,
            "warnings": usage.warnings,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read JSON report: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("Report JSON must be an object")
    return value


def _roles(value: str) -> list[str]:
    roles = [item.strip() for item in value.split(",") if item.strip()]
    if not roles:
        raise SystemExit("At least one review role is required")
    unknown = [role for role in roles if role not in REVIEW_ROLES]
    if unknown:
        raise SystemExit(f"Unknown review role(s): {', '.join(unknown)}")
    return list(dict.fromkeys(roles))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
