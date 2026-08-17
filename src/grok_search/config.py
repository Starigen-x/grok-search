"""配置：只读环境变量，零落盘（防两处默认值漂移，见 architecture.md ADR-5）。"""

import os
from dataclasses import dataclass


class ConfigError(ValueError):
    """配置缺失或非法。message 必须是老板能看懂的人话。"""


@dataclass(frozen=True)
class Config:
    api_url: str
    api_key: str
    model: str
    timeout_s: float
    debug: bool
    empty_retries: int = 2


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"配置错误: 环境变量 {name} 未设置。"
            f"本服务需要三项配置: GROK_API_URL(服务地址)、GROK_API_KEY(密钥)、GROK_MODEL(模型名)。"
        )
    return value


def load_config() -> Config:
    """每次调用时读取，环境变量是唯一事实来源。"""
    return Config(
        api_url=_require("GROK_API_URL").rstrip("/"),
        api_key=_require("GROK_API_KEY"),
        model=_require("GROK_MODEL"),
        timeout_s=float(os.getenv("GROK_TIMEOUT_S", "120")),
        debug=os.getenv("GROK_DEBUG", "").lower() in ("1", "true", "yes"),
        empty_retries=int(os.getenv("GROK_EMPTY_RETRIES", "2")),
    )


def mask_key(key: str) -> str:
    """脱敏：任何对外输出中的密钥必须先过这里。"""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"
