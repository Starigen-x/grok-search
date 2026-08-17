"""功能 #1：web_search——SSE 解析、来源事件收集、人话报错。"""

import json

import httpx
import pytest
import respx
from fastmcp import Client

from grok_search import grok
from grok_search.config import Config, load_config
from grok_search.server import mcp

CFG = Config(
    api_url="https://gw.test/v1",
    api_key="sk-test",
    model="test-model",
    timeout_s=5.0,
    debug=False,
)
CHAT_URL = "https://gw.test/v1/chat/completions"


def sse_body(*events: dict) -> str:
    lines = [f"data: {json.dumps(e, ensure_ascii=False)}" for e in events]
    lines.append("data: [DONE]")
    return "\n\n".join(lines)


def delta_chunk(**delta: object) -> dict:
    return {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta}]}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_API_URL", CFG.api_url)
    monkeypatch.setenv("GROK_API_KEY", CFG.api_key)
    monkeypatch.setenv("GROK_MODEL", CFG.model)


@respx.mock
async def test_search_assembles_sse_content_and_collects_sources() -> None:
    respx.post(CHAT_URL).respond(
        200,
        text=sse_body(
            delta_chunk(role="assistant", reasoning_content="🔍 web_search: xxx\n"),
            delta_chunk(content="答案第一段 [[1]](https://a.test/1)"),
            {
                **delta_chunk(content="，第二段。"),
                "search_sources": [
                    {"url": "https://s.test/page", "title": "Searched Page", "type": "web"}
                ],
            },
            delta_chunk(
                annotations=[
                    {
                        "type": "url_citation",
                        "url_citation": {"url": "https://a.test/1", "title": "Cite One"},
                    }
                ]
            ),
        ),
    )
    outcome = await grok.search(CFG, "问题", attempts=1)
    assert outcome.content == "答案第一段 [[1]](https://a.test/1)，第二段。"
    assert "🔍" not in outcome.content, "思考痕迹必须被过滤"
    assert outcome.annotations == [{"url": "https://a.test/1", "title": "Cite One"}]
    assert outcome.search_sources == [{"url": "https://s.test/page", "title": "Searched Page"}]


@respx.mock
async def test_search_falls_back_to_plain_json() -> None:
    respx.post(CHAT_URL).respond(
        200,
        json={"choices": [{"index": 0, "message": {"role": "assistant", "content": "普通答案"}}]},
    )
    outcome = await grok.search(CFG, "问题", attempts=1)
    assert outcome.content == "普通答案"


@respx.mock
async def test_search_retries_transient_500_then_succeeds() -> None:
    route = respx.post(CHAT_URL)
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, text=sse_body(delta_chunk(content="重试后成功"))),
    ]
    outcome = await grok.search(CFG, "问题", attempts=3)
    assert outcome.content == "重试后成功"
    assert route.call_count == 2


@respx.mock
@pytest.mark.parametrize(
    ("status", "prefix"),
    [(401, "配置错误"), (404, "配置错误"), (429, "上游错误"), (503, "上游错误")],
)
async def test_search_http_errors_speak_human(status: int, prefix: str) -> None:
    respx.post(CHAT_URL).respond(status)
    with pytest.raises(grok.UpstreamError, match=f"^{prefix}"):
        await grok.search(CFG, "问题", attempts=1)


@respx.mock
async def test_search_network_error_speaks_human() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(grok.UpstreamError, match="^网络错误"):
        await grok.search(CFG, "问题", attempts=1)


@respx.mock
async def test_search_empty_content_is_an_error() -> None:
    # 功能 #8 起：空内容会自动重发，全空才报错（详见下方 #8 专项测试）
    respx.post(CHAT_URL).respond(200, text=sse_body(delta_chunk(role="assistant")))
    with pytest.raises(grok.UpstreamError, match="空内容"):
        await grok.search(CFG, "问题", attempts=1)


@respx.mock
async def test_web_search_tool_returns_content_and_session(env: None) -> None:
    respx.post(CHAT_URL).respond(200, text=sse_body(delta_chunk(content="工具层答案")))
    async with Client(mcp) as client:
        result = await client.call_tool("web_search", {"query": "问题"})
    # 功能 #2 起：无来源时 content 追加明示标注，故此处断言前缀而非全等
    assert result.data["content"].startswith("工具层答案")
    assert result.data["session_id"]


async def test_web_search_tool_reports_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GROK_API_URL", "GROK_API_KEY", "GROK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    async with Client(mcp) as client:
        result = await client.call_tool("web_search", {"query": "问题"})
    assert result.data["error"].startswith("配置错误")


def test_platform_and_time_context_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    del monkeypatch
    assert "Current time" in grok._time_context()


def test_load_config_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_API_URL", "https://gw.test/v1/")
    monkeypatch.setenv("GROK_API_KEY", "k")
    monkeypatch.setenv("GROK_MODEL", "m")
    assert load_config().api_url == "https://gw.test/v1"


# ---------- 功能 #8：空内容自动重试 ----------


@respx.mock
async def test_empty_content_is_retried_and_succeeds() -> None:
    """上游首次吐空、第二次有内容 → 用户无感拿到答案。"""
    route = respx.post(CHAT_URL)
    route.side_effect = [
        httpx.Response(200, text=sse_body(delta_chunk(role="assistant"))),
        httpx.Response(200, text=sse_body(delta_chunk(content="重发后的答案"))),
    ]
    outcome = await grok.search(CFG, "问题", attempts=1)
    assert outcome.content == "重发后的答案"
    assert route.call_count == 2


@respx.mock
async def test_all_rounds_empty_reports_human_error() -> None:
    """连续全空才报错，且说明重发过几次。"""
    route = respx.post(CHAT_URL)
    route.side_effect = [
        httpx.Response(200, text=sse_body(delta_chunk(role="assistant"))) for _ in range(3)
    ]
    with pytest.raises(grok.UpstreamError, match="连续 3 次返回空内容"):
        await grok.search(CFG, "问题", attempts=1)
    assert route.call_count == 3


@respx.mock
async def test_empty_retries_is_configurable() -> None:
    cfg = Config(**{**CFG.__dict__, "empty_retries": 0})
    route = respx.post(CHAT_URL)
    route.side_effect = [httpx.Response(200, text=sse_body(delta_chunk(role="assistant")))]
    with pytest.raises(grok.UpstreamError, match="连续 1 次返回空内容"):
        await grok.search(cfg, "问题", attempts=1)
    assert route.call_count == 1


@respx.mock
async def test_non_empty_first_round_does_not_retry() -> None:
    """正常返回时不得多打一次上游（省钱、省时间）。"""
    route = respx.post(CHAT_URL).respond(200, text=sse_body(delta_chunk(content="一次就好")))
    outcome = await grok.search(CFG, "问题", attempts=1)
    assert outcome.content == "一次就好"
    assert route.call_count == 1


def test_empty_retries_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_API_URL", "https://gw.test/v1")
    monkeypatch.setenv("GROK_API_KEY", "k")
    monkeypatch.setenv("GROK_MODEL", "m")
    monkeypatch.setenv("GROK_EMPTY_RETRIES", "5")
    assert load_config().empty_retries == 5
