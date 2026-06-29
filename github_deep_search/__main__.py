from __future__ import annotations

import argparse
import asyncio
import json

from github_deep_search.engine import deep_search
from github_deep_search.serializers import report_to_dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lightweight GitHub deep search")
    parser.add_argument("query", help="Natural-language requirement")
    parser.add_argument("--mode", choices=["light", "detailed"], default="detailed")
    parser.add_argument("--budget", choices=["standard", "high", "continue"], default="continue")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    report = await deep_search(args.query, args.mode, args.budget)
    if args.format == "json":
        print(
            json.dumps(
                report_to_dict(report),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(report.report_markdown)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
