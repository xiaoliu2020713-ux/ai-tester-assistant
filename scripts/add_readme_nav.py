"""给 README 的每个一级章节末尾插入「返回目录」导航链接。

用法：
    python scripts/add_readme_nav.py            # 预览将要插入的位置
    python scripts/add_readme_nav.py --apply    # 实际写入

规则：
    * 在每个 `## ` 一级章节（除目录本身）的最后一行（下一个 `## ` 之前）插入
          ---
          <div align="right"><a href="#-目录">⬆ 返回目录</a></div>
    * 幂等：已经有该标记的章节会跳过，重复执行不会重复插入。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

NAV = '<div align="right"><a href="#-目录">⬆ 返回目录</a></div>'
MARKER = "⬆ 返回目录"


def main() -> int:
    parser = argparse.ArgumentParser(description="给 README 章节加返回目录链接")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认只预览）")
    args = parser.parse_args()

    lines = README.read_text(encoding="utf-8").splitlines()
    h2_indexes = [i for i, line in enumerate(lines) if re.match(r"^## ", line)]
    if not h2_indexes:
        print("未找到一级章节标题")
        return 1

    insertions: list[int] = []
    for position, start in enumerate(h2_indexes):
        title = lines[start]
        # 只跳过「目录」章节本身（注意不能简单判定 title 里是否含"目录"，
        # 因为"完整项目目录树"这类正文标题也含这两个字）
        if "📖" in title:
            continue
        end = h2_indexes[position + 1] if position + 1 < len(h2_indexes) else len(lines)
        # 若该区间已包含标记则跳过（幂等）
        if any(MARKER in line for line in lines[start:end]):
            continue
        # 从区间末尾往前找到最后一个非空行，插到它后面
        cursor = end - 1
        while cursor > start and not lines[cursor].strip():
            cursor -= 1
        insertions.append(cursor + 1)
        print(f"  将在第 {cursor + 2} 行前插入导航 ← {title}")

    if not insertions:
        print("✅ 所有章节都已有返回目录链接，无需改动。")
        return 0

    if not args.apply:
        print()
        print(f"共需插入 {len(insertions)} 处（预览模式，未写入）。加 --apply 实际执行。")
        return 0

    for offset, at in enumerate(insertions):
        target = at + offset * 3          # 每次插入 3 行，后面位置要顺延
        lines[target:target] = ["", "---", NAV]

    README.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print()
    print(f"✅ 已插入 {len(insertions)} 处导航链接。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
