from __future__ import annotations

import argparse
import asyncio
import json

from github_deep_search.engine import deep_search
from github_deep_search.run_trace import SearchRunFailed
from github_deep_search.serializers import failure_artifact_to_dict, report_to_dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lightweight GitHub deep search")
    parser.add_argument("query", help="Natural-language requirement")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    try:
        report = await deep_search(args.query)
    except SearchRunFailed as exc:
        if args.format == "json":
            print(json.dumps(failure_artifact_to_dict(exc.artifact), ensure_ascii=False, indent=2))
        else:
            print(exc.artifact.error_report_markdown)
        raise SystemExit(1) from exc
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
