"""网页读取：本地抓取 + trafilatura 正文提取，零外部密钥（architecture.md ADR-4）。"""

import asyncio

import httpx
import trafilatura

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_MAX_BYTES = 8 * 1024 * 1024  # 超大页面直接拒绝，防内存失控
_TIMEOUT_S = 60.0

_BINARY_TYPES = ("application/pdf", "image/", "video/", "audio/", "application/octet-stream")


class FetchError(RuntimeError):
    """str(e) 是直接给客户端看的人话。"""


async def fetch_markdown(url: str) -> str:
    """抓取网页并返回正文 Markdown。失败抛 FetchError（人话）。"""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise FetchError(f"参数错误: 网址必须以 http:// 或 https:// 开头(收到: {url[:80]!r})。")

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_S, follow_redirects=True, headers={"User-Agent": _UA}
        ) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        raise FetchError(f"网络错误: 抓取超时(超过 {_TIMEOUT_S:.0f} 秒): {url}") from None
    except httpx.HTTPError as exc:
        raise FetchError(f"网络错误: 无法访问 {url}({exc.__class__.__name__})。") from exc

    if response.status_code >= 400:
        hint = {
            401: "需要登录",
            403: "拒绝访问(可能有反爬)",
            404: "页面不存在",
            429: "被限流",
        }.get(response.status_code, "")
        raise FetchError(
            f"抓取错误: 网页返回 HTTP {response.status_code}"
            + (f"({hint})" if hint else "")
            + f": {url}"
        )

    content_type = response.headers.get("content-type", "").lower()
    if any(content_type.startswith(t) for t in _BINARY_TYPES):
        raise FetchError(f"抓取错误: 该链接是 {content_type.split(';')[0]} 而非网页，暂不支持。")

    if len(response.content) > _MAX_BYTES:
        raise FetchError(f"抓取错误: 页面超过 {_MAX_BYTES // 1024 // 1024}MB，放弃提取: {url}")

    html = response.text
    # trafilatura 是 CPU 密集的同步库，放线程池避免阻塞事件循环
    markdown = await asyncio.to_thread(
        trafilatura.extract,
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        with_metadata=True,
    )
    if not markdown or not markdown.strip():
        raise FetchError(f"抓取错误: 页面已下载但提取不出正文(可能是纯动态渲染页面或空页面): {url}")
    return markdown
