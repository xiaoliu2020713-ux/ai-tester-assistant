"""ChromaDB 向量库封装：按业务域隔离的持久化集合 + 混合检索。

设计要点
--------
1. **一域一集合**：`domain_library` / `domain_ecommerce` / `domain_course` / `domain_common`，
   检索时优先查询当前选中的知识域（可选追加通用基线域）。
2. **显式向量**：所有向量由 `rag/embeddings.py` 计算后写入，Chroma 不下载任何默认模型。
3. **元数据记录维度**：集合元数据写入 `dim` / `embedding_kind`，维度不一致时自动回退关键词检索，
   避免「换向量模型后检索报错」。
4. **混合检索**：向量检索 + 关键词（中文 bigram）检索，用 RRF 融合，提升召回稳定性。
5. **持久化**：向量与元数据落盘在 `storage/chroma`；同时把切分文本写入
   `storage/index/<domain>.json` 侧车文件，供关键词检索与离线重建使用。
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config as app_config
from rag.embeddings import EmbeddingInfo, get_embeddings

LOGGER = app_config.get_logger("rag.vectorstore")

_INDEX_DIR = app_config.STORAGE_DIR / "index"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# LangChain Embeddings 适配器（Chroma 需要该接口形态）
# ---------------------------------------------------------------------------
def _to_langchain_embeddings(provider: Any):
    """把内部 provider 包装成 LangChain ``Embeddings``，供 ChromaVectorStore 使用。"""
    from langchain_core.embeddings import Embeddings

    class _Adapter(Embeddings):
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def embed_documents(self, texts: List[str]) -> List[List[float]]:  # type: ignore[override]
            return self.inner.embed_documents(texts)

        def embed_query(self, text: str) -> List[float]:  # type: ignore[override]
            return self.inner.embed_query(text)

    return _Adapter(provider)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Chunk:
    """一条知识片段（检索的最小单位）。"""

    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0

    def cite(self) -> str:
        """生成引用标签，例如「图书管理系统 / 01_business_rules.md #3」。"""
        domain = self.metadata.get("domain_name") or self.metadata.get("domain", "")
        source = self.metadata.get("source", "未知来源")
        chunk_no = self.metadata.get("chunk_index")
        suffix = f" #{int(chunk_no) + 1}" if isinstance(chunk_no, int) else ""
        return f"{domain} / {source}{suffix}"


@dataclass
class DomainStatus:
    """某个业务域知识库的状态。"""

    key: str
    name: str
    count: int
    collection: str
    sources: List[str] = field(default_factory=list)
    embedding_kind: str = ""
    dim: Optional[int] = None
    built_at: str = ""

    @property
    def ready(self) -> bool:
        return self.count > 0

    def is_stale_for(self, embedding_kind: str, dim: Optional[int]) -> bool:
        """判断索引是否与当前向量模型不一致（换了向量模型就必须重建）。

        注意：不同向量模型可能维度恰好相同（例如 bge-small-zh 与内置哈希都是 512 维），
        因此**必须比对 kind，而不能只比维度**，否则会拿语义向量去查哈希索引，召回全是噪声。
        """
        if not self.count:
            return False
        if not self.embedding_kind or self.embedding_kind == "legacy-unknown":
            # 早期索引未记录向量模型 → 无法保证一致，必须重建
            return True
        if embedding_kind and self.embedding_kind != embedding_kind:
            return True
        if self.dim and dim and int(self.dim) != int(dim):
            return True
        return False


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
def _chroma_client():
    """创建 Chroma 持久化客户端。

    说明：
    1. 使用 `chromadb.api.client.Client`（直接构造）而非 `chromadb.PersistentClient`，
       以减少对 FastAPI/OTel 埋点导入链的依赖；
    2. 关闭遥测：部分 chromadb 版本与新版 posthog 的 `capture()` 签名不兼容，会往
       stderr 打印 `Failed to send telemetry event ...` 噪音日志，这里一并静音。
    """
    from chromadb.api.client import Client as _Client
    from chromadb.config import Settings

    for noisy in ("chromadb.telemetry", "chromadb.telemetry.product", "chromadb.telemetry.product.posthog"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)

    app_config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return _Client(
        settings=Settings(
            is_persistent=True,
            persist_directory=str(app_config.CHROMA_DIR),
            anonymized_telemetry=False,
            allow_reset=True,
        )
    )


def _sidecar_path(domain_key: str) -> Path:
    _INDEX_DIR.mkdir(parents=True, exist_ok=True)
    return _INDEX_DIR / f"{domain_key}.json"


def _load_sidecar(domain_key: str) -> List[Dict[str, Any]]:
    path = _sidecar_path(domain_key)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("chunks") or [])
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("读取侧车索引失败 %s: %s", path, exc)
        return []


def _load_sidecar_info(domain_key: str) -> Dict[str, Any]:
    """读取侧车文件里记录的向量模型信息（embedding_kind / dim）。"""
    path = _sidecar_path(domain_key)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        info = payload.get("info") or {}
        return info if isinstance(info, dict) else {}
    except Exception:  # pragma: no cover
        return {}


def _save_sidecar(domain_key: str, chunks: Sequence[Dict[str, Any]], info: Dict[str, Any]) -> None:
    payload = {"domain": domain_key, "info": info, "chunks": list(chunks)}
    _sidecar_path(domain_key).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------
class KnowledgeBase:
    """基于 ChromaDB 的多域知识库。"""

    def __init__(self, provider: Optional[str] = None, *, on_log=None) -> None:
        self.provider, self.embedding_info = get_embeddings(provider, on_log=on_log)
        self.embeddings = _to_langchain_embeddings(self.provider)
        self._client = None
        self.last_error: str = ""

    # -- 基础 -------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            self._client = _chroma_client()
        return self._client

    def collection_name(self, domain_key: str) -> str:
        return app_config.get_domain(domain_key).collection_name

    def _get_collection(self, domain_key: str, *, create: bool = False):
        name = self.collection_name(domain_key)
        try:
            if create:
                return self.client.get_or_create_collection(
                    name=name,
                    metadata={"embedding_kind": self.embedding_info.kind, "dim": int(self.embedding_info.dim or 0)},
                )
            return self.client.get_collection(name=name)
        except Exception as exc:
            self.last_error = f"{name}: {type(exc).__name__}: {exc}"
            LOGGER.warning("获取集合失败 %s: %s", name, exc)
            return None

    def _collection_meta(self, domain_key: str) -> Dict[str, Any]:
        name = self.collection_name(domain_key)
        try:
            col = self.client.get_collection(name=name)
            return dict(col.metadata or {})
        except Exception:
            return {}

    # -- 写入 -------------------------------------------------------------
    def index_chunks(
        self,
        domain_key: str,
        chunks: Sequence[Dict[str, Any]],
        *,
        append: bool = False,
        source_type: str = "preset",
    ) -> int:
        """把切分好的片段写入指定业务域集合。

        `chunks` 元素格式：``{"text": str, "metadata": {...}}``。
        返回写入条数。`append=False` 时会重建集合（预置知识库重建）。
        """
        if not chunks:
            return 0
        domain = app_config.get_domain(domain_key)
        if not append:
            self.drop_domain(domain_key)

        collection = self._get_collection(domain_key, create=True)
        if collection is None:  # pragma: no cover
            raise RuntimeError(
                f"无法创建/获取 Chroma 集合 {self.collection_name(domain_key)}：{getattr(self, 'last_error', '未知原因')}"
            )

        texts = [c["text"] for c in chunks]
        metadatas: List[Dict[str, Any]] = []
        ids: List[str] = []
        for idx, chunk in enumerate(chunks):
            meta = dict(chunk.get("metadata") or {})
            meta.setdefault("domain", domain.key)
            meta.setdefault("domain_name", domain.name)
            meta.setdefault("source_type", source_type)
            meta["chunk_index"] = idx
            # Chroma 元数据只支持标量
            cleaned: Dict[str, Any] = {}
            for key, value in meta.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    cleaned[key] = "" if value is None else value
            metadatas.append(cleaned)
            seed = f"{domain.key}|{cleaned.get('source_type','')}|{cleaned.get('source','')}|{cleaned.get('chunk_index')}|{texts[idx][:200]}"
            ids.append(f"{domain.key}-{abs(hash(seed)) % (10**12)}-{idx}")

        vectors = self.provider.embed_documents(texts)
        collection.add(ids=ids, documents=texts, metadatas=metadatas, embeddings=vectors)
        if self.embedding_info.dim is None and vectors:
            self.embedding_info.dim = len(vectors[0])
        try:
            collection.modify(
                metadata={
                    "embedding_kind": self.embedding_info.kind,
                    "dim": int(self.embedding_info.dim or 0),
                    "built_at": _now_iso(),
                }
            )
        except Exception:
            pass

        # 侧车文件（关键词检索 / 离线重建用）
        existing = _load_sidecar(domain_key) if append else []
        merged = existing + [
            {"text": t, "metadata": m} for t, m in zip(texts, metadatas)
        ]
        _save_sidecar(
            domain_key,
            merged,
            {
                "embedding_kind": self.embedding_info.kind,
                "dim": self.embedding_info.dim,
                "count": len(merged),
            },
        )
        LOGGER.info("索引写入 %s：%d 条（append=%s）", domain.name, len(texts), append)
        return len(texts)

    def drop_domain(self, domain_key: str, *, drop_sidecar: bool = False) -> None:
        name = self.collection_name(domain_key)
        try:
            self.client.delete_collection(name=name)
            LOGGER.info("已删除集合 %s", name)
        except Exception:
            pass
        if drop_sidecar:
            path = _sidecar_path(domain_key)
            if path.exists():
                path.unlink()

    def clear_uploads(self, domain_key: str) -> int:
        """清除某业务域下由用户上传/粘贴产生的片段（保留预置知识库）。"""
        collection = self._get_collection(domain_key)
        removed = 0
        if collection is not None:
            try:
                collection.delete(where={"source_type": "upload"})
                removed = 1
            except Exception as exc:
                LOGGER.info("删除上传片段失败（可能不存在）: %s", exc)
        chunks = [c for c in _load_sidecar(domain_key) if c.get("metadata", {}).get("source_type") != "upload"]
        _save_sidecar(
            domain_key,
            chunks,
            {"embedding_kind": self.embedding_info.kind, "dim": self.embedding_info.dim, "count": len(chunks)},
        )
        return removed

    # -- 状态 -------------------------------------------------------------
    def status(self, domain_key: str) -> DomainStatus:
        domain = app_config.get_domain(domain_key)
        count = 0
        collection = self._get_collection(domain_key)
        if collection is not None:
            try:
                count = int(collection.count())
            except Exception:
                count = 0
        chunks = _load_sidecar(domain_key)
        if count == 0 and chunks:
            count = len(chunks)
        sources = sorted({str(c.get("metadata", {}).get("source", "")) for c in chunks if c.get("metadata")})
        meta = self._collection_meta(domain_key)
        # 向量模型标识的读取优先级：侧车文件 > 集合元数据 > legacy
        # 为什么侧车优先：ChromaDB 不允许修改集合的自定义元数据（collection.modify 只能改
        # hnsw:space），所以在既有集合上重建索引时，集合元数据里留的是**旧值**，
        # 只有侧车文件是每次写入时刷新的。
        sidecar_info = _load_sidecar_info(domain_key)
        kind = str(sidecar_info.get("embedding_kind") or meta.get("embedding_kind") or "")
        dim = sidecar_info.get("dim") or meta.get("dim") or None
        if count and not kind:
            kind = "legacy-unknown"
        return DomainStatus(
            key=domain.key,
            name=domain.name,
            count=count,
            collection=domain.collection_name,
            sources=[s for s in sources if s],
            embedding_kind=kind,
            dim=dim,
            built_at=str(meta.get("built_at") or ""),
        )

    def all_status(self) -> List[DomainStatus]:
        return [self.status(key) for key in app_config.DOMAIN_CHOICES]

    # -- 检索 -------------------------------------------------------------
    def retrieve(
        self,
        query: str,
        domain_key: str,
        *,
        top_k: int = app_config.RETRIEVAL_TOP_K,
        include_common: bool = True,
        max_chars: int = app_config.RETRIEVAL_MAX_CHARS,
        prefer_upload: bool = True,
    ) -> Tuple[List[Chunk], List[str]]:
        """检索知识片段。

        返回 ``(chunks, notes)``；`notes` 记录降级/异常信息，界面可直接展示。
        """
        query = (query or "").strip()
        notes: List[str] = []
        if not query:
            return [], notes

        keys = [domain_key]
        if include_common and domain_key != "common":
            keys.append("common")

        results: List[Chunk] = []
        for key in keys:
            domain_weight = 1.0 if key == domain_key else 0.8
            for chunk in self._search_one(key, query, top_k=top_k, notes=notes):
                # 上传/粘贴的来源适当加权（用户最新提供的文档通常最相关）
                kind_weight = 1.15 if chunk.metadata.get("source_type") == "upload" else 1.0
                chunk.score = chunk.score * domain_weight * kind_weight
                results.append(chunk)

        results.sort(key=lambda c: c.score, reverse=True)
        # 归一化到 0~1，便于界面展示「相似度」
        if results:
            top_score = results[0].score or 1.0
            for chunk in results:
                chunk.score = min(chunk.score / top_score, 1.0)

        trimmed: List[Chunk] = []
        budget = max_chars
        for chunk in results[: max(top_k * 2, top_k)]:
            if budget - len(chunk.text) < 0 and trimmed:
                break
            trimmed.append(chunk)
            budget -= len(chunk.text)
        return trimmed[: max(top_k, 1) * 2], notes

    def _search_one(
        self, domain_key: str, query: str, *, top_k: int, notes: List[str]
    ) -> List[Chunk]:
        """单域混合检索：向量检索 + 关键词检索，用 RRF（倒数排名融合）合并。"""
        domain = app_config.get_domain(domain_key)
        collection = self._get_collection(domain_key)
        vector_chunks: List[Chunk] = []
        keyword_chunks: List[Chunk] = []

        if collection is not None and collection.count() > 0:
            meta = self._collection_meta(domain_key)
            stored_dim = meta.get("dim") or None
            if stored_dim and self.embedding_info.dim and int(stored_dim) != int(self.embedding_info.dim):
                notes.append(
                    f"「{domain.name}」集合是用 {stored_dim} 维向量建立的，当前向量模型维度为 "
                    f"{self.embedding_info.dim}，已自动改用关键词检索；建议点击「重建知识库」。"
                )
            else:
                try:
                    query_vector = self.provider.embed_query(query)
                    payload = collection.query(
                        query_embeddings=[query_vector],
                        n_results=max(top_k * 3, 8),
                        include=["documents", "metadatas", "distances"],
                    )
                    docs = (payload.get("documents") or [[]])[0]
                    metas = (payload.get("metadatas") or [[]])[0]
                    dists = (payload.get("distances") or [[]])[0]
                    for doc, item_meta, dist in zip(docs, metas, dists):
                        # Chroma 默认空间为 L2；查询与文档向量均已归一化，
                        # 因此 cos = 1 - d²/2，映射到 0~1 便于展示与排序。
                        distance = float(dist)
                        similarity = max(0.0, min(1.0, 1.0 - (distance ** 2) / 2.0))
                        vector_chunks.append(Chunk(text=doc, metadata=dict(item_meta), score=similarity))
                except Exception as exc:
                    notes.append(f"「{domain.name}」向量检索失败（{type(exc).__name__}），已改用关键词检索。")
                    LOGGER.warning("向量检索失败 %s: %s", domain.name, exc)

        # 关键词检索（基于侧车索引，中文 bigram）
        keyword_chunks = _keyword_search(_load_sidecar(domain_key), query, top_k=max(top_k * 3, 8))

        # 索引与当前向量模型不一致时给出明确提示（否则召回会是无意义的噪声）
        meta = self._collection_meta(domain_key)
        stored_kind = str(meta.get("embedding_kind") or "")
        if stored_kind and stored_kind != self.embedding_info.kind:
            notes.append(
                f"「{domain.name}」索引是用「{stored_kind}」向量建立的，当前向量模型是"
                f"「{self.embedding_info.kind}」，**必须重建知识库**后才能获得正确的语义检索结果。"
            )

        # RRF 融合：score = Σ 1 / (k + rank)，k 取 60
        rrf_k = 60
        fused: Dict[str, Chunk] = {}
        for rank, chunk in enumerate(vector_chunks):
            key = _chunk_key(chunk)
            entry = fused.setdefault(key, Chunk(text=chunk.text, metadata=dict(chunk.metadata), score=0.0))
            entry.score += 1.0 / (rrf_k + rank + 1)
            entry.metadata["vector_similarity"] = round(chunk.score, 4)
        for rank, chunk in enumerate(keyword_chunks):
            key = _chunk_key(chunk)
            entry = fused.setdefault(key, Chunk(text=chunk.text, metadata=dict(chunk.metadata), score=0.0))
            entry.score += 1.0 / (rrf_k + rank + 1)
            entry.metadata["keyword_score"] = round(chunk.score, 4)

        if not fused and collection is None:
            if self.status(domain_key).count == 0:
                notes.append(f"「{domain.name}」知识库为空，请先在侧边栏点击「构建/重建知识库」。")
        return sorted(fused.values(), key=lambda c: c.score, reverse=True)[: max(top_k * 2, top_k)]


def _chunk_key(chunk: Chunk) -> str:
    """片段唯一键（用于融合去重）。"""
    meta = chunk.metadata or {}
    key = f"{meta.get('source','')}#{meta.get('chunk_index','')}"
    if key == "#":
        return chunk.text[:64]
    return key


# ---------------------------------------------------------------------------
# 关键词检索（中文 bigram + 英文词）
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> List[str]:
    tokens = _TOKEN_RE.findall((text or "").lower())
    cjk = [t for t in tokens if "\u4e00" <= t <= "\u9fff"]
    bigrams = [a + b for a, b in zip(cjk, cjk[1:])]
    return tokens + bigrams


def _keyword_search(chunks: Sequence[Dict[str, Any]], query: str, *, top_k: int = 8) -> List[Chunk]:
    if not chunks:
        return []
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    query_set = set(query_tokens)
    scored: List[Chunk] = []
    for item in chunks:
        text = item.get("text", "")
        meta = dict(item.get("metadata") or {})
        tokens = _tokenize(text)
        if not tokens:
            continue
        token_set = set(tokens)
        overlap = len(query_set & token_set)
        if overlap == 0:
            continue
        # 简化 BM25 风格打分：命中词数 / sqrt(文档词数)，裁剪到 0~1
        raw = overlap / math.sqrt(len(tokens)) * 6.0
        scored.append(Chunk(text=text, metadata=meta, score=min(raw, 1.0)))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:top_k]


__all__ = ["KnowledgeBase", "Chunk", "DomainStatus"]
