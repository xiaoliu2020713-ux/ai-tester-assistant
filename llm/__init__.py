"""模型调用封装包。

- `client.LLMClient`      : 统一的聊天调用入口（本地 127.0.0.1:8080 / DeepSeek / 自定义）
- `client.test_connection` : 连接自检（/v1/models 探测 + 最小对话探针）
"""

from .client import (  # noqa: F401
    ChatResult,
    ConnectionReport,
    LLMClient,
    LLMError,
    build_llm,
    test_connection,
)

__all__ = [
    "LLMClient",
    "ChatResult",
    "ConnectionReport",
    "LLMError",
    "build_llm",
    "test_connection",
]
