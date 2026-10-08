"""校验 README 目录锚点是否与标题严格匹配。

GitHub 的锚点生成规则（简化但等价）：
    1. 转小写
    2. 去掉除「字母 / 数字 / 空格 / 连字符 / 下划线 / 中文」以外的所有字符
       （emoji、`、（）`、`、`、`.`、`/` 等都会被删掉）
    3. 空格替换为 `-`
    4. 同名标题追加 `-1`、`-2` …

本脚本把 README 里所有 `](#xxx)` 链接与按上述规则算出的标题锚点比对，
不匹配就报错并给出正确锚点。

运行：
    python scripts/check_readme_toc.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

# GitHub 保留的字符：字母、数字、空格、连字符、下划线、CJK
KEEP = re.compile(r"[^\w\s\-\u4e00-\u9fff]", re.UNICODE)


def github_slug(title: str) -> str:
    """按 GitHub 规则把标题转成锚点。"""
    slug = title.strip().lower()
    slug = KEEP.sub("", slug)          # 去掉 emoji、括号、反引号等
    slug = slug.replace(" ", "-")      # 空格转连字符
    return slug


def main() -> int:
    text = README.read_text(encoding="utf-8")
    lines = text.splitlines()

    # 收集标题时跳过围栏代码块（否则 ``` 里的 `# 注释` 会被误判为标题）
    headings: list[str] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            headings.append(match.group(2).strip())

    # 同名标题加 -1 / -2 后缀
    anchors: dict[str, str] = {}
    seen: dict[str, int] = {}
    for title in headings:
        base = github_slug(title)
        count = seen.get(base, 0)
        anchors[title] = base if count == 0 else f"{base}-{count}"
        seen[base] = count + 1

    valid = set(anchors.values())
    print("=" * 74)
    print(f"README 共 {len(headings)} 个标题，生成 {len(valid)} 个锚点")
    print("=" * 74)
    for title, anchor in anchors.items():
        print(f"  #{anchor:<46} ← {title}")

    print()
    print("=" * 74)
    print("目录链接校验")
    print("=" * 74)
    problems: list[str] = []
    for index, line in enumerate(lines, start=1):
        for link in re.findall(r"\]\(#([^)]+)\)", line):
            if link in valid:
                print(f"  ✅ 行 {index}: #{link}")
            else:
                problems.append(link)
                print(f"  ❌ 行 {index}: #{link}  ← 无对应标题")

    print()
    if problems:
        print(f"发现 {len(problems)} 个失效锚点：")
        for item in problems:
            print(f"  - #{item}")
        print()
        print("可用的锚点列表见上方。")
        return 1
    print("✅ 全部目录锚点均有效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
