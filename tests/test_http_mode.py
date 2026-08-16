"""功能 #4：网络服务模式——令牌门禁、存活页、端到端 HTTP 客户端（live 标记）。"""

import os
import signal
import socket
import subprocess
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from grok_search.server import enable_http_auth, mcp, require_http_token


@asynccontextmanager
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    """离线驱动 HTTP 应用；生命周期在同一任务内进出，避开 pytest 跨任务坑。"""
    enable_http_auth("test-token-abc")
    app = mcp.http_app()
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                yield client
    finally:
        mcp.auth = None


MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


async def test_mcp_endpoint_requires_token() -> None:
    async with http_client() as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 401


async def test_mcp_endpoint_rejects_wrong_token() -> None:
    async with http_client() as client:
        response = await client.post(
            "/mcp",
            json=INITIALIZE,
            headers={**MCP_HEADERS, "Authorization": "Bearer wrong-token"},
        )
    assert response.status_code == 401


async def test_mcp_endpoint_accepts_correct_token() -> None:
    async with http_client() as client:
        response = await client.post(
            "/mcp",
            json=INITIALIZE,
            headers={**MCP_HEADERS, "Authorization": "Bearer test-token-abc"},
        )
    assert response.status_code == 200


async def test_health_page_is_open_and_friendly() -> None:
    async with http_client() as client:
        response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "grok-search"
    assert data["status"] == "ok"


def test_http_mode_refuses_to_start_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_HTTP_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="GROK_HTTP_TOKEN"):
        require_http_token()


def test_http_token_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_HTTP_TOKEN", "  tok  ")
    assert require_http_token() == "tok"


@pytest.mark.live
async def test_real_server_full_roundtrip() -> None:
    """起真实进程：带令牌客户端列出恰好 3 个工具并成功调用 web_fetch。"""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    env = {**os.environ, "GROK_HTTP_TOKEN": "live-test-token"}
    proc = subprocess.Popen(
        ["uv", "run", "grok-search", "--transport", "http", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        else:
            pytest.fail("服务 30 秒内未启动")

        assert httpx.post(f"{base}/mcp", json=INITIALIZE, headers=MCP_HEADERS).status_code == 401

        transport = StreamableHttpTransport(f"{base}/mcp", auth="live-test-token")
        async with Client(transport) as client:
            tools = await client.list_tools()
            assert sorted(t.name for t in tools) == ["get_sources", "web_fetch", "web_search"]
            result = await client.call_tool("web_fetch", {"url": "https://example.com"})
            assert "Example Domain" in result.data["content"]
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
