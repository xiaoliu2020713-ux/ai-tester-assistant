"""知识库索引构建：读取预置文档 / 用户上传文档 → 切分 → 写入向量库。

切分策略
--------
预置知识库是 Markdown，按「标题层级」切分能保留语义边界：
每个片段都会带上 `heading`（标题路径，如 `2. 业务规则清单 > 2.1 借阅规则`），
检索后连同 `cite()` 一起送入模型，便于模型引用出处。

用户粘贴/上传的 API 文档同样受益：JSON 走结构化切分，Markdown 走标题切分，
纯文本走递归字符切分。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import config as app_config

LOGGER = app_config.get_logger("rag.indexer")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*```")


# ---------------------------------------------------------------------------
# 文档装载
# ---------------------------------------------------------------------------
@dataclass
class LoadedDoc:
    """装载后的文档。"""

    source: str
    text: str
    path: Optional[Path] = None
    media_type: str = "markdown"


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def load_manifest(path: Optional[Path] = None) -> Dict[str, Any]:
    """读取知识库清单 knowledge/manifest.json（不存在时按目录自动发现）。"""
    manifest_path = path or (app_config.KNOWLEDGE_DIR / "manifest.json")
    if manifest_path.exists():
        try:
            return json.loads(_read_text(manifest_path))
        except Exception as exc:
            LOGGER.warning("manifest.json 解析失败：%s", exc)
    # 自动发现兜底
    domains = []
    for key in app_config.DOMAIN_CHOICES:
        domain = app_config.get_domain(key)
        docs = sorted(
            p.name for p in domain.knowledge_path.glob("*") if p.suffix.lower() in {".md", ".txt", ".json"}
        ) if domain.knowledge_path.exists() else []
        domains.append(
            {
                "key": domain.key,
                "name": domain.name,
                "description": domain.description,
                "path": domain.knowledge_subdir,
                "collection": domain.collection_name,
                "documents": docs,
            }
        )
    return {"version": 1, "default_domain": app_config.DEFAULT_DOMAIN, "domains": domains}


def manifest_domains(manifest: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    manifest = manifest or load_manifest()
    return list(manifest.get("domains") or [])


def manifest_stat(manifest: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """统计预置知识库文件数量（供界面显示）。"""
    manifest = manifest or load_manifest()
    files = 0
    chars = 0
    for domain in manifest.get("domains") or []:
        base = app_config.KNOWLEDGE_DIR / str(domain.get("path") or domain.get("key") or "")
        for name in domain.get("documents") or []:
            path = base / name
            if path.exists():
                files += 1
                chars += len(_read_text(path))
    return {"files": files, "chars": chars, "domains": len(manifest.get("domains") or [])}


def load_domain_docs(domain_key: str, manifest: Optional[Dict[str, Any]] = None) -> List[LoadedDoc]:
    """装载某个业务域的预置文档（按 manifest 声明顺序，以保持叙述逻辑）。"""
    domain = app_config.get_domain(domain_key)
    base = domain.knowledge_path
    docs: List[LoadedDoc] = []
    declared: List[str] = []
    for item in manifest_domains(manifest):
        if str(item.get("key")) == domain_key:
            declared = [str(n) for n in (item.get("documents") or [])]
            break
    if not declared and base.exists():
        declared = sorted(p.name for p in base.glob("*") if p.suffix.lower() in {".md", ".txt", ".json"})
    for name in declared:
        path = base / name
        if not path.exists():
            LOGGER.warning("知识文件缺失：%s", path)
            continue
        text = _read_text(path)
        if not text.strip():
            continue
        media = "json" if path.suffix.lower() == ".json" else ("markdown" if path.suffix.lower() == ".md" else "text")
        docs.append(LoadedDoc(source=name, text=text, path=path, media_type=media))
    return docs


# ---------------------------------------------------------------------------
# 切分
# ---------------------------------------------------------------------------
def _hard_split(text: str, size: int, overlap: int) -> List[str]:
    """按长度硬切，尽量在换行处断开。"""
    if len(text) <= size:
        return [text]
    parts: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text.rfind("\n", start + int(size * 0.6), end)
            if window > start:
                end = window
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in parts if p]


def split_markdown(text: str, *, chunk_size: int, chunk_overlap: int, source: str) -> List[Dict[str, Any]]:
    """按 Markdown 标题层级切分，保留标题路径。"""
    lines = text.splitlines()
    stack: List[tuple] = []  # (level, title)
    buffer: List[str] = []
    buffer_heading: List[str] = []
    chunks: List[Dict[str, Any]] = []
    in_fence = False

    def heading_path() -> List[str]:
        return [title for _, title in stack]

    def flush() -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if not body:
            buffer = []
            return
        heading = " > ".join(buffer_heading)
        for piece in _hard_split(body, chunk_size, chunk_overlap):
            chunks.append(
                {
                    "text": (f"【{heading}】\n{piece}" if heading else piece),
                    "metadata": {"source": source, "heading": heading},
                }
            )
        buffer = []

    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            if buffer:
                buffer.append(line)
            continue
        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            stack = [(lv, tt) for lv, tt in stack if lv < level]
            stack.append((level, title))
            buffer_heading = heading_path()
            buffer = [line]
            continue
        if not buffer:
            buffer_heading = heading_path()
        buffer.append(line)
    flush()
    return chunks


def split_json(text: str, *, source: str, max_chars: int = 1200) -> List[Dict[str, Any]]:
    """把 JSON 文档按「顶层键 / 数组元素」拆成片段，避免整篇被截断。"""
    chunks: List[Dict[str, Any]] = []
    try:
        payload = json.loads(text)
    except Exception:
        return [
            {"text": piece, "metadata": {"source": source, "heading": ""}}
            for piece in _hard_split(text, max_chars, 120)
        ]

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        else:
            rendered = json.dumps({path or "value": node}, ensure_ascii=False)
            chunks.append({"text": rendered, "metadata": {"source": source, "heading": path}})

    walk(payload, "")
    if not chunks:
        chunks = [{"text": text[:max_chars], "metadata": {"source": source, "heading": ""}}]
    grouped: List[Dict[str, Any]] = []
    current = ""
    for chunk in chunks:
        if len(current) + len(chunk["text"]) > max_chars and current:
            grouped.append({"text": current, "metadata": {"source": source, "heading": "JSON 片段"}})
            current = ""
        current += chunk["text"] + "\n"
    if current.strip():
        grouped.append({"text": current.strip(), "metadata": {"source": source, "heading": "JSON 片段"}})
    return grouped


def split_text(
    text: str,
    *,
    source: str,
    media_type: str = "markdown",
    chunk_size: int = app_config.CHUNK_SIZE,
    chunk_overlap: int = app_config.CHUNK_OVERLAP,
) -> List[Dict[str, Any]]:
    """统一入口：按媒体类型选择切分器，并对超长片段做二次切分。"""
    if media_type == "json":
        base = split_json(text, source=source)
    elif media_type == "markdown":
        base = split_markdown(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap, source=source)
    else:
        base = [
            {"text": piece, "metadata": {"source": source, "heading": ""}}
            for piece in _hard_split(text, chunk_size, chunk_overlap)
        ]
    # 兜底：二次切分超长片段
    result: List[Dict[str, Any]] = []
    for chunk in base:
        if len(chunk["text"]) <= chunk_size * 2:
            result.append(chunk)
            continue
        for piece in _hard_split(chunk["text"], chunk_size, chunk_overlap):
            result.append({"text": piece, "metadata": dict(chunk["metadata"])})
    return [c for c in result if c["text"].strip()]


# ---------------------------------------------------------------------------
# 便捷索引 API
# ---------------------------------------------------------------------------
def build_domain(
    kb,
    domain_key: str,
    *,
    manifest: Optional[Dict[str, Any]] = None,
    on_log=None,
) -> Dict[str, Any]:
    """构建（重建）某个业务域的预置知识库。"""
    domain = app_config.get_domain(domain_key)
    docs = load_domain_docs(domain_key, manifest)
    if not docs:
        return {"domain": domain_key, "chunks": 0, "files": 0, "message": f"{domain.name} 目录下没有可用文档"}

    chunks: List[Dict[str, Any]] = []
    for doc in docs:
        pieces = split_text(doc.text, source=doc.source, media_type=doc.media_type)
        for piece in pieces:
            piece["metadata"].update({"doc_path": str(doc.path) if doc.path else ""})
        chunks.extend(pieces)
        if on_log:
            on_log(f"切分 {doc.source} → {len(pieces)} 个片段")

    count = kb.index_chunks(domain_key, chunks, append=False, source_type="preset")
    if on_log:
        on_log(f"{domain.name} 索引完成：{len(docs)} 个文件，{count} 个片段")
    return {"domain": domain_key, "chunks": count, "files": len(docs), "message": "ok"}


def build_all(kb, *, manifest: Optional[Dict[str, Any]] = None, on_log=None) -> List[Dict[str, Any]]:
    manifest = manifest or load_manifest()
    results = []
    for key in app_config.DOMAIN_CHOICES:
        results.append(build_domain(kb, key, manifest=manifest, on_log=on_log))
    return results


def index_user_document(
    kb,
    domain_key: str,
    text: str,
    *,
    source: str = "当前会话粘贴的API文档",
    media_type: str = "markdown",
    on_log=None,
) -> Dict[str, Any]:
    """把用户粘贴/上传的 API 文档索引到当前业务域（`source_type=upload`）。"""
    text = (text or "").strip()
    if not text:
        return {"chunks": 0, "message": "内容为空，未索引"}
    pieces = split_text(text, source=source, media_type=media_type)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    for piece in pieces:
        piece["metadata"].update({"uploaded_at": stamp, "heading": piece["metadata"].get("heading") or source})
    count = kb.index_chunks(domain_key, pieces, append=True, source_type="upload")
    if on_log:
        on_log(f"已索引「{source}」到 {app_config.get_domain(domain_key).name}：{count} 个片段")
    return {"chunks": count, "message": "ok", "source": source, "domain": domain_key}


def save_upload(filename: str, content: bytes) -> Path:
    """把上传文件落盘到 storage/uploads（便于复现与二次索引）。"""
    app_config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fff.\-]+", "_", filename)[:120] or "upload.txt"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = app_config.UPLOAD_DIR / f"{stamp}-{safe}"
    path.write_bytes(content)
    return path


__all__ = [
    "LoadedDoc",
    "load_manifest",
    "manifest_domains",
    "manifest_stat",
    "load_domain_docs",
    "split_text",
    "split_markdown",
    "split_json",
    "build_domain",
    "build_all",
    "index_user_document",
    "save_upload",
]
