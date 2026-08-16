"""MCP 服务入口：工具定义与启动。工具面固定 3 个（architecture.md ADR-7）。"""

import argparse
import uuid
from typing import Annotated, Any

from fastmcp import FastMCP

from grok_search import __version__, grok
from grok_search.config import ConfigError, load_config

mcp = FastMCP("grok-search")


@mcp.tool(
    name="web_search",
    description=(
        "Search the web and get an up-to-date answer with citations. "
        "Returns session_id (pass it to get_sources for the full source list), "
        "content (the answer), and sources_count. "
        "On failure returns an 'error' field with a human-readable reason."
    ),
)
async def web_search(
    query: Annotated[str, "Clear, self-contained natural-language search query."],
    platform: Annotated[
        str,
        "Optional platform to focus on, e.g. 'Twitter', 'GitHub', 'Reddit'. "
        "Leave empty for general web search.",
    ] = "",
) -> dict[str, Any]:
    try:
        cfg = load_config()
    except ConfigError as e:
        return {"error": str(e)}
    try:
        outcome = await grok.search(cfg, query, platform)
    except grok.UpstreamError as e:
        return {"error": str(e)}

    session_id = uuid.uuid4().hex[:12]
    return {"session_id": session_id, "content": outcome.content}


def main() -> None:
    parser = argparse.ArgumentParser(prog="grok-search", description="联网搜索 MCP 服务")
    parser.add_argument("--version", action="version", version=f"grok-search {__version__}")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio=桌面客户端本地安装；http=网络服务形态（手机/网页端）",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
