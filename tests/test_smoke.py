"""冒烟：确认测试链路、包装配与 MCP 服务骨架真在工作。"""

import pytest
from fastmcp import Client

from grok_search import __version__
from grok_search.config import ConfigError, load_config, mask_key
from grok_search.server import mcp


def test_version() -> None:
    assert __version__


async def test_server_boots_in_memory() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert isinstance(tools, list)


def test_missing_config_speaks_human(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GROK_API_URL", "GROK_API_KEY", "GROK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ConfigError, match="配置错误"):
        load_config()


def test_mask_key_never_leaks_full_key() -> None:
    assert mask_key("sk-1234567890abcdef") == "sk-1...cdef"
    assert mask_key("short") == "***"
