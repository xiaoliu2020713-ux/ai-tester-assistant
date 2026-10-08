"""业务域知识库门面：给界面层提供单一入口。

界面（app.py）只依赖本模块，不直接接触 Chroma / 切分细节：

    kb = KnowledgeBaseManager()
    kb.rebuild("library")                      # 构建/重建预置知识库
    kb.add_document("library", text, name)     # 用户粘贴的 API 文档入域
    chunks = kb.search("借阅接口的边界值", "library")   # 检索当前知识域
    context = format_context(chunks)           # 拼成提示词上下文
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import config as app_config
from rag import indexer
from rag.vectorstore import Chunk, DomainStatus, KnowledgeBase

LOGGER = app_config.get_logger("rag.kb")


# ---------------------------------------------------------------------------
# API 文档识别（用于界面提示与自动索引建议）
# ---------------------------------------------------------------------------
_ENDPOINT_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[A-Za-z0-9_\-./{}$:]+)", re.IGNORECASE
)
_PATH_ONLY_RE = re.compile(r"`(/(?:api|v\d)[A-Za-z0-9_\-./{}$:]*)`")
_JSON_HINT_RE = re.compile(r'"\s*(code|message|data|items|total)\s*"\s*:')
_FIELD_TABLE_RE = re.compile(r"\|\s*(参数|字段|名称|name|field)\s*\|", re.IGNORECASE)


@dataclass
class ApiDocAnalysis:
    """粘贴文本的初步分析结果。"""

    is_api_doc: bool
    endpoints: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.is_api_doc:
            return "看起来是普通文本/需求描述（仍可索引，但不会按接口文档重点分析）"
        return f"识别为接口文档，共发现 {len(self.endpoints)} 个接口：{', '.join(self.endpoints[:6])}"


def analyze_api_text(text: str) -> ApiDocAnalysis:
    """粗略判断文本是否为 API 文档，并抽取接口列表。"""
    text = text or ""
    reasons: List[str] = []
    endpoints: List[str] = []

    for method, path in _ENDPOINT_RE.findall(text):
        endpoints.append(f"{method.upper()} {path}")
    if not endpoints:
        for path in _PATH_ONLY_RE.findall(text):
            endpoints.append(path)
    if endpoints:
        reasons.append(f"出现 {len(endpoints)} 处「方法 + 路径」写法")
    if _JSON_HINT_RE.search(text):
        reasons.append("存在统一响应体字段（code/message/data）")
    if _FIELD_TABLE_RE.search(text):
        reasons.append("存在参数说明表格")
    if re.search(r"(请求体|请求参数|响应|返回|状态码|业务码|error\s*code)", text, re.IGNORECASE):
        reasons.append("出现接口文档常见章节词")

    is_api_doc = bool(endpoints) or len(reasons) >= 2
    unique: List[str] = []
    for item in endpoints:
        if item not in unique:
            unique.append(item)
    return ApiDocAnalysis(is_api_doc=is_api_doc, endpoints=unique, reasons=reasons)


# ---------------------------------------------------------------------------
# 上下文拼装
# ---------------------------------------------------------------------------
def format_context(chunks: Sequence[Chunk], *, max_chars: int = app_config.RETRIEVAL_MAX_CHARS) -> str:
    """把检索片段拼成带编号的提示词上下文块。"""
    if not chunks:
        return "（当前知识域未检索到相关资料，请基于通用测试设计方法作答，并说明这是基于通用经验。）"
    blocks: List[str] = []
    used = 0
    for index, chunk in enumerate(chunks, start=1):
        header = f"[资料{index}] 来源：{chunk.cite()}（相似度 {chunk.score:.2f}）"
        block = f"{header}\n{chunk.text.strip()}"
        if used + len(block) > max_chars and blocks:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n---\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 门面
# ---------------------------------------------------------------------------
class KnowledgeBaseManager:
    """多域 RAG 知识库管理器（默认缓存底层实例，避免重复连接 Chroma）。"""

    def __init__(self, provider: Optional[str] = None, *, on_log: Optional[Callable[[str], None]] = None) -> None:
        self.provider = provider
        self.on_log = on_log
        self._kb: Optional[KnowledgeBase] = None

    # -- 底层 -------------------------------------------------------------
    @property
    def kb(self) -> KnowledgeBase:
        if self._kb is None:
            self._kb = KnowledgeBase(self.provider, on_log=self.on_log)
        return self._kb

    @property
    def embedding_info(self):
        return self.kb.embedding_info

    # -- 构建 -------------------------------------------------------------
    def rebuild(self, domain_key: str) -> Dict[str, Any]:
        return indexer.build_domain(self.kb, domain_key, on_log=self.on_log)

    def rebuild_all(self) -> List[Dict[str, Any]]:
        return indexer.build_all(self.kb, on_log=self.on_log)

    def add_document(
        self,
        domain_key: str,
        text: str,
        *,
        source: str,
        media_type: str = "markdown",
    ) -> Dict[str, Any]:
        return indexer.index_user_document(
            self.kb, domain_key, text, source=source, media_type=media_type, on_log=self.on_log
        )

    def clear_uploads(self, domain_key: str) -> None:
        self.kb.clear_uploads(domain_key)

    def drop(self, domain_key: str) -> None:
        self.kb.drop_domain(domain_key, drop_sidecar=True)

    # -- 查询 -------------------------------------------------------------
    def statuses(self) -> List[DomainStatus]:
        return self.kb.all_status()

    def stale_domains(self) -> List[DomainStatus]:
        """返回与当前向量模型不一致、需要重建的业务域。"""
        kind = self.embedding_info.kind
        dim = self.embedding_info.dim
        return [s for s in self.statuses() if s.is_stale_for(kind, dim)]

    def status(self, domain_key: str) -> DomainStatus:
        return self.kb.status(domain_key)

    def search(
        self,
        query: str,
        domain_key: str,
        *,
        top_k: int = app_config.RETRIEVAL_TOP_K,
        include_common: bool = True,
    ) -> List[Chunk]:
        chunks, notes = self.kb.retrieve(query, domain_key, top_k=top_k, include_common=include_common)
        for note in notes:
            LOGGER.info("检索提示：%s", note)
        if self.on_log:
            for note in notes:
                self.on_log(f"⚠️ {note}")
        return chunks

    def search_with_notes(self, query: str, domain_key: str, **kwargs) -> tuple:
        return self.kb.retrieve(query, domain_key, **kwargs)

    # -- 统计 -------------------------------------------------------------
    def preset_stat(self) -> Dict[str, int]:
        return indexer.manifest_stat()

    def embedding_summary(self) -> str:
        """一句话说明当前向量模型（供界面/日志展示）。"""
        info = self.embedding_info
        return f"{info.quality_hint}（{info.kind} · {info.model or '-'} · {info.dim or '-'} 维）"

    def domain_catalog(self) -> List[Dict[str, Any]]:
        """业务域目录（含预置文档名），用于界面展示与新增域说明。"""
        manifest = indexer.load_manifest()
        catalog: List[Dict[str, Any]] = []
        for key in app_config.DOMAIN_CHOICES:
            domain = app_config.get_domain(key)
            documents: List[str] = []
            for item in indexer.manifest_domains(manifest):
                if str(item.get("key")) == key:
                    documents = [str(n) for n in (item.get("documents") or [])]
                    break
            if not documents and domain.knowledge_path.exists():
                documents = sorted(p.name for p in domain.knowledge_path.glob("*.md"))
            catalog.append(
                {
                    "key": domain.key,
                    "name": domain.name,
                    "description": domain.description,
                    "documents": documents,
                    "keywords": domain.keywords,
                }
            )
        return catalog


__all__ = ["KnowledgeBaseManager", "format_context", "analyze_api_text", "ApiDocAnalysis", "Chunk"]
