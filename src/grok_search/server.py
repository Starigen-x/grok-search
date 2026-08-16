"""MCP 服务入口：工具定义与启动。工具面固定 3 个（architecture.md ADR-7）。"""

import argparse
import os
import sys
import uuid
from typing import Annotated, Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from grok_search import __version__, fetch, grok
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


@mcp.tool(
    name="web_fetch",
    description=(
        "Fetch a web page and return its main content as Markdown "
        "(text, links, tables preserved). Works without any API key. "
        "Returns 'content' on success or 'error' with a human-readable reason. "
        "Note: JavaScript-only pages and paywalled content may not be extractable."
    ),
)
async def web_fetch(
    url: Annotated[str, "Complete http(s):// URL of the page to read."],
) -> dict[str, Any]:
    try:
        content = await fetch.fetch_markdown(url)
    except fetch.FetchError as e:
        return {"error": str(e)}
    return {"url": url, "content": content}


@mcp.custom_route("/", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """无鉴权的存活页：老板在浏览器里一眼确认服务活着。"""
    del request
    return JSONResponse(
        {
            "service": "grok-search",
            "version": __version__,
            "status": "ok",
            "hint": "MCP 端点在 /mcp，需要 Bearer 访问令牌(GROK_HTTP_TOKEN)。",
        }
    )


def require_http_token() -> str:
    """HTTP 形态必须有访问令牌，防止陌生人盗用搜索额度。"""
    token = os.getenv("GROK_HTTP_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "配置错误: 网络服务模式必须设置环境变量 GROK_HTTP_TOKEN(访问令牌)，"
            "否则任何人都能连上来消耗你的搜索额度。"
            "生成一个随机长字符串填入即可，客户端连接时用同一令牌。"
        )
    return token


def enable_http_auth(token: str) -> None:
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    mcp.auth = StaticTokenVerifier(tokens={token: {"client_id": "grok-search-owner"}})


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
        enable_http_auth(require_http_token())
        mcp.run(transport="http", host=args.host, port=args.port, show_banner=False)
    else:
        mcp.run(transport="stdio", show_banner=False)


def _print_config_error_and_exit(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
