"""功能 #2：来源合并去重、行内引用解析、会话缓存、get_sources 工具。"""

import json

import pytest
import respx
from fastmcp import Client

from grok_search.grok import SearchOutcome
from grok_search.server import mcp
from grok_search.sources import (
    NO_SOURCES_NOTE,
    SessionCache,
    extract_inline_citations,
    merge_sources,
)

# ---------- 行内引用解析 ----------


def test_inline_double_bracket_citations() -> None:
    content = "结论甲 [[1]](https://a.test/x) 结论乙 [[2]](https://b.test/y)"
    assert extract_inline_citations(content) == [
        {"url": "https://a.test/x", "title": ""},
        {"url": "https://b.test/y", "title": ""},
    ]


def test_inline_single_bracket_and_dedupe() -> None:
    content = "甲 [1](https://a.test/x) 乙 [[1]](https://a.test/x)"
    assert extract_inline_citations(content) == [{"url": "https://a.test/x", "title": ""}]


def test_plain_markdown_links_are_not_citations() -> None:
    content = "参见 [官方文档](https://docs.test/guide) 与 [首页](https://home.test)"
    assert extract_inline_citations(content) == []


# ---------- 三路合并 ----------


def test_merge_priority_and_dedupe() -> None:
    outcome = SearchOutcome(
        content="正文 [[1]](https://cite.test/a) [[2]](https://inline-only.test/b)",
        annotations=[{"url": "https://cite.test/a", "title": "标注标题"}],
        search_sources=[
            {"url": "https://cite.test/a", "title": "搜索里也出现"},
            {"url": "https://searched.test/c", "title": "仅搜索"},
        ],
    )
    merged = merge_sources(outcome)
    assert merged == [
        {"url": "https://cite.test/a", "title": "标注标题", "kind": "cited"},
        {"url": "https://inline-only.test/b", "title": "", "kind": "cited"},
        {"url": "https://searched.test/c", "title": "仅搜索", "kind": "searched"},
    ]


def test_merge_backfills_missing_title() -> None:
    outcome = SearchOutcome(
        content="正文 [[1]](https://a.test/x)",
        annotations=[],
        search_sources=[{"url": "https://a.test/x", "title": "来自搜索的标题"}],
    )
    merged = merge_sources(outcome)
    assert merged[0]["title"] == "来自搜索的标题"
    assert merged[0]["kind"] == "cited"


# ---------- 会话缓存 ----------


async def test_cache_roundtrip_and_miss() -> None:
    cache = SessionCache(max_size=4)
    await cache.set("s1", [{"url": "u", "title": "", "kind": "cited"}])
    assert await cache.get("s1") == [{"url": "u", "title": "", "kind": "cited"}]
    assert await cache.get("nope") is None


async def test_cache_evicts_least_recently_used() -> None:
    cache = SessionCache(max_size=2)
    await cache.set("a", [])
    await cache.set("b", [])
    assert await cache.get("a") is not None  # 触摸 a，b 变最旧
    await cache.set("c", [])
    assert await cache.get("b") is None
    assert await cache.get("a") is not None
    assert await cache.get("c") is not None


# ---------- 工具层全流程 ----------


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_API_URL", "https://gw.test/v1")
    monkeypatch.setenv("GROK_API_KEY", "sk-test")
    monkeypatch.setenv("GROK_MODEL", "test-model")


def sse(*events: dict) -> str:
    lines = [f"data: {json.dumps(e, ensure_ascii=False)}" for e in events]
    lines.append("data: [DONE]")
    return "\n\n".join(lines)


@respx.mock
async def test_search_then_get_sources_roundtrip(env: None) -> None:
    respx.post("https://gw.test/v1/chat/completions").respond(
        200,
        text=sse(
            {
                "choices": [{"index": 0, "delta": {"content": "答案 [[1]](https://a.test/1)"}}],
                "search_sources": [{"url": "https://s.test/p", "title": "S", "type": "web"}],
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {"url": "https://a.test/1", "title": "A1"},
                                }
                            ]
                        },
                    }
                ]
            },
        ),
    )
    async with Client(mcp) as client:
        search = await client.call_tool("web_search", {"query": "问题"})
        assert search.data["sources_count"] == 2
        assert NO_SOURCES_NOTE not in search.data["content"]

        result = await client.call_tool("get_sources", {"session_id": search.data["session_id"]})
    assert result.data["sources_count"] == 2
    assert result.data["sources"][0] == {"url": "https://a.test/1", "title": "A1", "kind": "cited"}
    assert result.data["sources"][1]["kind"] == "searched"


@respx.mock
async def test_search_with_no_sources_says_so(env: None) -> None:
    respx.post("https://gw.test/v1/chat/completions").respond(
        200, text=sse({"choices": [{"index": 0, "delta": {"content": "无来源的答案"}}]})
    )
    async with Client(mcp) as client:
        search = await client.call_tool("web_search", {"query": "问题"})
    assert search.data["sources_count"] == 0
    assert search.data["content"].endswith(NO_SOURCES_NOTE)


async def test_get_sources_unknown_session_speaks_human() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("get_sources", {"session_id": "does-not-exist"})
    assert "未找到会话" in result.data["error"]
