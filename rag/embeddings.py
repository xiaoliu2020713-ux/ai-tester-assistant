"""向量模型（Embedding）封装：三级回退，保证离线也能跑起来。

优先级（`EMBEDDING_PROVIDER=auto` 时依次探测）：
1. ``openai_api``        —— 调用 OpenAI 兼容的 ``POST {base}/embeddings``
   （本地 127.0.0.1:8080 若由 Ollama / vLLM / Xinference 提供 embedding 模型，则最省资源）
2. ``sentence_transformer`` —— 本地 HuggingFace 模型（需安装 sentence-transformers）
3. ``hash``             —— 纯 Python 确定性哈希向量（零依赖、零下载，检索质量较弱）

无论使用哪个提供方，向量维度都会记录在 Chroma 集合元数据中；
若维度与已有集合不一致，检索层会自动回退到关键词检索并给出提示。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

import config as app_config

LOGGER = app_config.get_logger("rag.embeddings")

KIND_OPENAI = "openai_api"
KIND_ST = "sentence_transformer"
KIND_HASH = "hash"
KIND_AUTO = "auto"

_HASH_DIM = app_config.EMBEDDING_DIM_FALLBACK
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


# ---------------------------------------------------------------------------
# 1) 哈希向量（零依赖兜底）
# ---------------------------------------------------------------------------
class HashEmbeddings:
    """确定性哈希向量：把文本映射为「词 + 字符二元组」的稀疏特征再哈希到固定维度。

    对中文按字符切分并加入 bigram，因此对「借阅」「库存」这类中文检索词
    具有一定的召回能力；适合完全离线、无任何模型可用的场景。
    """

    kind = KIND_HASH

    def __init__(self, dim: int = _HASH_DIM) -> None:
        self.dim = dim

    # -- 特征 -------------------------------------------------------------
    @staticmethod
    def _features(text: str) -> List[str]:
        tokens = _TOKEN_RE.findall(text.lower())
        feats: List[str] = list(tokens)
        cjk = [t for t in tokens if "\u4e00" <= t <= "\u9fff"]
        feats.extend(a + b for a, b in zip(cjk, cjk[1:]))
        feats.extend(tokens[i] + tokens[i + 1] for i in range(len(tokens) - 1))
        return feats

    def _vector(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for feat in self._features(text):
            digest = hashlib.md5(feat.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._vector(text)


# ---------------------------------------------------------------------------
# 2) OpenAI 兼容 embedding 接口
# ---------------------------------------------------------------------------
class OpenAICompatEmbeddings:
    """调用 OpenAI 兼容的 ``/v1/embeddings``。"""

    kind = KIND_OPENAI

    def __init__(self, base_url: str, model: str, api_key: str = "sk-local", timeout: int = 60) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.model = model
        self.api_key = api_key or "sk-local"
        self.timeout = timeout
        self.dim: Optional[int] = None

    def _endpoint(self) -> str:
        return f"{self.base_url}/embeddings" if self.base_url.endswith("/v1") else f"{self.base_url}/v1/embeddings"

    def _call(self, inputs: List[str]) -> List[List[float]]:
        import requests  # 延迟导入，避免无网络环境下导入失败

        resp = requests.post(
            self._endpoint(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "input": inputs},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"embeddings HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        data = payload.get("data") or []
        if not data:
            raise RuntimeError(f"embeddings 响应缺少 data 字段: {str(payload)[:200]}")
        vectors = [item.get("embedding") for item in data]
        if any(not v for v in vectors):
            raise RuntimeError("embeddings 返回了空向量")
        self.dim = len(vectors[0])
        return vectors  # type: ignore[return-value]

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        batch = 16
        for start in range(0, len(texts), batch):
            vectors.extend(self._call(list(texts[start : start + batch])))
        return vectors

    def embed_query(self, text: str) -> List[float]:
        return self._call([text])[0]

    def probe(self) -> bool:
        try:
            self._call(["连通性测试"])
            return True
        except Exception as exc:  # pragma: no cover
            LOGGER.info("OpenAI embedding 探针失败（%s），将回退。原因: %s", self._endpoint(), exc)
            return False


# ---------------------------------------------------------------------------
# 3) SentenceTransformer
# ---------------------------------------------------------------------------
def _prepare_hf_environment() -> str:
    """配置 HuggingFace 下载环境，返回实际使用的 endpoint。

    两个实测问题：
    1. 本机（国内网络）直连 huggingface.co 不通，镜像 `hf-mirror.com` 可用；
    2. 默认缓存目录 `C:\\Users\\<user>\\.cache\\huggingface` 可能无写权限。
    这里把缓存指到项目内 `storage/models`，并在直连失败时自动切镜像。
    """
    endpoint = os.getenv("HF_ENDPOINT", "").strip()
    if not endpoint:
        endpoint = "https://hf-mirror.com" if not _hf_reachable() else "https://huggingface.co"
        os.environ["HF_ENDPOINT"] = endpoint
    if not os.getenv("HF_HOME"):
        cache_dir = app_config.STORAGE_DIR / "models"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            os.environ["HF_HOME"] = str(cache_dir)
        except Exception:  # pragma: no cover
            pass
    return endpoint


def _hf_reachable(timeout: int = 4) -> bool:
    try:
        import urllib.request

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("https://huggingface.co", timeout=timeout):
            return True
    except Exception:
        return False


class SentenceTransformerEmbeddings:
    """本地 HuggingFace 句向量模型（首次调用时会下载模型权重）。"""

    kind = KIND_ST

    def __init__(self, model_name: str) -> None:
        endpoint = _prepare_hf_environment()
        from sentence_transformers import SentenceTransformer  # 延迟导入

        LOGGER.info("加载本地向量模型 %s（HF endpoint=%s，缓存=%s）…", model_name, endpoint, os.getenv("HF_HOME", ""))
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        getter = getattr(self._model, "get_embedding_dimension", None) or getattr(
            self._model, "get_sentence_embedding_dimension"
        )
        self.dim = int(getter())

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        vectors = self._model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False, batch_size=32
        )
        return [list(map(float, v)) for v in vectors]

    def embed_query(self, text: str) -> List[float]:
        vector = self._model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
        return list(map(float, vector))


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
EmbeddingProvider = object  # 形态约定：embed_documents / embed_query / kind / dim

_CACHE: Dict[str, object] = {}


@dataclass
class EmbeddingInfo:
    """当前生效的向量模型信息（用于界面展示）。"""

    kind: str
    model: str
    dim: Optional[int]
    note: str = ""

    @property
    def quality_hint(self) -> str:
        return {
            KIND_OPENAI: "语义向量（接口）",
            KIND_ST: "语义向量（本地模型）",
            KIND_HASH: "哈希向量（离线兜底，建议换语义向量以提升召回）",
        }.get(self.kind, self.kind)


def _wrap(provider: object, note: str = "") -> tuple:
    info = EmbeddingInfo(
        kind=getattr(provider, "kind", "unknown"),
        model=getattr(provider, "model", None) or getattr(provider, "model_name", "") or getattr(provider, "kind", ""),
        dim=getattr(provider, "dim", None),
        note=note,
    )
    return provider, info


def get_embeddings(
    provider: Optional[str] = None,
    *,
    force_rebuild: bool = False,
    on_log: Optional[Callable[[str], None]] = None,
) -> tuple:
    """获取（并缓存）向量模型实例。

    返回 ``(provider_instance, EmbeddingInfo)``。
    """
    provider = (provider or app_config.EMBEDDING_PROVIDER or KIND_AUTO).lower()
    cache_key = (
        f"{provider}|{app_config.EMBEDDING_BASE_URL}|{app_config.EMBEDDING_MODEL}|"
        f"{app_config.EMBEDDING_ST_MODEL}"
    )
    if not force_rebuild and cache_key in _CACHE:
        return _CACHE[cache_key]  # type: ignore[return-value]

    def log(message: str) -> None:
        LOGGER.info(message)
        if on_log:
            on_log(message)

    if provider == KIND_HASH:
        result = _wrap(HashEmbeddings())
        _CACHE[cache_key] = result
        return result

    if provider in (KIND_OPENAI, KIND_AUTO):
        candidate = OpenAICompatEmbeddings(
            app_config.EMBEDDING_BASE_URL, app_config.EMBEDDING_MODEL, app_config.EMBEDDING_API_KEY
        )
        if candidate.probe():
            log(f"向量模型：使用 OpenAI 兼容 embedding 接口（{candidate._endpoint()}，模型 {candidate.model}）")
            result = _wrap(candidate)
            _CACHE[cache_key] = result
            return result
        if provider == KIND_OPENAI:
            log("指定的 embedding 接口不可用，自动回退到下一级方案。")

    if provider in (KIND_ST, KIND_AUTO):
        try:
            candidate_st = SentenceTransformerEmbeddings(app_config.EMBEDDING_ST_MODEL)
            log(f"向量模型：使用本地 sentence-transformers（{app_config.EMBEDDING_ST_MODEL}，维度 {candidate_st.dim}）")
            result = _wrap(candidate_st)
            _CACHE[cache_key] = result
            return result
        except Exception as exc:
            log(f"sentence-transformers 不可用（{type(exc).__name__}: {exc}），继续回退。")

    log("向量模型：回退到内置哈希向量（离线可用，检索质量有限）。")
    result = _wrap(HashEmbeddings(), note="离线兜底方案")
    _CACHE[cache_key] = result
    return result


def embedding_health() -> Dict[str, object]:
    """不加载模型，仅探测 OpenAI 兼容 embedding 接口是否可用（供界面显示）。"""
    candidate = OpenAICompatEmbeddings(
        app_config.EMBEDDING_BASE_URL, app_config.EMBEDDING_MODEL, app_config.EMBEDDING_API_KEY, timeout=8
    )
    ok = candidate.probe()
    return {"endpoint": candidate._endpoint(), "model": candidate.model, "ok": ok, "dim": candidate.dim}


__all__ = [
    "HashEmbeddings",
    "OpenAICompatEmbeddings",
    "SentenceTransformerEmbeddings",
    "EmbeddingInfo",
    "get_embeddings",
    "embedding_health",
    "KIND_OPENAI",
    "KIND_ST",
    "KIND_HASH",
    "KIND_AUTO",
]
