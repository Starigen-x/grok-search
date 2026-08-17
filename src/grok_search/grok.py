"""上游搜索客户端。

网关恒以 SSE 流返回（architecture.md ADR-6 修订），解析时同步收集三路来源事件；
普通 JSON 返回作为兼容后备。所有失败必须转成人话错误向上冒出，禁止吞错。
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from grok_search.config import Config

SEARCH_SYSTEM_PROMPT = (
    "You are a web search assistant. Search the web for the user's query and answer "
    "with up-to-date, factual information. Always cite sources as inline markdown "
    "links like [[1]](https://example.com). Answer in the same language as the query. "
    "Be thorough but not padded."
)

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class UpstreamError(RuntimeError):
    """str(e) 是直接给客户端看的人话，以「配置错误/网络错误/上游错误」开头。"""


@dataclass
class SearchOutcome:
    """一次搜索的全部产出：答案正文 + 三路来源原始事件（合并去重在 sources.py）。"""

    content: str = ""
    annotations: list[dict[str, str]] = field(default_factory=list)
    search_sources: list[dict[str, str]] = field(default_factory=list)


def _time_context() -> str:
    now = datetime.now().astimezone()
    return (
        f"[Current time: {now.strftime('%Y-%m-%d %H:%M %Z')}, "
        f"weekday {now.isoweekday()} (1=Monday)]"
    )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


def _human_error(exc: BaseException, cfg: Config) -> UpstreamError:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return UpstreamError(
                f"配置错误: 密钥被上游拒绝(HTTP {code})。请检查 GROK_API_KEY 是否有效、是否过期。"
            )
        if code == 404:
            return UpstreamError(
                f"配置错误: 接口地址不存在(HTTP 404)。请检查 GROK_API_URL(当前: {cfg.api_url})。"
            )
        if code == 429:
            return UpstreamError("上游错误: 触发限流(HTTP 429)，多次重试仍失败，请稍后再试。")
        return UpstreamError(f"上游错误: 上游服务返回 HTTP {code}，多次重试仍失败。")
    if isinstance(exc, httpx.TimeoutException):
        return UpstreamError(
            f"网络错误: 请求超时(超过 {cfg.timeout_s:.0f} 秒)。可用 GROK_TIMEOUT_S 调大超时。"
        )
    if isinstance(exc, httpx.ConnectError | httpx.NetworkError):
        return UpstreamError(f"网络错误: 无法连接上游服务({cfg.api_url})。请检查网络或地址。")
    return UpstreamError(f"上游错误: {exc.__class__.__name__}: {exc}")


def _collect_chunk(data: dict[str, object], outcome: SearchOutcome) -> None:
    """从单个流式分片中收集正文与来源事件；reasoning 痕迹一律丢弃。"""
    raw_sources = data.get("search_sources")
    if isinstance(raw_sources, list):
        for s in raw_sources:
            if isinstance(s, dict) and s.get("url"):
                outcome.search_sources.append(
                    {"url": str(s["url"]), "title": str(s.get("title") or "")}
                )
    choices = data.get("choices")
    if not isinstance(choices, list):
        return
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        # 流式分片是 delta，非流式后备是 message，两者结构相同
        delta = choice.get("delta") or choice.get("message")
        if not isinstance(delta, dict):
            continue
        piece = delta.get("content")
        if isinstance(piece, str):
            outcome.content += piece
        raw_annotations = delta.get("annotations")
        if isinstance(raw_annotations, list):
            for a in raw_annotations:
                if not isinstance(a, dict):
                    continue
                cite = a.get("url_citation")
                if isinstance(cite, dict) and cite.get("url"):
                    outcome.annotations.append(
                        {"url": str(cite["url"]), "title": str(cite.get("title") or "")}
                    )


def _parse_body(body: str) -> SearchOutcome:
    """解析 SSE 流；无 data: 行时按普通 JSON 后备解析。"""
    outcome = SearchOutcome()
    saw_sse = False
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            saw_sse = payload == "[DONE]" or saw_sse
            continue
        saw_sse = True
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            _collect_chunk(data, outcome)

    if not saw_sse and not outcome.content:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise UpstreamError(
                f"上游错误: 返回内容既不是流式格式也不是 JSON，无法解析。开头片段: {body[:200]!r}"
            ) from None
        if isinstance(data, dict):
            _collect_chunk(data, outcome)
    return outcome


async def _request_once(
    cfg: Config,
    payload: dict[str, object],
    attempts: int,
) -> SearchOutcome:
    """发一次请求（内含网络类错误的重试）。失败抛 UpstreamError（人话）。"""
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(connect=10.0, read=cfg.timeout_s, write=10.0, pool=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(attempts),
                wait=wait_random_exponential(multiplier=0.3, max=5),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    response = await client.post(
                        f"{cfg.api_url}/chat/completions", headers=headers, json=payload
                    )
                    response.raise_for_status()
                    return _parse_body(response.text)
    except UpstreamError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转人话，架构硬约束
        raise _human_error(exc, cfg) from exc
    raise UpstreamError("上游错误: 请求未能完成。")  # 理论不可达，兜底防止返回 None


async def search(
    cfg: Config,
    query: str,
    platform: str = "",
    attempts: int = 3,
) -> SearchOutcome:
    """执行一次联网搜索。

    上游模型偶发返回空内容（冷启动/限流），实测每几次就会遇到一次；此时整轮重发，
    最多 cfg.empty_retries 次，全空才报错——用户不该看到这种抖动。
    """
    user_content = f"{_time_context()}\n\n{query}"
    if platform:
        user_content += f"\n\n(Focus your web search on this platform: {platform})"
    payload: dict[str, object] = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": True,
    }

    rounds = max(0, cfg.empty_retries) + 1
    for _ in range(rounds):
        outcome = await _request_once(cfg, payload, attempts)
        if outcome.content.strip():
            return outcome

    raise UpstreamError(
        f"上游错误: 模型({cfg.model})连续 {rounds} 次返回空内容。"
        "可能是该模型暂不可用或被限流，请稍后再试，或换一个模型。"
    )
