"""MCP 服务入口：工具定义与启动。工具面固定 3 个（architecture.md ADR-7）。"""

import argparse
import uuid
from typing import Annotated, Any

from fastmcp import FastMCP

from grok_search import __version__, grok
from grok_search.config import ConfigError, load_config
from grok_search.sources import NO_SOURCES_NOTE, SessionCache, merge_sources

mcp = FastMCP("grok-search")

_SOURCES_CACHE = SessionCache(max_size=256)


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

    sources = merge_sources(outcome)
    session_id = uuid.uuid4().hex[:12]
    await _SOURCES_CACHE.set(session_id, sources)
    content = outcome.content if sources else outcome.content + NO_SOURCES_NOTE
    return {"session_id": session_id, "content": content, "sources_count": len(sources)}


@mcp.tool(
    name="get_sources",
    description=(
        "Retrieve the full list of sources behind a previous web_search answer. "
        "Pass the session_id that web_search returned. Each source has url, title, "
        "and kind ('cited' = referenced in the answer, 'searched' = visited during search)."
    ),
)
async def get_sources(
    session_id: Annotated[str, "The session_id returned by a previous web_search call."],
) -> dict[str, Any]:
    sources = await _SOURCES_CACHE.get(session_id)
    if sources is None:
        return {
            "error": (
                f"未找到会话 {session_id}: 可能输入有误，或已被淘汰"
                "(仅保留最近 256 次搜索，且服务重启后清空)。请重新执行 web_search。"
            )
        }
    return {"session_id": session_id, "sources": sources, "sources_count": len(sources)}


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
