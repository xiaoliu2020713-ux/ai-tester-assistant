"""命令行：构建 AI 测试员助手的多域 RAG 知识库。

用法：
    python scripts/build_kb.py                 # 构建全部业务域（预置知识库）
    python scripts/build_kb.py library         # 只构建图书管理系统的域
    python scripts/build_kb.py library course  # 构建指定多个域
    python scripts/build_kb.py --reset         # 先删除全部集合与索引侧车文件再重建
    python scripts/build_kb.py --provider hash # 指定向量模型提供方（auto/openai_api/sentence_transformer/hash）
    python scripts/build_kb.py --stat          # 只打印当前知识库状态
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许以 `python scripts/build_kb.py` 方式运行
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，统一改为 UTF-8，避免中文/符号输出报错
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

import config as app_config  # noqa: E402
from rag.knowledge_base import KnowledgeBaseManager  # noqa: E402


def print_status(manager: KnowledgeBaseManager) -> None:
    print("\n当前知识库状态")
    print("-" * 64)
    for status in manager.statuses():
        flag = "已就绪" if status.ready else "空（需要构建）"
        print(f"  {status.name:<12} {status.collection:<18} {status.count:>6} 片段  [{flag}]")
        if status.sources:
            preview = "、".join(status.sources[:4])
            print(f"     来源：{preview}{' …' if len(status.sources) > 4 else ''}")
    info = manager.embedding_info
    print(f"\n向量模型：{info.kind}（{info.quality_hint}），维度 {info.dim}")
    stat = manager.preset_stat()
    print(f"预置文档：{stat['domains']} 个业务域 / {stat['files']} 个文件 / 约 {stat['chars']} 字符\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建多域 RAG 知识库")
    parser.add_argument("domains", nargs="*", help="要构建的业务域 key（默认全部）")
    parser.add_argument("--reset", action="store_true", help="先删除已有集合与侧车索引再重建")
    parser.add_argument(
        "--provider",
        default=None,
        choices=["auto", "openai_api", "sentence_transformer", "hash"],
        help="向量模型提供方（默认读取 .env 的 EMBEDDING_PROVIDER）",
    )
    parser.add_argument("--stat", action="store_true", help="只查看状态，不构建")
    args = parser.parse_args()

    app_config.ensure_dirs()
    app_config.setup_logging()

    targets = args.domains or list(app_config.DOMAIN_CHOICES)
    for key in targets:
        if key not in app_config.DOMAINS:
            print(f"未知业务域：{key}（可选：{', '.join(app_config.DOMAINS)}）")
            return 2

    manager = KnowledgeBaseManager(args.provider, on_log=lambda m: print(f"  · {m}"))

    if args.reset:
        print("删除已有集合与侧车索引…")
        for key in targets:
            manager.drop(key)
    elif args.stat:
        print_status(manager)
        return 0

    print(f"开始构建：{', '.join(app_config.domain_label(k) for k in targets)}")
    for key in targets:
        result = manager.rebuild(key)
        print(
            f"✅ {app_config.domain_label(key)}："
            f"{result.get('files', 0)} 个文件 → {result.get('chunks', 0)} 个片段"
            + ("" if result.get("chunks") else f"（{result.get('message', '')}）")
        )

    print_status(manager)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
