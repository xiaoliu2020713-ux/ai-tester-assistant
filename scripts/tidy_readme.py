"""整理 README 的章节分隔线：每个「返回目录」导航前只保留一条 `---`。

背景：`scripts/add_readme_nav.py` 插入导航时会补一条 `---`，
如果原章节末尾本来就有分隔线，就会出现两条。本脚本做幂等整理。

用法：
    python scripts/tidy_readme.py            # 预览
    python scripts/tidy_readme.py --apply    # 写入
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
NAV_PREFIX = '<div align="right">'


def tidy(lines: list[str]) -> tuple[list[str], int]:
    """把连续的 `---`（允许中间夹空行）合并成一条，返回 (新行, 修复数)。

    典型场景：原文章节末尾已有一条 `---`，导航脚本又补了一条，形成
        ---
        (空行)
        ---
        <div align="right">…</div>
    这里合并为单条分隔线 + 导航。
    """
    out: list[str] = []
    fixed = 0
    pending_rule = False          # 已经见过一条 ---，正在等待是否重复
    for line in lines:
        is_rule = line.strip() == "---"
        if is_rule and pending_rule:
            fixed += 1            # 上一条 --- 与本条之间只有空行 → 丢掉这一条
            continue
        if is_rule:
            pending_rule = True
            out.append(line)
            continue
        if line.strip():
            pending_rule = False  # 出现实质内容，重置
        out.append(line)
    return out, fixed


def report(path: Path, lines: list[str]) -> None:
    duplicates = [i + 1 for i in range(len(lines) - 1)
                  if lines[i].strip() == "---" and lines[i + 1].strip() == "---"]
    nav = [i + 1 for i, line in enumerate(lines) if line.startswith(NAV_PREFIX)]
    print(f"  {path.name}: {len(lines)} 行，导航链接 {len(nav)} 处，连续分隔线 {duplicates or '无'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="整理 README 分隔线")
    parser.add_argument("--apply", action="store_true", help="写入（默认只预览）")
    args = parser.parse_args()

    original = README.read_text(encoding="utf-8").splitlines()
    print("整理前：")
    report(README, original)

    new_lines, fixed = tidy(original)
    print("整理后：")
    report(README, new_lines)
    print(f"  修正重复分隔线 {fixed} 处")

    if not args.apply:
        print()
        print("预览模式，未写入。加 --apply 实际执行。")
        return 0

    README.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print()
    print("✅ 已写入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
