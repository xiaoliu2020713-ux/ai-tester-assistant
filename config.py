"""配置管理模块。

职责：
1. 集中管理路径（项目根目录、知识库目录、向量库目录、上传缓存目录）；
2. 从 .env / 环境变量读取默认值（本地模型、DeepSeek、向量模型、生成参数）；
3. 定义业务域（知识域）注册表，新增业务域只需在此处与 knowledge/ 下同步添加；
4. 提供 DataClass 形式的 LLMConfig / RAGConfig，供 Streamlit 界面在运行时覆盖。

界面（app.py）中的「模型配置区」会把用户输入写进 st.session_state，
再通过 llm/ 与 rag/ 模块读取，因此配置是「实时生效」的。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent
KNOWLEDGE_DIR: Path = BASE_DIR / "knowledge"
STORAGE_DIR: Path = BASE_DIR / "storage"
UPLOAD_DIR: Path = STORAGE_DIR / "uploads"
LOG_DIR: Path = STORAGE_DIR / "logs"
PROMPTS_DIR: Path = BASE_DIR / "prompts"


def _load_dotenv() -> None:
    """可选地加载 .env（未安装 python-dotenv 时静默跳过）。"""
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:  # pragma: no cover - 可选依赖
        return
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


_load_dotenv()


def _env(key: str, default: str = "") -> str:
    value = os.getenv(key)
    return default if value is None or value.strip() == "" else value.strip()


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key, "").lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def ensure_dirs() -> None:
    """确保运行期需要的目录存在。"""
    for path in (STORAGE_DIR, UPLOAD_DIR, LOG_DIR, CHROMA_DIR, KNOWLEDGE_DIR):
        path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 生成参数
# ---------------------------------------------------------------------------
TEMPERATURE: float = _env_float("LLM_TEMPERATURE", 0.2)
MAX_TOKENS: int = _env_int("LLM_MAX_TOKENS", 4096)
REQUEST_TIMEOUT: int = _env_int("LLM_TIMEOUT", 180)
STREAM: bool = _env_bool("LLM_STREAM", True)
# 送入模型的历史对话轮数（1 轮 = 用户 + 助手）
HISTORY_MAX_TURNS: int = _env_int("HISTORY_MAX_TURNS", 8)
# 知识库检索条数
RETRIEVAL_TOP_K: int = _env_int("RETRIEVAL_TOP_K", 5)
# 单次检索上下文最大字符数（防止撑爆 4B 小模型上下文）
RETRIEVAL_MAX_CHARS: int = _env_int("RETRIEVAL_MAX_CHARS", 6000)
# 文档切分参数
CHUNK_SIZE: int = _env_int("CHUNK_SIZE", 800)
CHUNK_OVERLAP: int = _env_int("CHUNK_OVERLAP", 120)

CHROMA_DIR: Path = (BASE_DIR / _env("CHROMA_DIR", "storage/chroma")).resolve()


# ---------------------------------------------------------------------------
# 业务域（知识域）注册表
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Domain:
    """一个业务域（知识域）的元信息。"""

    key: str
    name: str
    description: str
    knowledge_subdir: str
    keywords: List[str] = field(default_factory=list)

    @property
    def knowledge_path(self) -> Path:
        return KNOWLEDGE_DIR / self.knowledge_subdir

    @property
    def collection_name(self) -> str:
        """Chroma 集合名（只允许字母数字与 -_）。"""
        return f"domain_{self.key}"


DOMAINS: Dict[str, Domain] = {
    "library": Domain(
        key="library",
        name="图书管理系统",
        description="图书、读者、借阅归还、预约续借、罚金与库存管理。",
        knowledge_subdir="library",
        keywords=["图书", "借阅", "归还", "reader", "book", "loan", "isbn", "续借", "预约"],
    ),
    "ecommerce": Domain(
        key="ecommerce",
        name="电商平台",
        description="商品、购物车、下单支付、优惠券、订单履约与售后。",
        knowledge_subdir="ecommerce",
        keywords=["电商", "商品", "订单", "支付", "购物车", "sku", "优惠券", "退款", "库存"],
    ),
    "course": Domain(
        key="course",
        name="学生选课系统",
        description="课程、选课退课、容量与时间冲突、学分上限、成绩与学籍。",
        knowledge_subdir="course",
        keywords=["选课", "课程", "学生", "学分", "退课", "排课", "成绩", "培养方案"],
    ),
    "common": Domain(
        key="common", 
        name="通用测试基线",
        description="跨业务域通用的测试设计方法、边界值清单与用例编写规范。",
        knowledge_subdir="common",
        keywords=["测试用例", "边界值", "等价类", "接口测试", "异常", "幂等", "鉴权"],
    ),
}

DOMAIN_ORDER: List[str] = ["library", "ecommerce", "course"]
DOMAIN_CHOICES: List[str] = DOMAIN_ORDER + ["common"]
DEFAULT_DOMAIN: str = _env("DEFAULT_DOMAIN", "library")
if DEFAULT_DOMAIN not in DOMAINS:
    DEFAULT_DOMAIN = "library"


def get_domain(key: str) -> Domain:
    """按 key 取业务域，未命中时回退到默认域。"""
    return DOMAINS.get(key) or DOMAINS[DEFAULT_DOMAIN]


def domain_label(key: str) -> str:
    return get_domain(key).name


def domain_options() -> Dict[str, str]:
    """返回 {显示名: key} 供 st.selectbox 使用（顺序与 DOMAIN_ORDER 一致）。"""
    options: Dict[str, str] = {}
    for key in DOMAIN_ORDER:
        options[DOMAINS[key].name] = key
    return options


# ---------------------------------------------------------------------------
# 大模型配置
# ---------------------------------------------------------------------------
PROVIDER_LOCAL = "local"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_CUSTOM = "custom"

PROVIDER_LABELS: Dict[str, str] = {
    PROVIDER_LOCAL: "本地模型 (OpenAI 兼容)",
    PROVIDER_DEEPSEEK: "DeepSeek 官方 API",
    PROVIDER_CUSTOM: "其他 OpenAI 兼容接口",
}

# 本地模型默认指向 127.0.0.1:8080（兼容 OpenAI 接口格式）
LOCAL_BASE_URL_DEFAULT: str = _env("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
LOCAL_MODEL_DEFAULT: str = _env("LOCAL_LLM_MODEL", "qwen3.5:4b")
LOCAL_API_KEY_DEFAULT: str = _env("LOCAL_LLM_API_KEY", "sk-local")

DEEPSEEK_BASE_URL_DEFAULT: str = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL_DEFAULT: str = _env("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_API_KEY_DEFAULT: str = _env("DEEPSEEK_API_KEY", "")


@dataclass
class LLMConfig:
    """一次模型调用的完整配置（界面可实时修改）。"""

    provider: str = _env("LLM_PROVIDER", PROVIDER_LOCAL)
    base_url: str = LOCAL_BASE_URL_DEFAULT
    model: str = LOCAL_MODEL_DEFAULT
    api_key: str = LOCAL_API_KEY_DEFAULT
    temperature: float = TEMPERATURE
    max_tokens: int = MAX_TOKENS
    timeout: int = REQUEST_TIMEOUT
    stream: bool = STREAM
    # 本地模型（如 Ollama 上的 qwen3 系列）可能默认开启思考链，可按需关闭
    enable_thinking: bool = False

    def normalized(self) -> "LLMConfig":
        """规范化：补齐 /v1 后缀、去掉首尾空格。"""
        base = (self.base_url or "").strip().rstrip("/")
        if base and not base.endswith("/v1") and "/v1/" not in base:
            # 允许用户只填 http://127.0.0.1:8080
            if base.count("/") == 2:
                base = f"{base}/v1"
        key = (self.api_key or "").strip()
        if not key:
            key = "sk-local"
        return replace(self, base_url=base, api_key=key, model=(self.model or "").strip())

    def is_local(self) -> bool:
        return self.provider == PROVIDER_LOCAL

    @property
    def display_name(self) -> str:
        return f"{PROVIDER_LABELS.get(self.provider, self.provider)} / {self.model or '未指定模型'}"


def preset_for_provider(provider: str) -> Dict[str, str]:
    """切换提供方时用于回填表单的默认值。"""
    if provider == PROVIDER_DEEPSEEK:
        return {
            "base_url": DEEPSEEK_BASE_URL_DEFAULT,
            "model": DEEPSEEK_MODEL_DEFAULT,
            "api_key": DEEPSEEK_API_KEY_DEFAULT,
        }
    if provider == PROVIDER_CUSTOM:
        return {"base_url": "", "model": "", "api_key": ""}
    return {
        "base_url": LOCAL_BASE_URL_DEFAULT,
        "model": LOCAL_MODEL_DEFAULT,
        "api_key": LOCAL_API_KEY_DEFAULT,
    }


# ---------------------------------------------------------------------------
# 向量模型配置
# ---------------------------------------------------------------------------
EMBEDDING_PROVIDER: str = _env("EMBEDDING_PROVIDER", "auto")
EMBEDDING_BASE_URL: str = _env("EMBEDDING_BASE_URL", LOCAL_BASE_URL_DEFAULT)
EMBEDDING_MODEL: str = _env("EMBEDDING_MODEL", "nomic-embed-text")
EMBEDDING_API_KEY: str = _env("EMBEDDING_API_KEY", "sk-local")
EMBEDDING_ST_MODEL: str = _env("EMBEDDING_ST_MODEL", "BAAI/bge-small-zh-v1.5")
EMBEDDING_DIM_FALLBACK: int = 512  # hash 向量维度


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
def setup_logging(level: int = logging.INFO) -> None:
    if logging.getLogger("ai_tester").handlers:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")
    except Exception:  # pragma: no cover
        handler = logging.NullHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    logger = logging.getLogger("ai_tester")
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"ai_tester.{name}")


__all__ = [
    "BASE_DIR",
    "KNOWLEDGE_DIR",
    "STORAGE_DIR",
    "UPLOAD_DIR",
    "LOG_DIR",
    "CHROMA_DIR",
    "DOMAINS",
    "DOMAIN_ORDER",
    "Domain",
    "LLMConfig",
    "get_domain",
    "domain_options",
    "domain_label",
    "ensure_dirs",
    "preset_for_provider",
    "get_logger",
    "TEMPERATURE",
    "MAX_TOKENS",
    "REQUEST_TIMEOUT",
    "STREAM",
    "HISTORY_MAX_TURNS",
    "RETRIEVAL_TOP_K",
    "RETRIEVAL_MAX_CHARS",
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_ST_MODEL",
]
