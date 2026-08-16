# grok-search

个人自用的联网搜索 MCP 服务：`web_search`（搜索）、`get_sources`（查来源）、`web_fetch`（读网页）三个工具，支持桌面客户端本地安装（stdio）与手机/网页端接入（HTTP）双形态。

## 客户端配置（共三项）

| 环境变量 | 说明 |
|---|---|
| `GROK_API_URL` | 上游搜索 API 地址（OpenAI 兼容格式） |
| `GROK_API_KEY` | 上游 API 密钥 |
| `GROK_MODEL` | 模型名，如 `grok-4.3-fast` |

安装命令（桌面客户端）：

```
uvx --from git+https://github.com/Starigen-x/grok-search grok-search
```

## 开发

```
make check   # 质量门：格式 + lint + 类型 + 测试
make serve   # 网络服务形态，http://127.0.0.1:8000
```
