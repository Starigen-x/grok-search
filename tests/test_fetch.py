"""功能 #3：web_fetch——本地正文提取、人话报错、真实抓取（live 标记）。"""

import httpx
import pytest
import respx
from fastmcp import Client

from grok_search import fetch
from grok_search.server import mcp

SAMPLE_HTML = """
<html><head><title>测试文章</title></head><body>
<nav>导航栏噪音 首页 关于 联系</nav>
<article>
<h1>可再生能源的现状</h1>
<p>2026 年，全球光伏装机容量继续增长，多个国家的可再生能源发电占比已超过煤电。
这是正文的第二句，用来确保提取器认为这是一篇真实的文章内容而不是模板噪音。</p>
<p>详见 <a href="https://report.test/2026">年度报告</a>。</p>
<table><tr><th>年份</th><th>占比</th></tr><tr><td>2026</td><td>42%</td></tr></table>
</article>
<footer>页脚版权噪音</footer>
</body></html>
"""


@respx.mock
async def test_fetch_extracts_main_content_as_markdown() -> None:
    respx.get("https://site.test/article").respond(
        200, text=SAMPLE_HTML, headers={"content-type": "text/html; charset=utf-8"}
    )
    md = await fetch.fetch_markdown("https://site.test/article")
    assert "可再生能源的现状" in md
    assert "光伏装机容量" in md
    assert "导航栏噪音" not in md


@respx.mock
async def test_fetch_http_error_speaks_human() -> None:
    respx.get("https://site.test/gone").respond(404)
    with pytest.raises(fetch.FetchError, match="HTTP 404.*页面不存在"):
        await fetch.fetch_markdown("https://site.test/gone")


@respx.mock
async def test_fetch_network_error_speaks_human() -> None:
    respx.get("https://down.test/").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(fetch.FetchError, match="^网络错误"):
        await fetch.fetch_markdown("https://down.test/")


async def test_fetch_rejects_non_http_url() -> None:
    with pytest.raises(fetch.FetchError, match="^参数错误"):
        await fetch.fetch_markdown("ftp://files.test/x")


@respx.mock
async def test_fetch_rejects_binary_content() -> None:
    respx.get("https://site.test/doc.pdf").respond(
        200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}
    )
    with pytest.raises(fetch.FetchError, match="而非网页"):
        await fetch.fetch_markdown("https://site.test/doc.pdf")


@respx.mock
async def test_fetch_empty_page_speaks_human() -> None:
    respx.get("https://site.test/empty").respond(
        200, text="<html><body></body></html>", headers={"content-type": "text/html"}
    )
    with pytest.raises(fetch.FetchError, match="提取不出正文"):
        await fetch.fetch_markdown("https://site.test/empty")


@respx.mock
async def test_web_fetch_tool_returns_content_or_error() -> None:
    respx.get("https://site.test/article").respond(
        200, text=SAMPLE_HTML, headers={"content-type": "text/html"}
    )
    respx.get("https://site.test/gone").respond(404)
    async with Client(mcp) as client:
        ok = await client.call_tool("web_fetch", {"url": "https://site.test/article"})
        bad = await client.call_tool("web_fetch", {"url": "https://site.test/gone"})
    assert "可再生能源" in ok.data["content"]
    assert bad.data["error"].startswith("抓取错误")


@pytest.mark.live
async def test_fetch_example_dot_com_live() -> None:
    md = await fetch.fetch_markdown("https://example.com")
    assert "Example Domain" in md
