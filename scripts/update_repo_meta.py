"""更新 GitHub 仓库的描述与主题标签（让仓库页展示更完整）。

用法：
    set GITHUB_TOKEN=<PAT>          # 需要 repo 权限
    python scripts/update_repo_meta.py
    python scripts/update_repo_meta.py --description "..." --topics a b c
    python scripts/update_repo_meta.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

OWNER = "xiaoliu2020713-ux"
REPO = "ai-tester-assistant"

DEFAULT_DESCRIPTION = (
    "AI 测试员助手平台：Streamlit 对话界面 + 多域 RAG 知识库（LangChain/ChromaDB）+ 可配置大模型；"
    "可把用例转成 pytest 脚本真实执行，自带 FastAPI+SQLAlchemy+SQLite+JWT 被测系统（含 Allure 报告）"
)

# GitHub 主题标签：最多 20 个，只能用小写字母/数字/连字符，每个 ≤ 35 字符
DEFAULT_TOPICS = [
    "ai-testing", "test-automation", "pytest", "allure", "fastapi", "sqlalchemy",
    "sqlite", "jwt", "rag", "langchain", "chromadb", "streamlit",
    "llm", "openai-compatible", "test-case-generation", "api-testing",
    "python", "bug-detection", "knowledge-base", "llama-cpp",
]

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(token: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(f"https://api.github.com{path}", data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "ai-tester-assistant")
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with OPENER.open(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except Exception:
            return exc.code, {"raw": raw[:300]}


def main() -> int:
    parser = argparse.ArgumentParser(description="更新 GitHub 仓库描述与主题")
    parser.add_argument("--owner", default=OWNER)
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION)
    parser.add_argument("--topics", nargs="*", default=DEFAULT_TOPICS)
    parser.add_argument("--dry-run", action="store_true", help="只打印将要写入的内容")
    args = parser.parse_args()

    print("=" * 74)
    print("将要写入的仓库元信息")
    print("=" * 74)
    print(f"  仓库：{args.owner}/{args.repo}")
    print(f"  描述：{args.description}")
    print(f"  主题（{len(args.topics)} 个）：{', '.join(args.topics)}")

    if args.dry_run:
        print()
        print("（dry-run，未调用 API）")
        return 0

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        print()
        print("❌ 未设置 GITHUB_TOKEN 环境变量，无法调用 API。")
        print("   PowerShell: $env:GITHUB_TOKEN='<你的PAT>'; python scripts/update_repo_meta.py")
        return 2

    print()
    print("=" * 74)
    print("写入")
    print("=" * 74)

    status, payload = api(token, "PATCH", f"/repos/{args.owner}/{args.repo}",
                          {"description": args.description,
                           "homepage": f"https://github.com/{args.owner}/{args.repo}#readme"})
    if status == 200:
        print(f"  ✅ 描述已更新（{len(args.description)} 字符）")
    else:
        print(f"  ❌ 描述更新失败：HTTP {status} {payload.get('message', payload)}")

    status, payload = api(token, "PUT", f"/repos/{args.owner}/{args.repo}/topics",
                          {"names": args.topics})
    if status == 200:
        actual = payload.get("names") or []
        print(f"  ✅ 主题已更新，共 {len(actual)} 个：{', '.join(actual)}")
    else:
        print(f"  ❌ 主题更新失败：HTTP {status} {payload.get('message', payload)}")

    print()
    print(f"仓库页：https://github.com/{args.owner}/{args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
