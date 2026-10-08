"""大模型调用封装。

设计要点
--------
1. **默认本地优先**：`LLMConfig` 默认 `provider="local"`，Base URL 为
   `http://127.0.0.1:8080/v1`（兼容 OpenAI 接口格式，适配 Ollama / vLLM / LM Studio /
   llama.cpp server 等），模型名默认 `qwen3.5:4b`。
2. **可实时切换**：界面把用户输入写入 `LLMConfig` 后调用 `LLMClient`，
   每次请求都新建底层模型实例（代价极低），因此配置「即时生效」。
3. **不静默失败**：连接异常统一抛 `LLMError`，并把诊断建议带进消息，
   便于在界面上直接给用户看（例如本地服务未启动）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence

import config as app_config

LOGGER = app_config.get_logger("llm")

try:  # langchain-openai 是核心依赖
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
except Exception as exc:  # pragma: no cover - 依赖缺失时给出明确指引
    raise ImportError(
        "缺少依赖 langchain-openai / langchain-core，请先执行：pip install -r requirements.txt"
    ) from exc


class LLMError(RuntimeError):
    """模型调用异常（携带用户可读的排查建议）。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ChatResult:
    """一次对话调用的结果。"""

    content: str
    model: str
    provider: str
    elapsed_ms: int
    usage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectionReport:
    """连接自检报告。"""

    ok: bool
    provider: str
    base_url: str
    model: str
    title: str
    detail: str
    models: List[str] = field(default_factory=list)
    latency_ms: int = 0
    probe_answer: str = ""


# ---------------------------------------------------------------------------
# 消息转换
# ---------------------------------------------------------------------------
_ROLE_MAP = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}


def to_langchain_messages(messages: Sequence[Dict[str, str]]) -> List[BaseMessage]:
    """把 [{'role': 'user', 'content': '...'}] 转成 LangChain 消息对象。"""
    converted: List[BaseMessage] = []
    for message in messages:
        role = (message.get("role") or "user").lower()
        content = message.get("content") or ""
        cls = _ROLE_MAP.get(role, HumanMessage)
        converted.append(cls(content=content))
    return converted


def _friendly_error(exc: Exception, cfg: app_config.LLMConfig) -> str:
    """把底层异常翻译成中文排查建议。"""
    text = str(exc)
    low = text.lower()
    hints: List[str] = []
    if cfg.is_local():
        if "connection" in low or "connect" in low or "10061" in low or "refused" in low:
            hints.append(
                f"无法连接本地模型服务 {cfg.base_url}。请确认推理服务已在 127.0.0.1:8080 启动"
                "（Ollama：`ollama serve` + `ollama pull <模型名>`；"
                "vLLM：`python -m vllm.entrypoints.openai.api_server --model <模型路径> --port 8080`；"
                "LM Studio：开启 Local Server）。"
            )
        if "502" in low or "bad gateway" in low:
            hints.append(
                f"{cfg.base_url} 返回 502，说明该端口上有代理但**后端模型服务没有起来**。"
                "请直接确认本地推理进程是否在运行（例如 `ollama list` 能否列出模型），"
                "或把 Base URL 改成推理服务真实监听的端口。"
            )
        if "404" in low or "not found" in low:
            hints.append(
                "服务可达但接口路径不存在，请检查 Base URL 是否带 `/v1`"
                f"（当前为 {cfg.base_url}）。"
            )
        if "model" in low and ("not" in low or "unknown" in low):
            hints.append(
                f"模型名 `{cfg.model}` 可能不存在，请在界面上点击「刷新模型列表」后选择实际可用的模型名。"
            )
    else:
        if "401" in low or "invalid api key" in low or "unauthorized" in low:
            hints.append("API Key 无效或未填写，请在模型配置区检查。")
        if "402" in low or "insufficient" in low or "balance" in low:
            hints.append("账户余额不足，请检查云服务账户状态。")
        if "429" in low or "rate limit" in low:
            hints.append("触发限流，请降低频率后重试。")
        if "timeout" in low or "timed out" in low:
            hints.append("请求超时，可在模型配置区调大超时时间，或改用本地模型。")
    hint_text = ("\n\n排查建议：\n- " + "\n- ".join(hints)) if hints else ""
    return f"模型调用失败：{text}{hint_text}"


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class LLMClient:
    """OpenAI 兼容聊天客户端（本地优先，可切换 DeepSeek / 自定义）。"""

    def __init__(self, cfg: app_config.LLMConfig) -> None:
        self.cfg = cfg.normalized()
        self._llm: Optional[ChatOpenAI] = None

    # -- 底层模型 ---------------------------------------------------------
    def _build(self, *, stream: bool, max_tokens: Optional[int] = None) -> ChatOpenAI:
        kwargs: Dict[str, Any] = {
            "model": self.cfg.model,
            "api_key": self.cfg.api_key,
            "base_url": self.cfg.base_url,
            "temperature": self.cfg.temperature,
            "timeout": self.cfg.timeout,
            "max_retries": 1,
            "streaming": stream,
        }
        if max_tokens or self.cfg.max_tokens:
            kwargs["max_tokens"] = max_tokens or self.cfg.max_tokens
        if self.cfg.is_local() and not self.cfg.enable_thinking:
            # 关闭 qwen3 系列思考链（vLLM/SGLang 支持，Ollama 会忽略未知字段）
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        return ChatOpenAI(**kwargs)

    @property
    def llm(self) -> ChatOpenAI:
        if self._llm is None:
            self._llm = self._build(stream=self.cfg.stream)
        return self._llm

    # -- 调用 -------------------------------------------------------------
    def invoke(self, messages: Sequence[Dict[str, str]], **overrides: Any) -> ChatResult:
        """同步调用，返回完整文本。"""
        cfg = self.cfg
        temperature = overrides.pop("temperature", cfg.temperature)
        max_tokens = overrides.pop("max_tokens", cfg.max_tokens)
        model = self._build(stream=False, max_tokens=max_tokens)
        model.temperature = temperature  # type: ignore[misc]
        started = time.time()
        try:
            response = model.invoke(to_langchain_messages(messages))
        except Exception as exc:  # pragma: no cover - 网络/服务异常
            LOGGER.warning("LLM invoke failed: %s | base_url=%s model=%s", exc, cfg.base_url, cfg.model)
            raise LLMError(_friendly_error(exc, cfg)) from exc
        content = _message_text(response)
        usage = _message_usage(response)
        elapsed = int((time.time() - started) * 1000)
        LOGGER.info("LLM invoke ok | model=%s | %dms | %d chars", cfg.model, elapsed, len(content))
        return ChatResult(content, cfg.model, cfg.provider, elapsed, usage)

    def stream(self, messages: Sequence[Dict[str, str]], **overrides: Any) -> Iterator[str]:
        """流式调用，逐块产出文本增量。"""
        cfg = self.cfg
        temperature = overrides.pop("temperature", cfg.temperature)
        max_tokens = overrides.pop("max_tokens", cfg.max_tokens)
        model = self._build(stream=True, max_tokens=max_tokens)
        model.temperature = temperature  # type: ignore[misc]
        try:
            for chunk in model.stream(to_langchain_messages(messages)):
                text = _message_text(chunk, allow_empty=True)
                if text:
                    yield text
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("LLM stream failed: %s | base_url=%s model=%s", exc, cfg.base_url, cfg.model)
            raise LLMError(_friendly_error(exc, cfg)) from exc

    # -- 元信息 -----------------------------------------------------------
    def _candidate_model_urls(self) -> List[str]:
        """不同 OpenAI 兼容实现的模型列表路径不一致，这里依次尝试。"""
        base = self.cfg.base_url.rstrip("/")
        root = base[:-3].rstrip("/") if base.endswith("/v1") else base
        return [
            f"{base}/models",          # OpenAI / vLLM / LM Studio / llama.cpp server（base 含 /v1）
            f"{root}/v1/models",       # base 未带 /v1
            f"{root}/api/tags",        # Ollama 原生接口
            f"{root}/models",          # 少数实现挂在根路径
        ]

    def list_models(self) -> List[str]:
        """调用 OpenAI 兼容的 `GET /v1/models` 获取可用模型名（自动尝试多个路径）。

        注意：本地探测必须绕过系统代理。实测本机系统代理（Clash 类）会把
        `127.0.0.1:<任意端口>` 都转发到同一个后端，导致「端口探测出现幻觉」、
        以及在服务未启动时返回 502 而不是连接被拒绝。
        """
        try:
            import requests
        except Exception:  # pragma: no cover
            return []
        seen: List[str] = []
        no_proxy = {"http": None, "https": None}
        for url in self._candidate_model_urls():
            try:
                resp = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                    timeout=min(self.cfg.timeout, 15),
                    proxies=no_proxy,
                )
                if resp.status_code >= 400:
                    LOGGER.info("list_models HTTP %s @ %s", resp.status_code, url)
                    continue
                payload = resp.json()
            except Exception as exc:  # pragma: no cover
                LOGGER.info("list_models failed @ %s: %s", url, exc)
                continue
            names: List[str] = []
            items = payload.get("data") or payload.get("models") or []
            for item in items:
                if isinstance(item, dict):
                    name = item.get("id") or item.get("name") or item.get("model")
                    if name:
                        names.append(str(name))
                elif isinstance(item, str):
                    names.append(item)
            if names:
                seen = sorted(set(names))
                break
        return seen


def _message_text(message: Any, allow_empty: bool = False) -> str:
    """兼容 str / list[dict] 两种 content 结构（部分模型返回多段内容）。"""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        text = "".join(parts)
        return text if text or allow_empty else str(content)
    return "" if content is None else str(content)


def _message_usage(message: Any) -> Dict[str, Any]:
    meta = getattr(message, "usage_metadata", None)
    if isinstance(meta, dict):
        return dict(meta)
    resp = getattr(message, "response_metadata", None)
    if isinstance(resp, dict) and isinstance(resp.get("token_usage"), dict):
        return dict(resp["token_usage"])
    return {}


def build_llm(cfg: app_config.LLMConfig) -> ChatOpenAI:
    """便捷函数：直接拿到 LangChain ChatOpenAI 实例（供高级用法/链式调用）。"""
    return LLMClient(cfg).llm


# ---------------------------------------------------------------------------
# 连接自检
# ---------------------------------------------------------------------------
def test_connection(cfg: app_config.LLMConfig, *, probe: bool = True) -> ConnectionReport:
    """检测模型服务是否可用。

    步骤：
      1. `GET /v1/models` 列出可用模型（部分服务不支持，不影响结论）；
      2. 发一条最小对话「ping」探针（`probe=True`），确认真的能推理。

    返回 `ConnectionReport`，界面据此展示成功/失败与排查建议。
    """
    normalized = cfg.normalized()
    client = LLMClient(normalized)
    started = time.time()
    models = client.list_models()
    model_known = (not models) or (normalized.model in models)

    if not probe:
        elapsed = int((time.time() - started) * 1000)
        if models and not model_known:
            return ConnectionReport(
                ok=False,
                provider=normalized.provider,
                base_url=normalized.base_url,
                model=normalized.model,
                title="服务可达，但模型名不在列表中",
                detail=(
                    f"`{normalized.base_url}/models` 返回 {len(models)} 个模型，"
                    f"其中没有 `{normalized.model}`。可用示例：{', '.join(models[:8])}"
                ),
                models=models,
                latency_ms=elapsed,
            )
        return ConnectionReport(
            ok=bool(models),
            provider=normalized.provider,
            base_url=normalized.base_url,
            model=normalized.model,
            title="服务可达" if models else "无法列出模型",
            detail=(
                f"检测到 {len(models)} 个模型：{', '.join(models[:8])}"
                if models
                else f"无法访问 `{normalized.base_url}/models`，请确认服务已启动且地址正确。"
            ),
            models=models,
            latency_ms=elapsed,
        )

    try:
        result = client.invoke(
            [
                {"role": "system", "content": "你是连通性探针，只输出要求的内容。"},
                {"role": "user", "content": "请只回复两个字：连接"},
            ],
            max_tokens=32,
            temperature=0.0,
        )
    except LLMError as exc:
        return ConnectionReport(
            ok=False,
            provider=normalized.provider,
            base_url=normalized.base_url,
            model=normalized.model,
            title="连接失败",
            detail=str(exc),
            models=models,
            latency_ms=int((time.time() - started) * 1000),
        )

    elapsed = int((time.time() - started) * 1000)
    detail = f"探针耗时 {result.elapsed_ms} ms，模型返回：{result.content.strip()[:80] or '(空)'}"
    if result.usage:
        detail += f"\nToken 用量：{result.usage}"
    if models and not model_known:
        detail += f"\n注意：`{normalized.model}` 不在模型列表中，可用模型：{', '.join(models[:8])}"
    return ConnectionReport(
        ok=True,
        provider=normalized.provider,
        base_url=normalized.base_url,
        model=normalized.model,
        title="连接成功，模型可正常推理",
        detail=detail,
        models=models,
        latency_ms=elapsed,
        probe_answer=result.content.strip(),
    )


__all__ = [
    "LLMClient",
    "LLMError",
    "ChatResult",
    "ConnectionReport",
    "build_llm",
    "test_connection",
    "to_langchain_messages",
]
