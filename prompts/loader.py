"""提示词装载：把 prompts/ 下的 Markdown 组合成运行时提示词。

这样设计的目的是让「AI 测试员的身份与规则」以文件形式可编辑、可版本化，
而不是硬编码在 Python 字符串里。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import config as app_config

LOGGER = app_config.get_logger("prompts")

SYSTEM_PROMPT_FILE = app_config.PROMPTS_DIR / "system_prompt.md"
API_DOC_PROMPT_FILE = app_config.PROMPTS_DIR / "api_doc_prompt.md"
CHAT_PROMPT_FILE = app_config.PROMPTS_DIR / "chat_prompt.md"

_FALLBACK_SYSTEM = (
    "你是「AI 测试员」，一名资深测试架构师。中文回答，语气专业。"
    "你的职责是解析 API 文档并设计结构化测试用例，始终关注测试覆盖率、边界值与风险点。"
    "用例使用 Markdown 表格输出，列顺序为："
    "用例ID | 用例标题 | 接口 | 优先级 | 用例类型 | 前置条件 | 请求参数 | 测试步骤 | 预期结果 | 备注。"
    "预期结果必须写出明确的状态码、业务错误码或数据状态变化，禁止模糊描述。"
)


def _read(path: Path, fallback: str = "") -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("读取提示词文件失败 %s: %s", path, exc)
    return fallback


@lru_cache(maxsize=8)
def _cached(path_str: str, fallback: str) -> str:
    return _read(Path(path_str), fallback) or fallback


def system_prompt() -> str:
    """基础系统提示词（AI 测试员身份）。"""
    return _cached(str(SYSTEM_PROMPT_FILE), _FALLBACK_SYSTEM)


def api_doc_instructions() -> str:
    """用户提供 API 文档时的附加指令。"""
    return _read(API_DOC_PROMPT_FILE, "请先解析接口，再按 正常流程/异常流程/边界值/权限/并发 分组输出用例表格。")


def chat_instructions() -> str:
    """普通对话的附加指令。"""
    return _read(CHAT_PROMPT_FILE, "请结合知识库资料直接作答，避免空泛表述。")


def domain_prompt(domain_key: str, extra_notes: Optional[str] = None) -> str:
    """把当前业务域的上下文拼进系统提示词。"""
    domain = app_config.get_domain(domain_key)
    lines = [
        "# 当前业务域（知识库）",
        f"- 名称：{domain.name}",
        f"- 说明：{domain.description}",
        f"- 关键词：{', '.join(domain.keywords)}",
        "- 检索时优先使用该业务域的资料；若资料中带有规则编号（BR-xx / EC-xx / CS-xx），请在用例「备注」中引用。",
    ]
    if extra_notes:
        lines.append(f"- 补充说明：{extra_notes}")
    return "\n".join(lines)


def build_system_prompt(
    domain_key: str,
    *,
    has_api_doc: bool = False,
    context_block: Optional[str] = None,
    extra_notes: Optional[str] = None,
) -> str:
    """组装最终系统提示词：身份 → 业务域 → 场景指令 → 检索资料。"""
    sections = [system_prompt(), domain_prompt(domain_key, extra_notes)]
    sections.append(api_doc_instructions() if has_api_doc else chat_instructions())
    if context_block:
        sections.append(
            "# 知识库检索资料（请优先采用其中的规则、阈值与字段名）\n\n" + context_block.strip()
        )
    return "\n\n---\n\n".join(section for section in sections if section)


__all__ = [
    "system_prompt",
    "api_doc_instructions",
    "chat_instructions",
    "domain_prompt",
    "build_system_prompt",
]
