from __future__ import annotations

import argparse

from github_deep_search.engine import deep_search
from github_deep_search.serializers import report_to_dict


try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install MCP support with: pip install -r requirements-mcp.txt") from exc


mcp = FastMCP("github-deep-search")


@mcp.tool()
async def github_deep_search(query: str, max_results: int = 3) -> dict:
    """Search and analyze GitHub repositories for a natural-language requirement."""
    report = await deep_search(query)  # type: ignore[arg-type]
    data = report_to_dict(report)
    data["topProjects"] = data["topProjects"][:max_results]
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GitHub Deep Search MCP server.")
    parser.parse_args()
    mcp.run()


if __name__ == "__main__":
    main()
