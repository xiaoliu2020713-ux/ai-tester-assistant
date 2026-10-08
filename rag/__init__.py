"""RAG 包：多业务域知识库的构建与检索。

- `rag.embeddings`     向量模型封装（OpenAI 兼容接口 / sentence-transformers / 哈希兜底）
- `rag.vectorstore`    ChromaDB 持久化集合 + 向量/关键词混合检索
- `rag.indexer`        文档装载与切分、预置知识库构建、用户文档入域
- `rag.knowledge_base` 界面层使用的门面（KnowledgeBaseManager）
"""

from .knowledge_base import (  # noqa: F401
    ApiDocAnalysis,
    KnowledgeBaseManager,
    analyze_api_text,
    format_context,
)

__all__ = ["KnowledgeBaseManager", "format_context", "analyze_api_text", "ApiDocAnalysis"]
