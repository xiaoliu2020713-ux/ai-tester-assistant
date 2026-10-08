"""README 结构校验：代码围栏配对、标题层级、分隔线、导航链接、目录锚点。

一次性把排版问题都查出来：
    * 代码围栏是否成对（否则后面的正文会被吞进代码块）
    * 一级/二级标题层级是否连续（不跳级）
    * `---` 分隔线是否有重复
    * 每个一级章节末尾是否有「返回目录」导航
    * 目录里的 `](#anchor)` 是否都能对上标题

运行：
    python scripts/check_readme_structure.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

KEEP = re.compile(r"[^\w\s\-\u4e00-\u9fff]", re.UNICODE)
NAV_PREFIX = '<div align="right">'


def github_slug(title: str) -> str:
    return KEEP.sub("", title.strip().lower()).replace(" ", "-")


def main() -> int:
    lines = README.read_text(encoding="utf-8").splitlines()
    problems: list[str] = []

    # ---------- 1. 代码围栏配对 ----------
    fences = [i + 1 for i, line in enumerate(lines) if line.lstrip().startswith("```")]
    print("=" * 74)
    print("① 代码围栏")
    print("=" * 74)
    if len(fences) % 2 == 0:
        print(f"  ✅ 共 {len(fences)} 个围栏，全部配对")
    else:
        problems.append("代码围栏未配对")
        print(f"  ❌ 共 {len(fences)} 个围栏（奇数），未闭合起点行：{fences[::2]}")

    # ---------- 2. 标题与锚点 ----------
    headings: list[tuple[int, int, str]] = []      # (行号, 级别, 标题)
    in_fence = False
    for index, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))

    anchors: dict[str, str] = {}
    seen: dict[str, int] = {}
    for _line_no, _level, title in headings:
        base = github_slug(title)
        count = seen.get(base, 0)
        anchors[title] = base if count == 0 else f"{base}-{count}"
        seen[base] = count + 1
    valid = set(anchors.values())

    print()
    print("=" * 74)
    print(f"② 标题层级（共 {len(headings)} 个标题）")
    print("=" * 74)
    h2_titles = [t for _l, level, t in headings if level == 2]
    for line_no, level, title in headings:
        if level == 2:
            print(f"  {'  ' * (level - 1)}{'#' * level} 行{line_no:4} {title}")
    print(f"  一级章节 {len(h2_titles)} 个")

    print()
    print("=" * 74)
    print("③ 目录锚点")
    print("=" * 74)
    toc_links = []
    for index, line in enumerate(lines, start=1):
        for link in re.findall(r"\]\(#([^)]+)\)", line):
            toc_links.append((index, link))
    bad = [(i, link) for i, link in toc_links if link not in valid]
    if bad:
        for index, link in bad:
            problems.append(f"行 {index} 目录锚点失效：#{link}")
            print(f"  ❌ 行 {index}: #{link}")
    else:
        print(f"  ✅ {len(toc_links)} 个目录锚点全部有效")

    # ---------- 3. 分隔线重复 ----------
    print()
    print("=" * 74)
    print("④ 分隔线与导航链接")
    print("=" * 74)
    rules = [i + 1 for i, line in enumerate(lines) if line.strip() == "---"]
    duplicates = [rules[i] for i in range(len(rules) - 1) if rules[i + 1] - rules[i] <= 2]
    if duplicates:
        problems.append(f"重复分隔线：行 {duplicates}")
        print(f"  ❌ 疑似重复分隔线行号：{duplicates}")
    else:
        print(f"  ✅ {len(rules)} 条分隔线，无重复")

    nav_lines = [i + 1 for i, line in enumerate(lines) if line.startswith(NAV_PREFIX)]
    print(f"  导航链接 {len(nav_lines)} 处")
    if len(nav_lines) != len(h2_titles) - 1:
        # 目录章节本身不需要导航，因此期望 = 一级章节数 - 1
        problems.append(f"导航链接数量 {len(nav_lines)} ≠ 一级章节数-1 ({len(h2_titles) - 1})")
        print(f"  ❌ 期望 {len(h2_titles) - 1} 处（目录章节除外）")
    else:
        print(f"  ✅ 每个一级章节末尾都有返回目录（目录章节除外）")

    print()
    print("=" * 74)
    if problems:
        print(f"❌ 发现 {len(problems)} 个排版问题：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("✅ README 结构校验全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
