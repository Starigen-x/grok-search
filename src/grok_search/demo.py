"""真实调用演示：make demo-search q="问题"（经 uv run --env-file .env 注入密钥）。"""

import asyncio
import sys

from grok_search import grok
from grok_search.config import ConfigError, load_config


def main() -> None:
    query = " ".join(sys.argv[1:]) or "今天有什么重要的科技新闻？"
    try:
        cfg = load_config()
    except ConfigError as e:
        print(str(e))
        raise SystemExit(1) from None
    try:
        outcome = asyncio.run(grok.search(cfg, query))
    except grok.UpstreamError as e:
        print(str(e))
        raise SystemExit(1) from None
    print(outcome.content)
    print(
        f"\n--- [来源事件: annotations={len(outcome.annotations)}, "
        f"search_sources={len(outcome.search_sources)}] ---"
    )


if __name__ == "__main__":
    main()
