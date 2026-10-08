"""用本机已保存的 GitHub 凭据创建仓库（不打印 token 本身）。

流程：
    1. 通过 `git credential fill` 取出 Windows 凭据管理器里已存的 GitHub token；
    2. 调 GitHub API 校验 token 与账号；
    3. 创建仓库（幂等：已存在则直接返回）；
    4. 只输出结果，绝不回显 token。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

GIT = r"C:\Program Files\Git\cmd\git.exe"
OWNER = "xiaoliu2020713-ux"
REPO = "ai-tester-assistant"
DESCRIPTION = (
    "AI 测试员助手平台：Streamlit 对话界面 + 多域 RAG 知识库 + 可配置大模型（本地 llama.cpp/DeepSeek）；"
    "阶段二把用例转成 pytest 脚本，并自带 4 个 FastAPI+SQLAlchemy+SQLite+JWT 被测系统（44 条故意植入的缺陷）"
)
# 直连 GitHub（本机代理出口 IP 被限流，直连正常）
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def resolve_token() -> tuple[str, str]:
    """解析 GitHub token，返回 `(token, 来源说明)`。

    优先级：
        1. 命令行参数 `--token`
        2. 环境变量 `GITHUB_TOKEN` / `GH_TOKEN`
        3. `git credential fill`（Windows 凭据管理器）

    注意：Git Credential Manager 在无凭据时会返回 `password=...provider=github...` 这类
    **占位串**（而不是真正的 token），长度可能 80+ 但并不能用，因此必须靠 API 校验兜底。
    """
    import argparse

    parser = argparse.ArgumentParser(description="用 GitHub token 创建仓库并推送")
    parser.add_argument("--token", default=None, help="GitHub Personal Access Token（含 repo 权限）")
    parser.add_argument("--repo", default=REPO, help="仓库名")
    parser.add_argument("--private", action="store_true", help="创建为私有仓库")
    args = parser.parse_args()

    if args.token:
        return args.token.strip(), "命令行参数"
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.getenv(key)
        if value:
            return value.strip(), f"环境变量 {key}"
    return git_credential_token(), "git 凭据管理器"


def git_credential_token() -> str:
    """从 git 凭据管理器取 token（不落盘、不回显）。"""
    payload = "protocol=https\nhost=github.com\n\n"
    try:
        proc = subprocess.run(
            [GIT, "credential", "fill"],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"},
        )
    except Exception:
        return ""
    for line in (proc.stdout or "").splitlines():
        if not line.startswith("password="):
            continue
        value = line.split("=", 1)[1].strip()
        # GCM 的占位串形如 "password=...provider=github.com..."，含 provider= 即为无效凭据
        if "provider=" in value or value.startswith("Pass"):
            return ""
        return value
    return ""


def api(token: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
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
    token, source = resolve_token()
    if not token:
        print("[FAIL] 未找到可用的 GitHub token")
        print("       请任选一种方式提供：")
        print("         1) .\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\github_publish.py --token <你的PAT>")
        print("         2) 设置环境变量 GITHUB_TOKEN 后重跑")
        print("       token 需要 repo 权限（classic 勾选 repo；fine-grained 勾选 Administration: Read and write）")
        return 2
    print(f"[1/3] 使用凭据来源：{source}（前缀 {token[:4]}…，长度 {len(token)}，不显示完整 token）")

    status, payload = api(token, "GET", "/user")
    if status != 200:
        print(f"[FAIL] token 校验失败：HTTP {status} {payload.get('message', '')}")
        print("       该凭据已失效或权限不足，请提供新的 Personal Access Token。")
        return 3
    login = payload.get("login", "")
    print(f"[2/3] token 有效，登录账号：{login}")
    if login != OWNER:
        print(f"[WARN] 当前账号 {login} 与预期 {OWNER} 不一致，后续将在 {login} 下创建仓库")

    owner = login or OWNER
    status, payload = api(token, "GET", f"/repos/{owner}/{REPO}")
    if status == 200:
        print(f"[3/3] 仓库已存在：{payload.get('html_url')}")
        return 0

    status, payload = api(token, "POST", "/user/repos", {
        "name": REPO,
        "description": DESCRIPTION,
        "private": False,
        "has_issues": True,
        "has_wiki": False,
        "has_projects": False,
        "auto_init": False,
    })
    if status in (200, 201):
        print(f"[3/3] ✅ 仓库已创建：{payload.get('html_url')}")
        return 0
    print(f"[FAIL] 创建仓库失败：HTTP {status} {payload.get('message', '')}")
    if status == 403:
        print("       该 token 缺少创建仓库的权限（需要 repo / Administration: Read and write）。")
    if status == 422:
        print("       可能是仓库名冲突。")
    return 4


if __name__ == "__main__":
    sys.exit(main())
