"""检查知识库索引状态与语义检索质量。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag.knowledge_base import KnowledgeBaseManager

PROBES = [
    ("library", "同一本书被很多人抢着借，怎么排队和扣库存"),
    ("library", "逾期还书要罚多少钱，有没有上限"),
    ("ecommerce", "钱付了但订单状态没更新怎么办"),
    ("ecommerce", "一个人用同一个请求号重复下单会怎样"),
    ("course", "抢最后一个名额按什么顺序，超了怎么办"),
    ("course", "学分修够多少才能毕业"),
]


def main() -> int:
    started = time.time()
    manager = KnowledgeBaseManager()
    print(f"KnowledgeBaseManager 初始化：{time.time() - started:.2f}s")
    print(f"embedding_info：{manager.embedding_info}")
    print()

    print("=" * 78)
    print("索引状态")
    print("=" * 78)
    tick = time.time()
    statuses = manager.statuses()
    print(f"statuses() 耗时 {time.time() - tick:.2f}s")
    for status in statuses:
        print(f"  {status.key:<11} {status.name:<12} {status.count:>4} 片段  "
              f"kind={status.embedding_kind:<20} dim={status.dim}")
        print(f"      built_at={status.built_at}")
    stale = manager.stale_domains()
    print(f"  需重建的域：{[s.key for s in stale] or '无（全部一致）✅'}")
    print(f"  预置文档：{manager.preset_stat()}")
    print()

    print("=" * 78)
    print("语义检索实测（提问用词与文档不同，检验是否真语义）")
    print("=" * 78)
    for domain, question in PROBES:
        tick = time.time()
        hits = manager.search(question, domain, top_k=3)
        elapsed = time.time() - tick
        print(f"\n  [{domain}] {question}   （{elapsed:.2f}s，命中 {len(hits)}）")
        for hit in hits:
            text = hit.text.replace("\n", " ")[:70]
            source = (hit.metadata or {}).get("source") or (hit.metadata or {}).get("source_name") or "?"
            print(f"      {hit.score:.3f}  {source}: {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
