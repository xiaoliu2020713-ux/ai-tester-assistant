"""审计 GitHub 仓库内容：确认没有敏感文件与大文件被公开，并统计顶层结构。"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter

REPO = "xiaoliu2020713-ux/ai-tester-assistant"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(url: str) -> dict:
    request = urllib.request.Request(url)
    request.add_header("User-Agent", "ai-tester-assistant-audit")
    request.add_header("Accept", "application/vnd.github+json")
    with OPENER.open(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    data = fetch(f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1")
    items = [t for t in data.get("tree", []) if t["type"] == "blob"]
    total_kb = sum(t.get("size", 0) for t in items) / 1024
    print(f"GitHub 上共 {len(items)} 个文件，合计 {total_kb:.1f} KB")
    print()

    print("=== 安全检查（这些不该出现在公开仓库）===")
    patterns = (".env", "storage/", ".venv", "ghp_", ".log", ".db", ".gguf", "credential")
    danger = [t["path"] for t in items if any(p in t["path"] for p in patterns)]
    if danger:
        for path in danger:
            print(f"  ⚠️ {path}")
    else:
        print("  ✅ 无 .env / storage / .venv / 模型 / 数据库 / 日志 / token")

    print()
    print("=== 最大的 5 个文件 ===")
    for item in sorted(items, key=lambda t: -t.get("size", 0))[:5]:
        print(f"  {item['size'] / 1024:8.1f} KB  {item['path']}")

    print()
    print("=== 顶层结构 ===")
    counter = Counter(t["path"].split("/")[0] for t in items)
    dirs = Counter()
    for item in items:
        parts = item["path"].split("/")
        if len(parts) > 1:
            dirs[parts[0]] += 1
    for name in sorted(counter):
        if name in dirs:
            print(f"  {name}/  ({dirs[name]} 个文件)")
        else:
            print(f"  {name}")

    print()
    print("=== .env.example 是否含真实密钥 ===")
    try:
        blob = fetch(f"https://api.github.com/repos/{REPO}/contents/.env.example")
        import base64

        text = base64.b64decode(blob["content"]).decode("utf-8")
        risky = [line for line in text.splitlines()
                 if "API_KEY" in line and "=" in line and line.split("=", 1)[1].strip() not in ("", "sk-local")]
        print("  风险行:", risky or "无 ✅（只含 sk-local / 空值占位）")
    except Exception as exc:
        print("  跳过:", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
