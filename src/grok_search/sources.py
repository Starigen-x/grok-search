"""来源处理：三路合并去重 + 会话缓存。

三路来源（grok.SearchOutcome）按可信度排序合并：
1. annotations（上游引用标注，最可信）→ kind="cited"
2. 正文行内引用 [[n]](url) / [n](url) → kind="cited"
3. search_sources（搜索过程访问的页面清单）→ kind="searched"
同一 url 只保留最先出现（即最可信）的一条。
"""

import asyncio
import re
from collections import OrderedDict

from grok_search.grok import SearchOutcome

# 只匹配引用样式 [[1]](url) 与 [1](url)，不误伤普通 markdown 链接 [text](url)
_INLINE_CITATION_RE = re.compile(r"\[\[?(\d{1,3})\]?\]\((https?://[^\s)]+)\)")

NO_SOURCES_NOTE = "\n\n（本次搜索未返回可引用来源）"


def extract_inline_citations(content: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for _, url in _INLINE_CITATION_RE.findall(content):
        if url not in seen:
            seen.add(url)
            result.append({"url": url, "title": ""})
    return result


def merge_sources(outcome: SearchOutcome) -> list[dict[str, str]]:
    merged: OrderedDict[str, dict[str, str]] = OrderedDict()
    for kind, items in (
        ("cited", outcome.annotations),
        ("cited", extract_inline_citations(outcome.content)),
        ("searched", outcome.search_sources),
    ):
        for item in items:
            url = item.get("url", "").strip()
            if not url:
                continue
            if url in merged:
                # 已有条目缺标题时，用后来者补全
                if not merged[url]["title"] and item.get("title"):
                    merged[url]["title"] = item["title"]
                continue
            merged[url] = {"url": url, "title": item.get("title", ""), "kind": kind}
    return list(merged.values())


class SessionCache:
    """按 session_id 缓存来源列表；LRU，容量满淘汰最久未访问的会话。"""

    def __init__(self, max_size: int = 256) -> None:
        self._max_size = max_size
        self._data: OrderedDict[str, list[dict[str, str]]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def set(self, session_id: str, sources: list[dict[str, str]]) -> None:
        async with self._lock:
            self._data[session_id] = sources
            self._data.move_to_end(session_id)
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)

    async def get(self, session_id: str) -> list[dict[str, str]] | None:
        async with self._lock:
            if session_id not in self._data:
                return None
            self._data.move_to_end(session_id)
            return self._data[session_id]
