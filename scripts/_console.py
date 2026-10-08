"""脚本控制台输出工具：统一 UTF-8 编码，兼容 Windows GBK 控制台。

Windows 下 `python scripts/xxx.py` 默认使用 GBK 编码 stdout，
输出 emoji（✅ / ❌ / ⚠️）会抛 UnicodeEncodeError。本模块：

1. 尽力把 stdout/stderr 切到 UTF-8；
2. 若编码仍不支持 emoji（如被重定向到 GBK 文件），自动把标记替换为 ASCII 文本。
"""

from __future__ import annotations

import sys

_ASCII_MAP = {
    "✅": "[OK]",
    "❌": "[FAIL]",
    "⚠️": "[WARN]",
    "⚠": "[WARN]",
    "ℹ️": "[INFO]",
    "ℹ": "[INFO]",
    "📚": "",
    "🧪": "",
    "·": "-",
    "—": "-",
    "…": "...",
}


def setup() -> None:
    """在脚本开头调用一次。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # pragma: no cover
            pass


def safe(text: str) -> str:
    """对当前 stdout 编码做安全化处理。"""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        for source, target in _ASCII_MAP.items():
            text = text.replace(source, target)
        return text


def out(text: str = "") -> None:
    """带安全化处理的 print。"""
    print(safe(text))


def mark(ok: bool) -> str:
    return safe("✅" if ok else "❌")
