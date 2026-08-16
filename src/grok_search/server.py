"""MCP 服务入口：工具定义与启动。工具面固定 3 个（architecture.md ADR-7）。"""

import argparse

from fastmcp import FastMCP

from grok_search import __version__

mcp = FastMCP("grok-search")


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
