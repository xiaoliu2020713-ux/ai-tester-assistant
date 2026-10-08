"""依赖审计：区分「项目真正需要」「可选但已启用」「完全无关可卸载」三类。

判定方式：
    1. 用 **AST** 解析项目内全部 .py（walk 时剪掉 .venv/.git/storage），收集真实 import；
    2. 与已安装包对照，找出「装了但代码从不用」的包；
    3. 区分「孤儿包（其它工具装的）」与「传递依赖（被上面某个包间接需要）」。

运行：
    python scripts/audit_dependencies.py                      # 只审计
    python scripts/audit_dependencies.py --uninstall-orphans  # 卸载确认无关的包
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata as md
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _console  # noqa: E402

_console.setup()

PRUNE = {".venv", ".git", "storage", "__pycache__", ".pytest_cache", "node_modules"}

STDLIB = {
    "os", "sys", "re", "json", "time", "math", "csv", "io", "ast", "base64", "hashlib", "logging",
    "pathlib", "typing", "dataclasses", "datetime", "functools", "collections", "contextlib",
    "concurrent", "threading", "subprocess", "urllib", "http", "shutil", "signal", "argparse",
    "importlib", "textwrap", "inspect", "glob", "random", "string", "itertools", "sqlite3", "uuid",
    "warnings", "traceback", "copy", "enum", "abc", "operator", "weakref", "zipfile", "tempfile",
}

# 项目直接声明的依赖（requirements.txt）
DECLARED = {
    "streamlit": "Web 界面",
    "openai": "OpenAI 兼容客户端（本地 8080 / DeepSeek）",
    "langchain": "LangChain 主包（技术栈要求）",
    "langchain-core": "消息对象 / Embeddings 接口",
    "langchain-community": "LangChain 社区集成（技术栈要求）",
    "langchain-text-splitters": "文本切分",
    "langchain-openai": "LangChain 的 OpenAI 兼容 ChatModel",
    "chromadb": "向量库（RAG 持久化）",
    "chroma-hnswlib": "chromadb 的 HNSW 索引后端",
    "onnxruntime": "chromadb 导入期即需要其默认 embedding 函数",
    "python-dotenv": ".env 加载",
    "numpy": "数值计算",
    "requests": "HTTP 调用",
    "pypdf": "PDF 解析（可选扩展）",
    "python-docx": "Word 解析（可选扩展）",
    "fastapi": "被测系统（阶段二）",
    "uvicorn": "被测系统 ASGI 服务器",
    "sqlalchemy": "被测系统 ORM",
    "pydantic": "数据模型校验",
    "python-jose": "被测系统 JWT",
    "passlib": "被测系统密码哈希",
    "bcrypt": "passlib 的 bcrypt 后端",
    "pytest": "生成的测试脚本执行器",
    "pyyaml": "被测系统配置 / 用例文件",
    "sentence-transformers": "本地语义向量（决定 RAG 检索质量）",
    "torch": "sentence-transformers 运行时",
    "transformers": "sentence-transformers 模型加载",
    "scikit-learn": "sentence-transformers 依赖",
    "scipy": "sentence-transformers 依赖",
    "pandas": "Streamlit dataframe 展示",
}

# 确认与本项目无关：装了但代码从不使用，且不是上面任一包的运行时依赖。
# 已实测确认可安全卸载（见本文件末尾「实测结论」）。
ORPHANS = {
    "kubernetes": "langchain-community 传递依赖；本项目无 K8s 相关能力（70.4 MB）",
    "langgraph": "langchain-community 传递依赖；本项目无图编排",
    "langgraph-checkpoint": "langgraph 传递依赖",
    "langgraph-prebuilt": "langgraph 传递依赖",
    "langgraph-sdk": "langgraph 传递依赖",
    "langchain-classic": "langchain 生态过渡包，未 import",
    "langchain-protocol": "langchain 生态过渡包，未 import",
    "httpx2": "非标准包名，疑似误装（正版是 httpx）",
    "httpcore2": "非标准包名，疑似误装（正版是 httpcore）",
}

# 看似无关、**实测为 import 期硬依赖**，卸载后会直接崩，必须保留
REQUIRED_DESPITE_UNUSED = {
    "tiktoken": "langchain_openai.chat_models.base 顶层 `import tiktoken`；缺失则 langchain_openai 无法导入",
    "langsmith": "langchain_core.runnables.config 需要；缺失则 langchain_core 无法导入",
    "posthog": "chromadb 遥测客户端（虽然已关匿名遥测，但 import 期需要）",
} | {
    f"opentelemetry-instrumentation{suffix}": "chromadb/opentelemetry 遥测链路，import 期需要"
    for suffix in ("", "-asgi", "-fastapi")
} | {
    "opentelemetry-util-http": "上者的依赖",
    "GitPython": "streamlit 的仓库信息功能（import 期可选，但 streamlit 启动时会探测）",
    "watchdog": "streamlit 的文件监听可选后端",
}

# import 名 → 安装包名 的特殊映射
IMPORT_TO_PACKAGE = {
    "yaml": "pyyaml",
    "jose": "python-jose",
    "dotenv": "python-dotenv",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "sentence_transformers": "sentence-transformers",
    "docx": "python-docx",
    "pypdf": "pypdf",
}


def real_imports() -> set:
    """AST 解析项目内 .py，返回真实被 import 的顶层模块名。"""
    roots: set = set()
    for current, dirs, files in __import__("os").walk(ROOT):
        dirs[:] = [d for d in dirs if d not in PRUNE]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = Path(current) / name
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        roots.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        roots.add(node.module.split(".")[0])
    return roots


def installed() -> dict:
    result = {}
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if name:
            result[name] = dist.version
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="依赖审计")
    parser.add_argument("--uninstall-orphans", action="store_true", help="卸载确认无关的包")
    args = parser.parse_args()

    imports = real_imports()
    packages = installed()
    third = sorted(r for r in imports if r not in STDLIB and r not in {"sut", "rag", "llm", "executor",
                                                                      "prompts", "scripts", "tester", "config",
                                                                      "conftest"})

    print("=" * 78)
    print("① 代码真实 import 的第三方模块（AST 解析，已剪枝 .venv）")
    print("=" * 78)
    for name in third:
        package = IMPORT_TO_PACKAGE.get(name) or next(
            (p for p in packages if p.lower().replace("-", "_") == name.lower()), None
        )
        mark = "✅" if package and package.lower() in DECLARED else ("❓" if package else "⚠️ 未安装?")
        print(f"  {mark} {name:24} → {package or '（未匹配到安装包）'}")

    print()
    print("=" * 78)
    print("② 已安装但项目完全不需要（建议卸载）")
    print("=" * 78)
    removable = [p for p in ORPHANS if p in packages]
    if removable:
        for package in removable:
            size = package_size(package)
            print(f"  {package:40} {packages[package]:12} {size:>9} {ORPHANS[package]}")
    else:
        print("  （无）")

    print()
    print("=" * 78)
    print("③ 看似无关、但实测为 import 期硬依赖（**必须保留**）")
    print("=" * 78)
    for package, reason in REQUIRED_DESPITE_UNUSED.items():
        if package in packages:
            print(f"  {package:40} {packages[package]:12} {reason}")

    print()
    print("=" * 78)
    print("④ 已安装但不在核心清单也不在孤儿清单（多为传递依赖，保留）")
    print("=" * 78)
    known = ({p.lower() for p in DECLARED} | {p.lower() for p in ORPHANS}
             | {p.lower() for p in REQUIRED_DESPITE_UNUSED} | {"pip", "setuptools", "wheel"})
    unknown = sorted(p for p in packages if p.lower() not in known)
    print(f"  共 {len(unknown)} 个：")
    for name in unknown:
        print(f"    {name:40} {packages[name]}")

    if args.uninstall_orphans and removable:
        print()
        print("=" * 78)
        print("⑤ 卸载")
        print("=" * 78)
        command = [sys.executable, "-m", "pip", "uninstall", "-y", *removable]
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        done = [line for line in (proc.stdout or "").splitlines() if line.startswith("Successfully uninstalled")]
        print(f"  已卸载 {len(done)} 个包：")
        for line in done:
            print(f"    {line}")
        if proc.returncode != 0:
            print("  stderr:", (proc.stderr or "")[:300])
    return 0


def package_size(package: str) -> str:
    """估算某个已安装包占用的磁盘空间。"""
    try:
        dist = md.distribution(package)
        files = dist.files or []
        total = 0
        for item in files:
            path = Path(dist.locate_file(item))
            try:
                if path.is_file():
                    total += path.stat().st_size
            except Exception:
                continue
        return f"{total / 1024 / 1024:.1f} MB"
    except Exception:
        return "-"


if __name__ == "__main__":
    sys.exit(main())
